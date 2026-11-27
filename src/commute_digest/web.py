"""Chronological news reader with issue and article permalinks."""

from base64 import b64encode
from datetime import datetime
from hashlib import sha256
from html import escape
from urllib.parse import urlsplit

import streamlit as st

from commute_digest import accounts, edition_service, news_editor, web_settings
from commute_digest.schedule import BEIJING
from commute_digest.web_style import apply_style

PAGE_SIZE = 8
MAX_AUDIO_FILES = 10
ARCHIVE_PARAMS = ("q", "order", "period", "edition", "topic")
ENGLISH_CATEGORIES = {
    "科技与 AI": "Technology & AI", "商业与产业": "Business & Industry",
    "科学与环境": "Science & Environment", "社会与公共生活": "Society & Public Life",
    "财经与市场": "Finance & Markets", "国际事务": "World Affairs",
    "教育与研究": "Education & Research", "健康与医疗": "Health & Medicine",
    "能源与交通": "Energy & Transport", "文化与艺术": "Culture & Arts", "体育": "Sports",
}


def _navigate(query: dict[str, str]) -> None:
    st.query_params.from_dict(query)


def _change_archive_filter(name: str, default: str, query: dict[str, str]) -> None:
    updated = query.copy()
    value = st.session_state[f"archive_{name}"].strip()
    if value == default:
        updated.pop(name, None)
    else:
        updated[name] = value
    _navigate(updated)


def _archive_controls(
    account_id: int, mode: str, lang: str, pinned_issue: edition_service.Issue | None,
) -> dict[str, str]:
    """Keep search and filters in the URL so browser refresh and story links retain the list."""
    z = lang == "zh"
    numbers = edition_service.archive_issue_numbers(account_id=account_id, mode=mode)
    pinned = pinned_issue["id"] if pinned_issue else None
    if pinned_issue:
        numbers.setdefault(pinned_issue["id"], pinned_issue["issue_number"])
    all_label = "全部" if z else "All"
    issue_labels = {"": all_label}
    for global_id, number in numbers.items():
        issue_labels[str(global_id)] = f"第 {number} 期" if z else f"Issue {number}"
    controls = (
        ("order", "时间排序" if z else "Sort by time", {
            "desc": "最新在前" if z else "Newest first", "asc": "最早在前" if z else "Oldest first"}),
        ("period", "期数" if z else "Issue", issue_labels),
        ("edition", "晨报 / 晚报" if z else "Edition", {
            "": all_label, "morning": "晨报" if z else "Morning", "evening": "晚报" if z else "Evening"}),
        ("topic", "领域" if z else "Topic", {
            "": all_label, **{c: c if z else ENGLISH_CATEGORIES.get(c, c) for c in news_editor.CATEGORIES}}),
    )
    query = {"mode": mode, "lang": lang}
    needle = st.query_params.get("q", "").strip()
    if needle:
        query["q"] = needle
    for name, _, labels in controls:
        default = next(iter(labels))
        value = str(pinned) if name == "period" and pinned else st.query_params.get(name, default)
        if value in labels and value != default:
            query[name] = value

    with st.container(key="archive_toolbar"):
        caption, search = st.columns([1, 1.35], vertical_alignment="center")
        caption.markdown("## " + (("本期报道" if z else "This issue") if pinned else
                                  ("全部报道" if z else "All stories")))
        label = "搜索新闻标题" if z else "Search headlines"
        st.session_state["archive_q"] = needle
        search.text_input(label, key="archive_q", placeholder=label, label_visibility="collapsed",
                          icon=":material/search:", on_change=_change_archive_filter, args=("q", "", query))

    with st.container(key="archive_filters"):
        columns = st.columns(4, gap="small")
        for column, (name, label, labels) in zip(columns, controls):
            default = next(iter(labels))
            key = f"archive_{name}"
            st.session_state[key] = query.get(name, default)
            column.selectbox(label, list(labels), key=key, format_func=labels.__getitem__,
                             on_change=_change_archive_filter, args=(name, default, query))
    if any(name in query for name in ARCHIVE_PARAMS) or st.query_params.get("period"):
        st.button(("查看全部报道" if pinned else "重置筛选") if z else
                  ("View all stories" if pinned else "Reset filters"), key="archive_reset",
                  on_click=_navigate, args=({"mode": mode, "lang": lang},))
    return query


def _positive_number(value: str | None, maximum: int = edition_service.MAX_ISSUE_ID) -> int | None:
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or len(value) > 19:
        raise ValueError
    number = int(value)
    if not 1 <= number <= maximum:
        raise ValueError
    return number


