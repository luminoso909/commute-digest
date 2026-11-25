from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time

import pytest

from commute_digest import accounts, edition_service, news_editor, subscription_delivery
from commute_digest.schedule import BEIJING


@pytest.fixture(autouse=True)
def account_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "accounts.sqlite3"))


def test_registration_and_authentication_use_independent_accounts():
    account = accounts.register("Commuter_A", "a-local-test-password", "a@example.com")
    other = accounts.register("Commuter_B", "another-test-password", "b@example.com")

    assert account.id != other.id
    assert account.delivery_time is None
    assert accounts.authenticate("commuter_a", "incorrect-password") is None
    assert accounts.authenticate("commuter_a", "a-local-test-password") == account
    assert accounts.authenticate("commuter_b", "another-test-password") == other
    with pytest.raises(accounts.AccountError):
        accounts.register("commuter_A", "different-test-password", "elsewhere@example.com")


def test_delivery_settings_keep_default_and_override_separate():
    account = accounts.register("reader_one", "reader-one-password", "reader@example.com")
    other = accounts.register("reader_two", "reader-two-password", "other@example.com")
    instant = datetime(2026, 11, 25, 7, 0, tzinfo=BEIJING)

    updated = accounts.save_settings(
        account.id,
        default_email=account.default_email,
        delivery_override="work@example.com",
        delivery_time=time(8, 35),
        preferences={**account.preferences, "delivery_language": "en"},
        now=instant,
    )

    assert updated.default_email == "reader@example.com"
    assert updated.delivery_email == "work@example.com"
    assert updated.delivery_time == time(8, 35)
    assert updated.updated_at == instant
    assert updated.preferences["delivery_language"] == "en"
    assert accounts.get_account(other.id) == other
    assert accounts.scheduled_accounts() == [updated]

    disabled = accounts.save_settings(
        account.id,
        default_email=account.default_email,
        delivery_override="",
        delivery_time=None,
        preferences=account.preferences,
        now=instant,
    )

    assert disabled.delivery_email == "reader@example.com"
    assert accounts.scheduled_accounts() == []


def test_private_issues_require_their_owner_and_preserve_shared_issues(monkeypatch):
    account = accounts.register("reader_one", "reader-one-password", "reader@example.com")
    other = accounts.register("reader_two", "reader-two-password", "other@example.com")
    monkeypatch.setattr(news_editor, "generate_digest", lambda *args, **kwargs: {"cross": [], "focus": []})

    shared = edition_service.publish()
    personal = edition_service.publish(account_id=account.id)

    assert edition_service.get_issue(personal["id"], account_id=account.id) == personal
    assert edition_service.get_issue(personal["id"], account_id=other.id) is None
    assert edition_service.get_issue(personal["id"]) is None
    assert edition_service.get_issue() == shared
    assert edition_service.get_issue(shared["id"], account_id=other.id) == shared


def test_worker_delivers_at_each_accounts_time_to_the_chosen_mailbox(monkeypatch):
    first = accounts.register("first_reader", "first-test-password", "first@example.com")
    second = accounts.register("second_reader", "second-test-password", "second@example.com")

    def today(hour, minute):
        return datetime(2026, 11, 25, hour, minute, tzinfo=BEIJING)

    for reader, destination, delivery_time in (
        (first, "work@example.com", time(8, 30)),
        (second, "", time(9, 15)),
    ):
        accounts.save_settings(
            reader.id,
            default_email=reader.default_email,
            delivery_override=destination,
            delivery_time=delivery_time,
            preferences=reader.preferences,
            now=today(7, 0),
        )

    for key, value in {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "465",
        "SMTP_USER": "sender@example.com",
        "SMTP_PASSWORD": "test-only-smtp-password",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("RECIPIENT_EMAIL", "PUBLIC_BASE_URL", "SMTP_FROM"):
        monkeypatch.delenv(key, raising=False)

    generated = []
    messages = []

    def generate(topics, keywords, *, now, window_start, exclude_articles, required_mode):
        generated.append((window_start, now))
        story = {"zh": {"title": "测试简报", "summary": "该邮件仅用于离线测试。"}}
        return {"cross": [story], "focus": []}

    monkeypatch.setattr(news_editor, "generate_digest", generate)
    monkeypatch.setattr(edition_service, "_send_message", lambda message, settings: messages.append(message))

    subscription_delivery.run_due(now=today(8, 29))
    assert messages == []
    with ThreadPoolExecutor(max_workers=2) as workers:
        attempts = list(workers.map(subscription_delivery.run_due, [today(8, 30)] * 2))
    assert sorted(attempts) == [0, 1]
    subscription_delivery.run_due(now=today(8, 31))
    assert [str(message["To"]) for message in messages] == ["work@example.com"]

    accounts.save_settings(
        first.id,
        default_email=first.default_email,
        delivery_override="",
        delivery_time=time(8, 40),
        preferences=first.preferences,
        now=today(8, 35),
    )
    subscription_delivery.run_due(now=today(8, 40))
    subscription_delivery.run_due(now=today(9, 15))

    assert [str(message["To"]) for message in messages] == ["work@example.com", "second@example.com"]
    assert generated == [(datetime(2026, 11, 24, 17, tzinfo=BEIJING), today(5, 0))] * 2
    assert "测试简报" in messages[0].get_body(preferencelist=("plain",)).get_content()
