"""reactions on picks (#7): ✅ marks a link read, 👍🏿 marks it loved. removing one undoes it."""

import re

import asyncpg

READ = "✅"
LOVED = "👍🏿"  # darkest skin tone wherever the bot shows it; any tone counts when people react

SKIN_TONES = re.compile("[\U0001F3FB-\U0001F3FF]")
VARIATION_SELECTOR = "️"


def action_for(emoji: str) -> str | None:
    """'read', 'loved' or None, ignoring skin tone and the emoji variation selector."""
    base = SKIN_TONES.sub("", emoji).replace(VARIATION_SELECTOR, "")
    if base == READ:
        return "read"
    if base == "👍":
        return "loved"
    return None


async def apply(pool: asyncpg.Pool, guild_id: int, message_id: int, user_id: int,
                action: str, added: bool) -> int:
    """update the user's links behind this message. returns how many changed.

    works on a drop pick and on the user's original post in #read-later. only
    the link's owner can change it, so a friend's reaction does nothing.
    """
    link_ids = await pool.fetch(
        """SELECT l.id FROM drop_items i
             JOIN links l ON l.id = i.link_id
            WHERE i.discord_message_id = $1 AND l.guild_id = $2 AND l.user_id = $3
           UNION
           SELECT id FROM links
            WHERE source_message_id = $1 AND guild_id = $2 AND user_id = $3""",
        message_id, guild_id, user_id,
    )
    ids = [r["id"] for r in link_ids]
    if not ids:
        return 0

    if action == "read":
        # undoing only reverts read → unread, never touches archived links
        old, new = ("unread", "read") if added else ("read", "unread")
        result = await pool.execute(
            "UPDATE links SET status = $2 WHERE id = ANY($1::bigint[]) AND status = $3",
            ids, new, old,
        )
    else:
        result = await pool.execute(
            "UPDATE links SET loved = $2 WHERE id = ANY($1::bigint[]) AND loved <> $2",
            ids, added,
        )
    return int(result.split()[-1])
