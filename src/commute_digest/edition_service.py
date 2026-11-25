"""Publish and deliver the single subscriber's morning and evening briefings."""

import argparse
import json
import os
import smtplib
import sqlite3
import ssl
import sys
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import NotRequired, TypedDict
from urllib.parse import urlencode, urlsplit

from . import news_editor
from .schedule import (
    BEIJING,
    EVENING_TIME,
    MORNING_TIME,
    SLOT_INTERVAL,
    is_slot,
    latest_slot,
    slot_title,
)

MAX_ISSUE_ID = (1 << 63) - 1  # SQLite INTEGER PRIMARY KEY upper bound.
WIDGET_FEED_PATH = Path(__file__).resolve().with_name("static") / "widget-latest.json"


class Preferences(TypedDict):
    topics: list[str]
    keywords: list[str]
    delivery_mode: str
    delivery_language: str


class Issue(TypedDict):
    id: int
    created_at: str
    cross: list[dict]
    focus: list[dict]
    edition: str
    slot: NotRequired[str]
    window_start: NotRequired[str]
    window_end: NotRequired[str]


class WidgetIssue(TypedDict):
    issue_id: int
    slot: str
    edition: str
    mode: str
    language: str
    titles: list[str]


class MorningError(RuntimeError):
    """A local operation failed with a message safe for the web UI."""


