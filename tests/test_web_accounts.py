from datetime import time
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from commute_digest import accounts, edition_service, model_api, news_editor

WEB_ENTRY = Path(__file__).resolve().parents[1] / "src" / "commute_digest" / "web.py"


@pytest.fixture
def reader(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "digest.sqlite3"))
    return AppTest.from_file(str(WEB_ENTRY))


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


def item(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


def test_register_save_account_settings_and_log_in_again(reader):
    app = reader.run()
    assert not app.exception
    assert not app.text_input
    click(app, "设置")
    app.radio(key="account_action").set_value("注册").run()
    app.text_input(key="register_name").set_value("晨报读者")
    app.text_input(key="auth_password_注册").set_value("reader-test-password")
    click(app, "注册")
    public_id = item(app.text_input, "用户 ID").value
    assert re.fullmatch(r"[A-Z0-9]{8}", public_id)
    item(app.text_input, "邮箱").set_value("reader@example.org")
    click(app, "保存")
    assert [field.key for field in app.text_input] == ["archive_q"]

    click(app, "设置")
    item(app.checkbox, "启用每日邮件投递").check()
    item(app.time_input, "晨报时间（北京时间）").set_value(time(7, 12))
    item(app.time_input, "晚报时间（北京时间）").set_value(time(19, 42))
    click(app, "保存")
    user = accounts.authenticate(public_id, "reader-test-password")
    assert user.default_email == "reader@example.org"
    assert user.delivery_enabled
    assert (user.morning_time, user.evening_time) == (time(7, 12), time(19, 42))

    click(app, "设置")
    app.radio(key="settings_page").set_value("登录信息").run()
    item(app.text_input, "用户名").set_value("未保存的修改")
    click(app, "返回")
    assert accounts.get_account(user.id).username == "晨报读者"
    click(app, "设置")
    app.radio(key="settings_page").set_value("登录信息").run()
    click(app, "退出登录")
    click(app, "设置")
    app.text_input(key="login_id").set_value(public_id)
    app.text_input(key="auth_password_登录").set_value("reader-test-password")
    click(app, "登录")
    assert app.session_state["account_id"] == user.id


def test_archive_detail_and_on_demand_audio(reader, monkeypatch):
    user = accounts.register("读者", "reader-test-password", "reader@example.org")
    copy = {"title": "旧报道", "summary": "只发邮件的短摘要", "background": "旧稿的报道细节",
            "why_it_matters": "旧稿的影响分析"}
    article = {"zh": copy, "en": copy, "category": "科学与环境", "source_url": "https://example.org/a",
               "source_name": "示例媒体", "published_at": "2026-11-25T08:00:00+08:00"}
    with edition_service._database() as db:
        for day, title in ((24, "旧报道"), (25, "新报道")):
            copy["title"] = title
            slot = f"2026-11-{day}T17:00:00+08:00"
            payload = {"cross": [article], "focus": [article], "slot": slot}
            cursor = db.execute("INSERT INTO issues (created_at, payload) VALUES (?,?)", (slot, json.dumps(payload)))
            edition_service._assign_issue(db, user.id, cursor.lastrowid)
    accounts.save_api(user.id, model=user.model, api_key="test-tts-key")
    reader.session_state["account_id"] = user.id
    synthesize = Mock(return_value=b"RIFF" + bytes(40))
    monkeypatch.setattr(news_editor, "synthesize", synthesize)
    app = reader.run()
    assert not app.exception
    listed = [block.value for block in app.markdown if "digest-story-title" in block.value]
    assert ["新报道" in listed[0], "旧报道" in listed[1]] == [True, True]
    assert all("只发邮件的短摘要" not in block.value for block in app.markdown)
    assert "st-key-batch_latest_2" in str(app.get("flex_container")[0]) or any(
        getattr(el, "key", None) == "batch_latest_2" for el in app.get("flex_container")
    )
    app.button(key="detail_1_cross_1").click().run()
    assert not app.exception
    assert app.query_params["issue"] == "1" and app.query_params["article"] == "1"
    content = " ".join(block.value for block in app.markdown)
    assert "旧稿的报道细节" in content and "旧稿的影响分析" in content
    assert "只发邮件的短摘要" not in content and not app.expander
    assert not synthesize.called
    click(app, "生成朗读")
    synthesize.assert_called_once_with("旧报道\n\n旧稿的报道细节\n\n旧稿的影响分析")
    click(app, "返回列表")
    assert "article" not in app.query_params and "issue" not in app.query_params
    assert synthesize.call_count == 1


def test_api_dialog_tests_cancels_saves_and_publishes_with_the_selected_model(reader, monkeypatch):
    user = accounts.register("试读", "reader-test-password")
    default = news_editor.DEFAULT_MODEL
    choice = "qwen-plus"
    accounts.save_api(user.id, model=default, api_key="saved-test-key")
    accounts.save_reading(user.id, reading_mode="focus", reading_language="zh")
    reader.session_state["account_id"] = user.id
    app = reader.run()
    check = Mock()
    monkeypatch.setattr(news_editor, "check_model_connection", check)

    def open_api():
        click(app, "设置")
        app.radio(key="settings_page").set_value("API 设置").run()

    open_api()
    assert item(app.selectbox, "模型").value == default
    assert "••••-key" in app.button(key="test_selected_model").label
    assert "saved-test-key" not in app.button(key="test_selected_model").label
    item(app.toggle, "在测试按钮上显示完整密钥").set_value(True).run()
    assert "saved-test-key" in app.button(key="test_selected_model").label
    app.button(key="test_selected_model").click().run()
    check.assert_called_with(default, "saved-test-key")
    assert item(app.text_input, "API 密钥").value == "saved-test-key"
    assert app.code[0].value == "saved-test-key"
    item(app.text_input, "API 密钥").set_value("old-provider-draft")
    item(app.selectbox, "模型").select(choice).run()
    assert not item(app.text_input, "API 密钥").value
    app.button(key="test_selected_model").click().run()
    assert check.call_count == 1
    assert "请先填写" in app.error[0].value and not app.code

    item(app.text_input, "API 密钥").set_value("new-test-key")
    check.side_effect = news_editor.EditorError("模拟连接失败")
    app.button(key="test_selected_model").click().run()
    check.assert_called_with(choice, "new-test-key")
    assert app.error[0].value == "模拟连接失败"
    assert app.code[0].value == "new-test-key"
    item(app.text_input, "API 密钥").set_value("changed-test-key").run()
    assert not app.code and not app.error and not app.success
    item(app.text_input, "API 密钥").set_value("new-test-key")
    check.side_effect = None
    app.button(key="test_selected_model").click().run()
    assert "连接成功" in app.success[0].value and "new-test-key" not in app.success[0].value
    assert app.code[0].value == "new-test-key"
    click(app, "返回")
    open_api()
    assert item(app.selectbox, "模型").value == default
    assert accounts.get_api_key(user.id, choice) is None

    item(app.selectbox, "模型").select(choice).run()
    item(app.text_input, "API 密钥").set_value("new-test-key")
    click(app, "保存")

    def generate(*args, **kwargs):
        assert kwargs["required_mode"] == "focus"
        assert model_api.selected_model() == choice
        assert model_api.credentials(for_drafting=True) == (model_api.DASHSCOPE, "new-test-key")
        assert model_api.credentials(for_drafting=False) == (model_api.DEEPSEEK, "saved-test-key")
        return {"cross": [], "focus": []}

    draft = Mock(side_effect=generate)
    monkeypatch.setattr(news_editor, "generate_digest", draft)
    click(app, "日报发布")
    draft.assert_called_once()
    issue_id = int(app.query_params["issue"])
    assert edition_service.get_issue(issue_id, account_id=user.id) is not None
    assert edition_service.get_issue(issue_id) is None
    open_api()
    assert item(app.text_input, "API 密钥").value == "new-test-key"
    item(app.text_input, "API 密钥").set_value("")
    click(app, "保存")
    assert accounts.get_api_key(user.id, choice) is None
    click(app, "日报发布")
    assert draft.call_count == 1
    assert any("个人 API 密钥" in error.value for error in app.error)


def test_appearance_persists_the_selected_color_and_cancels_edits(reader):
    app = reader.run()

    def open_appearance():
        click(app, "设置")
        app.radio(key="settings_page").set_value("界面设置").run()

    open_appearance()
    item(app.radio, "主题色").set_value("松绿").run()
    item(app.radio, "显示模式").set_value("dark")
    click(app, "保存")
    assert app.session_state["guest_appearance"] == ("#268375", "dark")

    open_appearance()
    assert item(app.radio, "主题色").value == "松绿"
    item(app.radio, "主题色").set_value("自定义").run()
    item(app.text_input, "自定义色值").set_value("bad")
    click(app, "保存")
    assert "六位" in app.error[0].value
    item(app.text_input, "自定义色值").set_value("#C71C59")
    click(app, "返回")
    assert app.session_state["guest_appearance"] == ("#268375", "dark")

    open_appearance()
    item(app.radio, "主题色").set_value("自定义").run()
    item(app.text_input, "自定义色值").set_value("#C71C59")
    click(app, "保存")
    assert app.session_state["guest_appearance"] == ("#C71C59", "dark")


def test_switching_accounts_loads_only_the_current_readers_settings(reader):
    first = accounts.register("同名读者", "first-password", "first@example.org")
    second = accounts.register("同名读者", "second-password", "second@example.org")
    accounts.save_subscription(first.id, enabled=False, morning_time=time(6), evening_time=time(18),
                               preferences={**first.preferences, "keywords": ["火星"]})
    accounts.save_api(first.id, model=first.model, api_key="first-reader-only")
    accounts.save_reading(first.id, reading_mode="focus", reading_language="en")
    reader.session_state["account_id"] = first.id
    reader.query_params.update(mode="focus", lang="en")
    app = reader.run()

    click(app, "设置")
    assert item(app.text_area, "关键词").value == "火星"
    app.radio(key="settings_page").set_value("API 设置").run()
    assert item(app.text_input, "API 密钥").value == "first-reader-only"
    app.radio(key="settings_page").set_value("登录信息").run()
    assert item(app.text_input, "邮箱").value == "first@example.org"
    click(app, "退出登录")
    assert not app.query_params

    click(app, "设置")
    app.text_input(key="login_id").set_value(second.public_id)
    app.text_input(key="auth_password_登录").set_value("second-password")
    click(app, "登录")
    click(app, "设置")
    assert not item(app.text_area, "关键词").value
    app.radio(key="settings_page").set_value("API 设置").run()
    assert not item(app.text_input, "API 密钥").value
    app.radio(key="settings_page").set_value("登录信息").run()
    assert item(app.text_input, "邮箱").value == "second@example.org"
    app.radio(key="settings_page").set_value("阅读设置").run()
    assert app.radio(key="reader_mode").value == second.reading_mode
    assert app.radio(key="reader_language").value == second.reading_language
