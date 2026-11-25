"""Read the publication timestamp from an article's public HTTPS page."""

import http.client
import ipaddress
import json
import re
import socket
import ssl
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

_BEIJING = ZoneInfo("Asia/Shanghai")
_CHINESE_PUBLISHERS = (
    "163.com",
    "36kr.com",
    "cctv.com",
    "chinanews.com.cn",
    "cnstock.com",
    "gov.cn",
    "ithome.com",
    "jiemian.com",
    "jin10.com",
    "news.cn",
    "people.com.cn",
    "qq.com",
    "sina.com.cn",
    "sohu.com",
    "stcn.com",
    "thepaper.cn",
    "tmtpost.com",
    "xinhuanet.com",
    "yicai.com",
)
_PUBLICATION_FIELDS = {
    "article:published_time",
    "datepublished",
    "dc.date.issued",
    "parsely-pub-date",
    "pubdate",
    "publication_date",
    "publishdate",
    "published_time",
    "sailthru.date",
}
_TIMESTAMP = re.compile(
    r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:\s*(?:Z|[+-]\d{2}:?\d{2}))?",
    re.ASCII,
)
_XINHUA_DATELINE = re.compile(
    r'<div class="mheader domMobile"[^>]*>.{0,1200}?'
    r'<div class="info">\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',
    re.DOTALL,
)
_ITHOME_DATELINE = re.compile(
    r'<span class="news-time">\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\s*</span>'
)
_JIN10_DATELINE = re.compile(
    r'<div class="content-time">\s*<span>(\d{4}-\d{2}-\d{2})</span>\s*'
    r"<span>[^<]{0,12}</span>\s*<span>(\d{2}:\d{2}:\d{2})</span>"
)
_JIN10_DETAIL = re.compile(r"/detail/(\d{14})\d{6}/?")
_MAX_HTML = 512 * 1024
_REQUEST_TIMEOUT = 5


def _in_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


class _PublicationMetadata(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []
        self._json_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = {key: value or "" for key, value in attrs}
        keys = {fields.get(name, "").casefold() for name in ("property", "itemprop", "name")}
        if tag == "meta" and keys & _PUBLICATION_FIELDS:
            self.values.append(fields.get("content", ""))
        if tag == "time" and (keys & _PUBLICATION_FIELDS or "pubdate" in fields):
            self.values.append(fields.get("datetime", ""))
        if tag == "script":
            self._json_script = fields.get("type", "").casefold() == "application/ld+json"

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._json_script = False

    def handle_data(self, data: str) -> None:
        if not self._json_script:
            return
        try:
            structured = json.loads(data)
        except (ValueError, RecursionError):
            return
        roots = structured if isinstance(structured, list) else [structured]
        for root in roots:
            if not isinstance(root, dict):
                continue
            graph = root.get("@graph", [])
            nodes = [root, *graph] if isinstance(graph, list) else [root]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                value = node.get("datePublished") or node.get("pubDate")
                if isinstance(value, str):
                    self.values.append(value)


def publication_time_from_html(html: str, url: str) -> datetime | None:
    """Extract an explicit first-publication timestamp from an article page."""
    endpoint = urlsplit(url)
    host = endpoint.hostname or ""
    local_time = any(_in_domain(host, domain) for domain in _CHINESE_PUBLISHERS)
    parser = _PublicationMetadata()
    try:
        parser.feed(html)
    except (ValueError, AssertionError):
        return None

    times = set()
    for value in parser.values:
        published = _parse_timestamp(value, local_time)
        if published is not None:
            times.add(published)
    if times:
        # Conflicting metadata cannot establish a reliable first-publication time.
        return next(iter(times)) if len(times) == 1 else None

    if host == "flash.jin10.com":
        return _jin10_timestamp(html, endpoint.path)
    if _in_domain(host, "news.cn") or _in_domain(host, "xinhuanet.com"):
        match = _XINHUA_DATELINE.search(html)
    elif _in_domain(host, "ithome.com"):
        match = _ITHOME_DATELINE.search(html)
    else:
        match = None
    return _parse_timestamp(match[1], local_time) if match else None


def _jin10_timestamp(html: str, path: str) -> datetime | None:
    match = _JIN10_DATELINE.search(html)
    detail = _JIN10_DETAIL.fullmatch(path)
    if match and detail:
        published = _parse_timestamp(f"{match[1]} {match[2]}", local_time=True)
        if published and published.strftime("%Y%m%d%H%M%S") == detail[1]:
            return published
    return None


def _parse_timestamp(value: str, local_time: bool) -> datetime | None:
    value = value.strip()
    if not _TIMESTAMP.fullmatch(value):
        return None
    value = value.replace("/", "-")
    value = re.sub(r"\s+(?=Z|[+-]\d{2}:?\d{2}$)", "", value)
    try:
        published = datetime.fromisoformat(value)
    except ValueError:
        return None
    if published.utcoffset() is not None:
        return published
    return published.replace(tzinfo=_BEIJING) if local_time else None


def _public_address(host: str) -> str | None:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    for _, _, _, _, endpoint in sorted(addresses, key=lambda result: result[0] != socket.AF_INET):
        address = ipaddress.ip_address(endpoint[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address.is_global and not address.is_multicast and not address.is_reserved:
            return str(address)
    return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        super().__init__(host, timeout=_REQUEST_TIMEOUT, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        # Use the checked address for TCP and retain the publisher hostname for SNI and TLS.
        with_socket = socket.create_connection((self._address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(with_socket, server_hostname=self.host)
        except BaseException:
            with_socket.close()
            raise


def published_at(url: str) -> datetime | None:
    """Verify publication time from a public page without following redirects."""
    if any(ord(character) <= 32 or ord(character) == 127 for character in url):
        return None
    try:
        endpoint = urlsplit(url)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.port not in (None, 443)
            or endpoint.username is not None
        ):
            return None
        host = endpoint.hostname.encode("idna").decode("ascii")
        address = _public_address(host)
        if address is None:
            return None
        path = quote(endpoint.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if endpoint.query:
            path += "?" + quote(endpoint.query, safe="/%?:@!$&'()*+,;=-._~")
        connection = _PinnedHTTPSConnection(host, address)
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CommuteDigest/1.0)",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                },
            )
            with connection.getresponse() as response:
                if response.status != 200 or response.headers.get_content_type() not in {
                    "text/html",
                    "application/xhtml+xml",
                }:
                    return None
                page = response.read(_MAX_HTML)
                charset = response.headers.get_content_charset() or "utf-8"
        finally:
            connection.close()
        return publication_time_from_html(page.decode(charset, errors="replace"), url)
    except (OSError, ValueError, LookupError, http.client.HTTPException):
        return None
