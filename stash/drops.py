"""scheduled drops (#6): on each server's drop days, post a few saved links back.

the scheduler ticks once a minute. a drop is claimed by inserting its
(guild, user, slot) row first, so restarts and overlapping deploys never
send the same drop twice. a failed drop is retried a few times, then the
server owner and the host are alerted (#16).
"""

import logging
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
import discord

from .metadata import display_title

log = logging.getLogger(__name__)

PICKS_PER_DROP = 2
# a drop missed while the bot was down still goes out if it restarts within this window
CATCH_UP = timedelta(hours=6)
# a failed drop is retried this many times, this far apart, then the owner is alerted (#16)
RETRIES = 3
RETRY_DELAY = timedelta(minutes=5)
# how long one attempt holds a drop, so a slow attempt isn't started twice
LEASE = timedelta(minutes=2)
# each tag a link shares with your loved links adds this much to its weight (up to 2 tags: ×3)
LOVED_TAG_BOOST = 1.0
MAX_BOOSTED_TAGS = 2
DAY_NAMES = {1: "monday", 2: "tuesday", 3: "wednesday", 4: "thursday",
             5: "friday", 6: "saturday", 7: "sunday"}


class Channel(Protocol):
    async def send(self, content: str, **kwargs) -> discord.Message: ...


@dataclass(frozen=True)
class Candidate:
    id: int
    url: str
    title: str | None
    reading_minutes: int | None
    times_sent: int
    created_at: datetime
    tags: tuple[str, ...] = ()


def latest_slot(now: datetime, days: list[int], hour: int, tz_name: str) -> datetime | None:
    """the most recent scheduled drop time at or before `now`, in utc."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown timezone %r, using utc", tz_name)
        tz = ZoneInfo("UTC")
    local = now.astimezone(tz)
    for back in range(8):
        day = (local - timedelta(days=back)).date()
        if day.isoweekday() not in days:
            continue
        slot = datetime(day.year, day.month, day.day, hour, tzinfo=tz)
        if slot <= local:
            return slot.astimezone(ZoneInfo("UTC"))
    return None


def due_slot(now: datetime, days: list[int], hour: int, tz_name: str) -> datetime | None:
    slot = latest_slot(now, days, hour, tz_name)
    if slot is not None and now - slot <= CATCH_UP:
        return slot
    return None


def weight(link: Candidate, now: datetime, loved_tags: frozenset[str] = frozenset()) -> float:
    """rule-based pick weight: older and shorter links first, already-sent ones less often,
    and links sharing tags with ones you've loved more often."""
    age_days = max((now - link.created_at).total_seconds() / 86400, 0)
    age = math.sqrt(1 + age_days)
    length = 1 if link.reading_minutes is None else 1 / (1 + link.reading_minutes / 10)
    shared = len(loved_tags.intersection(link.tags))
    affinity = 1 + LOVED_TAG_BOOST * min(shared, MAX_BOOSTED_TAGS)
    return age * length * affinity / (1 + link.times_sent)


def pick(links: list[Candidate], now: datetime, k: int = PICKS_PER_DROP,
         rng: random.Random | None = None, loved_tags: frozenset[str] = frozenset()) -> list[Candidate]:
    """k links, weighted at random without repeats."""
    rng = rng or random.Random()
    pool = list(links)
    chosen = []
    while pool and len(chosen) < k:
        weights = [weight(link, now, loved_tags) for link in pool]
        i = rng.choices(range(len(pool)), weights=weights)[0]
        chosen.append(pool.pop(i))
    return chosen


def format_pick(n: int, link: Candidate) -> str:
    title = display_title(link.title, link.url)
    length = f" · {link.reading_minutes} min read" if link.reading_minutes else ""
    tags = f" · {', '.join(link.tags)}" if link.tags else ""
    return f"**{n}. {title}**{length}{tags}\n{link.url}"


Alert = Callable[[int, int, datetime, str], Awaitable[None]]


