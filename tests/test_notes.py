from stash import notes
from stash.cogs.links import THREAD_NAME_LIMIT, thread_name

THREAD = 700


def test_thread_name_uses_the_title_or_site_name():
    assert thread_name("Inside .git", "https://jvns.ca/a") == "Inside .git"
    assert thread_name(None, "https://www.example.com/a") == "example.com"


def test_long_thread_names_fit_discords_limit():
    name = thread_name("x" * 300, "https://example.com")
    assert len(name) == THREAD_NAME_LIMIT and name.endswith("…")


async def seed(pool, links=1):
    await pool.execute("INSERT INTO guilds (guild_id) VALUES (1)")
    ids = []
    for i in range(links):
        ids.append(await pool.fetchval(
            """INSERT INTO links (guild_id, user_id, url, url_normalized, thread_id)
               VALUES (1, 10, $1, $1, $2) RETURNING id""",
            f"https://example.com/{i}", THREAD,
        ))
    return ids


async def all_notes(pool):
    return [tuple(r) for r in await pool.fetch(
        "SELECT link_id, author_id, discord_message_id, content FROM notes ORDER BY id"
    )]


def test_thread_messages_become_notes(database):
    loop, pool = database

    async def go():
        (link_id,) = await seed(pool)
        assert await notes.save(pool, THREAD, 1, 10, "good intro, skip part 3")
        assert await notes.save(pool, THREAD, 2, 20, "a friend's note")
        return link_id, await all_notes(pool)

    link_id, saved = loop.run_until_complete(go())
    assert saved == [(link_id, 10, 1, "good intro, skip part 3"), (link_id, 20, 2, "a friend's note")]


def test_other_threads_and_empty_messages_are_ignored(database):
    loop, pool = database

    async def go():
        await seed(pool)
        unknown = await notes.save(pool, 999, 1, 10, "not a notes thread")
        empty = await notes.save(pool, THREAD, 2, 10, "   ")  # e.g. an image with no text
        return unknown, empty, await all_notes(pool)

    assert loop.run_until_complete(go()) == (False, False, [])


def test_notes_on_a_multi_link_post_go_to_the_first_link(database):
    loop, pool = database

    async def go():
        first, _second = await seed(pool, links=2)
        await notes.save(pool, THREAD, 1, 10, "note")
        return first, await all_notes(pool)

    first, saved = loop.run_until_complete(go())
    assert [n[0] for n in saved] == [first]


def test_edits_and_deletes_follow_the_message(database):
    loop, pool = database

    async def go():
        await seed(pool)
        await notes.save(pool, THREAD, 1, 10, "first draft")
        await notes.save(pool, THREAD, 2, 10, "will be emptied")
        await notes.save(pool, THREAD, 3, 10, "will be deleted")
        await notes.edit(pool, 1, "second draft")
        await notes.edit(pool, 2, "")  # editing to nothing removes the note
        await notes.delete(pool, 3)
        unrelated = await notes.edit(pool, 999, "not a note")
        return unrelated, [n[3] for n in await all_notes(pool)]

    assert loop.run_until_complete(go()) == (False, ["second draft"])


def test_deleting_the_thread_keeps_the_link(database):
    loop, pool = database

    async def go():
        (link_id,) = await seed(pool)
        await notes.save(pool, THREAD, 1, 10, "note")
        await notes.delete_thread(pool, THREAD)
        link = await pool.fetchrow("SELECT thread_id FROM links WHERE id = $1", link_id)
        return link["thread_id"], await all_notes(pool)

    assert loop.run_until_complete(go()) == (None, [])