def _date_label(issue: edition_service.Issue, language: str) -> str:
    value = issue.get("slot") or issue["created_at"]
    when = datetime.fromisoformat(value)
    if when.tzinfo is not None:
        when = when.astimezone(BEIJING)
    if language == "en":
        return f"{when:%Y-%m-%d} · {'Evening' if issue['edition'] == 'evening' else 'Morning'}"
    return f"{when:%Y-%m-%d} · {'晚报' if issue['edition'] == 'evening' else '晨报'}"


def _metadata(article: dict, issue: edition_service.Issue, language: str) -> str:
    category = article["category"]
    if language == "en":
        category = ENGLISH_CATEGORIES.get(category, category)
    number = f"第 {issue['issue_number']} 期" if language == "zh" else f"Issue {issue['issue_number']}"
    return (
        f'<div class="digest-meta"><span class="digest-topic">{escape(category)}</span>'
        f'<span>{number}</span><span>{escape(_date_label(issue, language))}</span></div>'
    )


def _audio_button(
    copy: dict, language: str, account: accounts.Account, key: str,
) -> bytes | None:
    label = "生成朗读" if language == "zh" else "Generate audio"
    details, analysis = news_editor.reading_sections(copy)
    script = "\n\n".join(part for part in (copy["title"], details, analysis) if part)
    audio_key = sha256(f"{account.id}\n{language}\n{script}".encode()).hexdigest()
    cache = st.session_state.setdefault("article_audio", {})
    if audio_key in cache:
        return cache[audio_key]
    if st.button(label, key=key, icon=":material/headphones:"):
        try:
            with st.spinner("正在合成朗读…" if language == "zh" else "Generating audio…"):
                with accounts.model_context(account):
                    audio = news_editor.synthesize(script)
        except news_editor.EditorError as error:
            st.error(str(error))
        else:
            if len(cache) >= MAX_AUDIO_FILES:
                del cache[next(iter(cache))]
            cache[audio_key] = audio
            return audio
    return None


def _private_audio_player(audio: bytes) -> None:
    # Deliver the recording inside the signed-in page, without a shared Streamlit media URL.
    st.audio("data:audio/wav;base64," + b64encode(audio).decode("ascii"), format="audio/wav")


