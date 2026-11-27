import re
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from io import BytesIO

import pytest
from PIL import Image

from commute_digest import accounts, edition_service, news_editor, subscription_delivery
from commute_digest.schedule import BEIJING


@pytest.fixture(autouse=True)
def account_storage(monkeypatch, tmp_path):
    database = tmp_path / "accounts.sqlite3"
    monkeypatch.setenv("COMMUTE_DB", str(database))
    return database


def today(hour, minute=0, day=25):
    return datetime(2026, 11, day, hour, minute, tzinfo=BEIJING)


def test_duplicate_display_names_use_unique_public_ids(monkeypatch):
    ids = iter(["ABCD1234", "ABCD1234", "ABCD1235"])
    monkeypatch.setattr(accounts, "_new_id", lambda: next(ids))
    first = accounts.register("读者", "first-reader-password")
    second = accounts.register("读者", "second-reader-password", "second@example.com")
    assert first.public_id == "ABCD1234"
    assert second.public_id == "ABCD1235"
    assert accounts.authenticate(first.public_id.lower(), "first-reader-password") == first
    assert accounts.authenticate(first.public_id, "second-reader-password") is None
    assert accounts.authenticate("读者", "first-reader-password") is None
    assert not first.delivery_enabled
    with pytest.raises(accounts.AccountError, match="收件邮箱"):
        accounts.save_subscription(first.id, enabled=True, morning_time=time(7), evening_time=time(18),
                                   preferences=first.preferences)


def test_profile_reading_theme_and_private_keys_are_persisted(account_storage):
    account = accounts.register("通勤读者", "reader-test-password")
    image = BytesIO()
    Image.new("RGB", (700, 320), "red").save(image, format="JPEG")
    updated = accounts.save_profile(account.id, username="读者", default_email="new@example.com",
                                    avatar=image.getvalue())
    assert updated.public_id == account.public_id
    with Image.open(BytesIO(updated.avatar)) as avatar:
        assert avatar.format == "PNG" and max(avatar.size) == 256
    with pytest.raises(accounts.AccountError):
        accounts.save_profile(account.id, username="bad", default_email="", avatar=b"bad image")
    accounts.save_reading(account.id, reading_mode="focus", reading_language="en")
    accounts.save_appearance(account.id, theme_color="#25354a", color_mode="dark")
    default = news_editor.DEFAULT_MODEL
    other_model = next(name for name in news_editor.MODEL_OPTIONS if name != default)
    accounts.save_api(account.id, model=default, api_key="private-test-token")
    second = accounts.save_api(account.id, model=other_model)
    assert not second.has_api_key
    assert accounts.get_api_key(account.id) is None
    assert accounts.get_api_key(account.id, default) == "private-test-token"
    with sqlite3.connect(account_storage) as db:
        assert db.execute("SELECT api_key FROM account_api_keys WHERE account_id=? AND model=?",
                          (account.id, default)).fetchone() == ("private-test-token",)
    assert stat.S_IMODE(account_storage.stat().st_mode) == 0o600
    other_model = next(model for model in news_editor.MODEL_OPTIONS
                       if not news_editor.same_model_provider(model, default))
    switched = accounts.save_api(account.id, model=other_model)
    assert not switched.has_api_key and accounts.get_api_key(account.id) is None
    accounts.save_api(account.id, model=other_model, api_key="other-provider-key")
    current = accounts.save_api(account.id, model=default)
    assert accounts.get_api_key(account.id, model=other_model) == "other-provider-key"
    assert current.has_api_key and current.reading_language == "en" and current.reading_mode == "focus"
    assert current.color_mode == "dark" and current.theme_color == "#25354a"
    assert accounts.get_api_key(account.id) == "private-test-token"
    accounts.save_api(account.id, model=default, api_key="")
    assert accounts.get_api_key(account.id) is None
    assert accounts.save_profile(account.id, username="读者", default_email="", avatar=None).avatar is None


