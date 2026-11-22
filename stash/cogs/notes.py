import logging

import discord
from discord.ext import commands

from .. import notes

log = logging.getLogger(__name__)


class Notes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not isinstance(message.channel, discord.Thread):
            return
        if await notes.save(self.bot.pool, message.channel.id, message.id, message.author.id, message.content):
            log.info("guild %s: saved a note in thread %s", message.guild.id, message.channel.id)

    # raw events so edits and deletes of older messages are seen too
    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        content = payload.data.get("content")
        if content is None:
            return  # embed-only update (e.g. a link preview loading), not a text edit
        await notes.edit(self.bot.pool, payload.message_id, content)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await notes.delete(self.bot.pool, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        await notes.delete_thread(self.bot.pool, payload.thread_id)


async def setup(bot) -> None:
    await bot.add_cog(Notes(bot))