class _SMTPSetupError(MorningError):
    """The connection or authentication failed before submitting the message."""


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    password: str
    sender: Address
    recipient: Address
    public_url: str


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    database = Path(os.getenv("COMMUTE_DB") or "data/morning.sqlite3")
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database, timeout=10)) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS issue_owners (
                    issue_id INTEGER PRIMARY KEY REFERENCES issues(id),
                    account_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    day TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    issue_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS delivery_editions (
                    day TEXT PRIMARY KEY REFERENCES deliveries(day),
                    mode TEXT NOT NULL CHECK (mode IN ('cross', 'focus')),
                    language TEXT NOT NULL CHECK (language IN ('zh', 'en'))
                );
                """
            )
            with connection:
                yield connection
    except (OSError, sqlite3.Error) as exc:
        raise MorningError("晨报存储暂时不可用，请检查数据目录。") from exc


def _default_preferences() -> Preferences:
    return {"topics": [], "keywords": [], "delivery_mode": "cross", "delivery_language": "zh"}


def get_preferences() -> Preferences:
    with _database() as connection:
        row = connection.execute("SELECT payload FROM preferences WHERE id = 1").fetchone()
    if row is None:
        return _default_preferences()
    return json.loads(row[0])


def _validated_preferences(
    topics: list[str], keywords: list[str], delivery_mode: str, delivery_language: str
) -> Preferences:
    if any(topic not in news_editor.CATEGORIES for topic in topics):
        raise MorningError("请在四个现有板块中选择。")
    if delivery_mode not in {"cross", "focus"} or delivery_language not in {"zh", "en"}:
        raise MorningError("请选择有效的邮件版本和语言。")
    return {
        "topics": list(dict.fromkeys(topics)),
        "keywords": list(dict.fromkeys(word.strip() for word in keywords if word.strip())),
        "delivery_mode": delivery_mode,
        "delivery_language": delivery_language,
    }


def save_preferences(
    topics: list[str], keywords: list[str], delivery_mode: str, delivery_language: str
) -> None:
    preferences = _validated_preferences(topics, keywords, delivery_mode, delivery_language)
    with _database() as connection:
        connection.execute(
            """
            INSERT INTO preferences (id, payload) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
            """,
            (json.dumps(preferences, ensure_ascii=False),),
        )


def recipient_email() -> str:
    return os.getenv("RECIPIENT_EMAIL", "").strip()


def _issue_from_payload(issue_id: int, created_at: str, payload: dict) -> Issue:
    issue: Issue = {
        "id": issue_id,
        "created_at": created_at,
        "cross": payload["cross"],
        "focus": payload["focus"],
        "edition": "morning",
    }
    if slot_text := payload.get("slot"):
        slot = datetime.fromisoformat(slot_text)
        issue["edition"] = "evening" if slot.time() == EVENING_TIME else "morning"
        issue["slot"] = slot_text
        issue["window_start"] = (slot - SLOT_INTERVAL).isoformat()
        issue["window_end"] = slot_text
    return issue


def _publish(
    preferences: Preferences,
    slot: datetime,
    *,
    exclude_articles: Sequence[dict] = (),
    account_id: int | None = None,
) -> Issue:
    try:
        digest = news_editor.generate_digest(
            preferences["topics"],
            preferences["keywords"],
            now=slot,
            window_start=slot - SLOT_INTERVAL,
            exclude_articles=exclude_articles,
            required_mode=preferences["delivery_mode"],
        )
    except news_editor.EditorError as exc:
        raise MorningError(str(exc)) from exc

    created_at = datetime.now(BEIJING).isoformat(timespec="seconds")
    payload = {"cross": digest["cross"], "focus": digest["focus"], "slot": slot.isoformat()}
    with _database() as connection:
        row = connection.execute(
            "INSERT INTO issues (created_at, payload) VALUES (?, ?) RETURNING id",
            (created_at, json.dumps(payload, ensure_ascii=False)),
        ).fetchone()
        if account_id is not None:
            connection.execute("INSERT INTO issue_owners VALUES (?, ?)", (row[0], account_id))
    return _issue_from_payload(row[0], created_at, payload)


def publish(*, account_id: int | None = None) -> Issue:
    slot = latest_slot(datetime.now(BEIJING))
    if account_id is None:
        return _publish(get_preferences(), slot)

    from . import accounts

    account = accounts.get_account(account_id)
    if account is None:
        raise MorningError("请重新登录后再生成简报。")
    return _publish(account.preferences, slot, account_id=account.id)


def get_issue(issue_id: int | None = None, *, account_id: int | None = None) -> Issue | None:
    with _database() as connection:
        row = connection.execute(
            """
            SELECT issues.id, created_at, payload FROM issues
            LEFT JOIN issue_owners ON issue_owners.issue_id = issues.id
            WHERE (issue_owners.account_id IS NULL OR issue_owners.account_id = ?)
              AND (? IS NULL OR issues.id = ?)
            ORDER BY issues.id DESC LIMIT 1
            """,
            (account_id, issue_id, issue_id),
        ).fetchone()
    if row is None:
        return None
    return _issue_from_payload(row[0], row[1], json.loads(row[2]))


def _latest_widget_issue(connection: sqlite3.Connection) -> WidgetIssue | None:
    rows = connection.execute(
        """
        SELECT deliveries.day, issues.id, delivery_editions.mode, delivery_editions.language,
               issues.payload
        FROM deliveries
        JOIN delivery_editions USING (day)
        JOIN issues ON issues.id = deliveries.issue_id
        WHERE deliveries.status = 'sent' AND issues.id > 0
        ORDER BY deliveries.day DESC
        """
    )
    for day, issue_id, mode, language, payload in rows:
        try:
            slot = datetime.fromisoformat(day)
            if not is_slot(slot) or day != slot.astimezone(BEIJING).isoformat():
                continue
            issue = json.loads(payload)
            if not isinstance(issue, dict) or issue.get("slot") != day:
                continue
            titles = [story[language]["title"] for story in issue[mode]]
        except (KeyError, TypeError, ValueError):
            continue
        if 3 <= len(titles) <= 6 and all(
            isinstance(title, str) and title.strip() for title in titles
        ):
            return {
                "issue_id": issue_id,
                "slot": day,
                "edition": "evening" if slot.time() == EVENING_TIME else "morning",
                "mode": mode,
                "language": language,
                "titles": titles,
            }
    return None


def reconcile_widget_feed() -> WidgetIssue | None:
    temporary_path = None
    try:
        with _database() as connection:
            # Serialize rebuilds with delivery commits so an older view cannot replace a newer one.
            connection.execute("BEGIN IMMEDIATE")
            issue = _latest_widget_issue(connection)
            WIDGET_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                dir=WIDGET_FEED_PATH.parent,
                prefix=".widget-latest.",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(issue, temporary, ensure_ascii=False)
                temporary.write("\n")
            temporary_path.replace(WIDGET_FEED_PATH)
        return issue
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _try_reconcile_widget_feed() -> None:
    try:
        reconcile_widget_feed()
    except (MorningError, OSError) as exc:
        print(f"桌面小组件快照更新失败，可重新生成。{exc}", file=sys.stderr)


def _stories_published_on(day: str) -> list[dict]:
    with _database() as connection:
        payloads = connection.execute(
            """
            SELECT payload FROM issues
            WHERE substr(created_at, 1, 10) = ?
              AND NOT EXISTS (SELECT 1 FROM issue_owners WHERE issue_id = issues.id)
            """,
            (day,),
        ).fetchall()
    stories = []
    for (payload,) in payloads:
        issue = json.loads(payload)
        stories.extend(issue["cross"])
        stories.extend(issue["focus"])
    return stories


def _mail_settings(recipient_override: str | None = None) -> MailSettings:
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "").strip() or username
    recipient = recipient_email() if recipient_override is None else recipient_override
    public_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not all((host, username, password, sender, recipient)):
        if recipient_override is not None:
            raise MorningError("请配置 SMTP 发信服务和收件邮箱。")
        raise MorningError("请配置真实 SMTP、收件邮箱和 PUBLIC_BASE_URL。")
    if recipient_override is None and not public_url:
        raise MorningError("请配置真实 SMTP、收件邮箱和 PUBLIC_BASE_URL。")

    try:
        port = int(os.getenv("SMTP_PORT", "465") or "465")
        parsed_url = urlsplit(public_url)
        from_address = Address(addr_spec=sender)
        to_address = Address(addr_spec=recipient)
    except (ValueError, HeaderParseError) as exc:
        raise MorningError("SMTP 端口、邮箱地址或站点网址格式有误。") from exc
    if not 1 <= port <= 65535:
        raise MorningError("请填写有效的 SMTP 端口。")
    if public_url and (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise MorningError("PUBLIC_BASE_URL 应为可直接访问的 HTTPS 网页地址。")

    return MailSettings(host, port, username, password, from_address, to_address, public_url)


def _make_message(
    issue: Issue,
    preferences: Preferences,
    settings: MailSettings,
    day: str,
    *,
    verification: bool = False,
) -> EmailMessage:
    mode = preferences["delivery_mode"]
    language = preferences["delivery_language"]
    issue_link = ""
    if settings.public_url:
        query = urlencode({"issue": issue["id"], "mode": mode, "lang": language})
        issue_link = f"{settings.public_url}/?{query}"
    if language == "en":
        title = "Commute Digest"
        edition = "Across Topics" if mode == "cross" else "My Topics"
        empty = "No recent stories matched this edition."
        source_label = "Source"
        supplemental_label = "From across topics"
        read_online = "Read this issue online"
    else:
        title = "通勤晨报"
        edition = "跨领域版" if mode == "cross" else "我的关注"
        empty = "本期暂无符合条件的新闻。"
        source_label = "新闻来源"
        supplemental_label = "跨领域补充"
        read_online = "打开本期"

    dateline = day
    if slot_text := issue.get("slot"):
        slot = datetime.fromisoformat(slot_text)
        title = slot_title(slot, language)
        start = slot - SLOT_INTERVAL
        timezone = "Beijing time" if language == "en" else "北京时间"
        dateline = f"{start:%m-%d %H:%M} – {slot:%m-%d %H:%M} ({timezone})"
    edition_label = edition
    if verification:
        edition_label = f"{'Preview' if language == 'en' else '试刊'} · {edition}"
    subject = f"{title} · {edition_label} · {day}"
    heading_font = "Georgia, 'Songti SC', SimSun, serif"
    body_font = "Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    body_style = "font-size:16px;line-height:1.65;margin:0"
    link_style = "color:#174c86;text-decoration:underline"
    lines = [subject, dateline] if dateline != day else [subject]
    stories = []
    articles = issue["cross"] if mode == "cross" else issue["focus"]
    for article in articles:
        local = article[language]
        lines.extend(("", local["title"], local["summary"]))
        source_url = news_editor._source_url(article.get("source_url"))
        source_html = ""
        if source_url:
            source_name = article.get("source_name")
            citation = f"{source_label} · {source_name}" if source_name else source_label
            if article.get("supplemental"):
                citation = f"{supplemental_label} · {citation}"
            lines.extend((citation, source_url))
            safe_url = escape(source_url)
            source_html = (
                '<p style="font-size:13px;line-height:1.5;margin:10px 0 0;color:#475569">'
                f"<strong>{escape(citation)}</strong><br>"
                f'<a href="{safe_url}" style="{link_style};word-break:break-all;'
                f'word-wrap:break-word">{safe_url}</a></p>'
            )
        stories.append(f"""
          <div style="border-top:1px solid #dbe3ed;padding:18px 0">
            <h2 style="font-family:{heading_font};font-size:21px;font-weight:700;
                       line-height:1.35;margin:0 0 8px;color:#12253c">{escape(local["title"])}</h2>
            <p style="{body_style}">{escape(local["summary"])}</p>
            {source_html}
          </div>""")
    if not articles:
        lines.extend(("", empty))
        stories.append(f'<p style="{body_style};padding:18px 0">{escape(empty)}</p>')
    link_html = ""
    if issue_link:
        lines.extend(("", read_online, issue_link))
        link_html = (
            '<p style="border-top:1px solid #dbe3ed;padding:16px 0 0;margin:0;font-size:15px">'
            f'<a href="{escape(issue_link)}" style="{link_style};font-weight:700">{read_online}</a></p>'
        )

    html = f"""<!doctype html>
<html lang="{language}">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(subject)}</title>
  </head>
  <body style="margin:0">
    <div style="max-width:640px;margin:0 auto;padding:24px 18px;
                font-family:{body_font};color:#243447">
      <h1 style="font-family:{heading_font};font-size:30px;font-weight:700;
                 line-height:1.2;margin:0 0 8px;color:#12253c">{title}</h1>
      <p style="font-size:14px;line-height:1.5;margin:0 0 20px;color:#475569">
        {escape(edition_label)} · {escape(dateline)}
      </p>
      {"".join(stories)}
      {link_html}
    </div>
  </body>
