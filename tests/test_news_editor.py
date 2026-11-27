import io
import json
import wave
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError

import pytest

from commute_digest import news_editor

NOW = datetime(2026, 11, 21, 9, tzinfo=UTC)
WINDOW_START = NOW - timedelta(hours=12)
FIELDS = {
    "title": "A title",
    "summary": "A summary",
    "details": "Reported facts and context",
    "analysis": "The mechanism and its impact",
}


def news_source(slug, published_at="2026-11-21T06:15:00Z", **fields):
    return {
        "url": f"https://news.example/{slug}",
        "title": "A valid headline",
        "site_name": "Test Publisher",
        "published_at": published_at,
        "snippet": "The article covers local transportation.",
        **fields,
    }


def research_response(*sources):
    return {
        "status": "completed",
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "sources": list(sources)},
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Research", "annotations": None}],
            },
        ],
    }


def entry(source_id, event_key, category=news_editor.CATEGORIES[0], **fields):
    return {
        "category": category,
        "event_key": event_key,
        "source_id": source_id,
        "zh": FIELDS,
        "en": FIELDS,
        **fields,
    }


def install_responses(monkeypatch, research, draft, *, verify_publishers=False):
    requests = []
    research_rounds = iter(research) if isinstance(research, list) else None

    def post(path, payload, **kwargs):
        requests.append((path, payload))
        if path == "/responses":
            return json.dumps(
                next(research_rounds) if research_rounds is not None else research
            ).encode()
        response = {
            "choices": [{"message": {"content": json.dumps(draft)}, "finish_reason": "stop"}]
        }
        return json.dumps(response).encode()

    monkeypatch.setattr(news_editor, "_post", post)
    if not verify_publishers:
        research_sources = news_editor._research_sources
        def local_sources(output, start, end, **kwargs):
            sources, notes = research_sources(output, start, end)
            return {key: replace(source, publisher_excerpt="The publisher reported confirmed facts.")
                    for key, source in sources.items()}, notes
        monkeypatch.setattr(news_editor, "_research_sources", local_sources)
    return requests


def test_generation_uses_publisher_timestamps_instead_of_search_dates(monkeypatch):
    result = research_response(
        news_source("late"),
        news_source("no-publisher-time"),
        news_source("date-padded", "2026-11-21T00:00:00+08:00"),
        news_source("exact-midnight", "2026-11-21T00:00:00+08:00"),
        news_source("on-cutoff"),
    )
    midnight = datetime.fromisoformat("2026-11-21T00:00:00+00:00")
    verified = {
        "late": NOW + timedelta(minutes=1),
        "date-padded": NOW - timedelta(hours=2),
        "exact-midnight": midnight,
        "on-cutoff": NOW,
    }
    def read_page(url):
        timestamp = verified.get(url.rsplit("/", 1)[-1])
        return news_editor.publisher_time.ArticlePage(timestamp, "Publisher's direct account.") if timestamp else None

    monkeypatch.setattr(news_editor.publisher_time, "read_article", read_page)
    requests = install_responses(
        monkeypatch,
        result,
        {"cross": [entry("S1", "first"), entry("S2", "second"), entry("S3", "third")]},
        verify_publishers=True,
    )
    digest = news_editor.generate_digest([], [], now=NOW)
    included = {
        item["source_url"].rsplit("/", 1)[-1]: item["published_at"] for item in digest["cross"]
    }
    assert included == {
        slug: stamp.isoformat() for slug, stamp in verified.items() if slug != "late"
    }
    draft_input = json.loads(requests[1][1]["messages"][1]["content"])
    assert len(draft_input["sources"]) == 3
    assert all(row["publisher_excerpt"] == "Publisher's direct account." for row in draft_input["sources"].values())


