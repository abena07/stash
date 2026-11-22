import asyncio
import itertools
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

import discord
import pytest

from stash import drops
from stash.drops import Candidate, due_slot, latest_slot, pick

UTC = timezone.utc
WED, SUN = 3, 7


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


# 2026-11-18 is a wednesday, 2026-11-22 a sunday
@pytest.mark.parametrize(
    "now, expected",
    [
        ("2026-11-18 09:00", "2026-11-18 09:00"),  # exactly on time
        ("2026-11-18 08:59", "2026-11-15 09:00"),  # just before: last sunday's
        ("2026-11-20 12:00", "2026-11-18 09:00"),  # friday: wednesday's
        ("2026-11-22 23:00", "2026-11-22 09:00"),
    ],
)
def test_latest_slot(now, expected):
    assert latest_slot(at(now), [WED, SUN], 9, "UTC") == at(expected)


def test_latest_slot_uses_the_server_timezone():
    # 09:00 in new york on a wednesday in november is 14:00 utc
    assert latest_slot(at("2026-11-18 14:30"), [WED], 9, "America/New_York") == at("2026-11-18 14:00")
    assert latest_slot(at("2026-11-18 13:30"), [WED], 9, "America/New_York") == at("2026-11-11 14:00")


def test_unknown_timezone_falls_back_to_utc():
    assert latest_slot(at("2026-11-18 10:00"), [WED], 9, "Not/AZone") == at("2026-11-18 09:00")


def test_due_slot_catches_up_after_a_short_outage_only():
    assert due_slot(at("2026-11-18 14:00"), [WED, SUN], 9, "UTC") == at("2026-11-18 09:00")
    assert due_slot(at("2026-11-18 15:01"), [WED, SUN], 9, "UTC") is None


def link(id, minutes=None, sent=0, age_days=10):
    return Candidate(id, f"https://example.com/{id}", None, minutes, sent, at("2026-11-18 09:00") - timedelta(days=age_days))


def test_pick_returns_distinct_links():
    links = [link(i) for i in range(5)]
    for seed in range(50):
        chosen = pick(links, at("2026-11-18 09:00"), rng=random.Random(seed))
        assert len(chosen) == 2 and len({c.id for c in chosen}) == 2


def test_pick_with_fewer_links_than_slots():
    assert [c.id for c in pick([link(1)], at("2026-11-18 09:00"))] == [1]
    assert pick([], at("2026-11-18 09:00")) == []


def test_pick_favours_old_short_unsent_links():
    favoured = link(1, minutes=3, sent=0, age_days=60)
    others = [link(2, minutes=40, sent=2, age_days=1), link(3, minutes=40, sent=2, age_days=1)]
    rng = random.Random(0)
    counts = Counter(c.id for _ in range(500) for c in pick([favoured, *others], at("2026-11-18 09:00"), k=1, rng=rng))
    assert counts[1] > 400


def test_format_pick_falls_back_to_site_name():
    assert drops.format_pick(1, link(7, minutes=5)) == "**1. example.com** · 5 min read\nhttps://example.com/7"


# ---- against a real postgres (skipped when none is running) ----

class FakeChannel:
    def __init__(self):
        self.sent: list[str] = []
        self.reactions: list[tuple[int, str]] = []
        self._ids = itertools.count(1000)

    async def send(self, content, **kwargs):
        self.sent.append(content)
        return FakeMessage(next(self._ids), self)


class FakeMessage:
    def __init__(self, id, channel):
        self.id = id
        self.channel = channel

    async def add_reaction(self, emoji):
        self.channel.reactions.append((self.id, emoji))


async def seed(pool, guild_id=1, user_id=10, links=3):
    await pool.execute("INSERT INTO guilds (guild_id) VALUES ($1)", guild_id)
    for i in range(links):
        await pool.execute(
            """INSERT INTO links (guild_id, user_id, url, url_normalized, title, reading_minutes)
               VALUES ($1, $2, $3, $3, $4, 4)""",
            guild_id, user_id, f"https://example.com/{i}", f"post {i}",
        )