def _article_detail(
    article: dict, issue: edition_service.Issue, account: accounts.Account,
    mode: str, lang: str, index: int,
) -> None:
    z = lang == "zh"
    return_query = {"issue": str(issue["id"]), "mode": mode, "lang": lang}
    if st.query_params.get("from") == "archive":
        return_query.pop("issue")
        for name in (*ARCHIVE_PARAMS, "page"):
            if value := st.query_params.get(name):
                return_query[name] = value
    st.button("返回列表" if z else "Back to list", key="back_to_list", icon=":material/arrow_back:",
              on_click=_navigate, args=(return_query,))
    copy = article[lang]
    details, analysis = news_editor.reading_sections(copy)
    sections = []
    for title, body in (("详细内容" if z else "Details", details), ("解读和分析" if z else "Analysis", analysis)):
        sections.append(f"<h3>{title}</h3>")
        for paragraph in body.split("\n\n"):
            if paragraph.strip():
                sections.append(f"<p>{escape(paragraph.strip())}</p>")
    url = article.get("source_url", "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme in ("http", "https") and parsed.hostname:
        source = article.get("source_name") or parsed.hostname
        caption = "查看原文" if z else "Read source"
        sections.append(f'<a class="digest-source" href="{escape(url, quote=True)}" '
                        f'target="_blank" rel="noopener noreferrer">{caption} · {escape(source)}</a>')
    st.markdown(
        f'<article class="digest-detail"><h2>{escape(copy["title"])}</h2>'
        f'{_metadata(article, issue, lang)}{"".join(sections)}</article>',
        unsafe_allow_html=True,
    )
    if audio := _audio_button(copy, lang, account, f"audio_detail_{issue['id']}_{mode}_{index}"):
        _private_audio_player(audio)


def _issue_rows(
    issue: edition_service.Issue, account: accounts.Account, mode: str, lang: str,
    *, indices: list[int], latest: bool, archive_query: dict[str, str] | None,
) -> None:
    group = "latest" if latest else "past"
    with st.container(key=f"batch_{group}_{issue['id']}"):
        for index in indices:
            article = issue[mode][index - 1]
            key = f"{issue['id']}_{mode}_{index}"
            copy = article[lang]
            with st.container(key=f"story_{key}"):
                text, actions = st.columns([3.3, 1.8], gap="small", vertical_alignment="center")
                text.markdown(
                    f'<div class="digest-story-title" role="heading" aria-level="2">{escape(copy["title"])}</div>'
                    + _metadata(article, issue, lang), unsafe_allow_html=True,
                )
                with actions.container(horizontal=True, horizontal_alignment="right", gap="xsmall"):
                    detail = {"issue": str(issue["id"]), "article": str(index), "mode": mode, "lang": lang}
                    if archive_query is not None:
                        detail.update(archive_query)
                        detail["from"] = "archive"
                    st.button("详细浏览" if lang == "zh" else "Read story", key=f"detail_{key}",
                              icon=":material/arrow_outward:", on_click=_navigate, args=(detail,))
                    audio = _audio_button(copy, lang, account, f"audio_{key}")
                if audio:
                    _private_audio_player(audio)


def _reader(account: accounts.Account | None, mode: str, lang: str) -> None:
    z = lang == "zh"
    if account is None:
        st.subheader("我的日报" if z else "My digest")
        st.write("登录后即可阅读你的日报和往期报道。" if z else "Sign in to read your editions and past stories.")
        st.button("登录 / 注册" if z else "Sign in / Register", type="primary",
                  on_click=web_settings.open_settings, args=("登录信息",))
        return
    account_id = account.id
    try:
        issue_id = _positive_number(st.query_params.get("issue"))
    except ValueError:
        st.warning("晨报链接中的期号无效。")
        return
    try:
        article_id = _positive_number(st.query_params.get("article"))
        page = _positive_number(st.query_params.get("page"), 1_000_000) or 1
    except ValueError:
        st.warning("报道链接无效。" if z else "Invalid story link.")
        return
    if article_id is not None and issue_id is None:
        st.warning("报道链接无效。" if z else "Invalid story link.")
        return
    issue = None
    if issue_id is not None:
        issue = edition_service.get_issue(issue_id, account_id=account_id)
        if issue is None:
            st.warning("你的日报中没有这一期。" if z else "This issue is unavailable in your archive.")
            st.button("返回我的日报" if z else "Back to my digest", on_click=_navigate,
                      args=({"mode": mode, "lang": lang},))
            return
        if article_id is not None:
            if article_id > len(issue[mode]):
                st.warning("报道链接无效。" if z else "Invalid story link.")
            else:
                _article_detail(issue[mode][article_id - 1], issue, account, mode, lang, article_id)
            return
    query = _archive_controls(account_id, mode, lang, issue)
    if issue_id is None and st.query_params.get("period") and "period" not in query:
        st.info("当前版式下无法查看所选期次。" if z else "The selected issue is unavailable in this edition.")
        return
    issues = edition_service.search_archive(
        account_id=account_id, mode=mode, language=lang, keyword=query.get("q", ""),
        issue_id=int(query["period"]) if "period" in query else None,
        edition=query.get("edition"), category=query.get("topic"), order=query.get("order", "desc"),
        offset=(page - 1) * PAGE_SIZE, limit=PAGE_SIZE + 1,
    )
    if not issues:
        if page == 1 and not any(name in query for name in ARCHIVE_PARAMS):
            st.info("当前阅读版式还没有报道。你可以设置关注领域，再点击侧栏的「日报发布」。" if z else
                    "No stories in this edition yet. Choose topics in Settings, then publish a digest from the sidebar.")
        else:
            st.info("没有符合条件的报道。" if z else "No matching stories.")
    # A plain issue link returns to that issue. A refined list restores its filters instead.
    filtered_pin = any(name in query for name in ("q", "topic", "order", "edition"))
    archive_query = {**query, "page": str(page)} if issue_id is None or filtered_pin else None
    for result in issues[:PAGE_SIZE]:
        _issue_rows(result["issue"], account, mode, lang, indices=result["article_indices"],
                    latest=result["is_latest"], archive_query=archive_query)
    if page != 1 or len(issues) > PAGE_SIZE:
        with st.container(horizontal=True, horizontal_alignment="right"):
            if page > 1:
                st.button("上一页" if z else "Previous", on_click=_navigate, args=({**query, "page": str(page - 1)},))
            if len(issues) > PAGE_SIZE:
                st.button("下一页" if z else "Next", on_click=_navigate, args=({**query, "page": str(page + 1)},))


def main() -> None:
    st.set_page_config(page_title="通勤日报", layout="wide", initial_sidebar_state="auto")
    try:
        account = web_settings.current_account()
        apply_style(*web_settings.appearance(account))
        mode, lang = web_settings.reading(account)
        web_settings.sidebar(account, mode, lang)
        _reader(account, mode, lang)
        if st.session_state.get("settings_open"):
            web_settings.settings_dialog(account, mode, lang)
    except edition_service.MorningError as error:
        st.error(str(error))


if __name__ == "__main__":
    main()
else:
    # `streamlit run` discovers this ASGI app and executes this same reader as __main__.
    from pathlib import Path
    from commute_digest.login_sessions import ROUTES

    app = st.App(Path(__file__).resolve(), routes=ROUTES)
