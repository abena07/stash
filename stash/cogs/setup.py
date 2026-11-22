import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .. import keys, modes, providers

log = logging.getLogger(__name__)

# what the bot needs in the read-later channel: save links, react, open notes threads, post picks
NEEDED = {
    "view_channel": "view channel",
    "send_messages": "send messages",
    "add_reactions": "add reactions",
    "read_message_history": "read message history",
    "create_public_threads": "create public threads",
    "send_messages_in_threads": "send messages in threads",
}


def missing_permissions(channel: discord.abc.GuildChannel, me: discord.Member) -> str:
    """the needed permissions the bot lacks in `channel`, as readable text ('' if none)."""
    perms = channel.permissions_for(me)
    missing = [f"**{label}**" for flag, label in NEEDED.items() if not getattr(perms, flag)]
    if len(missing) <= 1:
        return "".join(missing)
    return ", ".join(missing[:-1]) + " and " + missing[-1]

MODE_HELP = {
    modes.PERSONAL: "everyone has their own queue, and drops are sent to each person.",
    modes.SHARED: "everyone's links go into one pile, and each drop is one set of picks for the group, "
                  "showing who saved each link. anyone's ✅ marks a link read for everyone.",
}


class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="see or change how stash works in this server")
    @app_commands.describe(
        channel="the channel where links are saved and picks are posted (default: #read-later)",
        mode="personal: everyone has their own queue. shared: one reading pile for the group",
        alerts="channel for alerts when a drop fails (otherwise the server owner gets a dm)",
        dm_alerts="go back to dming the server owner",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="personal (everyone has their own queue)", value=modes.PERSONAL),
        app_commands.Choice(name="shared (one reading pile for the group)", value=modes.SHARED),
    ])
    @app_commands.guild_only()
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        mode: app_commands.Choice[str] | None = None,
        alerts: discord.TextChannel | None = None,
        dm_alerts: bool = False,
    ) -> None:
        pool = self.bot.pool
        guild = interaction.guild
        await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild.id)

        if channel is None and mode is None and alerts is None and not dm_alerts:
            row = await pool.fetchrow("SELECT mode, alert_channel_id FROM guilds WHERE guild_id = $1", guild.id)
            where = f"<#{row['alert_channel_id']}>" if row["alert_channel_id"] else "a dm to the server owner"
            current = await self.bot.read_later.find(guild)
            saving = current.mention if current else "**not set**: make a #read-later channel or use `/setup channel`"
            await interaction.response.send_message(
                f"links are saved in {saving}.\n"
                f"mode: **{row['mode']}**. {MODE_HELP[row['mode']]}\n"
                f"if a drop fails, the alert goes to {where}.",
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "only people who can manage this server can change this", ephemeral=True
            )
            return

        if channel is not None:
            missing = missing_permissions(channel, guild.me)
            if missing:
                await interaction.response.send_message(
                    f"i need {missing} in {channel.mention} first, then try again.", ephemeral=True
                )
                return

        if alerts is not None:
            perms = alerts.permissions_for(guild.me)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(
                    f"i can't post in {alerts.mention}. give me **view channel** and **send messages** "
                    "there, then try again.",
                    ephemeral=True,
                )
                return

        done = []
        if channel is not None:
            await self.bot.read_later.choose(guild.id, channel.id)
            log.info("guild %s: read-later channel set to %s", guild.id, channel.id)
            done.append(f"links posted in {channel.mention} are saved now, and picks go there.")
        if mode is not None:
            await pool.execute("UPDATE guilds SET mode = $2 WHERE guild_id = $1", guild.id, mode.value)
            log.info("guild %s: mode set to %s", guild.id, mode.value)
            done.append(f"mode is now **{mode.value}**. {MODE_HELP[mode.value]}")
        if dm_alerts:
            await pool.execute("UPDATE guilds SET alert_channel_id = NULL WHERE guild_id = $1", guild.id)
            done.append("failed drop alerts will be dmed to the server owner.")
        elif alerts is not None:
            await pool.execute("UPDATE guilds SET alert_channel_id = $2 WHERE guild_id = $1", guild.id, alerts.id)
            log.info("guild %s: alert channel set to %s", guild.id, alerts.id)
            done.append(f"alerts about failed drops will go to {alerts.mention}.")
        await interaction.response.send_message("done. " + "\n".join(done), ephemeral=True)

    @app_commands.command(description="bring your own ai key (claude, openai, gemini, deepseek…) to tag links")
    @app_commands.describe(
        provider="which ai company the key is from",
        remove="forget the stored key (tagging stops)",
    )
    @app_commands.choices(provider=[
        app_commands.Choice(name=p.name, value=p.id) for p in providers.PROVIDERS.values()
    ])
    @app_commands.guild_only()
    async def setkey(
        self,
        interaction: discord.Interaction,
        provider: app_commands.Choice[str] | None = None,
        remove: bool = False,
    ) -> None:
        pool = self.bot.pool
        if provider is None and not remove:
            row = await pool.fetchrow(
                "SELECT ai_provider, ai_model, ai_key_enc IS NOT NULL AS has_key FROM guilds WHERE guild_id = $1",
                interaction.guild_id,
            )
            if row and row["has_key"] and row["ai_provider"] in providers.PROVIDERS:
                p = providers.PROVIDERS[row["ai_provider"]]
                text = f"links are tagged with **{p.name}** using `{row['ai_model'] or p.default_model}`."
            else:
                text = "no ai key set, so links aren't tagged. pick a provider: `/setkey provider:…`"
            await interaction.response.send_message(text, ephemeral=True)
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "only people who can manage this server can set the key", ephemeral=True
            )
            return
        if remove:
            await pool.execute(
                "UPDATE guilds SET ai_key_enc = NULL, ai_provider = NULL, ai_model = NULL WHERE guild_id = $1",
                interaction.guild_id,
            )
            await interaction.response.send_message(
                "done. the key is gone and new links won't be tagged.", ephemeral=True
            )
            return
        if not self.bot.config.secret_key:
            await interaction.response.send_message(
                "this bot isn't set up to store keys yet (the host needs to set STASH_SECRET_KEY).",
                ephemeral=True,
            )
            return
        # a modal keeps the key out of the channel and out of the command history
        await interaction.response.send_modal(KeyModal(self.bot, providers.PROVIDERS[provider.value]))