def test_digest_only_uses_recent_api_sources_and_deduplicates(monkeypatch):
    research = research_response(
        news_source("recent"),
        news_source("stale", "2026-11-20T20:59:59Z"),
        news_source("future", "2026-11-21T09:00:01Z"),
        news_source("undated", None),
        news_source("same-event"),
        news_source("with-byline", snippet="An analysis by Chris Wu follows."),
        news_source("another"),
    )
    research["output"].append(
        {
            "type": "web_search_call",
            "status": "completed",
            "action": {"type": "open_page"},
            "page": {
                "url": "https://example.org/fetched",
                "title": "Research article",
                "published_at": "2026-11-21T08:10:00Z",
            },
        }
    )
    first = entry(
        "S1",
        "one-event",
        source_url="https://fabricated.example/x",
        published_at="2099-01-01",
        perspective={"author": "Chris Wu", "source_id": "S3", "zh": "中文解读", "en": "Analysis"},
    )
    draft = {
        "cross": [
            first,
            entry("S2", "ONE-EVENT"),
            entry("S42", "unsupported"),
            entry("S5", "second", category=news_editor.CATEGORIES[2]),
            entry("S4", "third", category=news_editor.CATEGORIES[3]),
        ],
        "focus": [first, entry("S5", "second", category=news_editor.CATEGORIES[2])],
    }
    requests = install_responses(monkeypatch, research, draft)

    report = news_editor.generate_digest([news_editor.CATEGORIES[0]], ["transport"], now=NOW)

    assert [path for path, _ in requests] == ["/responses", "/chat/completions"]
    assert requests[0][1]["reasoning"] == {"effort": "low"}
    assert requests[0][1]["max_output_tokens"] == 12000
    assert "2026-11-21T05:00:00+08:00, 2026-11-21T17:00:00+08:00" in requests[0][1]["input"]
    assert requests[0][1]["tools"] == [
        {"type": "deepseek_web_search"},
        {"type": "deepseek_read_url"},
    ]
    draft_sources = json.loads(requests[1][1]["messages"][1]["content"])["sources"]
    assert set(draft_sources) == {"S1", "S2", "S3", "S4", "S5"}
    first_result, fetched_result, last_result = report["cross"]
    assert first_result["source_url"] == "https://news.example/recent"
    assert first_result["published_at"] == "2026-11-21T06:15:00+00:00"
    assert first_result["perspective"]["source_url"] == "https://news.example/with-byline"
    assert fetched_result["source_name"] == "example.org"
    assert fetched_result["published_at"] == "2026-11-21T08:10:00+00:00"
    assert report["focus"] == [
        first_result,
        {**fetched_result, "supplemental": True},
        {**last_result, "supplemental": True},
    ]


def test_only_exact_times_in_the_window_are_eligible():
    result = research_response(
        news_source("previous-slot", "2026-11-21T05:00:00+08:00"),
        news_source("after-start", "2026-11-21T05:00:01+08:00"),
        news_source("end", "2026-11-21T17:00:00+08:00"),
        news_source("future", "2026-11-21T17:00:01+08:00"),
        news_source("date-only", "2026-11-21"),
        news_source("no-timezone", "2026-11-21T09:00:00"),
    )
    sources, _ = news_editor._research_sources(result["output"], WINDOW_START, NOW)
    assert [source.url for source in sources.values()] == [
        "https://news.example/after-start",
        "https://news.example/end",
    ]


@pytest.mark.parametrize(
    "value",
    (
        "2026-11-21T00:00:00+08:00",
        "2026-11-20T16:00:00Z",
        "2026-11-21T00:00:00Z",
        "2026-11-21T08:00:00+08:00",
    ),
)
def test_provider_midnight_timestamps_are_date_only(value):
    assert news_editor._reported_time(value) == datetime(2026, 11, 21)


def test_overnight_slot_requires_confirmed_non_midnight_time():
    result = research_response(
        news_source("midnight-placeholder", "2026-11-21T00:00:00+08:00"),
        news_source("confirmed", "2026-11-20T16:00:00Z"),
        news_source("published-after-cutoff", "2026-11-21T00:00:00+08:00"),
    )
    for slug, timestamp in (
        ("confirmed", "2026-11-21T00:00:01+08:00"),
        ("published-after-cutoff", "2026-11-21T06:37:00+08:00"),
    ):
        result["output"].append(
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "open_page"},
                "page": {
                    "url": f"https://news.example/{slug}",
                    "title": "Source page",
                    "published_at": timestamp,
                },
            }
        )
    end = datetime.fromisoformat("2026-11-21T05:00:00+08:00")
    sources, _ = news_editor._research_sources(result["output"], end - timedelta(hours=12), end)
    assert [(item.url, item.published_at) for item in sources.values()] == [
        ("https://news.example/confirmed", "2026-11-21T00:00:01+08:00"),
    ]


