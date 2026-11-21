import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from .. import drops
from .links import READ_LATER_CHANNEL

log = logging.getLogger(__name__)


class Drops(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self) -> None:
        try:
            await drops.run_due_drops(self.bot.pool, datetime.now(timezone.utc), self._channel)
        except Exception:
            # keep the loop alive: one bad tick shouldn't stop future drops
            log.exception("drop tick failed")

    @tick.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _channel(self, guild_id: int) -> discord.TextChannel | None:
        # /setup (#8) will read guilds.read_later_channel_id here first
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        return discord.utils.get(guild.text_channels, name=READ_LATER_CHANNEL)


async def setup(bot) -> None:
    await bot.add_cog(Drops(bot))
