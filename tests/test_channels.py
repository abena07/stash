"""choosing the read-later channel: /setup channel, falling back to #read-later."""

from types import SimpleNamespace

import discord

from stash.channels import ReadLaterChannels
from stash.cogs.setup import missing_permissions

GUILD = 1


class Text(discord.TextChannel):
    def __init__(self, id, name):  # skip discord.py's own setup
        self.id, self.name = id, name
        self.guild = SimpleNamespace(id=GUILD)


class Thread(discord.Thread):
    def __init__(self, id):
        self.id = id
        self.guild = SimpleNamespace(id=GUILD)


READ_LATER, LINKS, GENERAL = Text(100, "read-later"), Text(200, "links"), Text(300, "general")


class Guild:
    id = GUILD
    text_channels = [GENERAL, READ_LATER, LINKS]

    def get_channel(self, channel_id):
        return next((c for c in self.text_channels if c.id == channel_id), None)


def test_default_is_the_channel_named_read_later(database):
    loop, pool = database

    async def go():
        await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1)", GUILD)
        channels = ReadLaterChannels(pool)
        return (
            await channels.is_read_later(READ_LATER),
            await channels.is_read_later(LINKS),
            await channels.is_read_later(Thread(101)),  # threads hold notes, not new links
            (await channels.find(Guild())).id,
        )

    assert loop.run_until_complete(go()) == (True, False, False, 100)


def test_a_chosen_channel_replaces_the_default(database):
    loop, pool = database

    async def go():
        await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1)", GUILD)
        channels = ReadLaterChannels(pool)
        await channels.is_read_later(READ_LATER)  # warm the cache with the default
        await channels.choose(GUILD, LINKS.id)
        stored = await pool.fetchval("SELECT read_later_channel_id FROM guilds WHERE guild_id = $1", GUILD)
        fresh = ReadLaterChannels(pool)  # e.g. after a restart
        return (
            stored,
            await channels.is_read_later(LINKS),
            await channels.is_read_later(READ_LATER),
            (await channels.find(Guild())).id,
            await fresh.is_read_later(LINKS),
        )

    assert loop.run_until_complete(go()) == (200, True, False, 200, True)


def test_a_deleted_chosen_channel_is_not_found(database):
    loop, pool = database

    async def go():
        await pool.execute("INSERT INTO guilds (guild_id, read_later_channel_id) VALUES ($1, 999)", GUILD)
        return await ReadLaterChannels(pool).find(Guild())

    assert loop.run_until_complete(go()) is None  # the drop then fails with a reason pointing at /setup


class Perms:
    def __init__(self, **missing):
        self.missing = missing

    def __getattr__(self, flag):
        return not self.missing.get(flag, False)


class Channel:
    def __init__(self, perms):
        self.perms = perms

    def permissions_for(self, me):
        return self.perms


def test_missing_permissions_are_listed_plainly():
    assert missing_permissions(Channel(Perms()), None) == ""
    assert missing_permissions(Channel(Perms(add_reactions=True)), None) == "**add reactions**"
    assert missing_permissions(
        Channel(Perms(create_public_threads=True, send_messages_in_threads=True, view_channel=True)), None
    ) == "**view channel**, **create public threads** and **send messages in threads**"