</html>"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = settings.recipient
    message.set_content("\n".join(lines))
    message.add_alternative(html, subtype="html")
    return message


def _send_message(message: EmailMessage, settings: MailSettings) -> None:
    client = None
    try:
        try:
            client = smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=30, context=ssl.create_default_context()
            )
            client.login(settings.username, settings.password)
        except OSError as exc:
            raise _SMTPSetupError("SMTP 连接或登录失败，可重试本期投递。") from exc

        rejected = client.send_message(message)
        if rejected:
            raise MorningError("SMTP 拒绝了收件地址。")
    finally:
        if client is not None:
            with suppress(OSError):
                client.close()


def _claim_delivery(key: str) -> tuple[bool, int | None]:
    with _database() as connection:
        row = connection.execute(
            """
            INSERT INTO deliveries (day, status) VALUES (?, 'building')
            ON CONFLICT(day) DO UPDATE SET status = 'building'
            WHERE deliveries.status IN ('build_failed', 'smtp_setup_failed')
            RETURNING issue_id
            """,
            (key,),
        ).fetchone()
    if row is None:
        return False, None
    return True, row[0]


def _delivery_status(
    key: str,
    status: str,
    issue_id: int | None = None,
    *,
    preferences: Preferences | None = None,
) -> None:
    with _database() as connection:
        connection.execute(
            "UPDATE deliveries SET status = ?, issue_id = ? WHERE day = ?",
            (status, issue_id, key),
        )
        if status == "sent" and preferences is not None:
            connection.execute(
                "INSERT INTO delivery_editions (day, mode, language) VALUES (?, ?, ?)",
                (key, preferences["delivery_mode"], preferences["delivery_language"]),
            )


