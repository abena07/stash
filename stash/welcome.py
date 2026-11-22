"""the hello stash posts when it joins a server, with buttons to pick personal or shared mode.

stash can't tell a friend group from someone's own server, so instead of guessing it asks.
personal stays the default until someone who can manage the server presses a button.
"""

import logging

import discord

from . import modes
from .cogs.setup import MODE_HELP

log = logging.getLogger(__name__)

CHOICES = {modes.PERSONAL: "just me", modes.SHARED: "our group"}


def welcome_text(read_later: discord.abc.GuildChannel | None) -> str:
    where = (f"post links in {read_later.mention}" if read_later
             else "make a **#read-later** channel (or pick one with `/setup channel`) and post links there")
    return (
        f"👋🏿 hi, i'm **stash**. {where}, and i'll save them, then bring two back every "
        "wednesday and sunday (change it with `/schedule`) so they actually get read.\n\n"
        "**who's reading?**\n"
        f"- **{CHOICES[modes.PERSONAL]}**: {MODE_HELP[modes.PERSONAL]}\n"
        f"- **{CHOICES[modes.SHARED]}**: {MODE_HELP[modes.SHARED]}\n"
        "-# change it any time with `/setup mode`. only people who can manage the server can choose."
    )


def chosen_text(content: str, mode: str, user_id: int) -> str:
    """the welcome once a mode is picked: the question is answered, so the buttons go."""
    return f"{content}\n\n**{CHOICES[mode]}** it is ({mode} mode), chosen by <@{user_id}>."


class ModeButton(discord.ui.DynamicItem[discord.ui.Button], template=r"stash:mode:(?P<mode>personal|shared)"):
    def __init__(self, mode: str):
        super().__init__(
            discord.ui.Button(
                label=CHOICES[mode],
                style=discord.ButtonStyle.primary if mode == modes.SHARED else discord.ButtonStyle.secondary,
                custom_id=f"stash:mode:{mode}",
            )
        )
        self.mode = mode

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["mode"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "only people who can manage this server can choose this", ephemeral=True
            )
            return
        await set_mode(interaction.client.pool, interaction.guild_id, self.mode)
        log.info("guild %s: mode set to %s from the welcome message", interaction.guild_id, self.mode)
        await interaction.response.edit_message(
            content=chosen_text(interaction.message.content, self.mode, interaction.user.id),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def build_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ModeButton(modes.PERSONAL))
    view.add_item(ModeButton(modes.SHARED))
    return view


async def set_mode(pool, guild_id: int, mode: str) -> None:
    await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
    await pool.execute("UPDATE guilds SET mode = $2 WHERE guild_id = $1", guild_id, mode)


def where_to_post(guild: discord.Guild, read_later: discord.TextChannel | None) -> discord.TextChannel | None:
    """the read-later channel, else the server's system channel, else the first channel stash can post in."""
    candidates = [read_later, guild.system_channel, *guild.text_channels]
    for channel in candidates:
        if channel is None:
            continue
        perms = channel.permissions_for(guild.me)
        if perms.view_channel and perms.send_messages:
            return channel
    return None


async def send(bot, guild: discord.Guild) -> None:
    """post the welcome in the server, or dm the owner if stash can't post anywhere. never raises."""
    try:
        read_later = await bot.read_later.find(guild)
        text, view = welcome_text(read_later), build_view()
        channel = where_to_post(guild, read_later)
        if channel is None:
            # no buttons in a dm: they'd have no server to act on, so point to /setup mode instead
            owner = await bot.fetch_user(guild.owner_id)
            await owner.send(f"(from **{guild.name}**)\n" + text)
            log.info("guild %s: welcome dmed to the owner (no channel to post in)", guild.id)
        else:
            await channel.send(text, view=view, allowed_mentions=discord.AllowedMentions.none())
            log.info("guild %s: welcome posted in %s", guild.id, channel.id)
    except discord.HTTPException as e:
        log.warning("guild %s: couldn't send the welcome: %s", guild.id, e)
