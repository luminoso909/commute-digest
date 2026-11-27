import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from commute_digest import edition_service


def article(zh_title, en_title, category="科学与环境"):
    return {
        "category": category,
        "zh": {"title": zh_title, "details": "正文中的火星"},
        "en": {"title": en_title, "analysis": "Analysis of Mars"},
    }


@pytest.fixture
def archive(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "archive.sqlite3"))
    science = article("火星观测", "Mars Mission")
    first = article("新电池", "Storage grows 5%_yearly", "科技与 AI")
    traffic = article("道路维修", "Straße repairs", "社会与公共生活")
    with edition_service._database() as db:
        for number, created, slot, owner, cross, focus in [
            (1, "2026-11-22T06:00:00+08:00", None, 11, [science, traffic], [science]),
            (2, "2026-11-27T09:00:00+08:00", "2026-11-23T17:00:00+08:00", 11,
             [first, science, traffic], [first]),
            (3, "2026-11-25T05:02:00+08:00", "2026-11-25T05:00:00+08:00", 11, [first], []),
            (4, "2026-11-27T17:01:00+08:00", "2026-11-27T17:00:00+08:00", 22, [science], [science]),
        ]:
            payload = {"cross": cross, "focus": focus}
            if slot:
                payload["slot"] = slot
            db.execute("INSERT INTO issues VALUES (?, ?, ?)", (number, created, json.dumps(payload)))
            edition_service._assign_issue(db, owner, number)


def test_filters_before_pagination_and_retains_permalink(archive):
    args = dict(account_id=11, keyword=" 火星 ", category="科学与环境", limit=1)
    newest_match, = edition_service.search_archive(**args)
    assert newest_match["issue"]["id"] == 2
    assert newest_match["article_indices"] == [2]
    assert not newest_match["is_latest"]
    second, = edition_service.search_archive(**args, offset=1)
    assert second["issue"]["id"] == 1
    assert second["article_indices"] == [1]
    assert second == edition_service.search_archive(**args, order="asc")[0]

    matches = edition_service.search_archive(account_id=11, order="asc")
    assert [group["issue"]["id"] for group in matches] == [1, 2, 3]
    assert [group["is_latest"] for group in matches] == [False, False, True]
    assert matches[1]["article_indices"] == [1, 2, 3]


def test_title_language_category_and_visibility(archive):
    args = dict(account_id=11, language="en", issue_id=2, edition="evening")
    assert edition_service.search_archive(**args, keyword="火星") == []
    assert edition_service.search_archive(**args, keyword="社会与公共生活") == []
    assert edition_service.search_archive(**args, category="科技与 AI", keyword="mArS") == []
    for keyword, index in [("mArS", 2), ("%_", 1), ("STRASSE", 3)]:
        match, = edition_service.search_archive(**args, keyword=keyword)
        assert match["article_indices"] == [index]
    assert edition_service.search_archive(account_id=22, issue_id=2) == []
    assert edition_service.search_archive(issue_id=2) == []
    assert edition_service.search_archive(account_id=11, issue_id=2, edition="morning") == []
    legacy, = edition_service.search_archive(account_id=11, issue_id=1, edition="morning")
    assert legacy["issue"]["edition"] == "morning"


def test_edition_mode_changes_issue_options_and_results(archive):
    assert edition_service.archive_issue_ids(account_id=11) == [3, 2, 1]
    assert edition_service.archive_issue_ids(account_id=11, mode="focus") == [2, 1]
    assert edition_service.archive_issue_ids() == []
    assert edition_service.archive_issue_numbers(account_id=22) == {4: 1}
    args = dict(account_id=11, mode="focus", issue_id=2)
    assert edition_service.search_archive(**args, keyword="火星") == []
    match, = edition_service.search_archive(**args, keyword="新电池")
    assert match["is_latest"]
    assert match["article_indices"] == [1]


def test_upgrade_preserves_confirmed_recipients_and_unassigned_content(monkeypatch, tmp_path):
    path = tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("COMMUTE_DB", str(path))
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE accounts (id INTEGER PRIMARY KEY);
            CREATE TABLE issues (id INTEGER PRIMARY KEY, created_at TEXT, payload TEXT);
            CREATE TABLE issue_owners (issue_id INTEGER PRIMARY KEY, account_id INTEGER);
            CREATE TABLE subscription_deliveries (account_id INTEGER, issue_id INTEGER, status TEXT);
            CREATE TABLE subscription_deliveries_v1 (account_id INTEGER, issue_id INTEGER, status TEXT);
            INSERT INTO accounts VALUES (1), (2);
            INSERT INTO issue_owners VALUES (20, 1), (30, 2), (40, 99);
            INSERT INTO subscription_deliveries VALUES (1, 20, 'sent'), (2, 50, 'retry');
            INSERT INTO subscription_deliveries_v1 VALUES (1, 10, 'sent'), (2, 10, 'sent');
        """)
        payload = json.dumps({"cross": [article("标题", "Title")], "focus": []})
        db.executemany("INSERT INTO issues VALUES (?, '2026-11-24T05:00:00+08:00', ?)",
                       [(n, payload) for n in [10, 20, 30, 40, 50]])

    def load(account_id):
        return edition_service.archive_issue_numbers(account_id=account_id)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.map(load, [1, 2])
    assert first == {10: 1, 20: 2}
    assert second == {10: 1, 30: 2}
    assert edition_service.get_issue(20, account_id=2) is None
    assert edition_service.get_issue(40, account_id=99) is None
    assert edition_service.get_issue(50, account_id=2) is None
    assert edition_service.get_unassigned_issue(50)["cross"]
    assert edition_service.search_archive() == []


def test_concurrent_assignments_use_stable_numbers_per_account(archive):
    def assign(issue_id):
        with edition_service._database() as db:
            return edition_service._assign_issue(db, 22, issue_id)

    with ThreadPoolExecutor(max_workers=4) as workers:
        first, same, second, existing = workers.map(assign, [1, 1, 2, 4])
    assert first == same and {first, second} == {2, 3} and existing == 1
    assert edition_service.get_issue(4, account_id=22)["issue_number"] == 1
    assert edition_service.get_issue(2, account_id=11)["issue_number"] == 2
