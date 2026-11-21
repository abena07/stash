import pytest

from stash.reactions import LOVED, READ, action_for, apply

GUILD, OWNER, FRIEND = 1, 10, 20
PICK_MSG, POST_MSG = 500, 600


@pytest.mark.parametrize(
    "emoji, expected",
    [
        ("✅", "read"),
        ("👍🏿", "loved"),
        ("👍", "loved"),  # default yellow still counts
        ("👍🏻", "loved"),
        ("👍🏽", "loved"),
        ("👍️", "loved"),
        ("🔖", None),
        ("❤️", None),
    ],
)
def test_action_for(emoji, expected):
    assert action_for(emoji) == expected


def test_bot_uses_the_darkest_tone():
    assert LOVED == "👍\U0001F3FF" and READ == "✅"


async def seed(pool):
    await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1)", GUILD)
    link_id = await pool.fetchval(
        """INSERT INTO links (guild_id, user_id, url, url_normalized, source_message_id)
           VALUES ($1, $2, 'https://example.com/a', 'https://example.com/a', $3) RETURNING id""",
        GUILD, OWNER, POST_MSG,
    )
    drop_id = await pool.fetchval(
        "INSERT INTO drops (guild_id, user_id, scheduled_for) VALUES ($1, $2, now()) RETURNING id",
        GUILD, OWNER,
    )
    await pool.execute(
        "INSERT INTO drop_items (drop_id, link_id, discord_message_id) VALUES ($1, $2, $3)",
        drop_id, link_id, PICK_MSG,
    )
    return link_id


async def state(pool, link_id):
    row = await pool.fetchrow("SELECT status, loved FROM links WHERE id = $1", link_id)
    return row["status"], row["loved"]


def test_read_and_undo(database):
    loop, pool = database

    async def go():
        link_id = await seed(pool)
        assert await apply(pool, GUILD, PICK_MSG, OWNER, "read", True) == 1
        after_add = await state(pool, link_id)
        assert await apply(pool, GUILD, PICK_MSG, OWNER, "read", False) == 1
        return after_add, await state(pool, link_id)

    assert loop.run_until_complete(go()) == (("read", False), ("unread", False))


def test_loved_and_undo(database):
    loop, pool = database

    async def go():
        link_id = await seed(pool)
        await apply(pool, GUILD, PICK_MSG, OWNER, "loved", True)
        after_add = await state(pool, link_id)
        await apply(pool, GUILD, PICK_MSG, OWNER, "loved", False)
        return after_add, await state(pool, link_id)

    assert loop.run_until_complete(go()) == (("unread", True), ("unread", False))


def test_reacting_on_the_original_post_works_too(database):
    loop, pool = database

    async def go():
        link_id = await seed(pool)
        await apply(pool, GUILD, POST_MSG, OWNER, "read", True)
        return await state(pool, link_id)

    assert loop.run_until_complete(go()) == ("read", False)


def test_only_the_owner_can_mark_their_link(database):
    loop, pool = database

    async def go():
        link_id = await seed(pool)
        changed = await apply(pool, GUILD, PICK_MSG, FRIEND, "read", True)
        return changed, await state(pool, link_id)

    assert loop.run_until_complete(go()) == (0, ("unread", False))


def test_undoing_read_leaves_archived_links_alone(database):
    loop, pool = database

    async def go():
        link_id = await seed(pool)
        await pool.execute("UPDATE links SET status = 'archived'")
        await apply(pool, GUILD, PICK_MSG, OWNER, "read", False)
        return await state(pool, link_id)

    assert loop.run_until_complete(go()) == ("archived", False)


def test_unrelated_messages_do_nothing(database):
    loop, pool = database

    async def go():
        await seed(pool)
        return await apply(pool, GUILD, 999, OWNER, "read", True)

    assert loop.run_until_complete(go()) == 0
