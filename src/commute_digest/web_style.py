"""Reader typography, responsive story rows, and per-reader colors."""

import re

import streamlit as st

DEFAULT_ACCENT = "#3D5876"
ACCENTS = {"深蓝": DEFAULT_ACCENT, "松绿": "#268375", "靛紫": "#7761A4", "赭红": "#B25949", "琥珀": "#A47422"}

_STYLE = """
<style>
:root {
  --digest-ink: INK; --digest-muted: MUTED; --digest-paper: PAPER;
  --digest-panel: PANEL; --digest-line: LINE; --digest-accent: ACCENT;
  --digest-tint: color-mix(in srgb, var(--digest-accent) TINT, var(--digest-paper));
  --digest-rail: RAIL; --digest-action: ACTION; --digest-action-ink: ACTION_INK;
}
.stApp, [data-testid="stHeader"], [data-testid="stMain"] {
  background:var(--digest-paper); color:var(--digest-ink);
}
[data-testid="stHeader"] {height:3.3rem;}
.stMainBlockContainer {max-width:72rem; padding:3.8rem clamp(1rem,4vw,4rem) 4rem;}
.digest-masthead {
  border-bottom:2px solid var(--digest-action); padding:0 0 1.1rem; margin:0 0 .65rem;
}
.digest-masthead h1 {
  color:var(--digest-ink); font-family:"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:clamp(2.2rem,4vw,3.5rem); line-height:1.1; letter-spacing:-.06em;
  font-weight:750; margin:0; padding:0;
}
[data-testid="stSidebar"] {background:var(--digest-rail); color:#f3f6fa;}
[data-testid="stSidebarHeader"] {position:relative; padding:.7rem 1.3rem; min-height:3.6rem;}
[data-testid="stSidebarCollapseButton"] {opacity:1!important; visibility:visible!important; display:flex!important;}
[data-testid="stSidebarCollapseButton"] button, [data-testid="stExpandSidebarButton"] {
  width:auto; height:2rem; padding:.1rem .5rem; border-radius:.45rem;
}
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapseButton"] button span {color:#f3f6fa!important;}
[data-testid="stSidebarCollapseButton"] button::after,
[data-testid="stExpandSidebarButton"]::after {font-size:.79rem; white-space:nowrap; margin-left:.12rem;}
[data-testid="stSidebarCollapseButton"] button::after {content:"收起侧栏";}
[data-testid="stExpandSidebarButton"]::after {content:"展开侧栏"; color:var(--digest-ink);}
[data-testid="stSidebarUserContent"] {padding:1rem 1.35rem;}
.digest-profile {display:flex; flex-direction:column; align-items:flex-start; gap:.28rem;
  padding:.65rem .15rem 1.35rem; margin-bottom:.45rem; border-bottom:1px solid #ffffff32;}
.digest-avatar {height:3.3rem; width:3.3rem; border-radius:50%;
  background:color-mix(in srgb,var(--digest-accent) 16%,white);
  color:var(--digest-rail); display:grid; place-items:center; font:650 1.25rem sans-serif;
  overflow:hidden; margin-bottom:.5rem;}
.digest-avatar img {width:100%; height:100%; object-fit:cover;}
.digest-name {font-size:1.12rem; font-weight:650; color:#fff; overflow-wrap:anywhere;}
.digest-email {font-size:.83rem; color:#d1dbe7; overflow-wrap:anywhere;}
[data-testid="stSidebar"] .stButton button {min-height:2.65rem; justify-content:flex-start;
  border-radius:.55rem; border:1px solid #ffffff42; background:transparent; color:#f7f9fb;}
[data-testid="stSidebar"] .stButton button:hover {background:#ffffff18; border-color:#ffffff85;}
[data-testid="stSidebar"] .stButton button[kind="primary"] {
  background:color-mix(in srgb,var(--digest-accent) 22%,white); color:var(--digest-rail);}
[class*="st-key-batch_"] {border:1px solid var(--digest-line); border-radius:.78rem;
  padding:.1rem clamp(.8rem,2.3vw,1.6rem) .6rem; background:var(--digest-panel); gap:0!important;}
[class*="st-key-batch_latest_"] {background:var(--digest-tint); border-color:color-mix(in srgb,var(--digest-accent) 38%,var(--digest-line));}
[class*="st-key-story_"] {padding:.95rem 0; gap:.25rem!important;}
[class*="st-key-batch_"] > div:has(> [class*="st-key-story_"]) + div > [class*="st-key-story_"] {
  border-top:1px solid var(--digest-line);}
.digest-story-title {font-size:clamp(1.08rem,1.65vw,1.33rem); font-weight:650; line-height:1.47;
  color:var(--digest-ink); padding:0; margin:0; overflow-wrap:anywhere;}
.digest-meta {display:flex; flex-wrap:wrap; align-items:center; gap:.28rem .7rem;
  color:var(--digest-muted); font-size:.8rem; line-height:1.55; margin:.3rem 0 0;}
.digest-topic {font-weight:640; color:var(--digest-action);}
.digest-detail {max-width:50rem; margin:.8rem 0 1.3rem;}
.digest-detail h2 {margin:.4rem 0 .5rem; padding:0; color:var(--digest-ink);
  font-size:clamp(1.65rem,3vw,2.45rem); line-height:1.32;}
.digest-detail h3 {color:var(--digest-ink); margin:1.8rem 0 .55rem; padding:0; font-size:1.2rem;}
.digest-detail p {color:var(--digest-ink); line-height:1.85; font-size:1.04rem; margin:0 0 .8rem;
  white-space:pre-line; overflow-wrap:anywhere;}
.digest-source {color:var(--digest-action)!important; font-size:.9rem;}
.st-key-test_selected_model button {max-width:100%; height:auto;}
.st-key-test_selected_model button code {white-space:normal; overflow-wrap:anywhere;}
.stMainBlockContainer .stButton button, [data-testid="stDialog"] button[data-testid^="stBaseButton"] {
  color:var(--digest-ink); background:var(--digest-panel); border:1px solid var(--digest-line);
  border-radius:.52rem; transition:background .15s ease,border-color .15s ease;}
.stMainBlockContainer .stButton button:hover {background:var(--digest-tint); border-color:var(--digest-accent);}
[data-testid="stDialog"] [role="dialog"] {background:var(--digest-paper); color:var(--digest-ink);}
[data-testid="stElementContainer"]:has([data-digest-dialog-script]) {display:none;}
[data-digest-drag-handle] {cursor:grab; touch-action:none; user-select:none;}
[data-digest-drag-handle]:active {cursor:grabbing;}
[data-digest-drag-handle]::before {
  content:""; width:.52rem; height:.83rem; margin-right:.55rem;
  background:radial-gradient(var(--digest-muted) 1px,transparent 1.5px) 0 0 / 4px 4px;}
[data-digest-drag-handle]:focus-visible {outline:2px solid var(--digest-action); outline-offset:-3px;}
[data-testid="stDialog"] [data-digest-floating] {
  position:fixed; left:var(--digest-left); top:var(--digest-top); margin:0;
  width:min(var(--digest-window-width),calc(100% - 16px));
  max-height:calc(100dvh - 16px); overflow:auto; overscroll-behavior:contain;}
[data-testid="stDialog"] [data-testid="stRadioOption"][data-selected] > div > div:first-child {
  border-color:var(--digest-action); background:var(--digest-action);}
.st-key-appearance_palette [data-testid="stRadioOption"] > div > div:first-child {
  background:var(--swatch,var(--digest-panel)); border:1px solid var(--digest-muted);}
.st-key-appearance_palette [data-testid="stRadioOption"][data-selected] > div > div:first-child {
  outline:2px solid var(--digest-ink); outline-offset:1px;}
SWATCHES
[data-testid="stDialog"] :is(input,button):focus-visible {outline-color:var(--digest-action);}
[data-testid="stDialog"] [data-testid="stForm"] {border:0; padding:.55rem 0 0;}
[data-testid="stDialog"] [data-testid="stWidgetLabel"] p, [data-testid="stDialog"] h2,
[data-testid="stDialog"] [data-testid="stMarkdownContainer"] {color:var(--digest-ink);}
[data-testid="stDialog"] [data-baseweb="input"], [data-testid="stDialog"] [data-baseweb="base-input"],
[data-testid="stDialog"] [data-baseweb="textarea"], [data-testid="stDialog"] [data-baseweb="select"] > div {
  background:var(--digest-panel); color:var(--digest-ink);}
[data-testid="stDialog"] [data-testid="stTextInputRootElement"],
[data-testid="stDialog"] [data-testid="stTextInputRootElement"] > div,
[data-testid="stDialog"] [data-testid="stTextAreaRootElement"] {
  color:var(--digest-ink)!important; background:var(--digest-panel)!important;}
[data-testid="stDialog"] input:not([type="color"]), [data-testid="stDialog"] textarea {
  background:var(--digest-panel)!important; color:var(--digest-ink)!important; -webkit-text-fill-color:var(--digest-ink);}
[data-testid="stDialog"] input::placeholder, [data-testid="stDialog"] textarea::placeholder {
  -webkit-text-fill-color:var(--digest-muted); color:var(--digest-muted); opacity:1;}
[data-testid="stDialog"] button[aria-label="Close"], [data-testid="stDialog"] button[aria-label="Close dialog"] {
  color:var(--digest-ink)!important;}
[data-testid="stDialog"] hr {border-color:var(--digest-line);}
[data-testid="stDialog"] button[kind="primaryFormSubmit"], [data-testid="stDialog"] button[kind="primary"] {
  background:var(--digest-action)!important; color:var(--digest-action-ink)!important;}
[data-testid="stDialog"] button[kind="primaryFormSubmit"] p,
[data-testid="stDialog"] button[kind="primary"] p {color:var(--digest-action-ink)!important;}
@media (max-width:640px) {
 .stMainBlockContainer {padding:3.6rem .9rem 2.5rem;}
 .digest-masthead {padding-bottom:.8rem; margin-bottom:.3rem;}
 [class*="st-key-story_"] [data-testid="stHorizontalBlock"] {gap:.4rem;}
 [class*="st-key-story_"] [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {
    flex-direction:column; align-items:stretch; gap:.7rem!important;}
 [class*="st-key-story_"] [data-testid="stColumn"] {min-width:100%; flex:0 0 auto; width:100%;}
 [class*="st-key-story_"] [data-testid="stColumn"]:last-child {margin-top:.55rem;}
 [data-testid="stDialog"] [role="dialog"] {max-height:92dvh;}
}
@media (prefers-reduced-motion: reduce) {* {scroll-behavior:auto!important; transition:none!important;}}
</style>
"""


