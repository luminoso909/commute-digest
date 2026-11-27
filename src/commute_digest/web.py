"""Chronological news reader with issue and article permalinks."""

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
ENGLISH_CATEGORIES = {
    "科技与 AI": "Technology & AI", "商业与产业": "Business & Industry",
    "科学与环境": "Science & Environment", "社会与公共生活": "Society & Public Life",
    "财经与市场": "Finance & Markets", "国际事务": "World Affairs",
    "教育与研究": "Education & Research", "健康与医疗": "Health & Medicine",
    "能源与交通": "Energy & Transport", "文化与艺术": "Culture & Arts", "体育": "Sports",
}


def _navigate(query: dict[str, str]) -> None:
    st.query_params.from_dict(query)


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
    number = f"第 {issue['id']} 期" if language == "zh" else f"Issue {issue['id']}"
    return (
        f'<div class="digest-meta"><span class="digest-topic">{escape(category)}</span>'
        f'<span>{number}</span><span>{escape(_date_label(issue, language))}</span></div>'
    )


def _audio_button(
    copy: dict, language: str, account: accounts.Account | None, key: str,
) -> bytes | None:
    label = "生成朗读" if language == "zh" else "Generate audio"
    if account is None:
        st.button(label, key=key, icon=":material/headphones:", on_click=web_settings.open_settings,
                  args=("登录信息",))
        return None
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


def _article_detail(
    article: dict, issue: edition_service.Issue, account: accounts.Account | None,
    mode: str, lang: str, index: int,
) -> None:
    z = lang == "zh"
    return_query = {"issue": str(issue["id"]), "mode": mode, "lang": lang}
    if st.query_params.get("from") == "archive":
        return_query.pop("issue")
        if page := st.query_params.get("page"):
            return_query["page"] = page
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
        st.audio(audio, format="audio/wav")


def _issue_rows(
    issue: edition_service.Issue, account: accounts.Account | None, mode: str, lang: str,
    *, latest: bool, archive_page: int | None,
) -> None:
    if not issue[mode]:
        return
    group = "latest" if latest else "past"
    with st.container(key=f"batch_{group}_{issue['id']}"):
        for index, article in enumerate(issue[mode], 1):
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
                    if archive_page is not None:
                        detail.update({"from": "archive", "page": str(archive_page)})
                    st.button("详细浏览" if lang == "zh" else "Read story", key=f"detail_{key}",
                              icon=":material/arrow_outward:", on_click=_navigate, args=(detail,))
                    audio = _audio_button(copy, lang, account, f"audio_{key}")
                if audio:
                    st.audio(audio, format="audio/wav")


def _reader(account: accounts.Account | None, mode: str, lang: str) -> None:
    account_id = account.id if account is not None else None
    z = lang == "zh"
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
    query = {"mode": mode, "lang": lang}
    if issue_id is not None:
        issue = edition_service.get_issue(issue_id, account_id=account_id)
        if issue is None:
            st.warning("未找到这期晨报。")
            if account is None:
                st.button("登录查看个人日报" if z else "Sign in for private editions",
                          on_click=web_settings.open_settings, args=("登录信息",))
            return
        if article_id is not None:
            if article_id > len(issue[mode]):
                st.warning("报道链接无效。" if z else "Invalid story link.")
            else:
                _article_detail(issue[mode][article_id - 1], issue, account, mode, lang, article_id)
            return
        st.button("全部报道" if z else "All stories", icon=":material/arrow_back:",
                  on_click=_navigate, args=(query,))
        newest = edition_service.list_issues(account_id=account_id, mode=mode, limit=1)
        _issue_rows(issue, account, mode, lang, latest=bool(newest and newest[0]["id"] == issue_id),
                    archive_page=None)
        if not issue[mode]:
            st.info("本期这个版式没有新闻。" if z else "No stories in this edition.")
        return

    issues = edition_service.list_issues(account_id=account_id, mode=mode, offset=(page - 1) * PAGE_SIZE,
                                         limit=PAGE_SIZE + 1)
    if not any(item[mode] for item in issues[:PAGE_SIZE]):
        st.info("暂无可阅读的报道。" if z else "No stories to display.")
    for pos, issue in enumerate(issues[:PAGE_SIZE]):
        _issue_rows(issue, account, mode, lang, latest=(page == 1 and pos == 0), archive_page=page)
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
        st.markdown('<header class="digest-masthead"><h1>通勤日报</h1></header>', unsafe_allow_html=True)
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
