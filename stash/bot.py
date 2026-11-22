import logging

import asyncpg
import discord
from discord.ext import commands

from . import api, db, welcome
from .channels import ReadLaterChannels
from .config import Config

log = logging.getLogger(__name__)

EXTENSIONS = ["stash.cogs.core", "stash.cogs.links", "stash.cogs.drops", "stash.cogs.reactions", "stash.cogs.setup", "stash.cogs.notes", "stash.cogs.export"]


class StashBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True  # read urls in the read-later channel and notes in threads
        intents.reactions = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.pool: asyncpg.Pool | None = None
        self.read_later: ReadLaterChannels | None = None
        self.api_runner = None

    async def setup_hook(self) -> None:
        self.pool = await db.connect(self.config.database_url)
        self.read_later = ReadLaterChannels(self.pool)
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        self.add_dynamic_items(welcome.ModeButton)  # welcome buttons keep working after a restart
        if self.config.api_port:
            self.api_runner = await api.start(api.make_app(self.pool, self.display_names), self.config.api_port)

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
        log.info("joined guild %s", guild.id)
        await welcome.send(self, guild)  # asks personal or shared instead of guessing

    async def display_names(self, guild_id: int, user_ids: set[int]) -> dict[int, str]:
        """server display names for these people. anyone who has left is skipped."""
        guild = self.get_guild(guild_id)
        if guild is None:
            return {}
        names = {}
        for uid in user_ids:
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.HTTPException:
                    continue
            names[uid] = member.display_name
        return names

    async def close(self) -> None:
        if self.api_runner:
            await self.api_runner.cleanup()
        await super().close()
        if self.pool:
            await self.pool.close()