def apply_style(accent: str, mode: str) -> None:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = DEFAULT_ACCENT
    night = mode == "dark"
    # Very pale custom colors still need to work with the white sidebar/button text.
    pale = min(int(accent[index:index + 2], 16) for index in (1, 3, 5)) > 160
    tokens = {
        "SWATCHES": "\n".join(
            f'.st-key-appearance_palette [role="radiogroup"] > :nth-child({i}){{--swatch:{value};}}'
            for i, value in enumerate(ACCENTS.values(), 1)
        ),
        "ACTION_INK": "#1c2c3b" if night else "#ffffff",
        "ACTION": f"color-mix(in srgb,{accent} 45%, white)" if night else f"color-mix(in srgb,{accent} {'38%' if pale else '56%'}, #172334)",
        "INK": "#e8edf3" if night else "#23354a",
        "MUTED": "#bac7d5" if night else "#536579",
        "PAPER": "#202c3a" if night else "#ffffff",
        "PANEL": "#283647" if night else "#ffffff",
        "LINE": "#48566a" if night else "#d9e1e9",
        "RAIL": f"color-mix(in srgb,{accent} {'33%' if night or pale else '52%'}, #172536)",
        "ACCENT": accent,
        "TINT": "32%" if night else "14%",
    }
    css = _STYLE
    for token, value in tokens.items():
        css = css.replace(token, value)
    st.html(css)


