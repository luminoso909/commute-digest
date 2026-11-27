"""Reader typography, responsive story rows, and per-reader colors."""

import re

import streamlit as st

DEFAULT_ACCENT = "#3D5876"
ACCENTS = {"深蓝": DEFAULT_ACCENT, "松绿": "#268375", "靛紫": "#7761A4", "赭红": "#B25949", "琥珀": "#A47422"}

_STYLE = """
<style>
:root {
  --digest-ink: INK; --digest-muted: MUTED; --digest-paper: PAPER;
  --digest-panel: PANEL; --digest-field: FIELD; --digest-line: LINE; --digest-accent: ACCENT;
  --digest-tint: color-mix(in srgb, var(--digest-accent) TINT, var(--digest-paper));
  --digest-rail: RAIL; --digest-action: ACTION; --digest-action-ink: ACTION_INK;
  color-scheme: SCHEME;
}
.stApp, [data-testid="stHeader"], [data-testid="stMain"] {
  background:var(--digest-paper); color:var(--digest-ink);
}
[data-testid="stHeader"] {height:3.3rem;}
.stMainBlockContainer {max-width:72rem; padding:3.8rem clamp(1rem,4vw,4rem) 4rem;}
.digest-masthead {
  border-bottom:1px solid #ffffff32; padding:0 0 1rem; margin:0;
}
.digest-masthead h1 {
  color:#f4f7fa; font-family:"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:1.9rem; line-height:1.2; letter-spacing:.025em;
  font-weight:750; margin:0; padding:0;
}
.st-key-archive_toolbar h2 {font-size:1.45rem; padding:0; margin:0; color:var(--digest-ink);}
.st-key-archive_filters [data-testid="stWidgetLabel"] p {color:var(--digest-ink);}
[data-testid="stSidebar"] {background:var(--digest-rail); color:#f3f6fa;}
[data-testid="stSidebarHeader"] {position:relative; padding:.7rem 1.3rem; min-height:3.6rem;}
[data-testid="stSidebarCollapseButton"] {
  visibility:visible!important; display:flex; position:absolute; inset-block-start:.4rem;
  inset-inline-end:-.6rem;}
[data-testid="stSidebarCollapseButton"] button, button[data-testid="stExpandSidebarButton"] {
  display:inline-grid; place-items:center; width:2.75rem; height:2.75rem; padding:0;
  border:0; border-radius:.55rem; background:transparent; color:var(--digest-muted);}
[data-testid="stSidebarCollapseButton"] button {color:#dce5ef;}
[data-testid="stSidebarCollapseButton"] button:hover, button[data-testid="stExpandSidebarButton"]:hover {
  background:color-mix(in srgb,currentColor 12%,transparent);}
[data-testid="stSidebarCollapseButton"] button:focus-visible, button[data-testid="stExpandSidebarButton"]:focus-visible {
  outline:2px solid currentColor; outline-offset:-2px;}
[data-testid="stSidebarCollapseButton"] button > *, button[data-testid="stExpandSidebarButton"] > * {display:none;}
[data-testid="stSidebarCollapseButton"] button::before, button[data-testid="stExpandSidebarButton"]::before {
  content:""; box-sizing:border-box; width:1.25rem; height:1.05rem; border:1.5px solid currentColor;
  border-radius:.25rem; background:linear-gradient(currentColor 0 0) 28% 0 / 1.5px 100% no-repeat;}
[data-testid="stSidebarUserContent"] {padding:1rem 1.35rem;}
.digest-profile {position:relative; display:grid; grid-template-columns:auto minmax(0,1fr);
  align-items:center; gap:.85rem; padding:.55rem 0 1.15rem; margin-bottom:.45rem;
  border-bottom:1px solid #ffffff2b;}
.digest-avatar {height:3rem; width:3rem; border-radius:50%;
  background:color-mix(in srgb,var(--digest-accent) 16%,white);
  color:var(--digest-rail); display:grid; place-items:center; font:650 1.2rem sans-serif; overflow:hidden;}
.digest-avatar img {width:100%; height:100%; object-fit:cover;}
.digest-identity {min-width:0; display:grid; gap:.28rem;}
.digest-name {font-size:1.05rem; line-height:1.3; font-weight:650; color:#f5f7fa; overflow-wrap:anywhere;}
.digest-email-empty,.digest-email-status {font-size:.77rem; line-height:1.4; color:#d1dbe7;}
.digest-email-line {display:flex; flex-wrap:wrap; align-items:center; gap:.3rem .45rem;}
.digest-email-badge {font-family:inherit; font-size:.79rem;
  font-weight:600; line-height:1.2; color:var(--digest-rail); border:0; border-radius:.3rem;
  background:color-mix(in srgb,var(--digest-accent) 17%,#f5f7fa); cursor:pointer;
  padding:.24rem .58rem; min-height:1.55rem; max-width:100%; overflow:hidden;
  white-space:nowrap; text-overflow:ellipsis;}
.digest-email-badge:hover {background:#f5f7fa;}
.digest-email-badge:focus-visible {outline:2px solid #f5f7fa; outline-offset:3px;}
.digest-email-tooltip {visibility:hidden; opacity:0; position:absolute; z-index:3; left:0;
  bottom:.55rem; transform:translateY(100%); width:max-content; max-width:100%; box-sizing:border-box;
  padding:.4rem .6rem; border:1px solid var(--digest-line); border-radius:.35rem;
  background:var(--digest-panel); color:var(--digest-ink); font-size:.79rem;
  line-height:1.4; overflow-wrap:anywhere; pointer-events:none;}
.digest-email-badge:is(:hover,:focus-visible) + .digest-email-tooltip {visibility:visible; opacity:1;}
[data-testid="stSidebar"] .stButton button {min-height:2.65rem; justify-content:flex-start;
  border-radius:.55rem; border:1px solid #ffffff35; background:transparent; color:#f2f5f9;}
[data-testid="stSidebar"] .stButton button:hover {background:#ffffff18; border-color:#ffffff85;}
[data-testid="stSidebar"] .stButton button[kind="primary"] {
  background:color-mix(in srgb,var(--digest-accent) 15%,#f5f7fa); color:var(--digest-rail);}
[class*="st-key-batch_"] {border:1px solid var(--digest-line); border-radius:.78rem;
  padding:.1rem clamp(.8rem,2.3vw,1.6rem) .6rem; background:var(--digest-panel); gap:0!important;}
[class*="st-key-batch_latest_"] {background:var(--digest-tint); border-color:color-mix(in srgb,var(--digest-accent) 28%,var(--digest-line));}
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
.stMainBlockContainer .stButton button:enabled:hover, [data-testid="stDialog"] button[data-testid^="stBaseButton"]:enabled:hover {
  background:var(--digest-tint); border-color:var(--digest-action);}
.stMainBlockContainer .stButton button:focus-visible, [data-testid="stDialog"] button[data-testid^="stBaseButton"]:focus-visible {
  outline:2px solid var(--digest-action); outline-offset:2px;}
.stMainBlockContainer .stButton button:disabled, [data-testid="stDialog"] button[data-testid^="stBaseButton"]:disabled {
  opacity:.65;}
[data-testid="stDialog"] [role="dialog"] {background:var(--digest-panel); color:var(--digest-ink);}
[data-testid="stElementContainer"]:has([data-digest-dialog-script]) {display:none;}
[data-digest-drag-handle] {cursor:grab; touch-action:none; user-select:none;}
[data-digest-drag-handle]:active {cursor:grabbing;}
[data-digest-drag-handle]::before {
  content:""; width:.52rem; height:.83rem; margin-right:.55rem;
  background:radial-gradient(var(--digest-muted) 1px,transparent 1.5px) 0 0 / 4px 4px;}
[data-digest-drag-handle]:focus-visible {outline:2px solid var(--digest-action); outline-offset:-3px;}
/* Twice the distance from the 4/9 anchor to the 8px top margin. Keeping
   this scroll frame the same size while dragging avoids moving its handle. */
[data-testid="stDialog"] [data-digest-floating] {
  position:fixed; left:var(--digest-left); top:var(--digest-top); margin:0;
  max-height:calc(100dvh * 8 / 9 - 16px); overflow:auto; overscroll-behavior:contain;}
[data-testid="stDialog"] [data-testid="stRadioOption"][data-selected] > div > div:first-child {
  border-color:var(--digest-action); background:var(--digest-action);}
.st-key-settings_layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-settings_page) {
  display:grid; grid-template-columns:clamp(7.5rem,20%,10rem) minmax(0,1fr);
  align-items:stretch; gap:1.25rem;}
.st-key-settings_layout [data-testid="stColumn"]:has(.st-key-settings_page),
.st-key-settings_layout [data-testid="stColumn"]:has(.st-key-settings_page) + [data-testid="stColumn"] {
  width:100%; min-width:0;}
.st-key-settings_layout [data-testid="stColumn"]:has(.st-key-settings_page) {
  padding:.45rem; border-radius:.7rem;
  background:var(--digest-field);}
.st-key-settings_page [role="radiogroup"] {width:100%; align-items:stretch; gap:.28rem;}
.st-key-settings_page [data-testid="stRadioOption"] {
  display:flex; align-items:center; width:100%; min-height:2.85rem;
  padding:.5rem .8rem; border-radius:.48rem; color:var(--digest-ink); cursor:pointer;}
.st-key-settings_page [data-testid="stRadioOption"]:hover {background:var(--digest-tint);}
.st-key-settings_page [data-testid="stRadioOption"][data-selected] {
  background:var(--digest-tint); box-shadow:inset 3px 0 var(--digest-action);}
.st-key-settings_page [data-testid="stRadioOption"][data-selected] p {font-weight:680; color:var(--digest-action);}
.st-key-settings_page [data-testid="stRadioOption"] > div > div:first-child {display:none;}
.st-key-settings_page [data-testid="stRadioOption"]:focus-within {
  outline:2px solid var(--digest-action); outline-offset:-2px;}
.st-key-settings_actions {gap:.65rem!important;}
[data-testid="stDialog"] .st-key-settings_actions button {
  min-width:5.5rem; min-height:2.95rem; padding:.55rem 1.25rem; font-weight:600;}
.st-key-appearance_palette [data-testid="stRadioOption"] > div > div:first-child {
  background:var(--swatch,var(--digest-panel)); border:1px solid var(--digest-muted);}
.st-key-appearance_palette [data-testid="stRadioOption"][data-selected] > div > div:first-child {
  outline:2px solid var(--digest-ink); outline-offset:1px;}
SWATCHES
[data-testid="stDialog"] :is(input,button):focus-visible {outline-color:var(--digest-action);}
[data-testid="stDialog"] [data-testid="stForm"] {border:0; padding:.55rem 0 0;}
[data-testid="stDialog"] [data-testid="stWidgetLabel"] p, [data-testid="stDialog"] h2,
[data-testid="stDialog"] [data-testid="stMarkdownContainer"] {color:var(--digest-ink);}
:is([data-testid="stMain"],[data-testid="stDialog"]) :is(
  [data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"],
  [data-testid="stTimeInputTimeDisplay"], .react-aria-ComboBox > div:has(> input[role="combobox"]),
  [data-testid="stMultiSelect"] div:has(> [data-testid="stMultiSelectTagsContainer"])
) {
  background:var(--digest-field); border-color:var(--digest-line); color:var(--digest-ink);
  &:focus-within {border-color:var(--digest-action); box-shadow:0 0 0 1px var(--digest-action);}
  &:is([data-has-error],[data-invalid],:has([aria-invalid="true"])) {
    border-color:color-mix(in srgb,#d85751 73%,var(--digest-ink)); box-shadow:none;}}
:is([data-testid="stMain"],[data-testid="stDialog"]) :is(
  input[role="combobox"], [data-testid="stTextInputField"], [data-testid="stTextAreaRootElement"] textarea
) {color:var(--digest-ink); background:transparent;
  &::placeholder {color:var(--digest-muted); opacity:1;}}
:is([data-testid="stMain"],[data-testid="stDialog"]) :is(
  .react-aria-ComboBox, [data-testid="stTextInputRootElement"], [data-testid="stTimeInputTimeDisplay"]
) button:not(:disabled) {color:var(--digest-ink);}
[data-testid="stDialog"] [data-testid="stMultiSelectTagsContainer"] [data-tag] {
  background:var(--digest-action); color:var(--digest-action-ink);}
[data-testid="stDialog"] [data-testid="stMultiSelectTagsContainer"] [data-tag] button:not(:disabled) {color:inherit;}
[data-testid="stDialog"] [data-testid="stTimeInputTimeDisplay"] [data-type] {
  color:var(--digest-ink);
  &[data-type="literal"], &[data-placeholder] {color:var(--digest-muted);}
  &[data-focused] {background:var(--digest-action); color:var(--digest-action-ink);}}
/* React Aria mounts these menus outside the dialog and the archive toolbar. */
:is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]) {
  color:var(--digest-ink); background:var(--digest-panel); border-color:var(--digest-line);
  & [role="option"] {color:var(--digest-ink);}
  & [role="option"][aria-selected="true"] [data-item-hl] {
    color:var(--digest-action); box-shadow:inset 2px 0 var(--digest-action); font-weight:600;}
  & [role="option"]:is([aria-selected="true"],[data-focused],[data-hovered],:hover) [data-item-hl] {
    background:var(--digest-tint);}
  & [role="listbox"][data-empty] > * {color:var(--digest-muted);}}
[data-testid="stDialog"] [data-testid="stFileUploaderDropzone"] {
  background:var(--digest-field); border-color:var(--digest-line);}
[data-testid="stFileUploaderDropzone"] [data-testid="stFileUploaderDropzoneInstructions"] small {color:var(--digest-muted);}
[data-testid="stDialog"] [data-testid="stCheckbox"] label > div:has(+ [data-testid="stWidgetLabel"]) {
  border-color:var(--digest-muted); background:var(--digest-field);}
[data-testid="stDialog"] [data-testid="stCheckbox"] label[data-selected] > div:has(+ [data-testid="stWidgetLabel"]) {
  border-color:var(--digest-action); background:var(--digest-action);
  & svg {stroke:var(--digest-action-ink);}}
[data-testid="stDialog"] [data-testid="stCheckbox"] label[data-focus-visible] > div:has(+ [data-testid="stWidgetLabel"]) {
  outline:2px solid var(--digest-action); outline-offset:2px;}
[data-testid="stDialog"] button[aria-label="Close"], [data-testid="stDialog"] button[aria-label="Close dialog"] {
  color:var(--digest-ink)!important;}
[data-testid="stDialog"] hr {border-color:var(--digest-line);}
.stMainBlockContainer .stButton button[kind="primary"],
[data-testid="stDialog"] button[kind="primaryFormSubmit"], [data-testid="stDialog"] button[kind="primary"] {
  background:var(--digest-action)!important; color:var(--digest-action-ink)!important;}
[data-testid="stDialog"] button[kind="primaryFormSubmit"] p,
[data-testid="stDialog"] button[kind="primary"] p {color:var(--digest-action-ink)!important;}
@media (max-width:640px) {
 .st-key-settings_layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-settings_page) {
   grid-template-columns:4.8rem minmax(0,1fr); gap:.65rem;}
 .st-key-settings_layout [data-testid="stColumn"]:has(.st-key-settings_page) {padding:.23rem;}
 .st-key-settings_page [data-testid="stRadioOption"] {padding:.5rem .3rem; justify-content:center;}
 .st-key-settings_page [data-testid="stRadioOption"] p {font-size:.83rem; white-space:nowrap;}
 [data-testid="stDialog"] .st-key-settings_actions button {min-width:4.5rem; min-height:2.8rem; padding:.4rem .7rem;}
 .stMainBlockContainer {padding:3.6rem .9rem 2.5rem;}
 .st-key-archive_filters [data-testid="stHorizontalBlock"] {display:grid; grid-template-columns:repeat(2,minmax(0,1fr));}
 .st-key-archive_filters [data-testid="stColumn"] {width:100%; min-width:0;}
 [class*="st-key-story_"] [data-testid="stHorizontalBlock"] {gap:.4rem;}
 [class*="st-key-story_"] [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {
    flex-direction:column; align-items:stretch; gap:.7rem!important;}
 [class*="st-key-story_"] [data-testid="stColumn"] {min-width:100%; flex:0 0 auto; width:100%;}
 [class*="st-key-story_"] [data-testid="stColumn"]:last-child {margin-top:.55rem;}
}
@media (max-width:349px) {
 [data-testid="stDialog"] .st-key-settings_actions button {min-width:3.8rem;}
}
@media (prefers-reduced-motion: reduce) {* {scroll-behavior:auto!important; transition:none!important;}}
</style>
"""


