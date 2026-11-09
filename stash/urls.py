import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# stops at whitespace and at < > so discord's no-embed form <https://...> comes out clean
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)

# markdown and prose punctuation that ends up glued to the end of a url
TRAILING = ".,;:!?'\"*|"
BRACKETS = {")": "(", "]": "["}

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "si"}


def extract_urls(text: str) -> list[str]:
    """http(s) urls in a message, in order, without repeats."""
    urls: dict[str, None] = {}
    for match in URL_RE.finditer(text):
        url = _trim(match.group())
        if _is_valid(url):
            urls.setdefault(url, None)
    return list(urls)


def normalize(url: str) -> str:
    """the dedupe key: the same page posted different ways maps to one string."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    if ":" in host:  # ipv6
        host = f"[{host}]"
    if parts.port not in (None, 80, 443):
        host = f"{host}:{parts.port}"

    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)
    )
    return urlunsplit(("https", host, parts.path.rstrip("/"), urlencode(query), ""))


def _trim(url: str) -> str:
    while url:
        last = url[-1]
        if last in TRAILING:
            url = url[:-1]
        elif last in BRACKETS and url.count(last) > url.count(BRACKETS[last]):
            url = url[:-1]  # closing bracket from [text](url) or (see url)
        else:
            break
    return url


def _is_valid(url: str) -> bool:
    try:
        parts = urlsplit(url)
        parts.port  # raises on a junk port
    except ValueError:
        return False
    return bool(parts.hostname)
