import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import export as md, modes

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

        names = await self._names(interaction.guild, links)
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

    async def _names(self, guild: discord.Guild, links: list[md.ExportLink]) -> dict[int, str]:
        """display names for everyone who saved a link or wrote a note in the export."""
        ids = {link.saver for link in links} | {n.author_id for link in links for n in link.notes}
        names = {}
        for uid in ids:
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.HTTPException:
                    continue  # they've left the server
            names[uid] = member.display_name
        return names


async def setup(bot) -> None:
    await bot.add_cog(Export(bot))
