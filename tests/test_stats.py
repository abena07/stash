from datetime import datetime, timedelta, timezone

from stash import stats

NOW = datetime(2026, 11, 30, 9, tzinfo=timezone.utc)  # a monday
IN_WEEK = NOW - timedelta(days=2)
LAST_WEEK = NOW - timedelta(days=10)


def test_message_reads_like_a_summary():
    u = stats.Usage(
        since=NOW - stats.WEEK, until=NOW, servers=7, new_servers=3, links_saved=42, links_all_time=182,
        people_active=11, marked_read=18, loved=5, drops_sent=9, notes_written=4,
    )
    assert stats.message(u) == (
        "📊 **stash this week** · nov 23 – nov 30\n"
        "servers: 7 (+3 new)\n"
        "links saved: 42 (182 all time)\n"
        "people active: 11\n"
        "picks marked read: 18 · loved: 5\n"
        "drops sent: 9 · notes written: 4"
    )


def test_message_skips_new_servers_when_none_joined():
    u = stats.Usage(
        since=NOW - stats.WEEK, until=NOW, servers=4, new_servers=0, links_saved=0, links_all_time=0,
        people_active=0, marked_read=0, loved=0, drops_sent=0, notes_written=0,
    )
    assert "servers: 4\n" in stats.message(u)


def test_usage_counts_only_this_week(database):
    loop, pool = database

    async def seed():
        await pool.execute("INSERT INTO guilds (guild_id, created_at) VALUES (1, $1), (2, $2)", LAST_WEEK, IN_WEEK)
        link = """INSERT INTO links (guild_id, user_id, url, url_normalized, created_at, read_at, loved_at)
                  VALUES ($1, $2, $3, $3, $4, $5, $6) RETURNING id"""
        # user 10: saved last week, read + loved this week (active this week, not a new save)
        old = await pool.fetchval(link, 1, 10, "https://a.com", LAST_WEEK, IN_WEEK, IN_WEEK)
        # user 20: saved this week, untouched
        await pool.fetchval(link, 2, 20, "https://b.com", IN_WEEK, None, None)
        # user 30: everything last week: not active
        await pool.fetchval(link, 1, 30, "https://c.com", LAST_WEEK, LAST_WEEK, None)
        # user 40 only wrote a note this week
        await pool.execute(
            "INSERT INTO notes (link_id, author_id, discord_message_id, content, created_at) VALUES ($1, 40, 1, 'x', $2)",
            old, IN_WEEK,
        )
        await pool.execute(
            "INSERT INTO drops (guild_id, user_id, scheduled_for, sent_at) VALUES (1, 10, $1, $1), (1, 10, $2, $2)",
            IN_WEEK, LAST_WEEK,
        )

    loop.run_until_complete(seed())
    u = loop.run_until_complete(stats.usage(pool, servers=2, until=NOW))
    assert (u.servers, u.new_servers) == (2, 1)
    assert (u.links_saved, u.links_all_time) == (1, 3)
    assert (u.marked_read, u.loved) == (1, 1)
    assert (u.drops_sent, u.notes_written) == (1, 1)
    assert u.people_active == 3  # users 10, 20 and 40
