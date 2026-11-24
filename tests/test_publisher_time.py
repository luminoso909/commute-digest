import socket
from datetime import datetime

import pytest

from commute_digest import publisher_time


@pytest.mark.parametrize(
    ("html", "url", "expected"),
    [
        (
            (
                '<meta property="article:published_time" content="2026-11-23T19:03:45+08:00">'
                '<meta property="article:modified_time" content="2026-11-24T09:00:00+08:00">'
            ),
            "https://publisher.example/a",
            "2026-11-23T19:03:45+08:00",
        ),
        (
            (
                '<script type="application/ld+json">'
                '{"pubDate":"2026-11-24T06:37:21","dateModified":"2026-11-24T08:00:00"}'
                '</script><span class="news-time">2026-11-24 06:37</span>'
            ),
            "https://m.ithome.com/html/1031232.htm",
            "2026-11-24T06:37:21+08:00",
        ),
        (
            (
                '<meta name="publishdate" content="2026-11-23">'
                '<div class="mheader domMobile"><h1>上海地铁调价</h1>'
                '<div class="info">2026-11-23 18:51:29<span>来源：新华网</span></div></div>'
            ),
            "https://www.news.cn/20261123/abc/c.html",
            "2026-11-23T18:51:29+08:00",
        ),
        (
            (
                '<div class="content-time"><span>2026-11-23</span><span>周一</span>'
                "<span>21:46:07</span></div>"
            ),
            "https://flash.jin10.com/detail/20261123214607065800",
            "2026-11-23T21:46:07+08:00",
        ),
    ],
)
def test_publication_metadata_uses_original_time(html, url, expected):
    assert publisher_time.publication_time_from_html(html, url) == datetime.fromisoformat(expected)


@pytest.mark.parametrize(
    "html",
    [
        '<meta name="pubdate" content="2026-11-23">',
        '<meta name="pubdate" content="2026-11-23T19:03:45">',
        '<time datetime="2026-11-23T19:03:45Z">19:03</time>',
        '<meta property="article:modified_time" content="2026-11-23T19:03:45Z">',
        (
            '<meta name="pubdate" content="2026-11-23T18:00:00Z">'
            '<meta property="article:published_time" content="2026-11-23T19:03:45Z">'
        ),
    ],
)
def test_publication_metadata_requires_precise_unambiguous_evidence(html):
    assert publisher_time.publication_time_from_html(html, "https://publisher.example/a") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a",
        "https://example.com:8443/a",
        "https://user@example.com/a",
        "https://example.com/a\n",
    ],
)
def test_invalid_transport_rejects_before_lookup(monkeypatch, url):
    def unexpected_dns(*args, **kwargs):
        pytest.fail("invalid URL reached DNS discovery")

    monkeypatch.setattr(publisher_time.socket, "getaddrinfo", unexpected_dns)
    assert publisher_time.published_at(url) is None


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "::ffff:127.0.0.1", "224.0.0.1"]
)
def test_non_public_address_rejects_before_connection(monkeypatch, address):
    def resolve(*args, **kwargs):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    def unexpected_connection(*args, **kwargs):
        pytest.fail("non-public address reached TLS connection")

    monkeypatch.setattr(publisher_time.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(publisher_time, "_PinnedHTTPSConnection", unexpected_connection)
    assert publisher_time.published_at("https://example.com/a") is None
