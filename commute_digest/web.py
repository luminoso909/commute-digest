"""Streamlit reader and subscription settings for the two daily editions."""

import re
from datetime import datetime
from hashlib import sha256
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import streamlit as st

from commute_digest import edition_service, news_editor

MODE_LABELS = {"cross": "跨领域版", "focus": "我的关注"}
LANGUAGE_LABELS = {"zh": "中文", "en": "English"}
ENGLISH_CATEGORIES = {
    "科技与 AI": "Technology & AI",
    "商业与产业": "Business & Industry",
    "科学与环境": "Science & Environment",
    "社会与公共生活": "Society & Public Life",
}
BEIJING = ZoneInfo("Asia/Shanghai")
MAX_AUDIO_FILES = 10

STYLE = """
<style>
.stApp { background: #f7f7f0; }
[data-testid="stHeader"] { background: #f7f7f0; }
[data-testid="stSidebar"] { background: #edf2eb; }
.stMainBlockContainer { max-width: 54rem; padding-top: 2.25rem; }
.digest-masthead { border-bottom: solid 2px #213c3b; padding-bottom: 1rem; }
.digest-masthead h1 {
    color: #193532; font-family: Georgia, "Noto Serif CJK SC", "Songti SC", serif;
    font-size: clamp(2.6rem, 8vw, 4.65rem); font-weight: 700; letter-spacing: -0.045em;
    line-height: 1.08; margin: 0; padding: 0;
}
.digest-dateline {
    color: #46645b; font-size: 0.86rem; letter-spacing: 0.035em;
    margin-top: 0.7rem;
}
.digest-meta {
    color: #49675e; font-size: 0.85rem; line-height: 1.5;
    overflow-wrap: anywhere; margin: 0 0 0.45rem;
}
.digest-title {
    color: #193532; font-family: Georgia, "Noto Serif CJK SC", "Songti SC", serif;
    font-size: clamp(1.35rem, 4.8vw, 1.8rem); font-weight: 700;
    line-height: 1.3; margin: 0; padding: 0; overflow-wrap: anywhere;
}
.digest-section-heading { color: #193532; font-weight: 700; margin: 0.1rem 0 0.25rem; }
.digest-paragraph {
    color: #283c37; line-height: 1.68; white-space: pre-wrap; overflow-wrap: anywhere;
}
@media (max-width: 640px) {
    .stMainBlockContainer { padding-top: 1.25rem; }
    .digest-masthead { padding-bottom: 0.7rem; }
}
</style>
"""


def _display_date(value: str, language: str, *, with_time: bool = False) -> str:
    if not value:
        return ""
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return value
    if instant.tzinfo is not None:
        instant = instant.astimezone(BEIJING)
    date = (
        f"{instant:%d %b %Y}"
        if language == "en"
        else f"{instant.year} 年 {instant.month} 月 {instant.day} 日"
    )
    return f"{date} {instant:%H:%M}" if with_time else date


def _publish_sidebar(preferences: dict) -> dict | None:
    with st.sidebar:
        st.header("订阅设置")
        with st.form("delivery_preferences"):
            topics = st.multiselect(
                "关注领域",
                news_editor.CATEGORIES,
                default=[topic for topic in preferences["topics"] if topic in news_editor.CATEGORIES],
                help="我的关注版优先采用这些领域与下方的关键词。",
            )
            words = st.text_area(
                "关键词",
                value="、".join(preferences["keywords"]),
                placeholder="例如：大模型、芯片、气候",
                help="可以用逗号、顿号或换行分隔。",
            )
            delivery_mode = st.radio(
                "邮件版式",
                tuple(MODE_LABELS),
                index=tuple(MODE_LABELS).index(preferences["delivery_mode"]),
                format_func=MODE_LABELS.__getitem__,
            )
            delivery_language = st.radio(
                "邮件语言",
                tuple(LANGUAGE_LABELS),
                index=tuple(LANGUAGE_LABELS).index(preferences["delivery_language"]),
                format_func=LANGUAGE_LABELS.__getitem__,
                horizontal=True,
            )
            saved = st.form_submit_button("保存订阅设置")
        if saved:
            keywords = list(
                dict.fromkeys(
                    word.strip() for word in re.split(r"[,，、\n]+", words) if word.strip()
                )
            )
            try:
                edition_service.save_preferences(topics, keywords, delivery_mode, delivery_language)
            except edition_service.MorningError as error:
                st.error(str(error))
            else:
                st.success("订阅设置已保存。")

        st.divider()
        email = edition_service.recipient_email()
        if email:
            st.caption(f"收件人 · {email}")
        else:
            st.caption("收件邮箱待配置。")
        st.caption("每天北京时间 05:00 生成通勤晨报，17:00 生成通勤晚报并投递。")
        if st.button("生成并发布", type="primary"):
            try:
                with st.spinner("正在采集新闻、整理并发布本期…"):
                    issue = edition_service.publish()
            except edition_service.MorningError as error:
                st.error(str(error))
            else:
                st.success(f"第 {issue['id']} 期已发布。")
                return issue
    return None