def deliver_slot(slot: datetime | None = None, *, verification: bool = False) -> str:
    now = datetime.now(BEIJING)
    expected = (
        datetime.combine(now.date(), MORNING_TIME, tzinfo=BEIJING)
        if verification
        else latest_slot(now)
    )
    if slot is not None and (not is_slot(slot) or slot != expected):
        raise MorningError("只能投递当前时段，试刊对应当天 05:00 的晨报。")
    if expected > now:
        raise MorningError("当天晨报的 05:00 采集截止时间尚未到。")

    slot = expected
    day = slot.date().isoformat()
    # Keep legacy date-only rows as history. New rows distinguish slots and trial mail.
    key = f"preview:{slot.isoformat()}" if verification else slot.isoformat()
    settings = _mail_settings()
    claimed, retry_issue_id = _claim_delivery(key)
    if not claimed:
        if not verification:
            _try_reconcile_widget_feed()
        return f"{slot:%Y-%m-%d %H:%M} 已有一条投递记录，详见服务日志。"

    try:
        preferences = get_preferences()
        issue = get_issue(retry_issue_id) if retry_issue_id is not None else None
        if issue is None:
            excluded = _stories_published_on(day) if verification else ()
            issue = _publish(preferences, slot, exclude_articles=excluded)
        message = _make_message(issue, preferences, settings, day, verification=verification)
    except MorningError:
        _delivery_status(key, "build_failed", retry_issue_id)
        raise

    _delivery_status(key, "sending", issue["id"])
    try:
        _send_message(message, settings)
    except _SMTPSetupError:
        _delivery_status(key, "smtp_setup_failed", issue["id"])
        raise
    except (OSError, smtplib.SMTPException, MorningError) as exc:
        _delivery_status(key, "send_error", issue["id"])
        raise MorningError("SMTP 未确认接收，请核对发件记录和收件箱。") from exc
    _delivery_status(key, "sent", issue["id"], preferences=None if verification else preferences)
    if not verification:
        _try_reconcile_widget_feed()
    return f"{slot:%Y-%m-%d %H:%M} {slot_title(slot, 'zh')}第 {issue['id']} 期已由 SMTP 接收。"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("publish")
    commands.add_parser("widget-feed")
    deliver = commands.add_parser("deliver")
    deliver.add_argument("--slot", type=datetime.fromisoformat)
    deliver.add_argument("--verification", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "publish":
            issue = publish()
            print(f"已发布第 {issue['id']} 期。")
        elif args.command == "widget-feed":
            widget_issue = reconcile_widget_feed()
            print(f"已生成小组件第 {widget_issue['issue_id']} 期快照。" if widget_issue else "暂无正式投送期次。")
        else:
            print(deliver_slot(args.slot, verification=args.verification))
    except (MorningError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
