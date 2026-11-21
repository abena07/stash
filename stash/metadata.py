"""fetch a saved page's title, description and reading time (#3).

anyone can post a url, so fetching is locked down: only public ip addresses,
a short timeout, a size cap and a handful of redirects. any failure returns
None and the link simply stays saved without metadata.
"""

import ipaddress
import logging
import math
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import ResolveResult
from aiohttp.resolver import ThreadedResolver

log = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (compatible; stash-bot/1.0; +https://github.com/abena07/stash)"

WORDS_PER_MINUTE = 230
MIN_WORDS = 50  # below this it's a js app or a wall, not an article
MAX_TITLE = 300
MAX_DESCRIPTION = 1000

TITLE_KEYS = ("og:title", "twitter:title")
DESCRIPTION_KEYS = ("og:description", "description", "twitter:description")
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "nav", "footer"}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "source", "track", "wbr",
}
META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)


@dataclass(frozen=True)
class Metadata:
    title: str | None
    description: str | None
    reading_minutes: int | None


def display_title(title: str | None, url: str) -> str:
    """what to show for a link: its title, or the site name when there isn't one."""
    if title:
        return title
    return (urlsplit(url).hostname or url).removeprefix("www.")


def parse(html: str) -> Metadata:
    parser = _PageParser()
    parser.feed(html)
    parser.close()

    title = _first(parser.meta, TITLE_KEYS) or _clean(parser.title_text)
    description = _first(parser.meta, DESCRIPTION_KEYS)
    words = len(" ".join(parser.main_text or parser.body_text).split())
    minutes = math.ceil(words / WORDS_PER_MINUTE) if words >= MIN_WORDS else None
    return Metadata(
        title=_cap(title, MAX_TITLE),
        description=_cap(description, MAX_DESCRIPTION),
        reading_minutes=minutes,
    )


async def fetch(session: aiohttp.ClientSession, url: str) -> Metadata | None:
    """the page's metadata, or None if it can't be fetched or isn't html."""
    try:
        html = await _get_html(session, url)
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError, UnicodeError) as e:
        log.info("no metadata for %s: %s", url, e or type(e).__name__)
        return None
    if html is None:
        return None
    meta = parse(html)
    if not (meta.title or meta.description or meta.reading_minutes):
        return None
    return meta


def make_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=10)
    return aiohttp.ClientSession(
        connector=connector,
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )


class PublicResolver(ThreadedResolver):
    """dns that refuses private, loopback and link-local addresses.

    the connector connects to exactly the addresses returned here, so a
    hostname that resolves to an internal ip (railway's private network,
    cloud metadata) can't be reached even if its dns changes between lookups.
    """

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        results = [r for r in await super().resolve(host, port, family) if is_public(r["host"])]
        if not results:
            raise OSError(f"{host} doesn't resolve to a public address")
        return results


def is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global


async def _get_html(session: aiohttp.ClientSession, url: str) -> str | None:
    # redirects are followed by hand: aiohttp skips the resolver for ip literals,
    # so every hop's host has to be checked here
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url)
        async with session.get(url, allow_redirects=False) as resp:
            if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                url = urljoin(url, resp.headers["Location"])
                continue
            if resp.status != 200:
                log.info("no metadata for %s: http %d", url, resp.status)
                return None
            if resp.content_type not in ("text/html", "application/xhtml+xml"):
                return None
            return _decode(await _read_capped(resp), resp.charset)
    log.info("no metadata for %s: too many redirects", url)
    return None


async def _read_capped(resp: aiohttp.ClientResponse) -> bytes:
    # content.read(n) returns whatever has arrived so far, not n bytes, so read until done
    body = bytearray()
    async for chunk in resp.content.iter_chunked(64 * 1024):
        body += chunk
        if len(body) >= MAX_BYTES:
            break
    return bytes(body[:MAX_BYTES])


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not a web url: {url}")
    try:
        literal = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return  # a hostname: PublicResolver checks it
    if not is_public(str(literal)):
        raise ValueError(f"{parts.hostname} isn't a public address")


def _decode(body: bytes, charset: str | None) -> str:
    if not charset:
        match = META_CHARSET_RE.search(body[:4096])
        charset = match.group(1).decode() if match else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:  # unknown charset name
        return body.decode("utf-8", errors="replace")


def _first(meta: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if value := _clean(meta.get(key, "")):
            return value
    return None


def _clean(text: str) -> str | None:
    return " ".join(text.split()) or None


def _cap(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()  # convert_charrefs=True decodes &amp; etc.
        self.meta: dict[str, str] = {}
        self.title_text = ""
        self.body_text: list[str] = []
        self.main_text: list[str] = []
        self._in_title = False
        self._skip_depth = 0
        self._main_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"]
            return
        if tag in VOID_TAGS:
            return
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in ("article", "main"):
            self._main_depth += 1

    def handle_startendtag(self, tag, attrs):
        if tag == "meta":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in ("article", "main") and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_text += data
            return
        if self._skip_depth:
            return
        self.body_text.append(data)
        if self._main_depth:
            self.main_text.append(data)
