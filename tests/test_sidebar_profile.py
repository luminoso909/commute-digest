from html import escape
from types import SimpleNamespace

import pytest

from commute_digest.web_settings import _profile_html


@pytest.mark.parametrize(("domain", "provider"), [
    ("GMAIL.COM", "gmail"), ("163.com", "163"), ("googlemail.com", "gmail"),
    ("alumni.example.edu.cn", "alumni.example.edu.cn"),
])
def test_email_badge_shows_provider_and_keeps_original_address(domain, provider):
    email = f"reader+commute@{domain}"
    html = _profile_html(SimpleNamespace(username="晨报读者", avatar=None, default_email=email))
    assert f'aria-describedby="digest-email-address">{provider}</button>' in html
    assert f'role="tooltip">{email}</span>' in html
    assert email not in html.partition("<script>")[2]


def test_profile_escapes_account_text_and_omits_copy_for_missing_email():
    name = '<img src=x onerror="test()">'
    email = '"a<b>"@example.org'
    account = SimpleNamespace(username=name, avatar=None, default_email=email)
    html = _profile_html(account)
    assert escape(name) in html and name not in html
    assert escape(email) in html and email not in html
    account.default_email = ""
    empty = _profile_html(account)
    assert "邮箱待设置" in empty and "<button" not in empty and "<script" not in empty
    guest = _profile_html(None)
    assert "访客" in guest and "邮箱待设置" not in guest and "<button" not in guest