def test_invalid_ports_do_not_become_sources(monkeypatch):
    result = research_response(
        news_source("invalid", url="https://news.example:bad/article"),
        news_source("out-of-range", url="https://news.example:99999/article"),
    )
    requests = install_responses(monkeypatch, result, {})
    with pytest.raises(news_editor.EditorError, match="至少 3 条"):
        news_editor.generate_digest([], [], now=NOW)
    assert len(requests) == 2


def test_equivalent_source_urls_do_not_support_a_second_article_or_viewpoint(monkeypatch):
    result = research_response(
        news_source("original", url="https://news.example", snippet="Analysis by Jane Wu"),
        news_source("duplicate", url="HTTPS://NEWS.EXAMPLE:443/#analysis", snippet="By Jane Wu"),
        news_source("different", url="https://[2001:db8::1]:8443/Report?edition=B"),
        news_source("third"),
    )
    opinion = {"source_id": "S1", "author": "Jane Wu", "zh": "署名观点", "en": "Analysis"}
    draft = {
        "cross": [
            entry("S1", "one", perspective=opinion),
            entry("S1", "alias"),
            entry("S2", "two"),
            entry("S3", "three"),
        ],
        "focus": [],
    }
    install_responses(monkeypatch, result, draft)
    articles = news_editor.generate_digest([], [], now=NOW)["cross"]
    assert [a["source_url"] for a in articles] == [
        "https://news.example/",
        "https://[2001:db8::1]:8443/Report?edition=B",
        "https://news.example/third",
    ]
    assert "perspective" not in articles[0]


@pytest.mark.parametrize("pages_first", [False, True])
def test_opened_page_dates_override_search_dates(pages_first):
    result = research_response(
        *[
            news_source(slug)
            for slug in (
                "precise",
                "old",
                "future",
                "ambiguous",
                "missing",
                "failed",
                "zh",
                "slash",
            )
        ]
    )
    pages = []
    for slug, date in [
        ("precise", "2026-11-21T01:02:03Z"),
        ("old", "2026-11-15"),
        ("future", "2026-11-22"),
        ("ambiguous", "11/12/2026"),
        ("missing", None),
        ("failed", "2026-11-15"),
        ("zh", "2026年11月20日"),
        ("slash", "2026/11/21"),
    ]:
        pages.append(
            {
                "type": "web_search_call",
                "status": "failed" if slug == "failed" else "completed",
                "action": {"type": "find_in_page" if slug == "old" else "open_page"},
                "page": {
                    "url": f"https://news.example/{slug}",
                    "title": None,
                    "published_at": date,
                },
            }
        )
    if pages_first:
        result["output"] = pages + result["output"]
    else:
        result["output"].extend(pages)
    records, _ = news_editor._research_sources(result["output"], WINDOW_START, NOW)
    assert {r.url.rsplit("/", 1)[-1]: r.published_at for r in records.values()} == {
        "precise": "2026-11-21T01:02:03+00:00",
        "ambiguous": "2026-11-21T06:15:00+00:00",
        "missing": "2026-11-21T06:15:00+00:00",
        "failed": "2026-11-21T06:15:00+00:00",
        "slash": "2026-11-21T06:15:00+00:00",
    }
    assert all(r.title == "A valid headline" and r.snippet for r in records.values())


def test_page_date_matches_timestamp_in_the_briefings_timezone():
    research = research_response(news_source("midnight"))
    for action, date in [("open_page", "2026-11-20"), ("find_in_page", "2026-11-19T18:00:00Z")]:
        research["output"].append(
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": action},
                "page": {"url": "https://news.example/midnight", "published_at": date},
            }
        )
    end = datetime(2026, 11, 20, 1, tzinfo=UTC)
    sources, _ = news_editor._research_sources(research["output"], end - timedelta(hours=12), end)
    assert sources["S1"].published_at == "2026-11-19T18:00:00+00:00"


