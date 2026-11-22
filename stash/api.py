"""a small http api for the obsidian plugin (#39).

GET /health                       -> "ok"
GET /api/v1/links?which=read      -> the person's links as markdown files (same as /export)

every /api request needs `Authorization: Bearer <code from /connect obsidian>`.
a code only ever sees its owner's links, or the shared pile in a shared server.
"""

import logging
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

import asyncpg
from aiohttp import web

from . import export, modes, tokens

log = logging.getLogger(__name__)

RATE_LIMIT = 30          # requests…
RATE_WINDOW = 60.0       # …per this many seconds, per code
WHICH = {export.READ, export.LOVED, export.ALL}

# (guild id, user ids) -> {user id: display name}
NameLookup = Callable[[int, set[int]], Awaitable[dict[int, str]]]

POOL = web.AppKey("pool", asyncpg.Pool)
NAMES = web.AppKey("names", object)
LIMITER = web.AppKey("limiter", object)
OWNER = web.RequestKey("owner", tokens.Owner)


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        hits = self.hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


@web.middleware
async def auth(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not request.app[LIMITER].allow(token or request.remote or "anonymous"):
        return web.json_response({"error": "too many requests, slow down"}, status=429)
    owner = await tokens.owner_of(request.app[POOL], token) if token else None
    if owner is None:
        return web.json_response(
            {"error": "that code doesn't work. get a new one with /connect obsidian in discord"}, status=401
        )
    request[OWNER] = owner
    return await handler(request)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def links(request: web.Request) -> web.Response:
    owner = request[OWNER]
    which = request.query.get("which", export.READ)
    if which not in WHICH:
        return web.json_response({"error": f"which must be one of: {', '.join(sorted(WHICH))}"}, status=400)
    pool = request.app[POOL]
    shared = await modes.is_shared(pool, owner.guild_id)
    found = await export.fetch(pool, owner.guild_id, None if shared else owner.user_id, which)
    ids = {link.saver for link in found} | {n.author_id for link in found for n in link.notes}
    names = await request.app[NAMES](owner.guild_id, ids) if ids else {}
    files = export.files(found, lambda uid: names.get(uid, "someone"), show_saver=shared)
    return web.json_response({
        "mode": modes.SHARED if shared else modes.PERSONAL,
        "which": which,
        "files": [
            {"id": link.id, "name": name, "status": link.status, "loved": link.loved, "content": content}
            for name, content, link in files
        ],
    })


def make_app(pool: asyncpg.Pool, names: NameLookup, limiter: RateLimiter | None = None) -> web.Application:
    app = web.Application(middlewares=[auth])
    app[POOL] = pool
    app[NAMES] = names
    app[LIMITER] = limiter or RateLimiter()
    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/links", links)
    return app


async def start(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)  # no request logs: urls carry nothing secret, but keep it quiet
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("api listening on port %d", port)
    return runner
