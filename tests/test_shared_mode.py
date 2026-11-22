"""shared mode: one reading pile and one drop for the whole server."""

import itertools
from datetime import datetime, timezone
from types import SimpleNamespace

from stash import drops, reactions
from stash.cogs.links import save_links
from stash.modes import GROUP

CLUB, SOLO = 1, 2  # a shared server, and a personal one next door
AMA, KOFI, ESI = 10, 20, 30
WEDNESDAY = datetime(2026, 11, 18, 9, 1, tzinfo=timezone.utc)
_ids = itertools.count(5000)


def post(guild_id, user_id):
    return SimpleNamespace(id=next(_ids), guild=SimpleNamespace(id=guild_id), author=SimpleNamespace(id=user_id))


class Channel:
    def __init__(self):
        self.sent = []
        self.mentions = []

    async def send(self, content, **kwargs):
        self.sent.append(content)
        self.mentions.append(kwargs.get("allowed_mentions"))
        return SimpleNamespace(id=next(_ids))


async def setup(pool):
    await pool.execute("INSERT INTO guilds (guild_id, mode) VALUES ($1, 'shared'), ($2, 'personal')", CLUB, SOLO)
    await save_links(pool, post(CLUB, AMA), ["https://example.com/ama"])
    await save_links(pool, post(CLUB, KOFI), ["https://example.com/kofi"])
    await save_links(pool, post(SOLO, ESI), ["https://example.com/esi"])


def test_a_link_already_in_the_pile_is_a_duplicate_for_everyone(database):
    loop, pool = database

    async def go():
        await setup(pool)
        kofi_posts_amas = await save_links(pool, post(CLUB, KOFI), ["https://example.com/ama"])
        esi_posts_amas_elsewhere = await save_links(pool, post(SOLO, ESI), ["https://example.com/ama"])
        return kofi_posts_amas, esi_posts_amas_elsewhere

    (club_new, club_dupes), (solo_new, solo_dupes) = loop.run_until_complete(go())
    assert club_new == [] and len(club_dupes) == 1  # one pile, one copy
    assert len(solo_new) == 1 and solo_dupes == []  # the personal server next door is unaffected


def test_a_shared_server_gets_one_drop_for_the_group(database):
    loop, pool = database
    channels = {CLUB: Channel(), SOLO: Channel()}

    async def go():
        await setup(pool)

        async def get_channel(guild_id):
            return channels[guild_id]

        sent = await drops.run_due_drops(pool, WEDNESDAY, get_channel)
        club_drops = await pool.fetch("SELECT user_id FROM drops WHERE guild_id = $1", CLUB)
        return sent, [r["user_id"] for r in club_drops]

    sent, club_drop_users = loop.run_until_complete(go())
    assert sent == 2 and club_drop_users == [GROUP]  # one group drop + esi's personal one
    club = channels[CLUB].sent
    assert club[0] == "📬 @everyone wednesday's picks"
    assert sorted(m.split("saved by ")[1].split(">")[0] + ">" for m in club[1:]) == ["<@10>", "<@20>"]
    assert channels[SOLO].sent[0] == "📬 <@30> your wednesday picks"
    header, *picks = channels[CLUB].mentions
    assert header.everyone is True  # @everyone notifies the server
    assert all(m.users is False and m.everyone is False for m in picks)  # @everyone already reached them


def test_anyones_reaction_counts_for_the_group(database):
    loop, pool = database

    async def go():
        await setup(pool)
        pile = await drops.unread_links(pool, CLUB, None)
        amas = next(link for link in pile if link.saver == AMA)
        await drops.send_on_demand(pool, Pick(777), CLUB, KOFI, [amas], "📬 picks from the pile", credit=True)
        kofi_reads_amas = await reactions.apply(pool, CLUB, 777, KOFI, "read", True, shared=True)
        status = await pool.fetchval("SELECT status FROM links WHERE id = $1", amas.id)
        return kofi_reads_amas, status

    assert loop.run_until_complete(go()) == (1, "read")


def test_shared_reactions_still_stay_in_their_server(database):
    loop, pool = database

    async def go():
        await setup(pool)
        esis = (await drops.unread_links(pool, SOLO, ESI))[0]
        await drops.send_on_demand(pool, Pick(888), SOLO, ESI, [esis], "📬 your picks")
        # a reaction arriving with the club's id and shared=True must not reach the other server's link
        return await reactions.apply(pool, CLUB, 888, KOFI, "read", True, shared=True)

    assert loop.run_until_complete(go()) == 0


def test_the_pile_is_everyones_unread_links(database):
    loop, pool = database

    async def go():
        await setup(pool)
        return {link.url for link in await drops.unread_links(pool, CLUB, None)}

    assert loop.run_until_complete(go()) == {"https://example.com/ama", "https://example.com/kofi"}


def test_saved_by_is_shown_only_when_asked():
    link = drops.Candidate(1, "https://a.com/x", "X", None, 0, WEDNESDAY, (), AMA)
    assert drops.format_pick(1, link, credit=True) == "**1. X** · saved by <@10>\nhttps://a.com/x"
    assert drops.format_pick(1, link) == "**1. X**\nhttps://a.com/x"


class Pick(Channel):
    """header first, then the pick as message `pick_id`."""

    def __init__(self, pick_id):
        super().__init__()
        self.pick_id = pick_id

    async def send(self, content, **kwargs):
        self.sent.append(content)
        return SimpleNamespace(id=self.pick_id if len(self.sent) > 1 else next(_ids))


def test_on_demand_picks_notify_the_saver(database):
    loop, pool = database
    channel = Channel()

    async def go():
        await setup(pool)
        pile = await drops.unread_links(pool, CLUB, None)
        await drops.send_on_demand(pool, channel, CLUB, KOFI, pile[:1], "📬 picks from the pile", credit=True)

    loop.run_until_complete(go())
    header, pick = channel.mentions
    assert header.everyone is False  # /picks doesn't ping the server
    assert pick.users is True  # so "saved by @person" is their one notification