def test_digest_rejects_unverified_or_blank_sources(monkeypatch):
    result = research_response(
        news_source("stale", "2026-11-11T03:04:05Z"),
        news_source("invalid-clock", "2026-11-21T05:00:00"),
        news_source("invalid-link", url="javascript:alert(1)"),
        news_source("blank-headline", title=""),
    )
    requests = install_responses(monkeypatch, result, {})

    with pytest.raises(news_editor.EditorError, match="至少 3 条"):
        news_editor.generate_digest([], [], now=NOW)

    assert len(requests) == 2


def test_sparse_focus_and_unverified_attribution(monkeypatch):
    article = entry(
        "S1",
        "first",
        perspective={"author": "Unknown Name", "source_id": "S2", "zh": "中文", "en": "English"},
    )
    install_responses(
        monkeypatch,
        research_response(news_source("first"), news_source("second"), news_source("third")),
        {"cross": [article, entry("S2", "second"), entry("S3", "third")], "focus": [article]},
    )

    report = news_editor.generate_digest([news_editor.CATEGORIES[2]], [], now=NOW)

    assert "perspective" not in report["cross"][0]
    assert report["focus"] == [{**article, "supplemental": True} for article in report["cross"]]


def test_keyword_only_focus_requires_evidence_in_the_story(monkeypatch):
    rail = entry("S1", "rail", en={**FIELDS, "title": "New rail line", "analysis": "AI"})
    ai = entry("S2", "ai")
    chip = entry("S3", "chip", zh={**FIELDS, "summary": "研究团队公布芯片测试结果。"})
    install_responses(
        monkeypatch,
        research_response(
            news_source("rail"),
            news_source("model", snippet="新AI模型已发布。"),
            news_source("chip"),
        ),
        {"cross": [rail, ai, chip], "focus": [rail, ai, chip]},
    )

    report = news_editor.generate_digest([], ["ai", "芯片"], now=NOW)
    assert [item["source_url"] for item in report["focus"] if not item.get("supplemental")] == [
        "https://news.example/model",
        "https://news.example/chip",
    ]
    assert all(
        item["supplemental"] for item in news_editor.generate_digest([], ["遥感"], now=NOW)["focus"]
    )
    assert (
        len(news_editor.generate_digest([news_editor.CATEGORIES[0]], ["遥感"], now=NOW)["focus"])
        == 3
    )


def test_perspective_requires_a_full_author_name(monkeypatch):
    viewpoint = {"source_id": "S3", "zh": "中文观点", "en": "An opinion"}
    first = entry("S1", "first", perspective={**viewpoint, "author": "Ann"})
    second = entry("S2", "second", perspective={**viewpoint, "author": "李明"})
    install_responses(
        monkeypatch,
        research_response(
            news_source("first"),
            news_source("second"),
            news_source("report", title="Annual report", snippet="研究员李明表示应当公开数据。"),
        ),
        {"cross": [first, second, entry("S3", "third")], "focus": []},
    )

    articles = news_editor.generate_digest([], [], now=NOW)["cross"]
    assert "perspective" not in articles[0]
    assert articles[1]["perspective"]["author"] == "李明"


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ([1, 2, 3, 1, 1, 1, 1], ["3", "2", "1"]),
        ([2, 3, 2, 2, 3, 2, 1], ["2", "5", "1", "3", "4", "6"]),
    ],
)
def test_significance_controls_count_and_order(monkeypatch, scores, expected):
    candidates = [
        entry(f"S{index}", str(index), importance=score) for index, score in enumerate(scores, 1)
    ]
    install_responses(
        monkeypatch,
        research_response(*(news_source(str(index)) for index in range(1, 8))),
        {"cross": candidates, "focus": []},
    )

    report = news_editor.generate_digest([], [], now=NOW, window_start=WINDOW_START)

    assert [article["event_key"] for article in report["cross"]] == expected
    assert report["focus"] == report["cross"]


