import asyncio
import logging

import aiohttp
import asyncpg
import discord
from discord.ext import commands

from .. import metadata
from ..urls import extract_urls, normalize

log = logging.getLogger(__name__)

READ_LATER_CHANNEL = "read-later"
SAVED = "🔖"
ALREADY_QUEUED = "🔁"
FETCH_CONCURRENCY = 4


def is_read_later(channel: discord.abc.Messageable) -> bool:
    # threads are excluded on purpose: replies there are notes (#5).
    # /setup (#8) will check guilds.read_later_channel_id here first.
    return isinstance(channel, discord.TextChannel) and channel.name == READ_LATER_CHANNEL


async def save_links(
    pool: asyncpg.Pool, message: discord.Message, urls: list[str]
) -> tuple[list[tuple[int, str]], list[asyncpg.Record]]:
    """store urls for the message author. returns ((id, url) of new links, existing rows for duplicates)."""
    by_key = {}
    for url in urls:
        by_key.setdefault(normalize(url), url)

    new, dupes = [], []
    async with pool.acquire() as conn, conn.transaction():
        for key, url in by_key.items():
            link_id = await conn.fetchval(
                """INSERT INTO links (guild_id, user_id, url, url_normalized, source_message_id)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (guild_id, user_id, url_normalized) DO NOTHING
                   RETURNING id""",
                message.guild.id, message.author.id, url, key, message.id,
            )
            if link_id is not None:
                new.append((link_id, url))
                continue
            dupes.append(
                await conn.fetchrow(
                    """SELECT id, url, status, created_at FROM links
                       WHERE guild_id = $1 AND user_id = $2 AND url_normalized = $3""",
                    message.guild.id, message.author.id, key,
                )
            )
    return new, dupes


class ReaddButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"stash:readd:(?P<link_id>\d+):(?P<choice>yes|no)",
):
    """re-add prompt buttons. state lives in the custom_id so they survive restarts."""

    def __init__(self, link_id: int, choice: str):
        yes = choice == "yes"
        super().__init__(
            discord.ui.Button(
                label="re-add" if yes else "leave it",
                style=discord.ButtonStyle.primary if yes else discord.ButtonStyle.secondary,
                custom_id=f"stash:readd:{link_id}:{choice}",
            )
        )
        self.link_id = link_id
        self.choice = choice

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["link_id"]), match["choice"])

    async def callback(self, interaction: discord.Interaction) -> None:
        pool = interaction.client.pool
        owner_id = await pool.fetchval(
            "SELECT user_id FROM links WHERE id = $1 AND guild_id = $2",
            self.link_id, interaction.guild_id,
        )
        if owner_id is None:
            await interaction.response.edit_message(content="that link no longer exists", view=None)
            return
        if owner_id != interaction.user.id:
            await interaction.response.send_message("that's not your link", ephemeral=True)
            return

        if self.choice == "yes":
            # reset times_sent so the archive rule (#12) doesn't archive it straight away
            await pool.execute(
                "UPDATE links SET status = 'unread', times_sent = 0 WHERE id = $1 AND guild_id = $2",
                self.link_id, interaction.guild_id,
            )
            content = f"re-added {SAVED}"
        else:
            content = "ok, leaving it"
        await interaction.response.edit_message(content=content, view=None)


class Links(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.http: aiohttp.ClientSession | None = None
        self._fetch_slots = asyncio.Semaphore(FETCH_CONCURRENCY)
        self._tasks: set[asyncio.Task] = set()  # strong refs so tasks aren't garbage collected

    async def cog_load(self) -> None:
        self.http = metadata.make_session()

    async def cog_unload(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.http.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not is_read_later(message.channel):
            return
        urls = extract_urls(message.content)
        if not urls:
            return  # plain chat in the channel is fine

        new, dupes = await save_links(self.bot.pool, message, urls)
        log.info(
            "guild %s: saved %d link(s), %d duplicate(s)", message.guild.id, len(new), len(dupes)
        )
        for link_id, url in new:
            task = asyncio.create_task(self._fill_metadata(link_id, url))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        if new:
            await _react(message, SAVED)
        if any(d["status"] == "unread" for d in dupes):
            await _react(message, ALREADY_QUEUED)
        for dupe in dupes:
            if dupe["status"] != "unread":
                await _ask_readd(message, dupe)

    async def _fill_metadata(self, link_id: int, url: str) -> None:
        """runs after the link is saved; on any failure the link just keeps no metadata."""
        try:
            async with self._fetch_slots:
                meta = await metadata.fetch(self.http, url)
            if meta is None:
                return
            await self.bot.pool.execute(
                """UPDATE links SET title = $2, description = $3, reading_minutes = $4
                   WHERE id = $1""",
                link_id, meta.title, meta.description, meta.reading_minutes,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("metadata fetch for link %s failed", link_id)


async def _react(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException as e:
        log.warning("couldn't react in channel %s: %s", message.channel.id, e)


async def _ask_readd(message: discord.Message, link: asyncpg.Record) -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(ReaddButton(link["id"], "yes"))
    view.add_item(ReaddButton(link["id"], "no"))
    saved_on = discord.utils.format_dt(link["created_at"], "D")
    try:
        await message.reply(
            f"you saved <{link['url']}> before ({saved_on}) and it's marked {link['status']}. "
            "re-add it to your queue?",
            view=view,
            mention_author=False,
        )
    except discord.HTTPException as e:
        log.warning("couldn't send re-add prompt in channel %s: %s", message.channel.id, e)


async def setup(bot) -> None:
    bot.add_dynamic_items(ReaddButton)
    await bot.add_cog(Links(bot))


async def teardown(bot) -> None:
    bot.remove_dynamic_items(ReaddButton)
