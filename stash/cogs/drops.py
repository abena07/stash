import logging
import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import drops
from .links import READ_LATER_CHANNEL

log = logging.getLogger(__name__)

NOTHING_UNREAD = "nothing unread right now. post a link in #read-later to save it."


class InteractionChannel:
    """lets post_picks send through a slash command: the first message answers the command."""

    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.answered = False

    async def send(self, content: str, **kwargs):
        if not self.answered:
            self.answered = True
            await self.interaction.response.send_message(content, **kwargs)
            return await self.interaction.original_response()
        return await self.interaction.followup.send(content, wait=True, **kwargs)


class Drops(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._timezones = sorted(available_timezones())

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
        # /setup will read guilds.read_later_channel_id here first
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        return discord.utils.get(guild.text_channels, name=READ_LATER_CHANNEL)

    @app_commands.command(description="get a couple of picks from your saved links now")
    @app_commands.guild_only()
    async def picks(self, interaction: discord.Interaction) -> None:
        links = await drops.unread_links(self.bot.pool, interaction.guild_id, interaction.user.id)
        if not links:
            await interaction.response.send_message(NOTHING_UNREAD, ephemeral=True)
            return
        chosen = drops.pick(links, datetime.now(timezone.utc))
        await drops.send_on_demand(
            self.bot.pool, InteractionChannel(interaction), interaction.guild_id,
            interaction.user.id, chosen, "📬 your picks",
        )

    @app_commands.command(description="get one random link from your saved links")
    @app_commands.guild_only()
    async def random(self, interaction: discord.Interaction) -> None:
        links = await drops.unread_links(self.bot.pool, interaction.guild_id, interaction.user.id)
        if not links:
            await interaction.response.send_message(NOTHING_UNREAD, ephemeral=True)
            return
        await drops.send_on_demand(
            self.bot.pool, InteractionChannel(interaction), interaction.guild_id,
            interaction.user.id, [random.choice(links)], "🎲 a random pick",
        )

    @app_commands.command(description="see or change when drops are sent")
    @app_commands.describe(
        days="e.g. 'wed sun', 'monday, friday', 'weekdays' or 'daily'",
        hour="hour of the day, 0-23",
        timezone="e.g. Europe/London or Africa/Accra",
    )
    @app_commands.guild_only()
    async def schedule(
        self,
        interaction: discord.Interaction,
        days: str | None = None,
        hour: app_commands.Range[int, 0, 23] | None = None,
        timezone: str | None = None,
    ) -> None:
        pool = self.bot.pool
        await pool.execute(
            "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", interaction.guild_id
        )
        current = await pool.fetchrow(
            "SELECT drop_days, drop_hour, timezone FROM guilds WHERE guild_id = $1", interaction.guild_id
        )
        if days is None and hour is None and timezone is None:
            text = drops.describe_schedule(list(current["drop_days"]), current["drop_hour"], current["timezone"])
            await interaction.response.send_message(f"drops go out {text}", ephemeral=True)
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "only people who can manage this server can change the schedule", ephemeral=True
            )
            return
        try:
            new_days = drops.parse_days(days) if days is not None else list(current["drop_days"])
            new_tz = _check_timezone(timezone) if timezone is not None else current["timezone"]
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        new_hour = hour if hour is not None else current["drop_hour"]

        await pool.execute(
            "UPDATE guilds SET drop_days = $2, drop_hour = $3, timezone = $4 WHERE guild_id = $1",
            interaction.guild_id, new_days, new_hour, new_tz,
        )
        log.info("guild %s: schedule set to %s %s %s", interaction.guild_id, new_days, new_hour, new_tz)
        await interaction.response.send_message(
            f"done. drops now go out {drops.describe_schedule(new_days, new_hour, new_tz)}", ephemeral=True
        )

    @schedule.autocomplete("timezone")
    async def _timezone_autocomplete(self, interaction: discord.Interaction, current: str):
        needle = current.lower().replace(" ", "_")
        matches = [tz for tz in self._timezones if needle in tz.lower()]
        return [app_commands.Choice(name=tz, value=tz) for tz in matches[:25]]


def _check_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"i don't know the timezone {name!r}. try one like Europe/London or Africa/Accra")
    return name


async def setup(bot) -> None:
    await bot.add_cog(Drops(bot))
