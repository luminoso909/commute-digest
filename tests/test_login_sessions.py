import asyncio
from http.cookies import SimpleCookie

from starlette.requests import Request

from commute_digest import accounts, login_sessions as login


def exchange_request(body, origin="https://reader.example", cookie="", host="reader.example",
                     fetch_site="same-origin", header="1"):
    headers = [(name.encode(), value.encode()) for name, value in (
        ("origin", origin), ("host", host), ("cookie", cookie), ("x-commute-session", header),
        ("sec-fetch-site", fetch_site))]
    request = Request({"type": "http", "scheme": "https", "path": login.PATH, "headers": headers},
                      lambda: asyncio.sleep(0, result={"type": "http.request", "body": body.encode()}))
    return asyncio.run(login.session_route(request))


def test_http_cookie_rotation_replay_logout_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "reader.sqlite3"))
    user = accounts.register("同名", "reader-password-1")
    _, first_grant = login.issue(user.id)
    assert exchange_request(first_grant, origin="https://elsewhere.test").status_code == 403
    assert exchange_request(first_grant, fetch_site="cross-site").status_code == 403
    assert exchange_request(first_grant, header="").status_code == 403
    initial = exchange_request(first_grant)
    assert initial.status_code == 204
    cookie = SimpleCookie(initial.headers["set-cookie"])[login.COOKIE]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "lax"
    assert cookie["path"] == "/" and not cookie["domain"] and int(cookie["max-age"]) == login.LIFETIME
    assert login.by_cookie(cookie.value)["account_id"] == user.id
    assert not login.by_cookie(user.public_id) and not login.by_cookie(first_grant)
    assert exchange_request(first_grant).status_code == 401
    assert tmp_path.joinpath("reader.sqlite3").read_bytes().find(cookie.value.encode()) == -1

    other = accounts.register("同名", "reader-password-2")
    _, next_grant = login.issue(other.id)
    updated = exchange_request(next_grant, cookie=f"{login.COOKIE}={cookie.value}")
    new_cookie = SimpleCookie(updated.headers["set-cookie"])[login.COOKIE].value
    assert login.by_cookie(cookie.value) is None
    assert login.by_cookie(new_cookie)["account_id"] == other.id
    assert "set-cookie" not in exchange_request("clear", cookie=f"{login.COOKIE}={new_cookie}").headers

    start = int(login.time.time())
    _, expired = login.issue(other.id)
    monkeypatch.setattr(login.time, "time", lambda: start + login.LIFETIME + 1)
    assert exchange_request(expired).status_code == 401
    assert login.by_cookie(new_cookie) is None
    clear = exchange_request("clear", cookie=f"{login.COOKIE}={new_cookie}")
    assert int(SimpleCookie(clear.headers["set-cookie"])[login.COOKIE]["max-age"]) == 0


def test_loopback_http_uses_its_own_httponly_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMUTE_DB", str(tmp_path / "local.sqlite3"))
    user = accounts.register("本地", "reader-password")
    _, grant = login.issue(user.id)
    response = exchange_request(grant, origin="http://localhost:8501", host="localhost:8501")
    cookie = SimpleCookie(response.headers["set-cookie"])[login.LOCAL_COOKIE]
    assert not cookie["secure"] and cookie["httponly"]
    assert login.by_cookie(cookie.value)["account_id"] == user.id
