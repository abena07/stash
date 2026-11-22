"""#10: two fake servers and two fake people. nobody may see or change anyone else's data.

server 1 is kofi's (20). he invited his friend ama (10), so they share it.
server 2 is ama's own. so ama is in both servers, and kofi only in his.
"""

import itertools
from datetime import datetime, timezone
from types import SimpleNamespace

from stash import drops, notes, reactions
from stash.cogs.links import save_links

S1, S2 = 1, 2
AMA, KOFI = 10, 20
URL = "https://example.com/shared"  # the same page, saved in every corner
DROP_TIME = datetime(2026, 11, 18, 9, 1, tzinfo=timezone.utc)  # a wednesday, just after 09:00

_message_ids = itertools.count(9000)


def message(guild_id, user_id):
    return SimpleNamespace(
        id=next(_message_ids),
        guild=SimpleNamespace(id=guild_id),
        author=SimpleNamespace(id=user_id),
    )


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append(content)
        return SimpleNamespace(id=next(_message_ids))  # unique across channels, like real discord ids


async def setup(pool):
    """every corner saves the same url plus one link of its own."""
    for guild_id in (S1, S2):
        await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1)", guild_id)
    corners = {(S1, AMA): "ama-s1", (S1, KOFI): "kofi-s1", (S2, AMA): "ama-s2"}
    for (guild_id, user_id), name in corners.items():
        new, dupes = await save_links(pool, message(guild_id, user_id), [URL, f"https://example.com/{name}"])
        assert len(new) == 2 and dupes == [], "the same url in another corner is not a duplicate"
    return corners


async def urls_of(pool, guild_id, user_id):
    return {link.url for link in await drops.unread_links(pool, guild_id, user_id)}


def test_each_corner_keeps_its_own_links(database):
    loop, pool = database

    async def go():
        await setup(pool)
        # saving the shared url again in one corner is a duplicate there and only there
        new, dupes = await save_links(pool, message(S1, AMA), [URL])
        assert new == [] and len(dupes) == 1
        return {
            "ama-s1": await urls_of(pool, S1, AMA),
            "kofi-s1": await urls_of(pool, S1, KOFI),
            "ama-s2": await urls_of(pool, S2, AMA),
        }

    seen = loop.run_until_complete(go())
    assert seen == {
        "ama-s1": {URL, "https://example.com/ama-s1"},
        "kofi-s1": {URL, "https://example.com/kofi-s1"},
        "ama-s2": {URL, "https://example.com/ama-s2"},
    }


def test_drops_only_contain_your_own_links(database):
    loop, pool = database
    channels = {S1: Channel(), S2: Channel()}

    async def go():
        await setup(pool)

        async def get_channel(guild_id):
            return channels[guild_id]

        sent = await drops.run_due_drops(pool, DROP_TIME, get_channel)
        rows = await pool.fetch(
            """SELECT d.guild_id, d.user_id, l.guild_id AS link_guild, l.user_id AS link_user
               FROM drops d JOIN drop_items i ON i.drop_id = d.id JOIN links l ON l.id = i.link_id"""
        )
        return sent, rows

    sent, rows = loop.run_until_complete(go())
    assert sent == 3  # one drop per person per server
    assert rows and all((r["guild_id"], r["user_id"]) == (r["link_guild"], r["link_user"]) for r in rows)
    assert not any("kofi" in m for m in channels[S2].sent)  # kofi isn't in server 2
    assert not any("ama-s2" in m for m in channels[S1].sent)


def test_a_schedule_change_only_affects_its_own_server(database):
    loop, pool = database
    channels = {S1: Channel(), S2: Channel()}

    async def go():
        await setup(pool)
        await pool.execute("UPDATE guilds SET drop_days = '{5}' WHERE guild_id = $1", S2)  # fridays only

        async def get_channel(guild_id):
            return channels[guild_id]

        await drops.run_due_drops(pool, DROP_TIME, get_channel)  # a wednesday
        return await pool.fetch("SELECT DISTINCT guild_id FROM drops")

    assert [r["guild_id"] for r in loop.run_until_complete(go())] == [S1]
    assert channels[S2].sent == []