def apply_style(accent: str, mode: str) -> None:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = DEFAULT_ACCENT
    night = mode == "dark"
    # Pale or vivid yellow/green accents need a darker mix for text on light surfaces.
    r, g, b = (int(accent[index:index + 2], 16) for index in (1, 3, 5))
    bright = .2126 * r + .7152 * g + .0722 * b > 160
    tokens = {
        "SWATCHES": "\n".join(
            f'.st-key-appearance_palette [role="radiogroup"] > :nth-child({i}){{--swatch:{value};}}'
            for i, value in enumerate(ACCENTS.values(), 1)
        ),
        "SCHEME": "dark" if night else "light",
        "ACTION_INK": "#182530" if night else "#ffffff",
        "ACTION": f"color-mix(in srgb,{accent} 44%, #f3f6fa)" if night else f"color-mix(in srgb,{accent} {'31%' if bright else '67%'}, #1a2938)",
        "INK": "#e7ecf2" if night else "#283647",
        "MUTED": "#b0bfcd" if night else "#59697a",
        "PAPER": "#1c2632" if night else "#f7f8fa",
        "PANEL": "#25313f" if night else "#ffffff",
        "FIELD": "#2c3a49" if night else "#eff2f5",
        "LINE": "#415062" if night else "#d8dfe6",
        "RAIL": f"color-mix(in srgb,{accent} {'20%' if night or bright else '31%'}, {'#16222e' if night else '#233344'})",
        "ACCENT": accent,
        "TINT": "21%" if night else "9%",
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
  let opening = true;
  box.setAttribute("data-digest-floating", "");
  function place(x, y) {
    const frame = box.getBoundingClientRect();
    const clamp = (value, maximum) => Math.max(8, Math.min(value, maximum - 8));
    box.style.setProperty("--digest-left", clamp(x, innerWidth - frame.width) + "px");
    box.style.setProperty("--digest-top", clamp(y, innerHeight - frame.height) + "px");
  }
  function center() {
    const rect = box.getBoundingClientRect();
    place((innerWidth - rect.width) / 2, innerHeight * 4 / 9 - rect.height / 2);
  }
  // Streamlit can mount form fields across several frames. Follow the opening
  // size until the reader starts interacting, then preserve their location.
  function finishOpening() { opening = false; }
  box.addEventListener("pointerdown", finishOpening, {capture:true, once:true});
  box.addEventListener("keydown", finishOpening, {capture:true, once:true});
  box.addEventListener("click", finishOpening, {capture:true, once:true});
  center();
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
    if (event.key === "Home") center();
    else if (step) {
      const rect = box.getBoundingClientRect();
      place(rect.left + step[0], rect.top + step[1]);
    } else return;
    event.preventDefault();
  });
  // Keep the close and save controls reachable when the form or viewport changes.
  const resize = new ResizeObserver(() => {
    if (!box.isConnected) { resize.disconnect(); return; }
    if (opening) center();
    else {
      const rect = box.getBoundingClientRect();
      place(rect.left, rect.top);
    }
  });
  resize.observe(box);
  resize.observe(document.documentElement);
})();
</script>
"""
