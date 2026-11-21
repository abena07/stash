import asyncio

import pytest
from aiohttp import web

from stash import metadata
from stash.metadata import Metadata, display_title, fetch, is_public, parse

WORDS = " ".join(["word"] * 460)  # 2 minutes


def test_parse_prefers_open_graph():
    html = f"""<html><head>
        <title>Fallback | Site</title>
        <meta property="og:title" content="The &amp; Real Title">
        <meta name="description" content="  a   short
            summary ">
    </head><body><p>{WORDS}</p></body></html>"""
    assert parse(html) == Metadata("The & Real Title", "a short summary", 2)


def test_parse_falls_back_to_title_tag():
    meta = parse("<html><head><title>\n  Just a title </title></head><body>hi</body></html>")
    assert meta == Metadata("Just a title", None, None)


def test_reading_time_counts_the_article_not_the_chrome():
    html = f"""<body>
        <nav>{WORDS}</nav>
        <script>var x = "{WORDS}";</script>
        <article><p>{" ".join(["w"] * 240)}</p><img src="a.png"><p>more</p></article>
        <footer>{WORDS}</footer>
    </body>"""
    assert parse(html).reading_minutes == 2  # 241 words


def test_short_pages_get_no_reading_time():
    assert parse("<body>sign in to continue</body>").reading_minutes is None


def test_long_titles_are_capped():
    title = parse(f"<title>{'x' * 500}</title>").title
    assert len(title) == metadata.MAX_TITLE and title.endswith("…")


@pytest.mark.parametrize(
    "title, url, expected",
    [
        ("A title", "https://example.com/a", "A title"),
        (None, "https://www.example.com/a", "example.com"),
        ("", "https://blog.example.com/a", "blog.example.com"),
    ],
)
def test_display_title(title, url, expected):
    assert display_title(title, url) == expected


@pytest.mark.parametrize(
    "ip, expected",
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("fd12:3456::1", False),  # railway private network is ipv6
        ("::1", False),
        ("::ffff:127.0.0.1", False),
    ],
)
def test_is_public(ip, expected):
    assert is_public(ip) is expected


def test_fetch_refuses_private_addresses():
    async def go():
        async with metadata.make_session() as session:
            assert await fetch(session, "http://127.0.0.1:9/") is None
            assert await fetch(session, "http://169.254.169.254/latest/meta-data") is None
            assert await fetch(session, "http://localhost:9/") is None

    asyncio.run(go())


@pytest.fixture
def local_site(monkeypatch):
    """a real http server on localhost, with the public-address check switched off."""
    monkeypatch.setattr(metadata, "is_public", lambda ip: True)

    async def page(request):
        return web.Response(
            text=f"<title>Local page</title><body><main>{WORDS}</main></body>",
            content_type="text/html",
        )

    async def streamed(request):
        # sent in pieces, like a big real page
        resp = web.StreamResponse(headers={"Content-Type": "text/html"})
        await resp.prepare(request)
        await resp.write(b"<title>Streamed</title><body><main>")
        for _ in range(4):
            await asyncio.sleep(0.01)
            await resp.write(WORDS.encode() + b" ")
        await resp.write(b"</main></body>")
        return resp

    async def redirect(request):
        raise web.HTTPFound("/page")

    async def loop(request):
        raise web.HTTPFound("/loop")

    async def pdf(request):
        return web.Response(body=b"%PDF-1.7", content_type="application/pdf")

    async def missing(request):
        return web.Response(status=404, text="<title>Not found</title>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/page", page)
    app.router.add_get("/streamed", streamed)
    app.router.add_get("/redirect", redirect)
    app.router.add_get("/loop", loop)
    app.router.add_get("/doc.pdf", pdf)
    app.router.add_get("/missing", missing)
    return app


def run_against(app, paths):
    async def go():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with metadata.make_session() as session:
                return [await fetch(session, f"http://127.0.0.1:{port}{p}") for p in paths]
        finally:
            await runner.cleanup()

    return asyncio.run(go())


def test_fetch(local_site):
    page, redirected, looped, pdf, missing = run_against(
        local_site, ["/page", "/redirect", "/loop", "/doc.pdf", "/missing"]
    )
    assert page == Metadata("Local page", None, 2)
    assert redirected == page
    assert looped is None
    assert pdf is None
    assert missing is None


def test_fetch_reads_the_whole_page(local_site):
    (streamed,) = run_against(local_site, ["/streamed"])
    assert streamed == Metadata("Streamed", None, 8)  # 1840 words
