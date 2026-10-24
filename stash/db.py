import logging
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def connect(dsn: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    await migrate(pool)
    return pool


async def migrate(pool: asyncpg.Pool) -> None:
    """apply migrations/*.sql in filename order, each once, in its own transaction."""
    async with pool.acquire() as conn:
        # serialize concurrent boots (e.g. overlapping railway deploys)
        await conn.execute("SELECT pg_advisory_lock(727274)")
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       name text PRIMARY KEY,
                       applied_at timestamptz NOT NULL DEFAULT now())"""
            )
            applied = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in applied:
                    continue
                log.info("applying migration %s", path.name)
                async with conn.transaction():
                    await conn.execute(path.read_text())
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727274)")