def test_private_issues_require_their_owner_and_preserve_shared_issues(monkeypatch):
    account = accounts.register("reader_one", "reader-one-password", "reader@example.com")
    other = accounts.register("reader_two", "reader-two-password", "other@example.com")
    accounts.save_api(account.id, model=account.model, api_key="test-private-key")
    monkeypatch.setattr(news_editor, "generate_digest", lambda *args, **kwargs: {"cross": [], "focus": []})
    shared = edition_service.publish()
    personal = edition_service.publish(account_id=account.id)
    assert edition_service.get_issue(personal["id"], account_id=account.id) == personal
    assert edition_service.get_issue(personal["id"], account_id=other.id) is None
    assert edition_service.get_issue(personal["id"]) is None
    assert edition_service.get_issue() == shared
    assert edition_service.get_issue(shared["id"], account_id=other.id) == shared


def test_worker_sends_each_enabled_edition_once_at_its_own_time(monkeypatch):
    account = accounts.register("first", "first-test-password", "first@example.com")
    accounts.save_api(account.id, model=account.model, api_key="test-private-key")
    accounts.save_subscription(account.id, enabled=True, morning_time=time(8, 30), evening_time=time(20),
                               preferences=account.preferences, now=today(7))
    with pytest.raises(accounts.AccountError, match="晨报"):
        accounts.save_subscription(account.id, enabled=True, morning_time=time(4), evening_time=time(18),
                                   preferences=account.preferences)
    for key, value in {"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "465",
                       "SMTP_USER": "sender@example.com", "SMTP_PASSWORD": "test-password"}.items():
        monkeypatch.setenv(key, value)
    for key in ("RECIPIENT_EMAIL", "PUBLIC_BASE_URL", "SMTP_FROM"):
        monkeypatch.delenv(key, raising=False)
    generated, messages = [], []

    def generate(*args, now, window_start, **kwargs):
        generated.append((window_start, now))
        story = {"zh": {"title": "测试简报", "summary": "离线测试。"}}
        return {"cross": [story], "focus": []}

    monkeypatch.setattr(news_editor, "generate_digest", generate)
    monkeypatch.setattr(edition_service, "_send_message", lambda msg, _: messages.append(msg))
    assert subscription_delivery.run_due(today(8, 29)) == 0
    with ThreadPoolExecutor(max_workers=2) as workers:
        attempts = list(workers.map(subscription_delivery.run_due, [today(8, 30)] * 2))
    assert sorted(attempts) == [0, 1]
    accounts.save_reading(account.id, reading_mode="focus", reading_language="en")
    assert subscription_delivery.run_due(today(8, 31)) == 0
    assert subscription_delivery.run_due(today(19, 59)) == 0
    assert subscription_delivery.run_due(today(20)) == 1
    assert subscription_delivery.run_due(today(20, 1)) == 0
    accounts.save_subscription(account.id, enabled=True, morning_time=time(9), evening_time=time(0, 10),
                               preferences=account.preferences, now=today(21))
    assert subscription_delivery.run_due(today(0, 10, day=26)) == 0  # same evening edition
    updated = accounts.save_subscription(account.id, enabled=False, morning_time=time(9), evening_time=time(0, 10),
                                         preferences=account.preferences, now=today(4, day=26))
    assert not updated.delivery_enabled and updated.morning_time == time(9)
    assert not accounts.scheduled_accounts()
    assert generated == [(today(17, day=24), today(5)), (today(5), today(17))]
    assert all(str(message["To"]) == "first@example.com" for message in messages)
    assert "测试简报" in messages[0].get_body(preferencelist=("plain",)).get_content()


