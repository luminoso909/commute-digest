import json
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from commute_digest import accounts, edition_service, news_editor

WEB_ENTRY = Path(__file__).resolve().parents[1] / "src" / "commute_digest" / "web.py"


def article(category, slug, zh_title, en_title):
    return {
        "category": category,
        "source_name": "示例来源",
        "source_url": f"https://example.com/{slug}",
        "published_at": "2026-11-21T08:00:00+08:00",
        "zh": {
            "title": zh_title,
            "summary": "中文摘要",
            "background": "背景信息",
            "why_it_matters": "影响与意义",
        },
        "en": {
            "title": en_title,
            "summary": "English summary",
            "background": "Context",
            "why_it_matters": "Impact",
        },
    }


@pytest.fixture
def widget_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "digest.sqlite3"))
    feed = tmp_path / "static" / "widget-latest.json"
    monkeypatch.setattr(edition_service, "WIDGET_FEED_PATH", feed)
    return feed


@pytest.fixture
def sample_issue(monkeypatch, widget_storage):
    editions = {
        "cross": [article("科学与环境", "science", "科学报道", "Science update")],
        "focus": [article("科技与 AI", "technology", "科技报道", "Technology update")],
    }

    def generate(topics, keywords, *, now, window_start, exclude_articles, required_mode):
        return editions

    monkeypatch.setattr(news_editor, "generate_digest", generate)
    edition_service.save_preferences(["科技与 AI"], [" 机器人 ", "机器人"], "focus", "en")


@pytest.fixture
def sample_reader(sample_issue):
    account = accounts.register("归档读者", "reader-test-password")
    accounts.save_api(account.id, model=account.model, api_key="test-private-key")
    return account


def test_publish_saves_preferences_and_permalinks(sample_reader):
    aid = sample_reader.id
    first = edition_service.publish(account_id=aid)
    latest = edition_service.publish(account_id=aid)

    assert edition_service.get_preferences()["keywords"] == ["机器人"]
    assert edition_service.get_issue(first["id"], account_id=aid) == first
    assert edition_service.get_issue(account_id=aid) == latest
    assert (first["issue_number"], latest["issue_number"]) == (1, 2)
    assert [item["category"] for item in latest["focus"]] == ["科技与 AI"]
    assert edition_service.get_issue(latest["id"] + 1, account_id=aid) is None


def test_archive_orders_visible_publication_slots_before_paginating(widget_storage):
    local = {"title": "标题", "summary": "邮件用摘要", "details": "原文细节", "analysis": "分析内容"}
    story = {"zh": local, "en": local}
    records = [
        (1, "2026-11-24T05:00:00+08:00", None, None, [story]),
        (2, "2026-11-24T17:01:00+08:00", "2026-11-24T17:00:00+08:00", None, [story]),
        (3, "2026-11-25T17:01:00+08:00", "2026-11-25T17:00:00+08:00", 22, [story]),
        (4, "2026-11-25T18:00:00+08:00", "2026-11-24T05:00:00+08:00", 11, [story]),
        (5, "2026-11-26T05:01:00+08:00", "2026-11-26T05:00:00+08:00", None, []),
    ]
    with edition_service._database() as connection:
        for number, created, slot, owner, focus in records:
            payload = {"cross": [story], "focus": focus}
            if slot:
                payload["slot"] = slot
            connection.execute("INSERT INTO issues VALUES (?, ?, ?)", (number, created, json.dumps(payload)))
            if owner:
                edition_service._assign_issue(connection, owner, number)
            elif number in (1, 2):
                for recipient in (11, 22):
                    edition_service._assign_issue(connection, recipient, number)
            else:
                edition_service._assign_issue(connection, 22, number)

    assert edition_service.list_issues() == []
    assert [item["id"] for item in edition_service.list_issues(account_id=11, mode="focus")] == [2, 4, 1]
    assert [item["id"] for item in edition_service.list_issues(account_id=22, offset=1, limit=2)] == [3, 2]
    assert edition_service.get_issue(3, account_id=11) is None
    assert edition_service.get_issue(3, account_id=22)["focus"][0]["zh"] == local


@pytest.fixture
def mail_configuration(monkeypatch):
    configuration = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "465",
        "SMTP_USER": "sender@example.com",
        "SMTP_PASSWORD": "local-test-placeholder",
        "SMTP_FROM": "sender@example.com",
        "RECIPIENT_EMAIL": "reader@example.com",
        "PUBLIC_BASE_URL": "https://digest.example.com",
    }
    for key, value in configuration.items():
        monkeypatch.setenv(key, value)
    return configuration


