"""Run the daily email worker for registered Commute Digest accounts."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from time import sleep
from uuid import uuid4

from . import accounts, edition_service
from .schedule import BEIJING, latest_slot

POLL_SECONDS = 15
BUILD_LEASE = timedelta(minutes=30)
SMTP_RETRY_DELAY = timedelta(minutes=5)


def _initialize_deliveries() -> None:
    with edition_service._database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_deliveries (
                account_id INTEGER NOT NULL,
                delivery_day TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                settings_key TEXT NOT NULL,
                claim TEXT NOT NULL UNIQUE,
                claimed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_at TEXT,
                issue_id INTEGER REFERENCES issues(id),
                PRIMARY KEY (account_id, delivery_day)
            )
            """
        )


def _claim(account: accounts.Account, due: datetime, now: datetime) -> tuple[str, int | None] | None:
    claim = uuid4().hex
    settings_key = json.dumps((account.updated_at.isoformat(), account.preferences), sort_keys=True)
    with edition_service._database() as connection:
        row = connection.execute(
            """
            INSERT INTO subscription_deliveries (
                account_id, delivery_day, scheduled_at, settings_key, claim, claimed_at, status
            ) VALUES (:id, :day, :due, :settings, :claim, :now, 'building')
            ON CONFLICT (account_id, delivery_day) DO UPDATE SET
                issue_id = CASE WHEN scheduled_at = excluded.scheduled_at
                                AND settings_key = excluded.settings_key
                                AND status = 'retry' THEN issue_id ELSE NULL END,
                scheduled_at = excluded.scheduled_at,
                settings_key = excluded.settings_key,
                claim = excluded.claim,
                claimed_at = excluded.claimed_at,
                status = 'building',
                retry_at = NULL
            WHERE status = 'cancelled'
               OR (status = 'retry' AND (retry_at <= :now OR settings_key != :settings))
               OR (status = 'building' AND claimed_at <= :expired)
            RETURNING issue_id
            """,
            {
                "id": account.id,
                "day": due.date().isoformat(),
                "due": due.isoformat(),
                "settings": settings_key,
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
            UPDATE subscription_deliveries SET status = ?, issue_id = ?, retry_at = ?
            WHERE claim = ? AND status = ?
            """,
            (status, issue_id, retry_at.isoformat() if retry_at else None, claim, previous),
        )
        return result.rowcount == 1


def _report(account: accounts.Account, message: str) -> None:
    print(f"账号 {account.id} · {message}", file=sys.stderr, flush=True)


def _deliver(account: accounts.Account, due: datetime, now: datetime) -> bool:
    job = _claim(account, due, now)
    if job is None:
        return False

    claim, issue_id = job
    retry_at = now + SMTP_RETRY_DELAY
    try:
        settings = edition_service._mail_settings(account.delivery_email)
    except edition_service.MorningError as error:
        _set_status(claim, "building", "retry", issue_id, retry_at)
        _report(account, str(error))
        return False

    try:
        issue = edition_service.get_issue(issue_id, account_id=account.id) if issue_id else None
        if issue is None:
            issue = edition_service._publish(
                account.preferences, latest_slot(due), account_id=account.id,
            )
        message = edition_service._make_message(
            issue, account.preferences, settings, due.date().isoformat(),
        )
    except edition_service.MorningError as error:
        _set_status(claim, "building", "failed")
        _report(account, str(error))
        return False

    if accounts.get_account(account.id) != account:
        _set_status(claim, "building", "cancelled")
        return False
    if not _set_status(claim, "building", "sending", issue["id"]):
        return False

    try:
        edition_service._send_message(message, settings)
    except edition_service._SMTPSetupError as error:
        _set_status(claim, "sending", "retry", issue["id"], retry_at)
        _report(account, str(error))
        return False
    except (OSError, edition_service.MorningError):
        _set_status(claim, "sending", "unconfirmed", issue["id"])
        _report(account, "SMTP 未确认接收，请核对发件记录和收件箱。")
        return False

    _set_status(claim, "sending", "sent", issue["id"])
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
    for account in accounts.scheduled_accounts():
        if account.delivery_time is None:
            continue
        due = datetime.combine(moment.date(), account.delivery_time, tzinfo=BEIJING)
        if account.updated_at <= due <= moment and _deliver(account, due, moment):
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
