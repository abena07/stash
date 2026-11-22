"""which channel is a server's read-later channel.

a server can pick one with /setup channel. otherwise it's the text channel
named #read-later. the choice is cached so checking every message doesn't
hit the database.
"""

import asyncpg
import discord

DEFAULT_NAME = "read-later"


class ReadLaterChannels:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._chosen: dict[int, int | None] = {}  # guild id -> chosen channel id, or None for the default

    async def chosen_id(self, guild_id: int) -> int | None:
        if guild_id not in self._chosen:
            self._chosen[guild_id] = await self.pool.fetchval(
                "SELECT read_later_channel_id FROM guilds WHERE guild_id = $1", guild_id
            )
        return self._chosen[guild_id]

    async def choose(self, guild_id: int, channel_id: int | None) -> None:
        await self.pool.execute(
            "UPDATE guilds SET read_later_channel_id = $2 WHERE guild_id = $1", guild_id, channel_id
        )
        self._chosen[guild_id] = channel_id

    async def is_read_later(self, channel) -> bool:
        # threads are excluded on purpose: replies there are notes (#5)
        if not isinstance(channel, discord.TextChannel):
            return False
        chosen = await self.chosen_id(channel.guild.id)
        return channel.id == chosen if chosen else channel.name == DEFAULT_NAME

    async def find(self, guild: discord.Guild) -> discord.TextChannel | None:
        chosen = await self.chosen_id(guild.id)
        if chosen:
            channel = guild.get_channel(chosen)
            return channel if isinstance(channel, discord.TextChannel) else None
        return discord.utils.get(guild.text_channels, name=DEFAULT_NAME)
