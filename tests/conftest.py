import asyncio
import os

import asyncpg
import pytest

from stash import db

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/stash_test")


@pytest.fixture
def database():
    async def connect():
        try:
            pool = await db.connect(TEST_DSN)
        except (OSError, asyncpg.PostgresError) as e:
            pytest.skip(f"no test database: {e}")
        await pool.execute("TRUNCATE guilds CASCADE")
        return pool

    loop = asyncio.new_event_loop()
    pool = loop.run_until_complete(connect())
    yield loop, pool
    loop.run_until_complete(pool.close())
    loop.close()
