import asyncio
import os

import asyncpg
import pytest

from stash import db

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/stash_test")
# ci sets this so a missing database fails the run instead of quietly skipping half the tests
REQUIRE_DB = os.environ.get("REQUIRE_TEST_DB") == "1"


@pytest.fixture
def database():
    async def connect():
        try:
            pool = await db.connect(TEST_DSN)
        except (OSError, asyncpg.PostgresError) as e:
            if REQUIRE_DB:
                pytest.fail(f"no test database at {TEST_DSN}: {e}")
            pytest.skip(f"no test database: {e}")
        await pool.execute("TRUNCATE guilds CASCADE")
        # tests use fixed drop times (november 2026), so links they save count as saved before those
        await pool.execute("ALTER TABLE links ALTER COLUMN created_at SET DEFAULT '2026-11-01'")
        return pool

    loop = asyncio.new_event_loop()
    pool = loop.run_until_complete(connect())
    yield loop, pool
    loop.run_until_complete(pool.close())
    loop.close()
