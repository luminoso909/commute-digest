"""Local account authentication and per-account subscription preferences."""

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time
from email.errors import HeaderParseError
from email.headerregistry import Address
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_bytes

from . import edition_service
from .edition_service import Preferences
from .schedule import BEIJING

_PASSWORD_ITERATIONS = 600_000
_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}")
_PROFILE = "id, username, default_email, delivery_override, delivery_time, preferences, updated_at"


class AccountError(edition_service.MorningError):
    """An account operation failed for a reason safe to display."""


@dataclass(frozen=True)
class Account:
    id: int
    username: str
    default_email: str
    delivery_override: str
    delivery_time: time | None
    preferences: Preferences
    updated_at: datetime

    @property
    def delivery_email(self) -> str:
        return self.delivery_override or self.default_email


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    with edition_service._database() as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                username TEXT COLLATE NOCASE NOT NULL UNIQUE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                default_email TEXT NOT NULL,
                delivery_override TEXT NOT NULL DEFAULT '',
                delivery_time TEXT,
                preferences TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        yield connection


def _as_account(row: sqlite3.Row) -> Account:
    delivery_time = row["delivery_time"]
    return Account(
        id=row["id"],
        username=row["username"],
        default_email=row["default_email"],
        delivery_override=row["delivery_override"],
        delivery_time=time.fromisoformat(delivery_time) if delivery_time else None,
        preferences=json.loads(row["preferences"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _password_hash(password: str, salt: bytes) -> bytes:
    return pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)


def _email(value: str, *, optional: bool = False) -> str:
    label = "投递邮箱" if optional else "默认邮箱"
    value = value.strip()
    if optional and not value:
        return ""
    if not value or len(value) > 254:
        raise AccountError(f"请填写有效的{label}地址。")
    try:
        address = Address(addr_spec=value)
    except (HeaderParseError, ValueError) as error:
        raise AccountError(f"请填写有效的{label}地址。") from error
    if not address.username or not address.domain or address.addr_spec != value:
        raise AccountError(f"请填写有效的{label}地址。")
    return address.addr_spec


def register(username: str, password: str, default_email: str) -> Account:
    username = username.strip()
    if not _USERNAME.fullmatch(username):
        raise AccountError("用户名需为 3 至 32 位字母、数字、下划线或连字符，以字母或数字开头。")
    if not 8 <= len(password) <= 256:
        raise AccountError("密码长度需为 8 至 256 个字符。")
    email = _email(default_email)
    salt = token_bytes(16)
    password_hash = _password_hash(password, salt)
    with _database() as connection:
        row = connection.execute(
            f"""
            INSERT INTO accounts (
                username, password_salt, password_hash, default_email, preferences, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (username) DO NOTHING
            RETURNING {_PROFILE}
            """,
            (
                username, salt, password_hash, email,
                json.dumps(edition_service._default_preferences()), datetime.now(BEIJING).isoformat(),
            ),
        ).fetchone()
        if row is None:
            raise AccountError("该用户名已注册。")
    return _as_account(row)


def authenticate(username: str, password: str) -> Account | None:
    username = username.strip()
    if not _USERNAME.fullmatch(username) or not 8 <= len(password) <= 256:
        return None
    with _database() as connection:
        row = connection.execute(
            f"SELECT {_PROFILE}, password_salt, password_hash FROM accounts WHERE username = ?",
            (username,),
        ).fetchone()
    salt = row["password_salt"] if row is not None else bytes(16)
    expected = row["password_hash"] if row is not None else bytes(32)
    if compare_digest(_password_hash(password, salt), expected) and row is not None:
        return _as_account(row)
    return None


def get_account(account_id: int) -> Account | None:
    with _database() as connection:
        row = connection.execute(
            f"SELECT {_PROFILE} FROM accounts WHERE id = ?", (account_id,),
        ).fetchone()
    return _as_account(row) if row is not None else None


def save_settings(
    account_id: int,
    *,
    default_email: str,
    delivery_override: str,
    delivery_time: time | None,
    preferences: Preferences,
    now: datetime | None = None,
) -> Account:
    email = _email(default_email)
    override = _email(delivery_override, optional=True)
    if delivery_time is not None and (
        delivery_time.tzinfo is not None or delivery_time.second or delivery_time.microsecond
    ):
        raise AccountError("请选择精确到分钟的北京时间。")
    daily_time = delivery_time.isoformat(timespec="minutes") if delivery_time is not None else None
    now = now if now is not None else datetime.now(BEIJING)
    if now.utcoffset() is None:
        raise ValueError("Account setting timestamps must include a timezone.")
    clean_preferences = edition_service._validated_preferences(**preferences)
    with _database() as connection:
        row = connection.execute(
            f"""
            UPDATE accounts
            SET updated_at = CASE
                    WHEN COALESCE(NULLIF(delivery_override, ''), default_email) = ?
                         AND delivery_time IS ? THEN updated_at
                    ELSE ? END,
                default_email = ?, delivery_override = ?, delivery_time = ?, preferences = ?
            WHERE id = ?
            RETURNING {_PROFILE}
            """,
            (
                override or email, daily_time, now.astimezone(BEIJING).isoformat(),
                email, override, daily_time, json.dumps(clean_preferences, ensure_ascii=False),
                account_id,
            ),
        ).fetchone()
        if row is None:
            raise AccountError("账号不存在，请重新登录。")
    return _as_account(row)


def scheduled_accounts() -> list[Account]:
    with _database() as connection:
        rows = connection.execute(
            f"SELECT {_PROFILE} FROM accounts WHERE delivery_time IS NOT NULL ORDER BY delivery_time, id"
        ).fetchall()
    return [_as_account(row) for row in rows]
