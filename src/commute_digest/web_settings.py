"""Reader settings dialog and sidebar for 通勤日报."""

import base64
from hashlib import sha256
from html import escape
import re

import streamlit as st

from . import accounts, edition_service, login_sessions, news_editor
from .web_style import ACCENTS, DEFAULT_ACCENT, make_settings_draggable

MODE_LABELS = {"cross": "跨领域版", "focus": "我的关注"}
LANGUAGE_LABELS = {"zh": "中文", "en": "English"}
PAGES = ("订阅设置", "阅读设置", "API 设置", "界面设置", "登录信息")


def open_settings(page: str) -> None:
    st.session_state["settings_open"] = True
    st.session_state["settings_page"] = page


def _dismiss() -> None:
    st.session_state["settings_open"] = False


def _close() -> None:
    _dismiss()
    st.rerun()


def current_account() -> accounts.Account | None:
    return login_sessions.current_account()


def _footer() -> bool:
    with st.container(horizontal=True, horizontal_alignment="right"):
        # The first submit button is the form's default action on Enter.
        save = st.form_submit_button("保存", type="primary")
        cancel = st.form_submit_button("退出")
    if cancel:
        _close()
    return save


def _auth() -> None:
    action = st.radio("账号操作", ("登录", "注册"), horizontal=True, key="account_action")
    with st.form("authentication", clear_on_submit=False):
        if action == "注册":
            identifier = st.text_input("用户名", max_chars=32, key="register_name")
        else:
            identifier = st.text_input(
                "用户 ID", key="login_id", placeholder="8 位大写字母或数字",
                help="旧账户首次登录可使用原用户名，登录后可在此查看用户 ID。",
            )
        password = st.text_input("密码", type="password", key=f"auth_password_{action}",
                                 help="至少 8 个字符。" if action == "注册" else None)
        with st.container(horizontal=True, horizontal_alignment="right"):
            submitted = st.form_submit_button(action, type="primary")
            cancel = st.form_submit_button("退出")
        if cancel:
            _close()
        if submitted:
            if action == "注册":
                account = accounts.register(identifier, password)
            else:
                account = accounts.authenticate(identifier, password)
                if account is None:
                    st.error("用户 ID 或密码有误。")
                    return
            login_sessions.remember(account.id)
            st.session_state.pop("article_audio", None)
            if action == "注册":
                # Keep Login Info visible so the reader can copy the newly assigned ID.
                st.toast(f"注册成功，用户 ID 为 {account.public_id}")
                st.rerun()
            _close()


def _profile(account: accounts.Account) -> None:
    if st.button("退出登录", key="sign_out"):
        login_sessions.forget()
        _close()
    with st.form("profile_settings"):
        st.text_input("用户 ID", account.public_id, disabled=True)
        username = st.text_input("用户名", account.username, max_chars=32)
        email = st.text_input("邮箱", account.default_email, max_chars=254, placeholder="name@example.com")
        avatar = st.file_uploader("上传头像", type=["png", "jpg", "jpeg", "webp"], max_upload_size=5)
        remove = account.avatar is not None and st.checkbox("移除当前头像")
        save = _footer()
    if save:
        options = {}
        if remove:
            options["avatar"] = None
        elif avatar is not None:
            options["avatar"] = avatar.getvalue()
        accounts.save_profile(account.id, username=username, default_email=email, **options)
        _close()


def _subscribe(account: accounts.Account) -> None:
    prefs = account.preferences
    with st.form("subscription_settings"):
        enabled = st.checkbox("启用每日邮件投递", account.delivery_enabled)
        morning, evening = st.columns(2)
        morning_time = morning.time_input(
            "晨报时间（北京时间）", account.morning_time, step=60,
            help="05:00–16:59，可清空以关闭晨报。",
        )
        evening_time = evening.time_input(
            "晚报时间（北京时间）", account.evening_time, step=60,
            help="17:00–次日 04:59，可清空以关闭晚报。",
        )
        topics = st.multiselect("关注领域", news_editor.CATEGORIES, default=prefs["topics"])
        words = st.text_area("关键词", "、".join(prefs["keywords"]), placeholder="用逗号或换行分隔")
        mode = st.radio("邮件版式", MODE_LABELS, index=list(MODE_LABELS).index(prefs["delivery_mode"]),
                        format_func=MODE_LABELS.get, horizontal=True)
        lang = st.radio("邮件语言", LANGUAGE_LABELS, index=list(LANGUAGE_LABELS).index(prefs["delivery_language"]),
                        format_func=LANGUAGE_LABELS.get, horizontal=True)
        save = _footer()
    if save:
        preferences = {
            "topics": topics,
            "keywords": list(dict.fromkeys(w.strip() for w in re.split(r"[,，、\n]+", words) if w.strip())),
            "delivery_mode": mode,
            "delivery_language": lang,
        }
        accounts.save_subscription(account.id, enabled=enabled, morning_time=morning_time,
                                   evening_time=evening_time, preferences=preferences)
        _close()


