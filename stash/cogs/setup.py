import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import keys

log = logging.getLogger(__name__)


class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="see or change where the bot sends alerts about failed drops")
    @app_commands.describe(
        alerts="channel for alerts when a drop fails (otherwise the server owner gets a dm)",
        dm_alerts="go back to dming the server owner",
    )
    @app_commands.guild_only()
    async def setup(
        self,
        interaction: discord.Interaction,
        alerts: discord.TextChannel | None = None,
        dm_alerts: bool = False,
    ) -> None:
        pool = self.bot.pool
        guild = interaction.guild
        await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild.id)

        if alerts is None and not dm_alerts:
            channel_id = await pool.fetchval(
                "SELECT alert_channel_id FROM guilds WHERE guild_id = $1", guild.id
            )
            where = f"<#{channel_id}>" if channel_id else "a dm to the server owner"
            await interaction.response.send_message(
                f"if a drop fails, the alert goes to {where}.", ephemeral=True
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "only people who can manage this server can change this", ephemeral=True
            )
            return

        if dm_alerts:
            await pool.execute("UPDATE guilds SET alert_channel_id = NULL WHERE guild_id = $1", guild.id)
            await interaction.response.send_message(
                "done. failed drop alerts will be dmed to the server owner.", ephemeral=True
            )
            return

        perms = alerts.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(
                f"i can't post in {alerts.mention}. give me **view channel** and **send messages** "
                "there, then try again.",
                ephemeral=True,
            )
            return
        await pool.execute(
            "UPDATE guilds SET alert_channel_id = $2 WHERE guild_id = $1", guild.id, alerts.id
        )
        log.info("guild %s: alert channel set to %s", guild.id, alerts.id)
        await interaction.response.send_message(
            f"done. alerts about failed drops will go to {alerts.mention}.", ephemeral=True
        )


    @app_commands.command(description="set this server's claude api key, used to tag your links")
    @app_commands.describe(remove="forget the stored key (tagging stops)")
    @app_commands.guild_only()
    async def setkey(self, interaction: discord.Interaction, remove: bool = False) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "only people who can manage this server can set the key", ephemeral=True
            )
            return
        if remove:
            await self.bot.pool.execute(
                "UPDATE guilds SET claude_key_enc = NULL WHERE guild_id = $1", interaction.guild_id
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
        await interaction.response.send_modal(KeyModal(self.bot))


class KeyModal(discord.ui.Modal, title="claude api key"):
    key = discord.ui.TextInput(
        label="api key from console.anthropic.com",
        placeholder="sk-ant-...",
        min_length=20,
        max_length=200,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        api_key = self.key.value.strip()
        if not keys.looks_like_key(api_key):
            await interaction.response.send_message(
                "that doesn't look like a claude api key (they start with `sk-ant-`).", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        problem = await keys.check(api_key)
        if problem:
            await interaction.followup.send(problem, ephemeral=True)
            return
        await self.bot.pool.execute(
            "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", interaction.guild_id
        )
        await self.bot.pool.execute(
            "UPDATE guilds SET claude_key_enc = $2 WHERE guild_id = $1",
            interaction.guild_id, keys.encrypt(self.bot.config.secret_key, api_key),
        )
        log.info("guild %s: claude key set", interaction.guild_id)
        await interaction.followup.send(
            "key saved (encrypted). new links you save will be tagged by interest. "
            "usage is billed to this key.",
            ephemeral=True,
        )


async def setup(bot) -> None:
    await bot.add_cog(Setup(bot))
