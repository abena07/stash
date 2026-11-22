import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import export as md, modes, tokens

log = logging.getLogger(__name__)

MAX_BYTES = 9 * 1024 * 1024  # stay under discord's upload limit for bots


class Export(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="download your links and notes as markdown files for obsidian")
    @app_commands.describe(which="which links to include (default: the ones you've read)")
    @app_commands.choices(which=[
        app_commands.Choice(name="read (marked ✅)", value=md.READ),
        app_commands.Choice(name="loved (marked 👍)", value=md.LOVED),
        app_commands.Choice(name="all saved links", value=md.ALL),
    ])
    @app_commands.guild_only()
    async def export(self, interaction: discord.Interaction, which: app_commands.Choice[str] | None = None) -> None:
        kind = which.value if which else md.READ
        await interaction.response.defer(ephemeral=True, thinking=True)
        shared = await modes.is_shared(self.bot.pool, interaction.guild_id)
        links = await md.fetch(self.bot.pool, interaction.guild_id, None if shared else interaction.user.id, kind)
        if not links:
            empty = {md.READ: "nothing marked read yet. react ✅ on a link once you've read it.",
                     md.LOVED: "nothing marked loved yet. react 👍 on a link you loved.",
                     md.ALL: "no saved links yet."}[kind]
            await interaction.followup.send(empty, ephemeral=True)
            return

        ids = {link.saver for link in links} | {n.author_id for link in links for n in link.notes}
        names = await self.bot.display_names(interaction.guild_id, ids)
        data = md.build_zip(links, lambda uid: names.get(uid, "someone"), show_saver=shared)
        if len(data) > MAX_BYTES:
            await interaction.followup.send(
                "that export is too big to send in discord. try `which: read` or `which: loved`.", ephemeral=True
            )
            return
        log.info("guild %s: exported %d link(s) (%s)", interaction.guild_id, len(links), kind)
        await interaction.followup.send(
            f"{len(links)} link{'s' if len(links) != 1 else ''} as markdown. unzip the `stash` folder into "
            "your obsidian vault.",
            file=discord.File(io.BytesIO(data), filename="stash-export.zip"),
            ephemeral=True,
        )

    @app_commands.command(name="connect", description="connect the stash obsidian plugin to your links")
    @app_commands.describe(app="what to connect")
    @app_commands.choices(app=[app_commands.Choice(name="obsidian", value="obsidian")])
    @app_commands.guild_only()
    async def connect(self, interaction: discord.Interaction, app: app_commands.Choice[str]) -> None:
        code = await tokens.create(self.bot.pool, interaction.guild_id, interaction.user.id, app.value)
        log.info("guild %s: %s connect code created", interaction.guild_id, app.value)
        await interaction.response.send_message(
            "here's your code for the stash plugin in obsidian. paste it into the plugin's settings.\n"
            f"||`{code}`||\n"
            "only you can see this. it works like a password, so don't share it. "
            "running `/connect obsidian` again makes a new one and the old one stops working. "
            "`/disconnect obsidian` turns it off.",
            ephemeral=True,
        )

    @app_commands.command(name="disconnect", description="stop the stash obsidian plugin from syncing")
    @app_commands.describe(app="what to disconnect")
    @app_commands.choices(app=[app_commands.Choice(name="obsidian", value="obsidian")])
    @app_commands.guild_only()
    async def disconnect(self, interaction: discord.Interaction, app: app_commands.Choice[str]) -> None:
        gone = await tokens.revoke(self.bot.pool, interaction.guild_id, interaction.user.id, app.value)
        await interaction.response.send_message(
            "done. your obsidian code no longer works." if gone else "nothing was connected.", ephemeral=True
        )


async def setup(bot) -> None:
    await bot.add_cog(Export(bot))
