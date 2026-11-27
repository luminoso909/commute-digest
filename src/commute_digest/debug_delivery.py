"""Optional, reader-initiated trial editions. Enable with COMMUTE_DEBUG_DELIVERY=1."""

import os
from datetime import datetime

import streamlit as st

from . import accounts, edition_service, subscription_delivery
from .delivery_lock import delivery_in_progress
from .schedule import BEIJING, latest_slot


def _next_request(account_id: int, now: datetime) -> tuple[int, datetime]:
    """Open tabs share a request number until SMTP resolves that edition."""
    subscription_delivery._initialize_deliveries()
    with edition_service._database() as db:
        row = db.execute(
            """SELECT slot, scheduled_at, status, claimed_at, issue_id FROM subscription_deliveries
               WHERE account_id=? AND slot LIKE 'debug:%' ORDER BY rowid DESC LIMIT 1""",
            (account_id,),
        ).fetchone()
    slot = latest_slot(now)
    if row is None:
        return 1, slot
    key, scheduled_at, status, claimed_at, issue_id = row
    number = int(key.removeprefix("debug:"))
    # A web process may terminate during SMTP submission. Never repeat that request.
    # Once its lease expires, a new explicit click can start a separate trial.
    finished = status in ("sent", "unconfirmed") or (
        status == "sending" and datetime.fromisoformat(claimed_at) <= now - subscription_delivery.BUILD_LEASE
    )
    if finished:
        return number + 1, slot
    if status == "retry" and issue_id is not None:
        slot = datetime.fromisoformat(scheduled_at)
    return number, slot


@delivery_in_progress()
def send_trial(account_id: int, number: int) -> edition_service.Issue | None:
    if os.getenv("COMMUTE_DEBUG_DELIVERY") != "1":
        raise edition_service.MorningError("调试投递已关闭。")
    account = accounts.get_account(account_id)
    if account is None:
        raise edition_service.MorningError("请登录后再调试投递。")
    if not account.default_email:
        raise edition_service.MorningError("请先在「设置 → 登录信息」保存收件邮箱。")
    now = datetime.now(BEIJING)
    expected, slot = _next_request(account.id, now)
    if expected != number:
        return None
    job = subscription_delivery._claim(account, slot, now, trial_key=f"debug:{number}")
    if job is None:
        return None
    return subscription_delivery.send_claimed_issue(account, slot, now, job, trial=True)


def render_button(account: accounts.Account) -> None:
    number, _ = _next_request(account.id, datetime.now(BEIJING))
    key = f"debug_delivery_{account.id}_{number}"
    pressed = st.button("调试投递", key=key, icon=":material/forward_to_inbox:", width="stretch",
                        help="立即生成个人试刊并发送到你的邮箱，使用已保存的模型和邮件设置。")
    feedback_key = f"debug_delivery_result_{account.id}"
    if feedback := st.session_state.pop(feedback_key, None):
        level, text = feedback
        {"success": st.success, "error": st.error, "info": st.info}[level](text)
    if not pressed:
        return
    try:
        with st.spinner("正在生成试刊并投递到你的邮箱…"):
            issue = send_trial(account.id, number)
    except edition_service.MorningError as error:
        feedback = ("error", str(error))
    else:
        if issue is None:
            feedback = ("info", "这次调试请求正在处理或已经完成。")
        else:
            feedback = ("success", f"第 {issue['issue_number']} 期试刊已由 SMTP 接收。")
            st.query_params.from_dict({"issue": str(issue["id"]), "mode": account.preferences["delivery_mode"],
                                       "lang": account.preferences["delivery_language"]})
    st.session_state[feedback_key] = feedback
    st.rerun()
