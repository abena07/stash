import asyncio
import logging

import aiohttp
import asyncpg
import discord
from discord.ext import commands

from .. import keys, metadata, modes, providers, tagging
from ..urls import clean, extract_urls, normalize

log = logging.getLogger(__name__)

SAVED = "🔖"
ALREADY_QUEUED = "🔁"
FETCH_CONCURRENCY = 4
THREAD_ARCHIVE_MINUTES = 10080  # a week; posting in an archived thread reopens it
THREAD_NAME_LIMIT = 100  # discord's maximum


def thread_name(title: str | None, url: str) -> str:
    name = metadata.display_title(title, url)
    if len(name) > THREAD_NAME_LIMIT:
        name = name[: THREAD_NAME_LIMIT - 1].rstrip() + "…"
    return name


async def save_links(
    pool: asyncpg.Pool, message: discord.Message, urls: list[str]
) -> tuple[list[tuple[int, str]], list[asyncpg.Record]]:
    """store urls for the message author. returns ((id, url) of new links, existing rows for duplicates).

    in a shared server a link anyone already saved is a duplicate: one pile, one copy of each link.
    """
    by_key = {}
    for url in urls:
        by_key.setdefault(normalize(url), clean(url))

    shared = await modes.is_shared(pool, message.guild.id)
    new, dupes = [], []
    async with pool.acquire() as conn, conn.transaction():
        for key, url in by_key.items():
            if shared:
                existing = await conn.fetchrow(
                    """SELECT id, url, status, created_at FROM links
                       WHERE guild_id = $1 AND url_normalized = $2 ORDER BY id LIMIT 1""",
                    message.guild.id, key,
                )
                if existing is not None:
                    dupes.append(existing)
                    continue
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
        if owner_id != interaction.user.id and not await modes.is_shared(pool, interaction.guild_id):
            await interaction.response.send_message("that's not your link", ephemeral=True)
            return

        if self.choice == "yes":
            # reset times_sent so the archive rule (#12) doesn't archive it straight away
            await pool.execute(
                "UPDATE links SET status = 'unread', times_sent = 0, read_at = NULL WHERE id = $1 AND guild_id = $2",
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
        self.http = metadata.make_session()  # locked down: fetches pages anyone can post
        self.ai_http = aiohttp.ClientSession()  # fixed provider urls only (see providers.py)

    async def cog_unload(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.http.close()
        await self.ai_http.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not await self.bot.read_later.is_read_later(message.channel):
            return
        urls = extract_urls(message.content)
        if not urls:
            return  # plain chat in the channel is fine

        new, dupes = await save_links(self.bot.pool, message, urls)
        log.info(
            "guild %s: saved %d link(s), %d duplicate(s)", message.guild.id, len(new), len(dupes)
        )
        if new:
            task = asyncio.create_task(self._after_save(message, new))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        if new:
            await _react(message, SAVED)
        if any(d["status"] == "unread" for d in dupes):
            await _react(message, ALREADY_QUEUED)
        for dupe in dupes:
            if dupe["status"] != "unread":
                await _ask_readd(message, dupe)

    async def _after_save(self, message: discord.Message, new: list[tuple[int, str]]) -> None:
        """background work once links are saved: fetch metadata, then open the notes thread."""
        titles = await asyncio.gather(*(self._fill_metadata(link_id, url) for link_id, url in new))
        await self._open_thread(message, new, titles[0])
        await self._tag(message.guild.id, message.author.id, [link_id for link_id, _ in new])

    async def _tag(self, guild_id: int, user_id: int, link_ids: list[int]) -> None:
        """tag links (#4), only when the server has brought its own ai key (#11)."""
        g = await self.bot.pool.fetchrow(
            "SELECT ai_provider, ai_model, ai_key_enc FROM guilds WHERE guild_id = $1", guild_id
        )
        api_key = keys.decrypt(self.bot.config.secret_key, g["ai_key_enc"]) if g else None
        provider = providers.PROVIDERS.get(g["ai_provider"]) if g else None
        if api_key is None or provider is None:
            return
        # reuse the person's tags, or the whole pile's in a shared server
        tag_owner = None if await modes.is_shared(self.bot.pool, guild_id) else user_id
        existing = [r["tag"] for r in await self.bot.pool.fetch(
            """SELECT DISTINCT unnest(tags) AS tag FROM links
               WHERE guild_id = $1 AND ($2::bigint IS NULL OR user_id = $2) ORDER BY tag LIMIT 100""",
            guild_id, tag_owner,
        )]
        for link_id in link_ids:
            link = await self.bot.pool.fetchrow("SELECT url, title, description FROM links WHERE id = $1", link_id)
            tags = await tagging.tag_link(
                self.ai_http, provider, api_key, g["ai_model"] or provider.default_model,
                link["url"], link["title"], link["description"], existing,
            )
            if not tags:
                continue
            await self.bot.pool.execute("UPDATE links SET tags = $2 WHERE id = $1", link_id, tags)
            existing = sorted(set(existing) | set(tags))
            log.info("guild %s: tagged link %s via %s: %s", guild_id, link_id, provider.id, ", ".join(tags))

    async def _fill_metadata(self, link_id: int, url: str) -> str | None:
        """runs after the link is saved; on any failure the link just keeps no metadata.
        returns the title, if one was found."""
        try:
            async with self._fetch_slots:
                meta = await metadata.fetch(self.http, url)
            if meta is None:
                return None
            await self.bot.pool.execute(
                """UPDATE links SET title = $2, description = $3, reading_minutes = $4
                   WHERE id = $1""",
                link_id, meta.title, meta.description, meta.reading_minutes,
            )
            return meta.title
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("metadata fetch for link %s failed", link_id)
            return None

    async def _open_thread(self, message: discord.Message, new: list[tuple[int, str]],
                           title: str | None) -> None:
        """one notes thread per message (discord allows one), named after its first link (#5)."""
        first_url = new[0][1]
        try:
            thread = await message.create_thread(
                name=thread_name(title, first_url), auto_archive_duration=THREAD_ARCHIVE_MINUTES
            )
        except discord.HTTPException as e:
            log.warning("guild %s: couldn't open a notes thread: %s", message.guild.id, e)
            return
        await self.bot.pool.execute(
            "UPDATE links SET thread_id = $1 WHERE id = ANY($2::bigint[])",
            thread.id, [link_id for link_id, _ in new],
        )


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
            f"<{link['url']}> was saved before ({saved_on}) and it's marked {link['status']}. "
            "re-add it to the queue?",
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