def test_second_research_pass_reuses_verified_sources_from_incomplete_search(monkeypatch):
    first = research_response(news_source("first"))
    first.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    later = research_response(news_source("second"), news_source("third"))
    requests = install_responses(
        monkeypatch,
        [first, later],
        {"cross": [entry("S1", "first"), entry("S2", "second")], "focus": [entry("S3", "third")]},
    )

    report = news_editor.generate_digest([], [], now=NOW)

    assert [article["event_key"] for article in report["cross"]] == ["first", "second", "third"]
    assert [path for path, _ in requests] == ["/responses", "/responses", "/chat/completions"]
    assert "补充检索" in requests[1][1]["input"]


def test_manual_exclusions_match_url_normalized_title_and_event_key(monkeypatch):
    urls = ("same-url", "same-event", "same-title", "fresh-1", "fresh-2", "fresh-3")
    requests = install_responses(
        monkeypatch,
        research_response(*(news_source(slug) for slug in urls)),
        {
            "cross": [
                entry("S2", "OLD-EVENT"),
                entry("S3", "changed-key", zh={**FIELDS, "title": "Ｓａｍｅ！  story"}),
                entry("S4", "first"),
                entry("S5", "second"),
                entry("S6", "third"),
            ],
            "focus": [],
        },
    )
    previous = {
        "event_key": "old-event",
        "source_url": "HTTPS://NEWS.EXAMPLE:443/same-url#details",
        "zh": {"title": "Same story"},
    }

    report = news_editor.generate_digest([], [], now=NOW, exclude_articles=[previous])

    payload = json.loads(requests[-1][1]["messages"][1]["content"])
    assert all(not row["url"].endswith("/same-url") for row in payload["sources"].values())
    assert payload["excluded_articles"][0]["title_zh"] == "Same story"
    assert [article["source_url"].rsplit("/", 1)[-1] for article in report["cross"]] == list(
        urls[3:]
    )


