import logging

import discord
from discord.ext import commands

from .. import modes, reactions

log = logging.getLogger(__name__)


class Reactions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # raw events fire for every message, not only ones still in the bot's cache
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle(payload, added=False)

    async def _handle(self, payload: discord.RawReactionActionEvent, added: bool) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        action = reactions.action_for(str(payload.emoji))
        if action is None:
            return
        shared = await modes.is_shared(self.bot.pool, payload.guild_id)
        changed = await reactions.apply(
            self.bot.pool, payload.guild_id, payload.message_id, payload.user_id, action, added, shared
        )
        if changed:
            log.info(
                "guild %s: %s %s on %d link(s)",
                payload.guild_id, "set" if added else "unset", action, changed,
            )


async def setup(bot) -> None:
    await bot.add_cog(Reactions(bot))