def _reading(account: accounts.Account | None, mode: str, lang: str) -> None:
    with st.form("reading_settings"):
        chosen_mode = st.radio("阅读版式", MODE_LABELS, index=list(MODE_LABELS).index(mode),
                               format_func=MODE_LABELS.get, horizontal=True, key="reader_mode")
        chosen_lang = st.radio("阅读语言", LANGUAGE_LABELS, index=list(LANGUAGE_LABELS).index(lang),
                               format_func=LANGUAGE_LABELS.get, horizontal=True, key="reader_language")
        save = _footer()
    if save:
        if account:
            accounts.save_reading(account.id, reading_mode=chosen_mode, reading_language=chosen_lang)
        else:
            st.session_state["guest_reading"] = (chosen_mode, chosen_lang)
        query = st.query_params.to_dict()
        query.update(mode=chosen_mode, lang=chosen_lang)
        if chosen_mode != mode:
            query.pop("article", None)
        st.query_params.from_dict(query)
        _close()


def _appearance(account: accounts.Account | None, color: str, mode: str) -> None:
    choices = [*ACCENTS, "自定义"]
    selected = next((name for name, value in ACCENTS.items() if value.lower() == color.lower()), "自定义")
    # Existing accounts were created with a slightly darker navy.
    if color.lower() == "#314960":
        selected = "深蓝"
    preset = st.radio("主题色", choices, index=choices.index(selected), horizontal=True, key="appearance_palette")
    if preset == "自定义":
        chosen = st.text_input("自定义色值", color, max_chars=7, placeholder="#3D5876").strip()
    else:
        chosen = color if selected == preset else ACCENTS[preset]
    scheme = st.radio("显示模式", ("light", "dark"), index=int(mode == "dark"),
                      format_func={"light": "日间", "dark": "夜间"}.get, horizontal=True)
    with st.container(horizontal=True, horizontal_alignment="right"):
        save = st.button("保存", type="primary", key="save_appearance")
        if st.button("退出", key="cancel_appearance"):
            _close()
    if save:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", chosen):
            st.error("请输入六位十六进制颜色，例如 #3D5876。")
            return
        if account:
            accounts.save_appearance(account.id, theme_color=chosen, color_mode=scheme)
        else:
            st.session_state["guest_appearance"] = (chosen, scheme)
        _close()


def _api(account: accounts.Account) -> None:
    models = news_editor.MODEL_OPTIONS
    model = st.selectbox("模型", list(models), index=list(models).index(account.model), format_func=models.get)
    try:
        stored = accounts.get_api_key(account.id, model=model) or ""
    except accounts.AccountError as error:
        st.warning(str(error))
        stored = ""
    key = st.text_input(
        "API 密钥", value=stored, type="password", autocomplete="off", max_chars=2048,
        key=f"model_key_{account.id}_{model}_{account.api_revision}",
        help="点击眼睛可查看完整密钥。按回车或点击测试会应用当前输入，清空后保存可移除密钥。",
    ).strip()
    if not news_editor.same_model_provider(model, news_editor.DEFAULT_MODEL):
        st.caption("千问负责编稿。联网采集和朗读使用你在 DeepSeek 模型下保存的个人密钥。")
    fingerprint = (account.id, model, sha256(key.encode()).digest())
    previous = st.session_state.get("model_connection_result")
    if previous and previous[0] != fingerprint:
        st.session_state.pop("model_connection_result")
    show_key = st.toggle("在测试按钮上显示完整密钥",
                         key=f"reveal_test_key_{account.id}_{model}")
    identifier = key if show_key else "••••" + key[-4:]
    # Render the identifier literally, including punctuation that Markdown would interpret.
    fence = "`" * (1 + max(map(len, re.findall(r"`+", identifier)), default=0))
    label = f"测试连接 · {fence} {identifier} {fence}" if key else "测试连接 · 未填写"
    test = st.button(label, icon=":material/cable:", key="test_selected_model")
    if test:
        if not key:
            outcome = (False, "请先填写 API 密钥。")
        else:
            try:
                with st.spinner("正在连接模型…"):
                    news_editor.check_model_connection(model, key)
            except news_editor.EditorError as error:
                outcome = (False, str(error))
            else:
                outcome = (True, f"连接成功 · {models[model]}")
        st.session_state["model_connection_result"] = (fingerprint, *outcome)
    result = st.session_state.get("model_connection_result")
    if result and result[0] == fingerprint:
        (st.success if result[1] else st.error)(result[2])
        if key:
            with st.expander("查看本次测试使用的完整密钥"):
                st.code(key, language="text")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("保存", type="primary", key="save_selected_model"):
            accounts.save_api(account.id, model=model, api_key=key)
            st.session_state.pop("model_connection_result", None)
            _close()
        if st.button("退出", key="cancel_selected_model"):
            st.session_state.pop("model_connection_result", None)
            _close()