def test_missing_key_and_http_errors_are_explicit(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(news_editor.EditorError, match="DEEPSEEK_API_KEY"):
        news_editor.generate_digest([], [], now=NOW)

    def overloaded(request, timeout):
        raise HTTPError(request.full_url, 429, "Rate limited", None, None)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(news_editor, "urlopen", overloaded)
    with pytest.raises(news_editor.EditorError, match="HTTP 429"):
        news_editor.synthesize("Good morning")


def test_new_copy_stores_three_sections_and_focus_reuses_cross_entries(monkeypatch):
    primary = {**FIELDS, "summary": ["动作。", "规模。", "多余评价。"]}
    draft = {
        "cross": [entry("S1", "first", zh=primary), entry("S2", "second"), entry("S3", "third")],
        "focus": ["S1", "S2", "S3"],
    }
    install_responses(monkeypatch, research_response(*(news_source(str(i)) for i in range(3))), draft)
    digest = news_editor.generate_digest([news_editor.CATEGORIES[0]], [], now=NOW)
    assert digest["cross"] == digest["focus"]
    copy = digest["cross"][0]["zh"]
    assert set(copy) == {"title", "summary", "details", "analysis"}
    assert copy["summary"] == "动作。规模。"
    assert news_editor.reading_sections(copy) == (FIELDS["details"], FIELDS["analysis"])
    historical = {"summary": "headline facts", "background": "old body", "why_it_matters": "old impact"}
    assert news_editor.reading_sections(historical) == ("old body", "old impact")
    assert news_editor._summary("U.S. sales rose 2.5%. Costs fell. Further comment.") == (
        "U.S. sales rose 2.5%. Costs fell."
    )


def test_generation_requires_original_body_or_attributed_notes_from_opened_page(monkeypatch):
    slugs = ("headline-only", "read", "unopened", "original", "another", "lede-only")
    research = research_response(*(news_source(slug) for slug in slugs))
    url = "https://news.example/read"
    sentence = "班次从八班增至十班，高峰期的间隔缩短至二十分钟。"
    note = sentence + "\nOther story https://news.example/unopened"
    research["output"][1]["content"] = [{
        "type": "output_text", "text": note,
        "annotations": [{"type": "url_citation", "url": url, "start_index": 0, "end_index": len(sentence)}],
    }]
    research["output"].append({
        "type": "web_search_call", "status": "completed", "action": {"type": "open_page"},
        "page": {"url": url},
    })
    originals = {"original", "another"}
    def local_page(url):
        slug = url.rsplit("/", 1)[-1]
        body = "Original publisher reporting." if slug in originals else ""
        if slug == "lede-only":
            body = news_source(slug)["snippet"]
        return news_editor.publisher_time.ArticlePage(NOW, body)
    monkeypatch.setattr(news_editor.publisher_time, "read_article", local_page)
    draft = {"cross": [entry("S1", "read"), entry("S4", "original"), entry("S5", "another")]}
    requests = install_responses(monkeypatch, research, draft, verify_publishers=True)
    report = news_editor.generate_digest([], [], now=NOW)
    assert [row["event_key"] for row in report["cross"]] == ["read", "original", "another"]
    supplied = json.loads(requests[1][1]["messages"][1]["content"])
    assert supplied["research_notes"] == {"S1": sentence}
    assert {r["url"].rsplit("/", 1)[-1] for r in supplied["sources"].values()} == originals | {"read"}


def test_truncated_draft_retries_same_evidence_with_larger_budget(monkeypatch):
    requests = []
    def post(path, payload, **kwargs):
        requests.append(payload)
        finish, content = ("length", "{") if len(requests) == 1 else ("stop", '{"cross":[]}')
        return json.dumps({"choices": [{"finish_reason": finish, "message": {"content": content}}]}).encode()
    monkeypatch.setattr(news_editor, "_post", post)
    assert news_editor._draft({}, {}, [], [], [], "cross") == {"cross": []}
    assert [payload["max_tokens"] for payload in requests] == [16384, 32768]
    assert requests[0]["messages"] == requests[1]["messages"]
    assert news_editor._language_fields({**FIELDS, "details": FIELDS["summary"]}) is None


def test_selected_provider_routes_chat_and_keeps_keys_separate(monkeypatch):
    requests = []

    def reply(request, timeout):
        body = json.loads(request.data)
        requests.append((request.full_url, request.get_header("Authorization"), body))
        if "messages" in body:
            return io.BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}')
        return io.BytesIO(b"research-or-speech")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-deepseek")
    monkeypatch.setattr(news_editor, "urlopen", reply)
    with news_editor.use_model("qwen-plus", "personal-qwen", deepseek_key="personal-research"):
        news_editor._draft({}, "", [], [], [], "cross")
        news_editor.check_model_connection("deepseek-v4-pro", "personal-deepseek")
        news_editor._post("/v1/tools/tts", {"text": "Hello"}, operation="朗读", timeout=5)
        news_editor._post("/responses", {"model": news_editor.DEFAULT_MODEL}, operation="搜索", timeout=5)
        news_editor._draft({}, "", [], [], [], "cross")
    news_editor._draft({}, "", [], [], [], "cross")

    assert requests[0][0:2] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "Bearer personal-qwen"
    )
    assert requests[0][2]["model"] == "qwen-plus" and not requests[0][2]["enable_thinking"]
    assert requests[1][0:2] == ("https://api.deepseek.com/chat/completions", "Bearer personal-deepseek")
    assert requests[1][2]["model"] == "deepseek-v4-pro" and requests[1][2]["max_tokens"] == 16
    assert requests[2][0:2] == ("https://api.deepseek.com/v1/tools/tts", "Bearer personal-research")
    assert requests[3][0:2] == ("https://api.deepseek.com/responses", "Bearer personal-research")
    assert requests[4][1] == "Bearer personal-qwen"
    assert requests[5][1] == "Bearer server-deepseek"
    assert not news_editor.same_model_provider("qwen-plus", "deepseek-flash")


@pytest.fixture
def wav_audio():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 24_000, 128, "NONE", "not compressed"))
        wav.writeframes(bytes(256))
    return buffer.getvalue()


def test_tts_returns_audio_and_rejects_truncated_content(monkeypatch, wav_audio):

    def post(path, payload, **kwargs):
        assert path == "/v1/tools/tts"
        assert payload["text"] == "Good morning"
        return wav_audio

    monkeypatch.setattr(news_editor, "_post", post)
    assert news_editor.synthesize(" Good morning ") == wav_audio

    monkeypatch.setattr(news_editor, "_post", lambda *args, **kwargs: wav_audio[:-1])
    with pytest.raises(news_editor.EditorError, match="音频不完整"):
        news_editor.synthesize("Good morning")

    with pytest.raises(ValueError, match="不能为空"):
        news_editor.synthesize(" ")


