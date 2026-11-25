from datetime import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

WEB_ENTRY = Path(__file__).resolve().parents[1] / "src" / "commute_digest" / "web.py"


def _click_button(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


def test_register_save_delivery_settings_and_log_in_again(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "digest.sqlite3"))
    app = AppTest.from_file(str(WEB_ENTRY)).run()
    assert not app.exception
    assert all(button.label != "生成并发布" for button in app.button)

    app.radio(key="account_action").set_value("注册").run()
    app.text_input(key="auth_username").set_value("commuter_web")
    app.text_input(key="auth_password").set_value("commuter-web-test-password")
    app.text_input(key="registration_email").set_value("reader@example.com")
    _click_button(app, "注册并登录")

    assert app.text_input(key="default_email").value == "reader@example.com"
    assert not app.checkbox(key="schedule_enabled").value
    app.text_input(key="default_email").set_value("personal@example.com")
    app.text_input(key="delivery_override").set_value("work@example.com")
    app.checkbox(key="schedule_enabled").check()
    app.time_input(key="delivery_time").set_value(time(9, 17))
    _click_button(app, "保存订阅设置")

    assert [message.value for message in app.success] == ["订阅设置已保存。"]
    assert any("09:17" in item.value and "work@example.com" in item.value for item in app.text)

    _click_button(app, "退出登录")
    assert all(field.key != "default_email" for field in app.text_input)
    assert all(button.label != "生成并发布" for button in app.button)

    app.radio(key="account_action").set_value("登录").run()
    app.text_input(key="auth_username").set_value("commuter_web")
    app.text_input(key="auth_password").set_value("commuter-web-test-password")
    _click_button(app, "登录")

    assert app.text_input(key="default_email").value == "personal@example.com"
    assert app.text_input(key="delivery_override").value == "work@example.com"
    assert app.checkbox(key="schedule_enabled").value
    assert app.time_input(key="delivery_time").value == time(9, 17)