def test_migrates_existing_identity_schedule_ownership_and_sent_ledger(account_storage):
    import json
    from secrets import token_bytes
    salt = token_bytes(16)
    with sqlite3.connect(account_storage) as db:
        db.executescript("""
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY, username TEXT COLLATE NOCASE NOT NULL UNIQUE,
                password_salt BLOB NOT NULL, password_hash BLOB NOT NULL, default_email TEXT NOT NULL,
                delivery_override TEXT NOT NULL DEFAULT '', delivery_time TEXT, preferences TEXT NOT NULL,
                updated_at TEXT NOT NULL);
            CREATE TABLE subscription_deliveries (
                account_id INTEGER, delivery_day TEXT, scheduled_at TEXT, settings_key TEXT, claim TEXT UNIQUE,
                claimed_at TEXT, status TEXT, retry_at TEXT, issue_id INTEGER, PRIMARY KEY(account_id,delivery_day));
        """)
        db.execute("INSERT INTO accounts VALUES (1,'OldReader',?,?,?,?,'08:30',?,?)", (
            salt, accounts._password_hash("old-test-password", salt), "old@example.com", "work@example.com",
            json.dumps(edition_service._default_preferences()), today(7).isoformat()))
        db.execute("INSERT INTO subscription_deliveries VALUES (1,?,?,?,?,?,'sent',NULL,9)", (
            "2026-11-25", today(8, 30).isoformat(), "legacy-settings", "old-claim", today(8, 30).isoformat()))
    with ThreadPoolExecutor(max_workers=2) as workers:
        old1, old2 = workers.map(accounts.get_account, (1, 1))
    assert old1 == old2
    assert old1.id == 1 and re.fullmatch("[A-Z0-9]{8}", old1.public_id)
    assert old1.username == "OldReader" and old1.default_email == "work@example.com"
    assert old1.delivery_enabled and old1.morning_time == time(8, 30) and old1.evening_time is None
    assert accounts.authenticate("oldreader", "old-test-password") == old1
    assert accounts.authenticate(old1.public_id, "old-test-password") == old1
    accounts.register("OldReader", "different-password", "other@example.com")
    assert accounts.authenticate("oldreader", "different-password") is None
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(subscription_delivery.run_due, [today(8, 45)] * 2)) == [0, 0]
    with sqlite3.connect(account_storage) as db:
        assert db.execute("SELECT slot,status,issue_id FROM subscription_deliveries").fetchall() == [
            (today(5).isoformat(), "sent", 9)]


def test_safe_smtp_retry_keeps_issue_even_when_it_crosses_midnight(monkeypatch):
    account = accounts.register("late-reader", "reader-test-password", "reader@example.com")
    accounts.save_api(account.id, model=account.model, api_key="test-private-key")
    accounts.save_subscription(account.id, enabled=True, morning_time=time(7), evening_time=time(23, 58),
                               preferences=account.preferences, now=today(21))
    attempt_times = []
    generated = []
    fake_settings = object()
    monkeypatch.setattr(edition_service, "_mail_settings", lambda email: fake_settings)
    monkeypatch.setattr(edition_service, "_make_message", lambda *args, **kwargs: "same issue")

    def generate(*args, **kwargs):
        generated.append(kwargs["now"])
        return {"cross": [], "focus": []}

    def submit(message, settings):
        attempt_times.append(message)
        if len(attempt_times) == 1:
            raise edition_service._SMTPSetupError("连接失败")

    monkeypatch.setattr(news_editor, "generate_digest", generate)
    monkeypatch.setattr(edition_service, "_send_message", submit)
    assert subscription_delivery.run_due(today(23, 58)) == 0
    assert subscription_delivery.run_due(today(0, 2, day=26)) == 0
    assert subscription_delivery.run_due(today(0, 3, day=26)) == 1
    assert generated == [today(17)]
    assert attempt_times == ["same issue", "same issue"]
    assert subscription_delivery.run_due(today(0, 4, day=26)) == 0

def test_old_file_keys_migrate_independently_and_can_be_replaced(account_storage):
    user = accounts.register("old", "password-test")
    other = accounts.register("other", "password-test")
    directory = account_storage.with_name(account_storage.name + ".keys")
    directory.mkdir()
    key_file = directory / "key-old"
    key_file.write_text("test-migrated-key")
    with sqlite3.connect(account_storage) as conn:
        conn.execute("DROP TABLE account_api_keys")
        conn.execute("CREATE TABLE account_api_keys(account_id INTEGER, model TEXT, file_name TEXT NOT NULL, "
                     "PRIMARY KEY (account_id, model))")
        conn.executemany("INSERT INTO account_api_keys VALUES(?, ?, ?)", [
            (user.id, user.model, key_file.name), (other.id, other.model, "key-missing")])
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(accounts.get_api_key, [user.id, user.id])) == ["test-migrated-key"] * 2
    assert not key_file.exists()
    assert accounts.get_account(other.id).id == other.id
    with pytest.raises(accounts.AccountError, match="旧版密钥"):
        accounts.get_api_key(other.id)
    accounts.save_api(other.id, model=other.model, api_key="replacement-key")
    assert accounts.get_api_key(other.id) == "replacement-key"
    assert accounts.get_api_key(user.id) == "test-migrated-key"
