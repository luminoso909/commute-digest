"""Account identity, reader preferences and credentials for scheduled editions."""

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, time
from email.errors import HeaderParseError
from email.headerregistry import Address
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from io import BytesIO
from pathlib import Path
from secrets import choice, token_bytes
from string import ascii_uppercase, digits

from PIL import Image, ImageOps, UnidentifiedImageError

from . import edition_service, news_editor
from .edition_service import Preferences
from .schedule import BEIJING, EVENING_TIME, MORNING_TIME

_PASSWORD_ITERATIONS = 600_000
_ID_ALPHABET = ascii_uppercase + digits
_KEEP_AVATAR = object()
_PROFILE = """accounts.*, EXISTS (
    SELECT 1 FROM account_api_keys AS keys
    WHERE keys.account_id = accounts.id AND keys.model = accounts.model
) AS has_api_key"""
_ACCOUNT_SCHEMA = """
    CREATE TABLE accounts_new (
        id INTEGER PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL,
        legacy_login TEXT COLLATE NOCASE UNIQUE,
        password_salt BLOB NOT NULL,
        password_hash BLOB NOT NULL,
        default_email TEXT NOT NULL DEFAULT '',
        avatar BLOB,
        preferences TEXT NOT NULL,
        delivery_enabled INTEGER NOT NULL DEFAULT 0,
        morning_time TEXT,
        evening_time TEXT,
        morning_updated_at TEXT NOT NULL,
        evening_updated_at TEXT NOT NULL,
        reading_mode TEXT NOT NULL DEFAULT 'cross',
        reading_language TEXT NOT NULL DEFAULT 'zh',
        theme_color TEXT NOT NULL DEFAULT '#314960',
        color_mode TEXT NOT NULL DEFAULT 'light',
        model TEXT NOT NULL,
        api_revision INTEGER NOT NULL DEFAULT 0
    )
"""


class AccountError(edition_service.MorningError):
    """An account operation failed with a message suitable for the reader."""


@dataclass(frozen=True)
class Account:
    id: int
    public_id: str
    username: str
    default_email: str
    preferences: Preferences
    delivery_enabled: bool
    morning_time: time | None
    evening_time: time | None
    morning_updated_at: datetime
    evening_updated_at: datetime
    reading_mode: str
    reading_language: str
    theme_color: str
    color_mode: str
    model: str
    has_api_key: bool
    api_revision: int
    avatar: bytes | None = field(repr=False)

    @property
    def delivery_email(self) -> str:
        return self.default_email


def _new_id() -> str:
    return "".join(choice(_ID_ALPHABET) for _ in range(8))


def _is_morning(value: time) -> bool:
    return MORNING_TIME <= value < EVENING_TIME