def test_delivery_uses_subscriber_preferences_and_skips_a_duplicate(
    sample_issue, mail_configuration, monkeypatch
):
    messages = []
    monkeypatch.setattr(
        edition_service, "_send_message", lambda message, settings: messages.append(message)
    )

    assert "已由 SMTP 接收" in edition_service.deliver_slot()
    assert "已有一条投递记录" in edition_service.deliver_slot()

    assert len(messages) == 1
    assert str(messages[0]["To"]) == mail_configuration["RECIPIENT_EMAIL"]
    body = messages[0].get_body(preferencelist=("plain",)).get_content()
    assert "Technology update" in body
    assert "Science update" not in body
    assert "?issue=" not in body and "Read this issue online" not in body
    assert "https://example.com/technology" in body


@pytest.mark.parametrize(
    ("mode", "language", "edition_heading"),
    [("cross", "zh", "跨领域版"), ("focus", "en", "My Topics")],
)
def test_email_preserves_article_links_and_localized_copy_in_both_alternatives(
    sample_reader, mail_configuration, mode, language, edition_heading
):
    issue = edition_service.publish(account_id=sample_reader.id)
    preferences = edition_service.get_preferences()
    preferences.update(delivery_mode=mode, delivery_language=language)
    story = issue[mode][0]
    story["source_url"] += "?edition=print&view=full"
    local = story[language]
    local["title"] += " <literal> & example"
    local["summary"] += " <summary example> & details"
    local.update(details="长文细节只在网页显示", analysis="长文分析只在网页显示")
    other_mode = "cross" if mode == "focus" else "focus"
    other_language = "zh" if language == "en" else "en"

    message = edition_service._make_message(
        issue, preferences, edition_service._mail_settings(), "2026-11-21"
    )

    assert message.get_content_type() == "multipart/alternative"
    assert edition_heading in str(message["Subject"])
    plain_part, html_part = message.iter_parts()
    assert plain_part.get_content_type() == "text/plain"
    assert html_part.get_content_type() == "text/html"
    plain, html = plain_part.get_content(), html_part.get_content()
    source_url = story["source_url"]
    issue_url = (
        f"{mail_configuration['PUBLIC_BASE_URL']}/?issue={issue['id']}&mode={mode}&lang={language}"
    )

    for value in (local["title"], local["summary"], source_url, issue_url):
        assert value in plain
        assert escape(value) in html
    assert f'href="{escape(source_url)}"' in html
    assert f">{escape(source_url)}<" in html
    assert f'href="{escape(issue_url)}"' in html
    assert f'href="{escape(issue_url)}&amp;article=1"' in html
    for section in (local["details"], local["analysis"]):
        assert section not in plain
        assert section not in html
    assert "<literal>" not in html
    assert "<summary example>" not in html
    for unrelated in (story[other_language]["title"], issue[other_mode][0][language]["title"]):
        assert unrelated not in plain
        assert unrelated not in html


@pytest.mark.parametrize("failed_step", ["connect", "login"])
def test_delivery_retries_failed_smtp_setup_once_then_skips_after_success(
    sample_issue, mail_configuration, monkeypatch, failed_step
):
    smtp = Mock()
    smtp.send_message.return_value = {}
    if failed_step == "connect":
        connect = Mock(side_effect=[ConnectionRefusedError("refused"), smtp])
    else:
        connect = Mock(return_value=smtp)
        smtp.login.side_effect = [
            edition_service.smtplib.SMTPServerDisconnected("login lost"),
            None,
        ]
    monkeypatch.setattr(edition_service.smtplib, "SMTP_SSL", connect)

    with pytest.raises(edition_service.MorningError, match="可重试本期"):
        edition_service.deliver_slot()
    smtp.send_message.assert_not_called()
    with edition_service._database() as connection:
        issue_id, = connection.execute("SELECT issue_id FROM deliveries").fetchone()
    issue = edition_service.get_unassigned_issue(issue_id)
    assert issue is not None
    with edition_service._database() as connection:
        assert connection.execute("SELECT * FROM delivery_editions").fetchall() == []

    assert "已由 SMTP 接收" in edition_service.deliver_slot()
    with edition_service._database() as connection:
        assert connection.execute("SELECT id FROM issues").fetchall() == [(issue_id,)]
    assert "已有一条投递记录" in edition_service.deliver_slot()
    assert connect.call_count == 2
    smtp.send_message.assert_called_once()
    sent_message = smtp.send_message.call_args.args[0]
    plain = sent_message.get_body(preferencelist=("plain",)).get_content()
    assert "?issue=" not in plain
    with edition_service._database() as connection:
        assert connection.execute("SELECT * FROM delivery_editions").fetchall() == [
            (issue["slot"], "focus", "en"),
        ]


