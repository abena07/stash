"""connection codes for the obsidian plugin (#39).

a code is shown once in discord and pasted into the plugin. only its sha-256
hash is stored, so a database leak doesn't reveal working codes.
"""

import hashlib
import secrets
from dataclasses import dataclass

import asyncpg

PREFIX = "stash_"


@dataclass(frozen=True)
class Owner:
    guild_id: int
    user_id: int


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def create(pool: asyncpg.Pool, guild_id: int, user_id: int, purpose: str = "obsidian") -> str:
    """a fresh code for this person in this server. any previous code for it stops working."""
    token = PREFIX + secrets.token_urlsafe(32)
    await pool.execute(
        """INSERT INTO api_tokens (guild_id, user_id, purpose, token_hash) VALUES ($1, $2, $3, $4)
           ON CONFLICT (guild_id, user_id, purpose)
           DO UPDATE SET token_hash = EXCLUDED.token_hash, created_at = now(), last_used_at = NULL""",
        guild_id, user_id, purpose, _hash(token),
    )
    return token


async def revoke(pool: asyncpg.Pool, guild_id: int, user_id: int, purpose: str = "obsidian") -> bool:
    result = await pool.execute(
        "DELETE FROM api_tokens WHERE guild_id = $1 AND user_id = $2 AND purpose = $3",
        guild_id, user_id, purpose,
    )
    return result != "DELETE 0"


async def owner_of(pool: asyncpg.Pool, token: str) -> Owner | None:
    """who a code belongs to, or None. also records when it was last used."""
    if not token.startswith(PREFIX):
        return None
    row = await pool.fetchrow(
        "UPDATE api_tokens SET last_used_at = now() WHERE token_hash = $1 RETURNING guild_id, user_id",
        _hash(token),
    )
    return Owner(row["guild_id"], row["user_id"]) if row else None