def _update_view_query() -> None:
    st.query_params["mode"] = st.session_state["reader_mode"]
    st.query_params["lang"] = st.session_state["reader_language"]


def _clear_issue_query() -> None:
    if "issue" in st.query_params:
        del st.query_params["issue"]


def _body_section(title: str, body: str) -> None:
    st.markdown(
        f'<div class="digest-section-heading">{escape(title)}</div>'
        f'<div class="digest-paragraph">{escape(body)}</div>',
        unsafe_allow_html=True,
    )


def _valid_link(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _article_audio(copy: dict, language: str, widget_key: str) -> None:
    script = "\n\n".join(copy[key] for key in ("title", "summary", "background", "why_it_matters"))
    audio_key = sha256((language + "\n" + script).encode()).hexdigest()
    cache = st.session_state.setdefault("article_audio", {})
    label = "生成朗读" if language == "zh" else "Generate audio"
    if audio_key not in cache and st.button(label, key=widget_key):
        try:
            with st.spinner("正在合成朗读…" if language == "zh" else "Generating audio…"):
                audio = news_editor.synthesize(script)
        except news_editor.EditorError:
            st.error(
                "朗读暂时不可用，可以稍后重试。"
                if language == "zh"
                else "Audio unavailable. Try again."
            )
        else:
            if len(cache) >= MAX_AUDIO_FILES:
                del cache[next(iter(cache))]
            cache[audio_key] = audio
    if audio_key in cache:
        st.audio(cache[audio_key], format="audio/wav")


def _article_card(article: dict, language: str, widget_key: str) -> None:
    copy = article[language]
    chinese = language == "zh"
    category = article["category"]
    if not chinese:
        category = ENGLISH_CATEGORIES.get(category, category)
    date = _display_date(article["published_at"], language)
    metadata_parts = [category, article["source_name"], date]
    if article.get("supplemental"):
        metadata_parts.append("跨领域补充" if chinese else "From across topics")
    metadata = " · ".join(part for part in metadata_parts if part)
    with st.container(border=True):
        st.markdown(
            f'<div class="digest-meta">{escape(metadata)}</div>'
            f'<h2 class="digest-title">{escape(copy["title"])}</h2>',
            unsafe_allow_html=True,
        )
        with st.expander("摘要、背景与影响" if chinese else "Summary, context and impact"):
            _body_section("摘要" if chinese else "Summary", copy["summary"])
            _body_section("背景" if chinese else "Background", copy["background"])
            _body_section(
                "AI 解读 · 为什么重要" if chinese else "AI analysis · Why it matters",
                copy["why_it_matters"],
            )
            perspective = article.get("perspective")
            if perspective and perspective.get(language):
                heading = "外部观点" if chinese else "Outside perspective"
                _body_section(f"{heading} · {perspective['author']}", perspective[language])
                if _valid_link(perspective["source_url"]):
                    st.link_button(
                        "查看观点来源" if chinese else "View perspective source",
                        perspective["source_url"],
                    )
        if _valid_link(article["source_url"]):
            st.link_button(
                "查看新闻原文" if chinese else "Open source article", article["source_url"]
            )
        _article_audio(copy, language, widget_key)


def main() -> None:
    st.set_page_config(page_title="通勤晨报与晚报", layout="wide", initial_sidebar_state="auto")
    st.markdown(STYLE, unsafe_allow_html=True)

    try:
        preferences = edition_service.get_preferences()
        just_published = _publish_sidebar(preferences)
    except edition_service.MorningError as error:
        st.title("通勤晨报")
        st.error(str(error))
        return

    if just_published is not None:
        st.query_params["issue"] = str(just_published["id"])

    issue_arg = st.query_params.get("issue")
    mode_arg = st.query_params.get("mode")
    lang_arg = st.query_params.get("lang")
    default_mode = mode_arg if mode_arg in MODE_LABELS else preferences["delivery_mode"]
    default_lang = lang_arg if lang_arg in LANGUAGE_LABELS else preferences["delivery_language"]

    masthead = st.empty()
    view_column, language_column = st.columns([3, 2])
    with view_column:
        mode = st.radio(
            "阅读版式",
            tuple(MODE_LABELS),
            index=tuple(MODE_LABELS).index(default_mode),
            format_func=MODE_LABELS.__getitem__,
            horizontal=True,
            key="reader_mode",
            on_change=_update_view_query,
        )
    with language_column:
        language = st.radio(
            "阅读语言",
            tuple(LANGUAGE_LABELS),
            index=tuple(LANGUAGE_LABELS).index(default_lang),
            format_func=LANGUAGE_LABELS.__getitem__,
            horizontal=True,
            key="reader_language",
            on_change=_update_view_query,
        )

    issue = just_published
    invalid_issue = False
    load_error = ""
    if issue is None:
        try:
            issue_id = int(issue_arg) if issue_arg is not None else None
            if issue_id is not None and not 1 <= issue_id <= edition_service.MAX_ISSUE_ID:
                raise ValueError("An issue number is outside the database ID range.")
        except ValueError:
            invalid_issue = True
        else:
            try:
                issue = edition_service.get_issue(issue_id)
            except edition_service.MorningError as error:
                load_error = str(error)

    title = "通勤晨报" if language == "zh" else "Commute Digest"
    dateline = ""
    if issue is not None:
        if issue.get("edition") == "evening":
            title = "通勤晚报" if language == "zh" else "Commute Evening Digest"
        elif language == "en" and issue.get("window_end"):
            title = "Commute Morning Digest"
        date = _display_date(issue["created_at"], language)
        window_start = issue.get("window_start")
        window_end = issue.get("window_end")
        if window_start and window_end:
            start = _display_date(window_start, language, with_time=True)
            end = _display_date(window_end, language, with_time=True)
            timezone = "北京时间" if language == "zh" else "Beijing time"
            date = f"{start} – {end} {timezone}"
        count = len(issue[mode])
        edition = f"第 {issue['id']} 期" if language == "zh" else f"Issue {issue['id']}"
        story_count = (
            f"{count} 条新闻"
            if language == "zh"
            else f"{count} {'stories' if count != 1 else 'story'}"
        )
        details = " · ".join(part for part in (edition, date, story_count) if part)
        dateline = f'<div class="digest-dateline">{escape(details)}</div>'
    masthead.markdown(
        f'<header class="digest-masthead"><h1>{title}</h1>{dateline}</header>',
        unsafe_allow_html=True,
    )

    if issue_arg is not None and just_published is None:
        st.button("查看最新一期", on_click=_clear_issue_query)
    if load_error:
        st.error(load_error)
    elif invalid_issue:
        st.warning("晨报链接中的期号无效。")
    elif issue is None and issue_arg is not None:
        st.warning("未找到这期晨报。")
    elif issue is None:
        st.info("还没有已发布的新闻简报。可以在侧栏生成并发布第一期。")
    elif not issue[mode]:
        st.info("本期这个版式没有新闻。" if language == "zh" else "No stories in this edition.")
    else:
        for index, article in enumerate(issue[mode]):
            _article_card(article, language, f"audio_{issue['id']}_{mode}_{language}_{index}")


if __name__ == "__main__":
    main()
