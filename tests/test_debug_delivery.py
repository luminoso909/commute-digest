from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from commute_digest import accounts, debug_delivery, edition_service, model_api, news_editor, subscription_delivery
from commute_digest.schedule import BEIJING, SLOT_INTERVAL, latest_slot


@pytest.fixture
def trial(monkeypatch, tmp_path):
    for key, value in {
        "COMMUTE_DB": str(tmp_path / "trial.sqlite3"), "COMMUTE_DEBUG_DELIVERY": "1",
        "DEEPSEEK_API_KEY": "shared-key", "RECIPIENT_EMAIL": "system@example.org",
        "PUBLIC_BASE_URL": "https://test.example.org", "SMTP_HOST": "smtp.example.org",
        "SMTP_USER": "editor@example.org", "SMTP_PASSWORD": "offline-password",
    }.items():
        monkeypatch.setenv(key, value)
    account = accounts.register("读者", "reader-test-password", "reader@example.org")
    accounts.save_api(account.id, model=account.model, api_key="personal-key")
    copy = {"title": "公共交通", "summary": "新消息"}
    story = {"zh": copy, "en": {"title": "Transit", "summary": "Bus news."}, "category": "能源与交通"}
    def generate(topics, keywords, *, now, window_start, **kwargs):
        assert model_api.credentials(for_drafting=True)[1] == "personal-key"
        assert window_start == now - SLOT_INTERVAL and latest_slot(now) == now
        return {"cross": [story], "focus": [story]}
    draft = Mock(side_effect=generate)
    smtp = Mock()
    monkeypatch.setattr(news_editor, "generate_digest", draft)
    monkeypatch.setattr(edition_service, "_send_message", smtp)
    return account, draft, smtp


def test_parallel_clicks_share_one_personal_trial_and_leave_scheduled_slot_free(trial):
    account, draft, smtp = trial
    other = accounts.register("另一位读者", "other-test-password")
    assert not account.delivery_enabled
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: debug_delivery.send_trial(account.id, 1), range(2)))
    assert sum(issue is not None for issue in outcomes) == 1
    assert len(edition_service.archive_issue_numbers(account_id=account.id)) == 1
    assert edition_service.latest_widget_issue(other.id) is None
    assert edition_service.latest_widget_issue(account.id)["issue_number"] == 1
    assert smtp.call_count == draft.call_count == 1
    message = smtp.call_args.args[0]
    assert "试刊" in str(message["Subject"])
    assert str(message["To"]) == account.default_email
    assert "?issue=1&mode=cross&lang=zh" in message.get_body(preferencelist=("plain",)).get_content()

    assert debug_delivery.send_trial(account.id, 1) is None
    assert debug_delivery.send_trial(account.id, 2)["issue_number"] == 2
    now = datetime.now(BEIJING)
    slot = latest_slot(now)
    accounts.save_subscription(account.id, enabled=True, morning_time=slot.time() if slot.hour == 5 else None,
                               evening_time=slot.time() if slot.hour == 17 else None, preferences=account.preferences,
                               now=slot - timedelta(minutes=1))
    assert subscription_delivery.run_due(now) == 1
    assert subscription_delivery.run_due(now) == 0
    assert draft.call_count == smtp.call_count == 3
    assert "试刊" not in str(smtp.call_args.args[0]["Subject"])


def test_trial_reuses_issue_after_smtp_setup_failure_and_never_repeats_uncertain_send(trial):
    account, draft, smtp = trial
    smtp.side_effect = [edition_service._SMTPSetupError("连接失败"), OSError("uncertain"), None]
    with pytest.raises(edition_service.MorningError, match="连接失败"):
        debug_delivery.send_trial(account.id, 1)
    assert edition_service.latest_widget_issue(account.id) is None
    with pytest.raises(edition_service.MorningError, match="未确认接收"):
        debug_delivery.send_trial(account.id, 1)
    assert debug_delivery.send_trial(account.id, 1) is None
    assert draft.call_count == 1 and smtp.call_count == 2
    assert debug_delivery.send_trial(account.id, 2)["issue_number"] == 2


def test_trial_requires_enabled_switch_personal_email_and_own_key(trial, monkeypatch):
    account, draft, smtp = trial
    monkeypatch.delenv("COMMUTE_DEBUG_DELIVERY")
    with pytest.raises(edition_service.MorningError, match="已关闭"):
        debug_delivery.send_trial(account.id, 1)
    monkeypatch.setenv("COMMUTE_DEBUG_DELIVERY", "1")
    accounts.save_profile(account.id, username=account.username, default_email="")
    with pytest.raises(edition_service.MorningError, match="保存收件邮箱"):
        debug_delivery.send_trial(account.id, 1)
    accounts.save_profile(account.id, username=account.username, default_email="reader@example.org")
    accounts.save_api(account.id, model=account.model, api_key="")
    with pytest.raises(edition_service.MorningError, match="个人 API 密钥"):
        debug_delivery.send_trial(account.id, 1)
    assert smtp.call_count == draft.call_count == 0


def test_sidebar_button_requires_login_and_sends_only_on_click(trial, monkeypatch):
    account, draft, smtp = trial
    app = AppTest.from_file(str(Path(edition_service.__file__).with_name("web.py")))
    app.run()
    assert "调试投递" not in [b.label for b in app.button]
    app.session_state["account_id"] = account.id
    monkeypatch.setenv("COMMUTE_DEBUG_DELIVERY", "0")
    app.run()
    assert "调试投递" not in [b.label for b in app.button]
    monkeypatch.setenv("COMMUTE_DEBUG_DELIVERY", "1")
    app.run()
    assert smtp.call_count == 0
    next(b for b in app.button if b.label == "调试投递").click().run()
    assert not app.exception
    assert any("第 1 期试刊已由 SMTP 接收" in entry.value for entry in app.success)
    assert app.query_params["issue"] == "1"
    app.run()
    assert smtp.call_count == draft.call_count == 1
    monkeypatch.setenv("COMMUTE_DEBUG_DELIVERY", "0")
    app.run()
    assert "调试投递" not in [b.label for b in app.button]
    assert edition_service.latest_widget_issue(account.id)["issue_number"] == 1