def test_reactions_only_change_your_own_links(database):
    loop, pool = database

    async def go():
        await setup(pool)
        ama_s1 = await drops.unread_links(pool, S1, AMA)
        ama_shared = next(link for link in ama_s1 if link.url == URL)
        pick_message = 555
        await drops.send_on_demand(pool, ReplyWith(pick_message), S1, AMA, [ama_shared], "📬 your picks")

        kofi_on_amas_pick = await reactions.apply(pool, S1, pick_message, KOFI, "read", True)
        ama_from_wrong_server = await reactions.apply(pool, S2, pick_message, AMA, "read", True)
        ama_herself = await reactions.apply(pool, S1, pick_message, AMA, "loved", True)

        loved = await pool.fetch("SELECT guild_id, user_id FROM links WHERE loved")
        read = await pool.fetchval("SELECT count(*) FROM links WHERE status = 'read'")
        return kofi_on_amas_pick, ama_from_wrong_server, ama_herself, loved, read

    kofi, wrong_server, ama, loved, read = loop.run_until_complete(go())
    assert (kofi, wrong_server, ama) == (0, 0, 1)
    assert [(r["guild_id"], r["user_id"]) for r in loved] == [(S1, AMA)]  # only her shared link in server 1
    assert read == 0


class ReplyWith(Channel):
    """posts the header, then the pick as message `pick_id`."""

    def __init__(self, pick_id):
        super().__init__()
        self.pick_id = pick_id

    async def send(self, content, **kwargs):
        self.sent.append(content)
        return SimpleNamespace(id=self.pick_id if len(self.sent) > 1 else 1)


def test_loved_tags_are_per_person(database):
    loop, pool = database

    async def go():
        await setup(pool)
        await pool.execute(
            "UPDATE links SET tags = '{design}', loved = true WHERE guild_id = $1 AND user_id = $2", S1, KOFI
        )
        return (
            await drops.loved_tags(pool, S1, KOFI),
            await drops.loved_tags(pool, S1, AMA),
            await drops.loved_tags(pool, S2, AMA),
        )

    assert loop.run_until_complete(go()) == ({"design"}, set(), set())


def test_archiving_only_touches_your_own_links(database):
    loop, pool = database
    channels = {S1: Channel(), S2: Channel()}

    async def go():
        await setup(pool)
        await pool.execute("UPDATE links SET times_sent = 3")  # everyone's links are stale
        await pool.execute(
            "UPDATE guilds SET drop_days = '{5}' WHERE guild_id = $1", S2
        )  # only server 1 drops today

        async def get_channel(guild_id):
            return channels[guild_id]

        await drops.run_due_drops(pool, DROP_TIME, get_channel)
        return await pool.fetch("SELECT guild_id, status, count(*) AS n FROM links GROUP BY 1, 2 ORDER BY 1, 2")

    rows = [tuple(r) for r in loop.run_until_complete(go())]
    assert rows == [(S1, "archived", 4), (S2, "unread", 2)]


def test_notes_attach_to_the_right_link(database):
    loop, pool = database

    async def go():
        await setup(pool)
        ama_s1 = (await drops.unread_links(pool, S1, AMA))[0]
        ama_s2 = (await drops.unread_links(pool, S2, AMA))[0]
        await pool.execute("UPDATE links SET thread_id = 701 WHERE id = $1", ama_s1.id)
        await pool.execute("UPDATE links SET thread_id = 702 WHERE id = $1", ama_s2.id)
        await notes.save(pool, 701, 1, AMA, "a note in server 1")
        await notes.save(pool, 702, 2, AMA, "a note in server 2")
        rows = await pool.fetch(
            "SELECT l.guild_id, n.content FROM notes n JOIN links l ON l.id = n.link_id ORDER BY n.id"
        )
        return [tuple(r) for r in rows]

    assert loop.run_until_complete(go()) == [(S1, "a note in server 1"), (S2, "a note in server 2")]
