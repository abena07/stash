"""notes (#5): messages posted in a saved link's thread are kept as notes on the link.

a post with several links gets one thread (discord allows one per message),
so its notes attach to the first of those links.
"""

import asyncpg


async def save(pool: asyncpg.Pool, thread_id: int, message_id: int, author_id: int,
               content: str) -> bool:
    """store a thread message as a note. returns False if the thread isn't a notes thread."""
    link_id = await pool.fetchval("SELECT min(id) FROM links WHERE thread_id = $1", thread_id)
    if link_id is None or not content.strip():
        return False
    await pool.execute(
        """INSERT INTO notes (link_id, author_id, discord_message_id, content)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (discord_message_id) DO NOTHING""",
        link_id, author_id, message_id, content,
    )
    return True


async def edit(pool: asyncpg.Pool, message_id: int, content: str) -> bool:
    if not content.strip():
        return await delete(pool, message_id)
    result = await pool.execute(
        "UPDATE notes SET content = $2 WHERE discord_message_id = $1", message_id, content
    )
    return result != "UPDATE 0"


async def delete(pool: asyncpg.Pool, message_id: int) -> bool:
    result = await pool.execute("DELETE FROM notes WHERE discord_message_id = $1", message_id)
    return result != "DELETE 0"


async def delete_thread(pool: asyncpg.Pool, thread_id: int) -> None:
    """the thread itself was deleted: forget it and its notes, keep the link."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM notes WHERE link_id IN (SELECT id FROM links WHERE thread_id = $1)", thread_id
        )
        await conn.execute("UPDATE links SET thread_id = NULL WHERE thread_id = $1", thread_id)
