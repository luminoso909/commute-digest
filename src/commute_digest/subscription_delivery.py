"""Run the daily email worker for registered Commute Digest accounts."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from time import sleep
from uuid import uuid4

from . import accounts, edition_service, news_editor
from .delivery_lock import delivery_in_progress
from .schedule import BEIJING, MORNING_TIME, latest_slot

POLL_SECONDS = 15
BUILD_LEASE = timedelta(minutes=30)
SMTP_RETRY_DELAY = timedelta(minutes=5)


def _initialize_deliveries() -> None:
    with edition_service._database() as connection:
        def columns() -> set[str]:
            return {row[1] for row in connection.execute("PRAGMA table_info(subscription_deliveries)")}

        if "slot" in columns():
            return
        connection.execute("BEGIN IMMEDIATE")
        existing = columns()
        if "slot" in existing:
            return
        if existing:
            # Keep every old ledger row, including distinct dispatches for the same publication.
            connection.execute("ALTER TABLE subscription_deliveries RENAME TO subscription_deliveries_v1")
        connection.execute(
            """
            CREATE TABLE subscription_deliveries (
                account_id INTEGER NOT NULL,
                slot TEXT NOT NULL,
                delivery_day TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                settings_key TEXT NOT NULL,
                claim TEXT NOT NULL UNIQUE,
                claimed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_at TEXT,
                issue_id INTEGER REFERENCES issues(id),
                PRIMARY KEY (account_id, slot)
            )
            """
        )
        if existing:
            rows = connection.execute(
                """SELECT account_id, delivery_day, scheduled_at, settings_key, claim,
                          claimed_at, status, retry_at, issue_id FROM subscription_deliveries_v1
                   ORDER BY CASE status WHEN 'sent' THEN 0 WHEN 'sending' THEN 1
                              WHEN 'unconfirmed' THEN 2 ELSE 3 END, claimed_at DESC"""
            ).fetchall()
            for row in rows:
                slot = latest_slot(datetime.fromisoformat(row[2])).isoformat()
                # Old retries used the same default model and had no personal model credentials.
                # Retain their generated issue if the reader has not changed the subscription.
                settings_key = row[3]
                if row[6] == "retry":
                    original = json.loads(settings_key)
                    settings_key = json.dumps([*original, news_editor.DEFAULT_MODEL, 0], sort_keys=True)
                connection.execute(
                    "INSERT OR IGNORE INTO subscription_deliveries VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (row[0], slot, row[1], row[2], settings_key, *row[4:]),
                )


def _settings_key(account: accounts.Account, due: datetime) -> str:
    morning = latest_slot(due).hour == MORNING_TIME.hour
    changed = account.morning_updated_at if morning else account.evening_updated_at
    return json.dumps((changed.isoformat(), account.preferences, account.model, account.api_revision),
                      sort_keys=True)


def _claim(
    account: accounts.Account, due: datetime, now: datetime, *, trial_key: str | None = None,
) -> tuple[str, int | None] | None:
    claim = uuid4().hex
    with edition_service._database() as connection:
        row = connection.execute(
            """
            INSERT INTO subscription_deliveries (
                account_id, slot, delivery_day, scheduled_at, settings_key, claim, claimed_at, status
            ) VALUES (:id, :slot, :day, :due, :settings, :claim, :now, 'building')
            ON CONFLICT (account_id, slot) DO UPDATE SET
                issue_id = CASE WHEN scheduled_at = excluded.scheduled_at
                                AND settings_key = excluded.settings_key
                                AND status = 'retry' THEN issue_id ELSE NULL END,
                scheduled_at = excluded.scheduled_at,
                delivery_day = excluded.delivery_day,
                settings_key = excluded.settings_key,
                claim = excluded.claim,
                claimed_at = excluded.claimed_at,
                status = 'building',
                retry_at = NULL
            WHERE status = 'cancelled'
               OR (status = 'failed' AND (:manual OR settings_key != :settings))
               OR (status = 'retry' AND (:manual OR retry_at <= :now OR settings_key != :settings))
               OR (status = 'building' AND claimed_at <= :expired)
            RETURNING issue_id
            """,
            {
                "id": account.id,
                "slot": trial_key or latest_slot(due).isoformat(),
                "manual": trial_key is not None,
                "day": due.date().isoformat(),
                "due": due.isoformat(),
                "settings": _settings_key(account, due),
                "claim": claim,
                "now": now.isoformat(),
                "expired": (now - BUILD_LEASE).isoformat(),
            },
        ).fetchone()
    return (claim, row[0]) if row is not None else None


def _set_status(
    claim: str,
    previous: str,
    status: str,
    issue_id: int | None = None,
    retry_at: datetime | None = None,
) -> bool:
    with edition_service._database() as connection:
        result = connection.execute(
            """
            UPDATE subscription_deliveries SET status = ?, issue_id = ?, retry_at = ?,
                claimed_at = COALESCE(?, claimed_at)
            WHERE claim = ? AND status = ?
            """,
            (status, issue_id, retry_at.isoformat() if retry_at else None,
             datetime.now(BEIJING).isoformat() if status == "sending" else None, claim, previous),
        )
        return result.rowcount == 1


def _report(account: accounts.Account, message: str) -> None:
    print(f"账号 {account.id} · {message}", file=sys.stderr, flush=True)


def send_claimed_issue(
    account: accounts.Account, due: datetime, now: datetime, job: tuple[str, int | None],
    *, trial: bool = False,
) -> edition_service.Issue:
    """Generate or reuse the claimed private issue and record SMTP's outcome."""
    claim, issue_id = job
    retry_at = now + SMTP_RETRY_DELAY
    try:
        settings = edition_service._mail_settings(account.delivery_email)
    except edition_service.MorningError:
        _set_status(claim, "building", "retry", issue_id, retry_at)
        raise

    try:
        issue = edition_service.get_issue(issue_id, account_id=account.id) if issue_id else None
        if issue is None:
            issue = edition_service._publish(
                account.preferences, latest_slot(due), account_id=account.id,
            )
        message = edition_service._make_message(
            issue, account.preferences, settings, due.date().isoformat(), verification=trial,
        )
    except edition_service.MorningError:
        _set_status(claim, "building", "failed")
        raise

    current = accounts.get_account(account.id)
    if (current is None or (not trial and not current.delivery_enabled) or current.delivery_email != account.delivery_email
            or _settings_key(current, due) != _settings_key(account, due)):
        _set_status(claim, "building", "cancelled")
        raise edition_service.MorningError("账号的投递设置已更改，本次投递已取消。")
    if not _set_status(claim, "building", "sending", issue["id"]):
        raise edition_service.MorningError("此投递请求已由其他进程接手。")

    try:
        edition_service._send_message(message, settings)
    except edition_service._SMTPSetupError:
        _set_status(claim, "sending", "retry", issue["id"], retry_at)
        raise
    except (OSError, edition_service.MorningError) as error:
        _set_status(claim, "sending", "unconfirmed", issue["id"])
        raise edition_service.MorningError("SMTP 未确认接收，请核对发件记录和收件箱。") from error

    _set_status(claim, "sending", "sent", issue["id"])
    return issue