# Only this fixed program runs in the page. Account data and model output never
# enter executable HTML. Each newly opened Streamlit dialog gets its own handle.
def make_settings_draggable() -> None:
    st.html(_DIALOG_DRAG, unsafe_allow_javascript=True)


_DIALOG_DRAG = """
<span hidden data-digest-dialog-script></span>
<script>
(() => {
  const title = document.querySelector('[data-testid="stDialog"] [role="dialog"] > h2[slot="title"]');
  if (!title || title.hasAttribute("data-digest-drag-handle")) return;
  const box = title.closest('[role="dialog"]').parentElement;
  title.setAttribute("data-digest-drag-handle", "");
  title.tabIndex = 0;
  title.title = "拖动标题移动窗口，也可用方向键移动、Home 复位";
  title.setAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight ArrowUp ArrowDown Home");
  let pointer = null;
  function place(x, y) {
    if (!box.hasAttribute("data-digest-floating")) {
      box.style.setProperty("--digest-window-width", box.getBoundingClientRect().width + "px");
      box.setAttribute("data-digest-floating", "");
    }
    const frame = box.getBoundingClientRect();
    const clamp = (value, maximum) => Math.max(8, Math.min(value, maximum - 8));
    box.style.setProperty("--digest-left", clamp(x, innerWidth - frame.width) + "px");
    box.style.setProperty("--digest-top", clamp(y, innerHeight - frame.height) + "px");
  }
  title.addEventListener("pointerdown", event => {
    if (!event.isPrimary || event.button !== 0) return;
    const rect = box.getBoundingClientRect();
    pointer = {id:event.pointerId, x:event.clientX - rect.left, y:event.clientY - rect.top};
    title.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  title.addEventListener("pointermove", event => {
    if (event.pointerId === pointer?.id) place(event.clientX - pointer.x, event.clientY - pointer.y);
  });
  title.addEventListener("lostpointercapture", () => { pointer = null; });
  title.addEventListener("keydown", event => {
    const step = {ArrowLeft:[-16,0], ArrowRight:[16,0], ArrowUp:[0,-16], ArrowDown:[0,16]}[event.key];
    if (event.key === "Home") box.removeAttribute("data-digest-floating");
    else if (step) {
      const rect = box.getBoundingClientRect();
      place(rect.left + step[0], rect.top + step[1]);
    } else return;
    event.preventDefault();
  });
  // Keep the close and save controls reachable when the form or viewport changes.
  const resize = new ResizeObserver(() => {
    if (!box.isConnected) { resize.disconnect(); return; }
    if (box.hasAttribute("data-digest-floating")) {
      const rect = box.getBoundingClientRect();
      place(rect.left, rect.top);
    }
  });
  resize.observe(box);
  resize.observe(document.documentElement);
})();
</script>
"""
