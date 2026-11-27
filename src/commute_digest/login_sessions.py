"""Remember a verified login across Streamlit WebSocket connections."""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from secrets import token_urlsafe
import time
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
import streamlit as st

from . import accounts

COOKIE = "__Host-commute-login"
LOCAL_COOKIE = "commute-login-local"
PATH = "/account/session"
LIFETIME = 30 * 24 * 3600


@contextmanager
def _sessions():
    with accounts._database() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS login_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                cookie_hash TEXT UNIQUE, expires_at INTEGER NOT NULL,
                grant_hash TEXT UNIQUE, grant_deadline INTEGER NOT NULL
            )"""
        )
        yield db


def _hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def issue(account_id: int) -> tuple[int, str]:
    grant = token_urlsafe(32)
    now = int(time.time())
    with _sessions() as db:
        db.execute("DELETE FROM login_sessions WHERE expires_at<=? OR (cookie_hash IS NULL AND grant_deadline<=?)",
                   (now, now))
        cursor = db.execute(
            """INSERT INTO login_sessions(account_id,expires_at,grant_hash,grant_deadline)
               VALUES (?,?,?,?)""", (account_id, now + LIFETIME, _hash(grant), now + 120),
        )
        return cursor.lastrowid, grant


def exchange(grant: str, previous_cookie: str = "") -> str | None:
    """Atomically consume the browser's one-time grant and rotate any earlier login."""
    token = token_urlsafe(32)
    with _sessions() as db:
        changed = db.execute(
            """UPDATE login_sessions SET cookie_hash=?, grant_hash=NULL
               WHERE grant_hash=? AND grant_deadline>?""", (_hash(token), _hash(grant), int(time.time())),
        ).rowcount
        if not changed:
            return None
        if previous_cookie:
            db.execute("DELETE FROM login_sessions WHERE cookie_hash=?", (_hash(previous_cookie),))
    return token


def by_cookie(token: str):
    if not token or len(token) != 43:
        return None
    with _sessions() as db:
        return db.execute("SELECT id, account_id FROM login_sessions WHERE cookie_hash=? AND expires_at>?",
                          (_hash(token), int(time.time()))).fetchone()


async def session_route(request: Request) -> Response:
    # Only our page's same-origin fetch can send this header without a CORS grant.
    origin = request.headers.get("origin", "")
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return Response(status_code=403)
    allowed = parsed.netloc == request.headers.get("host") or origin == os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if (request.headers.get("x-commute-session") != "1" or parsed.scheme not in {"http", "https"}
            or not parsed.netloc or not allowed or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        return Response(status_code=403)
    cookie = COOKIE if parsed.scheme == "https" else LOCAL_COOKIE
    old = request.cookies.get(cookie, "")
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > 43:
            return Response(status_code=400)
        body.extend(part)
    if body == b"clear":
        # Another tab may already have signed in again.
        if await run_in_threadpool(by_cookie, old) is None:
            response.delete_cookie(cookie, secure=parsed.scheme == "https", httponly=True, samesite="lax")
        return response
    if len(body) != 43 or not body.isascii():
        return Response(status_code=400)
    token = await run_in_threadpool(exchange, body.decode("ascii"), old)
    if token is None:
        return Response(status_code=401)
    response.set_cookie(cookie, token, max_age=LIFETIME, secure=parsed.scheme == "https",
                        httponly=True, samesite="lax", path="/")
    return response


ROUTES = [Route(PATH, session_route, methods=["POST"])]


def remember(account_id: int) -> None:
    rowid, grant = issue(account_id)
    st.session_state.update(account_id=account_id, login_session=rowid, login_exchange=grant)


def _send_to_browser(value: str) -> None:
    # The credential that survives refresh is set exclusively by the HTTP response.
    st.html(f"""<span class="commute-cookie-sync" hidden></span>
    <style>[data-testid="stElementContainer"]:has(.commute-cookie-sync) {{display:none}}</style>
    <script>
      (() => {{
        const action = {json.dumps(value)};
        if (window.commuteCookieAction === action) return;
        window.commuteCookieAction = action;
        fetch({json.dumps(PATH)}, {{method:'POST',credentials:'same-origin',
          headers:{{'X-Commute-Session':'1'}},body:action}}).then(response => {{
          if (!response.ok) window.commuteCookieAction = null;
        }}).catch(() => {{window.commuteCookieAction = null;}});
      }})();
    </script>""", unsafe_allow_javascript=True)


def current_account() -> accounts.Account | None:
    state = st.session_state
    if state.pop("clear_login_cookie", False):
        _send_to_browser("clear")
    session_id = state.get("login_session")
    if session_id is not None:
        with _sessions() as db:
            session = db.execute(
                "SELECT account_id, grant_hash FROM login_sessions WHERE id=? AND expires_at>?",
                (session_id, int(time.time())),
            ).fetchone()
        if session is None:
            for key in ("account_id", "login_session", "login_exchange", "article_audio"):
                state.pop(key, None)
            return None
        if session["grant_hash"] and state.get("login_exchange"):
            _send_to_browser(state.login_exchange)
        else:
            state.pop("login_exchange", None)
        return accounts.get_account(session["account_id"])
    # Carry forward already authenticated WebSockets when introducing persistent logins.
    if state.get("account_id") is not None:
        remember(state.account_id)
        _send_to_browser(state.login_exchange)
        return accounts.get_account(state.account_id)
    cookies = st.context.cookies
    session = by_cookie(cookies.get(COOKIE) or cookies.get(LOCAL_COOKIE) or "")
    if session:
        state.update(account_id=session["account_id"], login_session=session["id"])
        return accounts.get_account(session["account_id"])
    return None


def forget() -> None:
    session_id = st.session_state.get("login_session")
    if session_id is not None:
        with _sessions() as db:
            db.execute("DELETE FROM login_sessions WHERE id=?", (session_id,))
    for key in ("account_id", "login_session", "login_exchange", "article_audio", "guest_reading", "guest_appearance"):
        st.session_state.pop(key, None)
    st.session_state["clear_login_cookie"] = True