def test_drop_is_sent_once_and_recorded(database):
    loop, pool = database
    channel = FakeChannel()

    async def go():
        await seed(pool)

        async def get_channel(guild_id):
            return channel

        now = at("2026-11-18 09:01")
        assert await drops.run_due_drops(pool, now, get_channel) == 1
        # a later tick for the same slot, or a second bot instance, sends nothing
        assert await drops.run_due_drops(pool, now + timedelta(minutes=1), get_channel) == 0

        drop = await pool.fetchrow("SELECT * FROM drops")
        items = await pool.fetch("SELECT * FROM drop_items")
        sent_counts = await pool.fetch("SELECT times_sent FROM links ORDER BY times_sent DESC")
        return drop, items, [r["times_sent"] for r in sent_counts]

    drop, items, sent_counts = loop.run_until_complete(go())
    assert drop["scheduled_for"] == at("2026-11-18 09:00") and drop["sent_at"] is not None
    assert len(items) == 2 and all(i["discord_message_id"] for i in items)
    assert sent_counts == [1, 1, 0]
    assert channel.sent[0] == "📬 <@10> your wednesday picks"
    assert len(channel.sent) == 3 and "4 min read" in channel.sent[1]
    assert channel.reactions == []  # the person reacts, the bot doesn't pre-fill


def test_no_drop_outside_drop_days(database):
    loop, pool = database
    channel = FakeChannel()

    async def go():
        await seed(pool)

        async def get_channel(guild_id):
            return channel

        return await drops.run_due_drops(pool, at("2026-11-20 09:01"), get_channel)  # friday

    assert loop.run_until_complete(go()) == 0 and channel.sent == []


def test_read_links_are_not_dropped(database):
    loop, pool = database
    channel = FakeChannel()

    async def go():
        await seed(pool, links=1)
        await pool.execute("UPDATE links SET status = 'read'")

        async def get_channel(guild_id):
            return channel

        return await drops.run_due_drops(pool, at("2026-11-18 09:01"), get_channel)

    assert loop.run_until_complete(go()) == 0 and channel.sent == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("wed sun", [3, 7]),
        ("Sunday, Wednesday", [3, 7]),
        ("mon and fri", [1, 5]),
        ("tues/thurs", [2, 4]),
        ("weekdays", [1, 2, 3, 4, 5]),
        ("weekends", [6, 7]),
        ("daily", [1, 2, 3, 4, 5, 6, 7]),
        ("every day", [1, 2, 3, 4, 5, 6, 7]),
        ("sat sat", [6]),
    ],
)
def test_parse_days(text, expected):
    assert drops.parse_days(text) == expected


@pytest.mark.parametrize("text", ["", "funday", "wed, someday"])
def test_parse_days_rejects_junk(text):
    with pytest.raises(ValueError):
        drops.parse_days(text)


def test_describe_schedule():
    assert drops.describe_schedule([7, 3], 9, "UTC") == "wednesday and sunday at 09:00 (UTC)"
    assert drops.describe_schedule([1], 18, "Africa/Accra") == "monday at 18:00 (Africa/Accra)"


def test_on_demand_picks_are_recorded_and_repeatable(database):
    loop, pool = database
    channel = FakeChannel()

    async def go():
        await seed(pool)
        links = await drops.unread_links(pool, 1, 10)
        first = await drops.send_on_demand(pool, channel, 1, 10, links[:2], "📬 your picks")
        second = await drops.send_on_demand(pool, channel, 1, 10, links[:1], "🎲 a random pick")
        drop_count = await pool.fetchval("SELECT count(*) FROM drops WHERE sent_at IS NOT NULL")
        item_count = await pool.fetchval("SELECT count(*) FROM drop_items")
        return first, second, drop_count, item_count

    assert loop.run_until_complete(go()) == (2, 1, 2, 3)
    assert channel.sent[0] == "📬 your picks" and channel.sent[3] == "🎲 a random pick"


# ---- retries and alerts (#16) ----

def http_error(cls, status):
    response = type("Response", (), {"status": status, "reason": "nope"})()
    return cls(response, "nope")


class FlakyChannel(FakeChannel):
    """fails the first `failures` sends, then works."""

    def __init__(self, failures, error=None):
        super().__init__()
        self.failures = failures
        self.error = error or http_error(discord.Forbidden, 403)

    async def send(self, content, **kwargs):
        if self.failures:
            self.failures -= 1
            raise self.error
        return await super().send(content, **kwargs)


