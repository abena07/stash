import logging

import asyncpg
import discord
from discord.ext import commands

from . import db
from .config import Config

log = logging.getLogger(__name__)

EXTENSIONS = ["stash.cogs.core", "stash.cogs.links", "stash.cogs.drops", "stash.cogs.reactions"]


class StashBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True  # read urls in #read-later and notes in threads
        intents.reactions = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self) -> None:
        self.pool = await db.connect(self.config.database_url)
        for ext in EXTENSIONS:
            await self.load_extension(ext)

        if self.config.dev_guild_id:
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()  # global sync can take up to an hour to show
        log.info("synced %d slash commands", len(synced))

    async def on_ready(self) -> None:
        # covers servers joined while the bot was offline
        await self.pool.executemany(
            "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
            [(g.id,) for g in self.guilds],
        )
        log.info("logged in as %s in %d servers", self.user, len(self.guilds))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.pool.execute(
            "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild.id
        )

    async def close(self) -> None:
        await super().close()
        if self.pool:
            await self.pool.close()
