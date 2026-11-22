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
                action: str, added: bool, shared: bool = False) -> int:
    """update the links behind a reacted message. returns how many changed.

    works on the original post in the read-later channel, and on a drop message holding a
    single pick. a message with several picks uses buttons instead, since a reaction there
    couldn't say which pick it means. in a personal server only the link's owner can change it;
    in a shared server anyone's reaction counts for the whole group.
    """
    link_ids = await pool.fetch(
        """SELECT l.id FROM drop_items i
             JOIN links l ON l.id = i.link_id
            WHERE i.discord_message_id = $1 AND l.guild_id = $2 AND ($4 OR l.user_id = $3)
              AND (SELECT count(*) FROM drop_items j WHERE j.discord_message_id = $1) = 1
           UNION
           SELECT id FROM links
            WHERE source_message_id = $1 AND guild_id = $2 AND ($4 OR user_id = $3)""",
        message_id, guild_id, user_id, shared,
    )
    return await set_state(pool, [r["id"] for r in link_ids], action, added)


async def set_state(pool: asyncpg.Pool, link_ids: list[int], action: str, on: bool) -> int:
    """mark links read/unread or loved/unloved. returns how many changed."""
    if not link_ids:
        return 0
    if action == "read":
        # undoing only reverts read → unread, never touches archived links
        old, new = ("unread", "read") if on else ("read", "unread")
        result = await pool.execute(
            # read_at records when it was read (for the obsidian export); undoing clears it
            """UPDATE links SET status = $2::link_status,
                                 read_at = CASE WHEN $2::link_status = 'read' THEN now() END
               WHERE id = ANY($1::bigint[]) AND status = $3::link_status""",
            link_ids, new, old,
        )
    else:
        result = await pool.execute(
            # loved_at records when (for the monthly roundup); un-loving clears it
            """UPDATE links SET loved = $2, loved_at = CASE WHEN $2 THEN now() END
               WHERE id = ANY($1::bigint[]) AND loved <> $2""",
            link_ids, on,
        )
    return int(result.split()[-1])
