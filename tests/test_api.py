"""the obsidian plugin api (#39): codes, auth, and that a code only sees its owner's links."""

import aiohttp
from aiohttp import web

from stash import api, tokens

AMA, KOFI = 10, 20


async def seed(pool, mode="personal"):
    await pool.execute("INSERT INTO guilds (guild_id, mode) VALUES (1, $1), (2, 'personal')", mode)
    for guild, user, title, status in [(1, AMA, "amas read", "read"), (1, AMA, "amas unread", "unread"),
                                       (1, KOFI, "kofis read", "read"), (2, AMA, "other server", "read")]:
        await pool.execute(
            """INSERT INTO links (guild_id, user_id, url, url_normalized, title, status)
               VALUES ($1, $2, $3, $3, $4, $5::link_status)""",
            guild, user, f"https://example.com/{title.replace(' ', '-')}", title, status,
        )


async def names(guild_id, ids):
    return {AMA: "ama", KOFI: "kofi"}


async def call(pool, path, token=None, limiter=None):
    """run the api on a local port and make one request. returns (status, json or text)."""
    runner = web.AppRunner(api.make_app(pool, names, limiter))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession() as s, s.get(f"http://127.0.0.1:{port}{path}", headers=headers) as r:
            body = await r.json() if r.content_type == "application/json" else await r.text()
            return r.status, body
    finally:
        await runner.cleanup()


def test_codes_are_stored_hashed_and_replaced_on_reconnect(database):
    loop, pool = database

    async def go():
        await seed(pool)
        first = await tokens.create(pool, 1, AMA)
        stored = await pool.fetchval("SELECT token_hash FROM api_tokens")
        second = await tokens.create(pool, 1, AMA)
        return first, stored, second, await tokens.owner_of(pool, first), await tokens.owner_of(pool, second)

    first, stored, second, old_owner, new_owner = loop.run_until_complete(go())
    assert first.startswith("stash_") and first.encode() not in bytes(stored)  # only the hash is kept
    assert old_owner is None  # reconnecting turns the old code off
    assert new_owner == tokens.Owner(1, AMA)


def test_disconnect_turns_the_code_off(database):
    loop, pool = database

    async def go():
        await seed(pool)
        code = await tokens.create(pool, 1, AMA)
        gone = await tokens.revoke(pool, 1, AMA)
        again = await tokens.revoke(pool, 1, AMA)
        return gone, again, await tokens.owner_of(pool, code)

    assert loop.run_until_complete(go()) == (True, False, None)


def test_health_needs_no_code(database):
    loop, pool = database
    assert loop.run_until_complete(call(pool, "/health")) == (200, "ok")


def test_api_refuses_missing_or_wrong_codes(database):
    loop, pool = database

    async def go():
        await seed(pool)
        return (await call(pool, "/api/v1/links"))[0], (await call(pool, "/api/v1/links", "stash_nope"))[0]

    assert loop.run_until_complete(go()) == (401, 401)


def test_a_code_only_sees_its_owners_read_links(database):
    loop, pool = database

    async def go():
        await seed(pool)
        code = await tokens.create(pool, 1, AMA)
        return await call(pool, "/api/v1/links", code)

    status, body = loop.run_until_complete(go())
    assert status == 200 and body["mode"] == "personal" and body["which"] == "read"
    assert [f["name"] for f in body["files"]] == ["amas read.md"]  # not kofi's, not the other server's
    content = body["files"][0]["content"]
    assert content.startswith("---\n") and "stash_id: " in content and "saved_by" not in content


def test_shared_servers_get_the_whole_pile_with_savers(database):
    loop, pool = database

    async def go():
        await seed(pool, mode="shared")
        code = await tokens.create(pool, 1, AMA)
        return await call(pool, "/api/v1/links?which=all", code)

    status, body = loop.run_until_complete(go())
    assert sorted(f["name"] for f in body["files"]) == ["amas read.md", "amas unread.md", "kofis read.md"]
    assert all("saved_by: " in f["content"] for f in body["files"])


def test_bad_which_is_rejected(database):
    loop, pool = database

    async def go():
        await seed(pool)
        return (await call(pool, "/api/v1/links?which=everything", await tokens.create(pool, 1, AMA)))[0]

    assert loop.run_until_complete(go()) == 400


def test_rate_limit():
    now = [0.0]
    limiter = api.RateLimiter(limit=3, window=60, clock=lambda: now[0])
    assert [limiter.allow("code") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("other code")  # limits are per code
    now[0] = 61
    assert limiter.allow("code")  # and they reset after the window