class KeyModal(discord.ui.Modal):
    def __init__(self, bot, provider: providers.Provider):
        super().__init__(title=f"{provider.name} key"[:45])
        self.bot = bot
        self.provider = provider
        self.key = discord.ui.TextInput(
            label=f"api key from {provider.console}"[:45], min_length=10, max_length=300
        )
        self.model = discord.ui.TextInput(
            label="model (a cheap one is filled in)", default=provider.default_model, max_length=100
        )
        self.add_item(self.key)
        self.add_item(self.model)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        api_key, model = self.key.value.strip(), self.model.value.strip()
        if " " in api_key:
            await interaction.response.send_message("that key has a space in it. paste it again?", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with aiohttp.ClientSession() as session:
                await providers.check(session, self.provider, api_key, model)
        except providers.ProviderError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        pool = self.bot.pool
        await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", interaction.guild_id)
        await pool.execute(
            "UPDATE guilds SET ai_provider = $2, ai_model = $3, ai_key_enc = $4 WHERE guild_id = $1",
            interaction.guild_id, self.provider.id, model, keys.encrypt(self.bot.config.secret_key, api_key),
        )
        log.info("guild %s: ai key set for %s (%s)", interaction.guild_id, self.provider.id, model)
        await interaction.followup.send(
            f"key saved (encrypted). new links will be tagged with {self.provider.name} using `{model}`. "
            "usage is billed to this key.",
            ephemeral=True,
        )


async def setup(bot) -> None:
    await bot.add_cog(Setup(bot))
