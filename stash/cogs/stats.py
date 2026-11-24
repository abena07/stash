"""weekly usage summary, posted to the host alert webhook (ALERT_WEBHOOK_URL) every monday.

the host can also ask any time with "@stash stats". it's a mention command, not a slash
command, so it doesn't show up in anyone's command list, and only the bot's owner can run it.
"""

import logging
from datetime import datetime, time, timezone

import aiohttp
from discord.ext import commands, tasks

from .. import alerts, stats

log = logging.getLogger(__name__)

POST_AT = time(9, 0, tzinfo=timezone.utc)
MONDAY = 1


class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.http = aiohttp.ClientSession()
        self.weekly.start()

    async def cog_unload(self) -> None:
        self.weekly.cancel()
        await self.http.close()

    async def summary(self) -> str:
        now = datetime.now(timezone.utc)
        return stats.message(await stats.usage(self.bot.pool, len(self.bot.guilds), now))

    @tasks.loop(time=POST_AT)
    async def weekly(self) -> None:
        if datetime.now(timezone.utc).isoweekday() != MONDAY:
            return
        try:
            await alerts.tell_host(self.http, self.bot.config.alert_webhook_url, await self.summary())
            log.info("weekly stats posted")
        except Exception:
            log.exception("weekly stats failed")

    @weekly.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @commands.command(name="stats", hidden=True)
    @commands.is_owner()
    async def stats_now(self, ctx: commands.Context) -> None:
        await ctx.reply(await self.summary(), mention_author=False)

    @stats_now.error
    async def _not_owner(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if not isinstance(error, commands.NotOwner):
            raise error  # only the owner gets an answer; everyone else is ignored


async def setup(bot) -> None:
    await bot.add_cog(Stats(bot))
