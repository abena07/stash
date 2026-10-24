import time

import discord
from discord import app_commands
from discord.ext import commands


class Core(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="check the bot and database are alive")
    async def ping(self, interaction: discord.Interaction) -> None:
        start = time.perf_counter()
        await self.bot.pool.fetchval("SELECT 1")
        db_ms = (time.perf_counter() - start) * 1000
        await interaction.response.send_message(
            f"pong · gateway {self.bot.latency * 1000:.0f}ms · db {db_ms:.0f}ms", ephemeral=True
        )


async def setup(bot) -> None:
    await bot.add_cog(Core(bot))