def _initialize_accounts(connection: sqlite3.Connection) -> None:
    def columns() -> set[str]:
        return {row["name"] for row in connection.execute("PRAGMA table_info(accounts)")}

    def key_columns() -> set[str]:
        return {row["name"] for row in connection.execute("PRAGMA table_info(account_api_keys)")}

    if "public_id" in columns() and "api_key" in key_columns():
        return
    connection.execute("BEGIN IMMEDIATE")
    existing = columns()
    if "public_id" not in existing:
        connection.execute(_ACCOUNT_SCHEMA)
        if existing:
            rows = connection.execute("SELECT * FROM accounts").fetchall()
            reserved = {row["username"].upper() for row in rows}
            for row in rows:
                public_id = _new_id()
                while public_id in reserved:
                    public_id = _new_id()
                reserved.add(public_id)
                old_time = row["delivery_time"]
                morning = old_time and _is_morning(time.fromisoformat(old_time))
                previous = json.loads(row["preferences"])
                connection.execute(
                    """INSERT INTO accounts_new (
                        id, public_id, username, legacy_login, password_salt, password_hash,
                        default_email, preferences, delivery_enabled, morning_time, evening_time,
                        morning_updated_at, evening_updated_at, reading_mode, reading_language, model
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["id"], public_id, row["username"], row["username"], row["password_salt"],
                     row["password_hash"], row["delivery_override"] or row["default_email"],
                     row["preferences"], int(bool(old_time)), old_time if morning else None,
                     old_time if not morning else None, row["updated_at"], row["updated_at"],
                     previous["delivery_mode"], previous["delivery_language"], news_editor.DEFAULT_MODEL),
                )
            connection.execute("DROP TABLE accounts")
        connection.execute("ALTER TABLE accounts_new RENAME TO accounts")
    files_to_retire = []
    keys = key_columns()
    if not keys:
        connection.execute("""
            CREATE TABLE account_api_keys (
                account_id INTEGER NOT NULL, model TEXT NOT NULL, legacy_file TEXT NOT NULL DEFAULT '',
                api_key TEXT, PRIMARY KEY (account_id, model)
            )
        """)
    elif "file_name" in keys:
        connection.execute("ALTER TABLE account_api_keys RENAME COLUMN file_name TO legacy_file")
        connection.execute("ALTER TABLE account_api_keys ADD COLUMN api_key TEXT")
        for row in connection.execute("SELECT account_id, model, legacy_file FROM account_api_keys").fetchall():
            try:
                path, key = _read_legacy_key(row["legacy_file"])
            except AccountError:
                # A restored or replaced key can still be picked up independently for this account.
                continue
            _store_key(connection, row["account_id"], row["model"], key)
            files_to_retire.append(path)
    connection.commit()
    for path in files_to_retire:
        _retire_key_file(path)


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    with edition_service._database() as connection:
        connection.row_factory = sqlite3.Row
        _initialize_accounts(connection)
        yield connection


def _as_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"], public_id=row["public_id"], username=row["username"],
        default_email=row["default_email"], preferences=json.loads(row["preferences"]),
        delivery_enabled=bool(row["delivery_enabled"]),
        morning_time=time.fromisoformat(row["morning_time"]) if row["morning_time"] else None,
        evening_time=time.fromisoformat(row["evening_time"]) if row["evening_time"] else None,
        morning_updated_at=datetime.fromisoformat(row["morning_updated_at"]),
        evening_updated_at=datetime.fromisoformat(row["evening_updated_at"]),
        reading_mode=row["reading_mode"], reading_language=row["reading_language"],
        theme_color=row["theme_color"], color_mode=row["color_mode"], model=row["model"],
        has_api_key=bool(row["has_api_key"]), api_revision=row["api_revision"], avatar=row["avatar"],
    )


def _account(connection: sqlite3.Connection, account_id: int) -> Account:
    row = connection.execute(f"SELECT {_PROFILE} FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise AccountError("账号不存在，请重新登录。")
    return _as_account(row)


def _password_hash(password: str, salt: bytes) -> bytes:
    return pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)


def _display_name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 48 or not value.isprintable():
        raise AccountError("用户名请填写 1 至 48 个可显示字符。")
    return value


def _email(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        address = Address(addr_spec=value)
    except (HeaderParseError, ValueError) as error:
        raise AccountError("请填写有效的邮箱地址。") from error
    if len(value) > 254 or not address.username or not address.domain or address.addr_spec != value:
        raise AccountError("请填写有效的邮箱地址。")
    return address.addr_spec


def register(username: str, password: str, default_email: str = "") -> Account:
    name = _display_name(username)
    if not 8 <= len(password) <= 256:
        raise AccountError("密码长度需为 8 至 256 个字符。")
    email = _email(default_email)
    salt = token_bytes(16)
    hashed = _password_hash(password, salt)
    now = datetime.now(BEIJING).isoformat()
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        public_id = _new_id()
        while connection.execute(
            "SELECT 1 FROM accounts WHERE public_id=? OR legacy_login=?", (public_id, public_id)
        ).fetchone():
            public_id = _new_id()
        result = connection.execute(
            """INSERT INTO accounts (
                public_id, username, password_salt, password_hash, default_email, preferences,
                morning_time, evening_time, morning_updated_at, evening_updated_at, model
            ) VALUES (?,?,?,?,?,?,'05:00','17:00',?,?,?)""",
            (public_id, name, salt, hashed, email, json.dumps(edition_service._default_preferences()),
             now, now, news_editor.DEFAULT_MODEL),
        )
        return _account(connection, result.lastrowid)


def authenticate(identifier: str, password: str) -> Account | None:
    identifier = identifier.strip()
    if not 1 <= len(identifier) <= 48 or not 8 <= len(password) <= 256:
        return None
    with _database() as connection:
        # The original username remains a login alias for accounts created before public IDs.
        row = connection.execute(
            f"SELECT {_PROFILE} FROM accounts WHERE public_id=? OR legacy_login=?",
            (identifier.upper(), identifier),
        ).fetchone()
    salt = row["password_salt"] if row is not None else bytes(16)
    expected = row["password_hash"] if row is not None else bytes(32)
    if compare_digest(_password_hash(password, salt), expected) and row is not None:
        return _as_account(row)
    return None


def get_account(account_id: int) -> Account | None:
    with _database() as connection:
        row = connection.execute(f"SELECT {_PROFILE} FROM accounts WHERE id=?", (account_id,)).fetchone()
        return _as_account(row) if row is not None else None


def _avatar(data: bytes) -> bytes:
    if not data or len(data) > 5 * 1024 * 1024:
        raise AccountError("头像请使用 5 MB 以内的 PNG、JPEG 或 WebP 图片。")
    try:
        with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
            if image.width * image.height > 16_000_000:
                raise AccountError("头像像素过多，请选择较小的图片。")
            image.thumbnail((256, 256))
            normalized = ImageOps.exif_transpose(image).convert("RGBA")
            normalized.info.clear()
            result = BytesIO()
            normalized.save(result, format="PNG")
            return result.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise AccountError("头像无法读取，请使用 PNG、JPEG 或 WebP 图片。") from error


def save_profile(
    account_id: int, *, username: str, default_email: str, avatar: bytes | None | object = _KEEP_AVATAR,
    now: datetime | None = None,
) -> Account:
    name, email = _display_name(username), _email(default_email)
    if avatar is not _KEEP_AVATAR and avatar is not None:
        if not isinstance(avatar, bytes):
            raise AccountError("请选择图片文件。")
        avatar = _avatar(avatar)
    stamp = (now or datetime.now(BEIJING)).astimezone(BEIJING).isoformat()
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = _account(connection, account_id)
        if account.delivery_enabled and not email:
            raise AccountError("已启用邮件投递，请填写邮箱或先关闭投递。")
        connection.execute(
            """UPDATE accounts SET username=?, default_email=?, avatar=?,
                morning_updated_at=CASE WHEN default_email=? THEN morning_updated_at ELSE ? END,
                evening_updated_at=CASE WHEN default_email=? THEN evening_updated_at ELSE ? END
               WHERE id=?""",
            (name, email, account.avatar if avatar is _KEEP_AVATAR else avatar,
             email, stamp, email, stamp, account_id),
        )
        return _account(connection, account_id)


def save_subscription(
    account_id: int, *, morning_time: time | None, evening_time: time | None,
    preferences: Preferences, enabled: bool | None = None, now: datetime | None = None,
) -> Account:
    if enabled is None:
        enabled = morning_time is not None or evening_time is not None
    if enabled and not (morning_time or evening_time):
        raise AccountError("请选择投递时间。")
    for value, morning in ((morning_time, True), (evening_time, False)):
        if value is None:
            continue
        if value.tzinfo is not None or value.second or value.microsecond:
            raise AccountError("请选择精确到分钟的北京时间。")
        if _is_morning(value) != morning:
            raise AccountError("晨报可在 05:00–16:59 投递，晚报可在 17:00 至次日 04:59 投递。")
    moment = now or datetime.now(BEIJING)
    if moment.utcoffset() is None:
        raise ValueError("Account setting timestamps must include a timezone.")
    stamp = moment.astimezone(BEIJING).isoformat()
    preferences = edition_service._validated_preferences(**preferences)
    morning = morning_time.isoformat(timespec="minutes") if morning_time else None
    evening = evening_time.isoformat(timespec="minutes") if evening_time else None
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = _account(connection, account_id)
        if enabled and not account.default_email:
            raise AccountError("请先在登录信息中填写收件邮箱。")
        connection.execute(
            """UPDATE accounts SET delivery_enabled=:enabled, preferences=:preferences,
                morning_time=:morning, evening_time=:evening,
                morning_updated_at=CASE WHEN delivery_enabled=:enabled AND morning_time IS :morning
                                        THEN morning_updated_at ELSE :stamp END,
                evening_updated_at=CASE WHEN delivery_enabled=:enabled AND evening_time IS :evening
                                        THEN evening_updated_at ELSE :stamp END
               WHERE id=:id""",
            dict(enabled=enabled, preferences=json.dumps(preferences, ensure_ascii=False),
                 morning=morning, evening=evening, stamp=stamp, id=account_id),
        )
        return _account(connection, account_id)


def save_reading(account_id: int, *, reading_mode: str, reading_language: str) -> Account:
    if reading_mode not in {"cross", "focus"} or reading_language not in {"zh", "en"}:
        raise AccountError("请选择有效的阅读版式和语言。")
    with _database() as connection:
        connection.execute("UPDATE accounts SET reading_mode=?, reading_language=? WHERE id=?",
                           (reading_mode, reading_language, account_id))
        return _account(connection, account_id)


def save_appearance(account_id: int, *, theme_color: str, color_mode: str) -> Account:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", theme_color) or color_mode not in {"light", "dark"}:
        raise AccountError("请选择有效的主题色和界面模式。")
    with _database() as connection:
        connection.execute("UPDATE accounts SET theme_color=?, color_mode=? WHERE id=?",
                           (theme_color, color_mode, account_id))
        return _account(connection, account_id)


def _database_path() -> Path:
    return Path(os.getenv("COMMUTE_DB") or "data/morning.sqlite3")


def _legacy_path(name: str) -> Path | None:
    if not re.fullmatch(r"key-[a-zA-Z0-9_-]+", name):
        return None
    database = _database_path()
    return database.with_name(database.name + ".keys") / name


def _read_legacy_key(name: str) -> tuple[Path, str]:
    path = _legacy_path(name)
    if path and not path.is_symlink():
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return path, value
        except (OSError, UnicodeError):
            pass
    raise AccountError("旧版密钥文件无法读取，请重新填写该模型的密钥。")


def _retire_key_file(path: Path | None) -> None:
    if path is not None:
        # The SQLite transaction containing the replacement has already committed.
        with suppress(OSError):
            path.unlink(missing_ok=True)


def _store_key(connection: sqlite3.Connection, account_id: int, model: str, key: str) -> None:
    if os.name == "posix":
        _database_path().chmod(0o600)
    connection.execute(
        """INSERT INTO account_api_keys (account_id, model, legacy_file, api_key) VALUES (?, ?, '', ?)
           ON CONFLICT(account_id, model) DO UPDATE SET api_key=excluded.api_key, legacy_file=''""",
        (account_id, model, key),
    )


def get_api_key(account_id: int, model: str | None = None) -> str | None:
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = _account(connection, account_id)
        model = model or account.model
        row = connection.execute(
            "SELECT api_key, legacy_file FROM account_api_keys WHERE account_id=? AND model=?", (account_id, model)
        ).fetchone()
        if row is None or not row["legacy_file"]:
            return row["api_key"] if row else None
        path, key = _read_legacy_key(row["legacy_file"])
        _store_key(connection, account_id, model, key)
    _retire_key_file(path)
    return key


@contextmanager
def model_context(account: Account) -> Iterator[None]:
    key = get_api_key(account.id, model=account.model)
    auxiliary_key = None
    if key and not news_editor.same_model_provider(account.model, news_editor.DEFAULT_MODEL):
        for model in news_editor.MODEL_OPTIONS:
            if news_editor.same_model_provider(model, news_editor.DEFAULT_MODEL):
                try:
                    auxiliary_key = get_api_key(account.id, model)
                except AccountError:
                    continue
                if auxiliary_key:
                    break
    with news_editor.use_model(account.model, key, deepseek_key=auxiliary_key):
        yield


def save_api(account_id: int, *, model: str, api_key: str | None = None) -> Account:
    if model not in news_editor.MODEL_OPTIONS:
        raise AccountError("请选择列表中的模型。")
    if api_key is not None:
        api_key = api_key.strip()
        if len(api_key) > 2048 or not api_key.isascii() or any(not ch.isprintable() or ch.isspace() for ch in api_key):
            raise AccountError("API 密钥格式无效。")
    legacy_path = None
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = _account(connection, account_id)
        changed = model != account.model
        if api_key is not None:
            stored = connection.execute(
                "SELECT api_key, legacy_file FROM account_api_keys WHERE account_id=? AND model=?", (account_id, model)
            ).fetchone()
            if stored:
                legacy_path = _legacy_path(stored["legacy_file"])
            if api_key:
                if not stored or stored["api_key"] != api_key:
                    _store_key(connection, account_id, model, api_key)
                    changed = True
            elif stored:
                connection.execute("DELETE FROM account_api_keys WHERE account_id=? AND model=?", (account_id, model))
                changed = True
        connection.execute("UPDATE accounts SET model=?, api_revision=api_revision+? WHERE id=?",
                           (model, int(changed), account_id))
        result = _account(connection, account_id)
    _retire_key_file(legacy_path)
    return result


def scheduled_accounts() -> list[Account]:
    with _database() as connection:
        rows = connection.execute(
            f"SELECT {_PROFILE} FROM accounts WHERE delivery_enabled=1 ORDER BY id"
        ).fetchall()
        return [_as_account(row) for row in rows]