def test_delivery_does_not_retry_when_smtp_disconnects_during_submission(
    sample_issue, mail_configuration, monkeypatch
):
    smtp = Mock()
    smtp.send_message.side_effect = edition_service.smtplib.SMTPServerDisconnected(
        "delivery uncertain"
    )
    connect = Mock(return_value=smtp)
    monkeypatch.setattr(edition_service.smtplib, "SMTP_SSL", connect)

    with pytest.raises(edition_service.MorningError, match="SMTP 未确认接收"):
        edition_service.deliver_slot()
    assert "已有一条投递记录" in edition_service.deliver_slot()
    connect.assert_called_once()
    smtp.send_message.assert_called_once()


def test_unusable_api_result_keeps_previous_issue(sample_reader, monkeypatch):
    previous = edition_service.publish(account_id=sample_reader.id)

    def api_error(topics, keywords, *, now, window_start, exclude_articles, required_mode):
        raise news_editor.EditorError("DeepSeek 联网采集未完成，请重试。")

    monkeypatch.setattr(news_editor, "generate_digest", api_error)
    with pytest.raises(edition_service.MorningError, match="未完成"):
        edition_service.publish(account_id=sample_reader.id)
    assert edition_service.get_issue(account_id=sample_reader.id) == previous


def test_morning_evening_and_verification_have_distinct_delivery_records(
    sample_issue, mail_configuration, monkeypatch
):
    class FrozenDatetime(datetime):
        instant = datetime.fromisoformat("2026-11-24T05:00:00+08:00")

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz)

    monkeypatch.setattr(edition_service, "datetime", FrozenDatetime)
    edition_service.save_preferences([], [], "cross", "zh")
    legacy = {"cross": [article("科学与环境", "legacy", "旧报道", "Legacy story")], "focus": []}
    with edition_service._database() as connection:
        connection.execute(
            "INSERT INTO issues (id, created_at, payload) VALUES (1, ?, ?)",
            (FrozenDatetime.instant.isoformat(), json.dumps(legacy)),
        )
        connection.execute("INSERT INTO deliveries VALUES ('2026-11-24', 'sent', 1)")

    assert edition_service.get_unassigned_issue(1)["edition"] == "morning"
    assert "slot" not in edition_service.get_unassigned_issue(1)
    generate = Mock(wraps=news_editor.generate_digest)
    send = Mock()
    monkeypatch.setattr(news_editor, "generate_digest", generate)
    monkeypatch.setattr(edition_service, "_send_message", send)

    assert "通勤晨报第 2 期" in edition_service.deliver_slot()
    assert "通勤晨报第 3 期" in edition_service.deliver_slot(verification=True)
    assert "已有一条投递记录" in edition_service.deliver_slot(verification=True)
    assert "已有一条投递记录" in edition_service.deliver_slot()
    FrozenDatetime.instant += timedelta(hours=12)
    assert "通勤晚报第 4 期" in edition_service.deliver_slot(FrozenDatetime.instant)
    assert "已有一条投递记录" in edition_service.deliver_slot()

    subjects = [str(call.args[0]["Subject"]) for call in send.call_args_list]
    assert subjects == [
        "通勤晨报 · 跨领域版 · 2026-11-24",
        "通勤晨报 · 试刊 · 跨领域版 · 2026-11-24",
        "通勤晚报 · 跨领域版 · 2026-11-24",
    ]
    assert edition_service.get_unassigned_issue(4)["window_start"] == "2026-11-24T05:00:00+08:00"
    assert edition_service.get_unassigned_issue(4)["window_end"] == "2026-11-24T17:00:00+08:00"
    preview_request = generate.call_args_list[1].kwargs
    assert preview_request["window_start"].isoformat() == "2026-11-23T17:00:00+08:00"
    assert preview_request["now"].isoformat() == "2026-11-24T05:00:00+08:00"
    assert legacy["cross"][0] in preview_request["exclude_articles"]
    assert edition_service.get_unassigned_issue(2)["cross"][0] in preview_request["exclude_articles"]


