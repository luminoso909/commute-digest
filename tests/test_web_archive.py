import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from commute_digest import accounts, edition_service


@pytest.fixture
def reader(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "web-archive.sqlite3"))
    account = accounts.register("归档读者", "archive-test-pass")
    with edition_service._database() as db:
        for number in range(1, 11):
            hour = 5 if number % 2 else 17
            slot = f"2026-11-{number:02}T{hour:02}:00:00+08:00"
            stories = []
            for title, topic in [("城市交通", "社会与公共生活"), ("海风观测", "科学与环境"),
                                 ("海风发电", "科技与 AI")]:
                local = {"title": f"{title} {number}", "details": "正文中的海风与潮汐", "analysis": "分析"}
                stories.append({"category": topic, "zh": local, "en": local})
            payload = {"slot": slot, "cross": stories, "focus": []}
            db.execute("INSERT INTO issues VALUES (?, ?, ?)", (number, slot, json.dumps(payload)))
            edition_service._assign_issue(db, account.id, number)
    entry = Path(__file__).resolve().parents[1] / "src" / "commute_digest" / "web.py"
    app = AppTest.from_file(str(entry))
    app.session_state["account_id"] = account.id
    return app


def details_on_screen(app):
    assert not app.exception
    return [button.key for button in app.button if (button.key or "").startswith("detail_")]


def test_combined_filters_title_search_and_return_from_article(reader):
    app = reader.run()
    app.selectbox(key="archive_order").select("asc").run()
    app.selectbox(key="archive_edition").select("evening").run()
    app.text_input(key="archive_q").set_value("海风").run()
    app.selectbox(key="archive_topic").select("科学与环境").run()
    assert details_on_screen(app) == [f"detail_{n}_cross_2" for n in [2, 4, 6, 8, 10]]
    app.selectbox(key="archive_period").select("4").run()
    assert details_on_screen(app) == ["detail_4_cross_2"]
    app.button(key="detail_4_cross_2").click().run()
    assert not app.exception
    assert app.query_params["article"] == "2"
    assert any("海风观测 4" in block.value and "正文中的海风与潮汐" in block.value
               for block in app.markdown)
    app.button(key="back_to_list").click().run()
    assert details_on_screen(app) == ["detail_4_cross_2"]
    assert app.text_input(key="archive_q").value == "海风"
    for key, value in [("order", "asc"), ("edition", "evening"), ("period", "4"), ("topic", "科学与环境")]:
        assert app.selectbox(key=f"archive_{key}").value == value
    app.text_input(key="archive_q").set_value("潮汐").run()
    assert not details_on_screen(app)
    assert any("没有符合条件" in notice.value for notice in app.info)


def test_issue_link_and_search_after_paging(reader):
    reader.query_params.update(issue="3", mode="cross", lang="zh")
    app = reader.run()
    assert app.selectbox(key="archive_period").value == "3"
    assert details_on_screen(app) == [f"detail_3_cross_{i}" for i in [1, 2, 3]]
    app.button(key="detail_3_cross_2").click().run()
    assert app.query_params["issue"] == "3" and app.query_params["article"] == "2"
    app.button(key="back_to_list").click().run()
    assert app.selectbox(key="archive_period").value == "3"
    app.button(key="archive_reset").click().run()
    assert app.selectbox(key="archive_period").value == ""
    next(button for button in app.button if button.label == "下一页").click().run()
    assert app.query_params["page"] == "2"
    assert details_on_screen(app)[0] == "detail_2_cross_1"
    app.text_input(key="archive_q").set_value("海风观测 10").run()
    assert "page" not in app.query_params
    assert details_on_screen(app) == ["detail_10_cross_2"]


def test_unavailable_issue_filter_does_not_show_unrelated_issues(reader):
    reader.query_params.update(period="99")
    app = reader.run()
    assert not details_on_screen(app)
    assert "无法查看所选期次" in app.info[0].value
    app.button(key="archive_reset").click().run()
    assert details_on_screen(app)
