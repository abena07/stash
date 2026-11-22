"""a server is either personal (each person their own queue) or shared (one pile for the group)."""

import asyncpg

PERSONAL, SHARED = "personal", "shared"
# drops for a shared server's group pile are stored under this user id (no real discord user has it)
GROUP = 0


async def mode(pool: asyncpg.Pool, guild_id: int) -> str:
    return await pool.fetchval("SELECT mode FROM guilds WHERE guild_id = $1", guild_id) or PERSONAL


async def is_shared(pool: asyncpg.Pool, guild_id: int) -> bool:
    return await mode(pool, guild_id) == SHARED