def appearance(account: accounts.Account | None) -> tuple[str, str]:
    if account:
        return account.theme_color, account.color_mode
    return st.session_state.get("guest_appearance", (DEFAULT_ACCENT, "light"))


def reading(account: accounts.Account | None) -> tuple[str, str]:
    if account:
        mode, lang = account.reading_mode, account.reading_language
    else:
        mode, lang = st.session_state.get("guest_reading", ("cross", "zh"))
    chosen_mode = st.query_params.get("mode")
    chosen_lang = st.query_params.get("lang")
    return (chosen_mode if chosen_mode in MODE_LABELS else mode,
            chosen_lang if chosen_lang in LANGUAGE_LABELS else lang)


@st.dialog("设置", width="medium", on_dismiss=_dismiss)
def settings_dialog(account: accounts.Account | None, mode: str, lang: str) -> None:
    make_settings_draggable()
    if account is not None:
        # A dialog reruns separately; another tab may have already signed out.
        account = current_account()
        if account is None:
            _close()
    page = st.radio("设置栏目", PAGES, key="settings_page", horizontal=True, label_visibility="collapsed")
    try:
        if page == "阅读设置":
            _reading(account, mode, lang)
        elif page == "界面设置":
            _appearance(account, *appearance(account))
        elif page == "登录信息":
            if account:
                _profile(account)
            else:
                _auth()
        elif not account:
            st.info("请先登录以保存个人设置。")
            with st.container(horizontal=True, horizontal_alignment="right"):
                st.button("前往登录", on_click=open_settings, args=("登录信息",))
                if st.button("退出"):
                    _close()
        elif page == "订阅设置":
            _subscribe(account)
        else:
            _api(account)
    except (edition_service.MorningError, news_editor.EditorError) as error:
        st.error(str(error))


def sidebar(account: accounts.Account | None, mode: str, lang: str) -> None:
    with st.sidebar:
        name = account.username if account else "访客"
        initials = escape(name[:1].upper())
        if account and account.avatar:
            encoded = base64.b64encode(account.avatar).decode("ascii")
            avatar = f'<img src="data:image/png;base64,{encoded}" alt="">'
        else:
            avatar = initials
        email = escape(account.default_email or "邮箱待设置") if account else ""
        st.html(
            f'<div class="digest-profile"><div class="digest-avatar">{avatar}</div>'
            f'<div class="digest-name">{escape(name)}</div>'
            f'<div class="digest-email">{email}</div></div>'
        )
        st.button("设置", icon=":material/tune:", width="stretch", on_click=open_settings,
                  args=("订阅设置" if account else "登录信息",))
        if st.button("日报发布", icon=":material/edit_note:", width="stretch", type="primary"):
            if account is None:
                open_settings("登录信息")
            else:
                try:
                    with st.spinner("正在整理并发布本期日报…"):
                        issue = edition_service.publish(account_id=account.id)
                except edition_service.MorningError as error:
                    st.error(str(error))
                    return
                st.query_params.from_dict({"issue": str(issue["id"]), "mode": mode, "lang": lang})
                st.rerun()
