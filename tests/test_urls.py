import pytest

from stash.urls import clean, extract_urls, normalize


@pytest.mark.parametrize(
    "text, expected",
    [
        ("no links here", []),
        ("look https://example.com/a", ["https://example.com/a"]),
        ("<https://example.com/a>", ["https://example.com/a"]),
        ("read this: https://example.com/a.", ["https://example.com/a"]),
        ("(see https://example.com/a)", ["https://example.com/a"]),
        ("[post](https://example.com/a)", ["https://example.com/a"]),
        ("**https://example.com/a**", ["https://example.com/a"]),
        ("||https://example.com/a||", ["https://example.com/a"]),
        ("https://en.wikipedia.org/wiki/Foo_(bar)", ["https://en.wikipedia.org/wiki/Foo_(bar)"]),
        (
            "https://a.com/1 and http://b.com/2\nhttps://a.com/1",
            ["https://a.com/1", "http://b.com/2"],
        ),
        ("HTTPS://Example.com/A", ["HTTPS://Example.com/A"]),
        ("https:// broken", []),
        ("https://example.com:notaport/a", []),
    ],
)
def test_extract_urls(text, expected):
    assert extract_urls(text) == expected


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://example.com/a", "http://example.com/a"),
        ("https://example.com/a", "https://www.example.com/a"),
        ("https://example.com/a", "https://EXAMPLE.com/a"),
        ("https://example.com/a", "https://example.com/a/"),
        ("https://example.com/a", "https://example.com/a#section"),
        ("https://example.com/a", "https://example.com:443/a"),
        ("https://example.com/a", "https://example.com/a?utm_source=x&utm_medium=y"),
        ("https://example.com/a", "https://example.com/a?fbclid=abc"),
        ("https://example.com/a?x=1&y=2", "https://example.com/a?y=2&x=1"),
        ("https://example.com", "https://example.com/"),
    ],
)
def test_normalize_same_page(a, b):
    assert normalize(a) == normalize(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://example.com/a", "https://example.com/b"),
        ("https://example.com/a", "https://example.com/A"),  # paths are case-sensitive
        ("https://example.com/a?id=1", "https://example.com/a?id=2"),
        ("https://example.com/a", "https://example.com:8080/a"),
        ("https://blog.example.com/a", "https://example.com/a"),
    ],
)
def test_normalize_different_pages(a, b):
    assert normalize(a) != normalize(b)


def test_normalize_output():
    assert (
        normalize("http://www.Example.com:80/post/?b=2&utm_campaign=z&a=1#top")
        == "https://example.com/post?a=1&b=2"
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        # the substack ios share link from a real drop: referral id and share junk go
        (
            "https://jessicananaadjoaasamoah.substack.com/p/the-trap-of-emotional-utility"
            "?r=80o2rq&utm_medium=ios&shareImageVariant=solid",
            "https://jessicananaadjoaasamoah.substack.com/p/the-trap-of-emotional-utility",
        ),
        ("https://example.com/a?utm_source=x&id=7", "https://example.com/a?id=7"),
        ("https://example.com/a?fbclid=abc", "https://example.com/a"),
        # r= only means referral on substack: kept elsewhere
        ("https://example.com/search?r=10", "https://example.com/search?r=10"),
        # untouched: no query, fragments, case and trailing slashes as posted
        ("https://Example.com/Path/", "https://Example.com/Path/"),
        ("https://example.com/a?x=1#part", "https://example.com/a?x=1#part"),
    ],
)
def test_clean(url, expected):
    assert clean(url) == expected
