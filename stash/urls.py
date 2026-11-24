import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# stops at whitespace and at < > so discord's no-embed form <https://...> comes out clean
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)

# markdown and prose punctuation that ends up glued to the end of a url
TRAILING = ".,;:!?'\"*|"
BRACKETS = {")": "(", "]": "["}

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "si"}
# share-sheet junk some apps add (substack's iOS share adds all three)
SHARE_PARAMS = {"sharepagevariant", "shareimagevariant", "triedredirect", "showwelcomeonshare"}
# substack's ?r= is the sharer's referral id: personal, so it shouldn't end up in a drop
SUBSTACK_PARAMS = {"r"}


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


def _is_tracking(key: str, host: str) -> bool:
    key = key.lower()
    if key.startswith("utm_") or key in TRACKING_PARAMS or key in SHARE_PARAMS:
        return True
    return key in SUBSTACK_PARAMS and (host == "substack.com" or host.endswith(".substack.com"))


def clean(url: str) -> str:
    """the url without tracking and share parameters, otherwise exactly as posted.
    this is what gets stored and shown in drops."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    host = (parts.hostname or "").lower()
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k, host)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


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