def test_widget_feed_uses_the_accounts_delivery_ledger(widget_storage):
    import asyncio
    from base64 import b64encode
    from starlette.requests import Request
    from commute_digest import accounts, login_sessions, subscription_delivery

    password = "widget-password"
    first = accounts.register("甲", password)
    second = accounts.register("乙", password)
    subscription_delivery._initialize_deliveries()
    slot = "2026-11-24T17:00:00+08:00"
    next_slot = "2026-11-25T05:00:00+08:00"
    preferences = {"delivery_mode": "focus", "delivery_language": "en"}
    with edition_service._database() as db:
        for issue_id, user, stamp, status in [
            (11, first, slot, "sent"), (12, first, next_slot, "sending"),
            (13, second, next_slot, "sent"),
        ]:
            selected = article("科学与环境", "example", "关注", f"Focus {issue_id}")
            shared = article("商业与产业", "business", "跨领域", f"Across {issue_id}")
            payload = {"slot": stamp, "cross": [shared], "focus": [selected]}
            db.execute("INSERT INTO issues VALUES (?,?,?)", (issue_id, stamp, json.dumps(payload)))
            edition_service._assign_issue(db, user.id, issue_id)
            db.execute(
                """INSERT INTO subscription_deliveries
                   (account_id,slot,delivery_day,scheduled_at,settings_key,claim,claimed_at,status,issue_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (user.id, stamp, stamp[:10], stamp, json.dumps(["time", preferences]), str(issue_id),
                 stamp, status, issue_id),
            )

    def request(**headers):
        scope = {"type": "http", "scheme": "https", "path": "/account/widget-feed",
                 "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
        return asyncio.run(login_sessions.widget_feed_route(Request(scope)))

    def credentials(user, value):
        return "Basic " + b64encode(f"{user.public_id}:{value}".encode()).decode()

    for headers in [{}, {"authorization": "Basic ***"}, {"authorization": credentials(first, "incorrect")}]:
        assert request(**headers).status_code == 401
    response = request(authorization=credentials(first, password))
    assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
    feed = json.loads(response.body)
    assert (feed["issue_id"], feed["issue_number"], feed["titles"]) == (11, 1, ["Focus 11"])
    _, grant = login_sessions.issue(second.id)
    cookie = login_sessions.exchange(grant)
    other = request(cookie=f"{login_sessions.COOKIE}={cookie}")
    assert json.loads(other.body)["titles"] == ["Focus 13"]
    assert not widget_storage.exists()


def test_widget_feed_retirement_cleans_legacy_data(widget_storage, monkeypatch):
    import asyncio
    from starlette.requests import Request
    from commute_digest import login_sessions

    widget_storage.parent.mkdir()
    widget_storage.write_text('{"titles":["legacy headline"]}')
    temporary = widget_storage.parent / ".widget-latest.old"
    temporary.write_text("legacy")
    monkeypatch.setattr(edition_service.sys, "argv", ["commute-digest", "widget-feed"])
    assert edition_service.main() == 0
    assert edition_service.main() == 0
    assert not temporary.exists() and not widget_storage.exists()
    old = asyncio.run(login_sessions.retired_widget_feed(Request({"type": "http"})))
    assert old.status_code == 410 and not old.body


def test_streamlit_opens_mailing_permalink_and_switches_editions(sample_issue):
    reader = accounts.register("邮件读者", "valid-test-password")
    accounts.save_api(reader.id, model=reader.model, api_key="mail-test-key")
    issue = edition_service.publish(account_id=reader.id)
    app = AppTest.from_file(str(WEB_ENTRY))
    app.query_params.update(issue=str(issue["id"]), mode="focus", lang="en")
    app.run()
    assert not app.exception
    assert not app.selectbox and not any("Technology update" in element.value for element in app.markdown)
    next(button for button in app.button if button.label == "Sign in / Register").click().run()
    app.text_input(key="login_id").set_value(reader.public_id)
    app.text_input(key="auth_password_登录").set_value("valid-test-password")
    next(button for button in app.button if button.label == "登录").click().run()
    assert not app.exception
    assert any("Technology update" in element.value for element in app.markdown)
    next(button for button in app.button if button.label == "设置").click().run()
    app.radio(key="settings_page").set_value("阅读设置").run()
    app.radio(key="reader_mode").set_value("cross")
    next(button for button in app.button if button.label == "保存").click().run()
    assert not app.exception
    assert any("Science update" in element.value for element in app.markdown)


@pytest.mark.parametrize(
    ("issue_id", "message"),
    [((1 << 63) - 1, "你的日报中没有这一期。"), (1 << 63, "晨报链接中的期号无效。")],
)
def test_streamlit_validates_the_sqlite_issue_id_range(sample_issue, issue_id, message):
    app = AppTest.from_file(str(WEB_ENTRY))
    app.session_state["account_id"] = accounts.register("登录读者", "valid-test-password").id
    app.query_params.update(issue=str(issue_id))
    app.run()

    assert not app.exception
    assert [warning.value for warning in app.warning] == [message]