class Alerts:
    def __init__(self):
        self.calls = []

    async def __call__(self, guild_id, user_id, slot, reason):
        self.calls.append((guild_id, user_id, slot, reason))


@pytest.mark.parametrize(
    "error, reason",
    [
        (drops.DropFailed("i couldn't find a #read-later channel"), "i couldn't find a #read-later channel"),
        (http_error(discord.Forbidden, 403), "i don't have permission to post in #read-later"),
        (http_error(discord.NotFound, 404), "the #read-later channel seems to have been deleted"),
        (http_error(discord.HTTPException, 500), "discord rejected the message (error 500)"),
        (RuntimeError("boom"), "something went wrong on my side"),
    ],
)
def test_failure_reason(error, reason):
    assert drops.failure_reason(error) == reason


def run_ticks(loop, pool, get_channel, alert, start, minutes):
    """one tick per minute, like the real scheduler."""
    async def go():
        sent = 0
        for m in range(minutes):
            sent += await drops.run_due_drops(pool, start + timedelta(minutes=m), get_channel, alert)
        return sent
    return loop.run_until_complete(go())


def test_failed_drop_retries_3_times_then_alerts_once(database):
    loop, pool = database
    channel, alert = FlakyChannel(failures=1000), Alerts()
    loop.run_until_complete(seed(pool))

    async def get_channel(guild_id):
        return channel

    sent = run_ticks(loop, pool, get_channel, alert, at("2026-11-18 09:00"), minutes=60)
    drop = loop.run_until_complete(pool.fetchrow("SELECT * FROM drops"))
    assert sent == 0
    assert drop["attempts"] == 4 and drop["failed_at"] == at("2026-11-18 09:15")  # 09:00 + 3 × 5 min
    assert "Forbidden" in drop["last_error"]
    assert alert.calls == [(1, 10, at("2026-11-18 09:00"), "i don't have permission to post in #read-later")]


def test_drop_that_recovers_on_retry_sends_once_without_alert(database):
    loop, pool = database
    channel, alert = FlakyChannel(failures=1), Alerts()
    loop.run_until_complete(seed(pool))

    async def get_channel(guild_id):
        return channel

    sent = run_ticks(loop, pool, get_channel, alert, at("2026-11-18 09:00"), minutes=30)
    drop = loop.run_until_complete(pool.fetchrow("SELECT * FROM drops"))
    assert sent == 1 and alert.calls == []
    assert drop["attempts"] == 1 and drop["sent_at"] is not None and drop["failed_at"] is None
    assert len(channel.sent) == 3  # header + 2 picks, once


def test_missing_channel_alerts_with_a_plain_reason(database):
    loop, pool = database
    alert = Alerts()
    loop.run_until_complete(seed(pool))

    async def no_channel(guild_id):
        return None

    run_ticks(loop, pool, no_channel, alert, at("2026-11-18 09:00"), minutes=20)
    assert [c[3] for c in alert.calls] == ["i couldn't find a #read-later channel"]


def test_two_instances_ticking_together_send_once(database):
    loop, pool = database
    channel = FakeChannel()
    loop.run_until_complete(seed(pool))

    async def get_channel(guild_id):
        return channel

    async def go():
        now = at("2026-11-18 09:01")
        return await asyncio.gather(*(drops.run_due_drops(pool, now, get_channel) for _ in range(3)))

    assert sum(loop.run_until_complete(go())) == 1
    assert len(channel.sent) == 3


def test_scheduler_ignores_drops_sent_from_commands(database):
    loop, pool = database
    channel = FakeChannel()

    async def go():
        await seed(pool)
        await pool.execute(
            "INSERT INTO drops (guild_id, user_id, scheduled_for) VALUES (1, 10, $1)", at("2026-11-20 12:00")
        )

        async def get_channel(guild_id):
            return channel

        return await drops.run_due_drops(pool, at("2026-11-20 12:01"), get_channel)  # friday, no slot

    assert loop.run_until_complete(go()) == 0 and channel.sent == []