def test_tts_splits_overlong_article_and_joins_only_complete_audio(monkeypatch, wav_audio):
    requests = []
    def post(path, payload, **kwargs):
        requests.append(payload["text"])
        return wav_audio
    monkeypatch.setattr(news_editor, "_post", post)
    first, second = "甲" * 4000 + "。", "乙" * 1500 + "。"
    output = news_editor.synthesize(first + second)
    assert requests == [first, second]
    with wave.open(io.BytesIO(output)) as audio:
        assert audio.getnframes() == 256
        assert audio.readframes(256) == bytes(512)
    assert news_editor._speech_chunks("a" * 20001) == ["a" * 10000, "a" * 10000, "a"]
    pieces = iter([wav_audio, wav_audio[:-1]])
    monkeypatch.setattr(news_editor, "_post", lambda *a, **k: next(pieces))
    with pytest.raises(news_editor.EditorError, match="音频不完整"):
        news_editor.synthesize(first + second)


def test_tts_rejects_riff_without_audio_chunks(monkeypatch):
    body = b"WAVEJUNK" + (32).to_bytes(4, "little") + bytes(32)
    response = b"RIFF" + len(body).to_bytes(4, "little") + body
    monkeypatch.setattr(news_editor, "_post", lambda *args, **kwargs: response)
    with pytest.raises(news_editor.EditorError, match="WAV"):
        news_editor.synthesize("Good morning")


@pytest.mark.parametrize(("alignment", "bits"), [(2, 0), (1, 16)])
def test_tts_rejects_inconsistent_pcm_header(monkeypatch, wav_audio, alignment, bits):
    response = bytearray(wav_audio)
    response[32:36] = alignment.to_bytes(2, "little") + bits.to_bytes(2, "little")
    monkeypatch.setattr(news_editor, "_post", lambda *args, **kwargs: bytes(response))
    with pytest.raises(news_editor.EditorError, match="有效的 WAV"):
        news_editor.synthesize("Good morning")


@pytest.mark.parametrize(
    ("riff_size", "data_size", "payload_size"),
    [(40, 512, 4), (0xFFFFFFFF, 8, 6), (0xFFFFFFFF, 0xFFFFFFFF, 3)],
)
def test_tts_rejects_short_data_or_partial_pcm_frame(
    monkeypatch, wav_audio, riff_size, data_size, payload_size
):
    response = (
        b"RIFF"
        + riff_size.to_bytes(4, "little")
        + wav_audio[8:40]
        + data_size.to_bytes(4, "little")
        + bytes(payload_size)
    )
    monkeypatch.setattr(news_editor, "_post", lambda *args, **kwargs: response)
    with pytest.raises(news_editor.EditorError, match="音频不完整"):
        news_editor.synthesize("Good morning")


@pytest.mark.parametrize("placeholder", [0, 0xFFFFFFFF])
def test_tts_accepts_complete_frames_with_streaming_sizes(monkeypatch, wav_audio, placeholder):
    size = placeholder.to_bytes(4, "little")
    response = b"RIFF" + size + wav_audio[8:40] + size + wav_audio[44:]
    monkeypatch.setattr(news_editor, "_post", lambda *args, **kwargs: response)
    assert news_editor.synthesize("Good morning") == response

def test_empty_personal_keys_cannot_use_shared_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "shared-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "shared-qwen-key")
    monkeypatch.setattr(news_editor, "urlopen", lambda *a, **k: pytest.fail("unexpected network request"))
    for model in news_editor.MODEL_OPTIONS:
        with pytest.raises(news_editor.EditorError, match="个人 API 密钥"):
            news_editor.check_model_connection(model, "")
    with news_editor.use_model("qwen-plus","only-qwen"):
        with pytest.raises(news_editor.EditorError, match="个人 DeepSeek"):
            news_editor.synthesize("test")
        with pytest.raises(news_editor.EditorError, match="个人 DeepSeek"):
            news_editor._post("/responses", {}, operation="搜索", timeout=5)