@delivery_in_progress()
def _deliver(account: accounts.Account, due: datetime, now: datetime) -> bool:
    job = _claim(account, due, now)
    if job is None:
        return False
    try:
        send_claimed_issue(account, due, now, job)
    except edition_service.MorningError as error:
        _report(account, str(error))
        return False
    print(f"{due:%Y-%m-%d %H:%M} · 账号 {account.id} 的简报已由 SMTP 接收。", flush=True)
    return True


def run_due(now: datetime | None = None) -> int:
    """Process eligible subscriptions for the current local date."""
    moment = now if now is not None else datetime.now(BEIJING)
    if moment.utcoffset() is None:
        raise ValueError("Delivery time must include a timezone.")
    moment = moment.astimezone(BEIJING)
    _initialize_deliveries()
    sent = 0
    slot = latest_slot(moment)
    for account in accounts.scheduled_accounts():
        if slot.hour == MORNING_TIME.hour:
            dispatch_time, updated = account.morning_time, account.morning_updated_at
        else:
            dispatch_time, updated = account.evening_time, account.evening_updated_at
        if dispatch_time is None:
            continue
        due = datetime.combine(slot.date(), dispatch_time, tzinfo=BEIJING)
        if due < slot:
            due += timedelta(days=1)
        if updated <= due <= moment and _deliver(account, due, moment):
            sent += 1
    return sent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Check for due subscriptions once and exit.")
    args = parser.parse_args()
    if not args.once:
        print("账号投递进程已启动，按北京时间检查每日投递设置。", flush=True)
    try:
        while True:
            try:
                run_due()
            except edition_service.MorningError as error:
                print(str(error), file=sys.stderr, flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