class DropFailed(Exception):
    """a drop couldn't be posted. `reason` is plain enough to show a server owner."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def failure_reason(error: Exception) -> str:
    if isinstance(error, DropFailed):
        return error.reason
    if isinstance(error, discord.Forbidden):
        return "i don't have permission to post in #read-later"
    if isinstance(error, discord.NotFound):
        return "the #read-later channel seems to have been deleted"
    if isinstance(error, discord.HTTPException):
        return f"discord rejected the message (error {error.status})"
    return "something went wrong on my side"


async def run_due_drops(
    pool: asyncpg.Pool,
    now: datetime,
    get_channel: Callable[[int], Awaitable[Channel | None]],
    alert: Alert | None = None,
    rng: random.Random | None = None,
) -> int:
    """claim newly due drops, then attempt every drop that is due, retries included.
    returns how many were sent."""
    await _claim_new_slots(pool, now)
    # take each due drop for LEASE so another tick or bot instance can't attempt it at the same time
    due = await pool.fetch(
        """UPDATE drops SET next_attempt_at = $1::timestamptz + $2::interval
           WHERE sent_at IS NULL AND failed_at IS NULL AND next_attempt_at <= $1
           RETURNING id, guild_id, user_id, scheduled_for, attempts""",
        now, LEASE,
    )
    sent = 0
    for d in due:
        if await _attempt(pool, d, now, get_channel, alert, rng):
            sent += 1
    return sent


async def _claim_new_slots(pool, now: datetime) -> None:
    guilds = await pool.fetch("SELECT guild_id, drop_days, drop_hour, timezone FROM guilds")
    for g in guilds:
        slot = due_slot(now, list(g["drop_days"]), g["drop_hour"], g["timezone"])
        if slot is None:
            continue
        # one row per (guild, user, slot): the unique key means a slot is only ever claimed once
        await pool.execute(
            """INSERT INTO drops (guild_id, user_id, scheduled_for, next_attempt_at)
               SELECT DISTINCT guild_id, user_id, $2::timestamptz, $3::timestamptz
                 FROM links WHERE guild_id = $1 AND status = 'unread'
               ON CONFLICT (guild_id, user_id, scheduled_for) DO NOTHING""",
            g["guild_id"], slot, now,
        )


async def _attempt(pool, d, now: datetime, get_channel, alert: Alert | None, rng) -> bool:
    guild_id, user_id, slot = d["guild_id"], d["user_id"], d["scheduled_for"]
    try:
        channel = await get_channel(guild_id)
        if channel is None:
            raise DropFailed("i couldn't find a #read-later channel")
        await _send_drop(pool, channel, d["id"], guild_id, user_id, slot, now, rng)
        return True
    except Exception as e:
        attempts = d["attempts"] + 1
        reason = failure_reason(e)
        log.warning("guild %s: drop %s attempt %d failed: %r", guild_id, d["id"], attempts, e)
        if attempts <= RETRIES:
            await pool.execute(
                """UPDATE drops SET attempts = $2, last_error = $3, next_attempt_at = $4
                   WHERE id = $1""",
                d["id"], attempts, repr(e), now + RETRY_DELAY,
            )
            return False
        await pool.execute(
            "UPDATE drops SET attempts = $2, last_error = $3, failed_at = $4 WHERE id = $1",
            d["id"], attempts, repr(e), now,
        )
        log.error("guild %s: drop %s gave up after %d attempts: %s", guild_id, d["id"], attempts, reason)
        if alert is not None:
            try:
                await alert(guild_id, user_id, slot, reason)
            except Exception:
                log.exception("guild %s: couldn't send the failure alert", guild_id)
        return False


async def _send_drop(pool, channel: Channel, drop_id: int, guild_id: int, user_id: int,
                     slot: datetime, now: datetime, rng) -> None:
    picks = pick(await unread_links(pool, guild_id, user_id), now, rng=rng,
                 loved_tags=await loved_tags(pool, guild_id, user_id))
    if not picks:
        # everything was marked read since the slot was claimed: nothing to send, nothing failed
        await pool.execute("UPDATE drops SET sent_at = now() WHERE id = $1", drop_id)
        return
    header = f"📬 <@{user_id}> your {DAY_NAMES[slot.isoweekday()]} picks"
    await post_picks(pool, channel, drop_id, guild_id, picks, header)


async def send_on_demand(pool, channel: Channel, guild_id: int, user_id: int, picks: list[Candidate],
                         header: str) -> int:
    """post picks asked for with a command. recorded as a drop so reactions map back to the links."""
    drop_id = await pool.fetchval(
        "INSERT INTO drops (guild_id, user_id, scheduled_for) VALUES ($1, $2, clock_timestamp()) RETURNING id",
        guild_id, user_id,
    )
    return await post_picks(pool, channel, drop_id, guild_id, picks, header)


async def loved_tags(pool, guild_id: int, user_id: int) -> frozenset[str]:
    """tags on links this person has marked 👍🏿, read or not."""
    rows = await pool.fetch(
        "SELECT DISTINCT unnest(tags) AS tag FROM links WHERE guild_id = $1 AND user_id = $2 AND loved",
        guild_id, user_id,
    )
    return frozenset(r["tag"] for r in rows)


async def unread_links(pool, guild_id: int, user_id: int) -> list[Candidate]:
    rows = await pool.fetch(
        """SELECT id, url, title, reading_minutes, times_sent, created_at, tags FROM links
           WHERE guild_id = $1 AND user_id = $2 AND status = 'unread'""",
        guild_id, user_id,
    )
    return [Candidate(**{**dict(r), "tags": tuple(r["tags"])}) for r in rows]


async def post_picks(pool, channel: Channel, drop_id: int, guild_id: int, picks: list[Candidate],
                     header: str) -> int:
    """post a header and one message per pick, then record what was sent. returns picks posted.
    raises if none of the picks could be posted."""
    mention = discord.AllowedMentions(users=True, everyone=False, roles=False)
    await channel.send(header, allowed_mentions=mention)
    posted = []  # (link id, message id)
    error = None
    for n, link in enumerate(picks, 1):
        try:
            msg = await channel.send(format_pick(n, link), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning("guild %s: couldn't post link %s: %s", guild_id, link.id, e)
            error = e
            continue
        posted.append((link.id, msg.id))
    if not posted:
        raise error

    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            "INSERT INTO drop_items (drop_id, link_id, discord_message_id) VALUES ($1, $2, $3)",
            [(drop_id, link_id, msg_id) for link_id, msg_id in posted],
        )
        await conn.execute(
            "UPDATE links SET times_sent = times_sent + 1 WHERE id = ANY($1::bigint[])",
            [link_id for link_id, _ in posted],
        )
        await conn.execute("UPDATE drops SET sent_at = now() WHERE id = $1", drop_id)
    log.info("guild %s: sent drop %s with %d link(s)", guild_id, drop_id, len(posted))
    return len(posted)


DAY_ALIASES = {name: n for n, name in DAY_NAMES.items()} | {name[:3]: n for n, name in DAY_NAMES.items()} | {
    "tues": 2, "thur": 4, "thurs": 4,
}


def parse_days(text: str) -> list[int]:
    """'wed, sun' or 'monday friday' → iso weekdays. raises ValueError on anything else."""
    words = [w for w in re.split(r"[\s,/&+]+", text.lower()) if w and w != "and"]
    if words in (["weekdays"], ["weekends"], ["everyday"], ["every", "day"], ["daily"]):
        return {"weekdays": [1, 2, 3, 4, 5], "weekends": [6, 7]}.get(words[0], list(range(1, 8)))
    days = set()
    for w in words:
        if w not in DAY_ALIASES:
            raise ValueError(f"i don't know the day {w!r}")
        days.add(DAY_ALIASES[w])
    if not days:
        raise ValueError("give at least one day")
    return sorted(days)


def describe_schedule(days: list[int], hour: int, tz_name: str) -> str:
    names = [DAY_NAMES[d] for d in sorted(days)]
    when = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{when} at {hour:02d}:00 ({tz_name})"
