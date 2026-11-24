"""how much stash is being used: a weekly summary for the host.

counts only. nothing here reads what people saved or wrote.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

WEEK = timedelta(days=7)


@dataclass(frozen=True)
class Usage:
    since: datetime
    until: datetime
    servers: int  # servers the bot is in right now
    new_servers: int
    links_saved: int
    links_all_time: int
    people_active: int  # saved, read, loved or wrote a note
    marked_read: int
    loved: int
    drops_sent: int
    notes_written: int


async def usage(pool, servers: int, until: datetime, period: timedelta = WEEK) -> Usage:
    since = until - period
    row = await pool.fetchrow(
        """SELECT
             (SELECT count(*) FROM guilds WHERE created_at >= $1 AND created_at < $2) AS new_servers,
             (SELECT count(*) FROM links WHERE created_at >= $1 AND created_at < $2) AS links_saved,
             (SELECT count(*) FROM links WHERE created_at < $2) AS links_all_time,
             (SELECT count(*) FROM links WHERE read_at >= $1 AND read_at < $2) AS marked_read,
             (SELECT count(*) FROM links WHERE loved_at >= $1 AND loved_at < $2) AS loved,
             (SELECT count(*) FROM drops WHERE sent_at >= $1 AND sent_at < $2) AS drops_sent,
             (SELECT count(*) FROM notes WHERE created_at >= $1 AND created_at < $2) AS notes_written,
             (SELECT count(DISTINCT who) FROM (
                SELECT user_id AS who FROM links
                 WHERE (created_at >= $1 AND created_at < $2)
                    OR (read_at >= $1 AND read_at < $2)
                    OR (loved_at >= $1 AND loved_at < $2)
                UNION
                SELECT author_id FROM notes WHERE created_at >= $1 AND created_at < $2
             ) people) AS people_active""",
        since, until,
    )
    return Usage(since=since, until=until, servers=servers, **dict(row))


def _day(d: datetime) -> str:
    return f"{d:%b} {d.day}".lower()


def message(u: Usage) -> str:
    new = f" (+{u.new_servers} new)" if u.new_servers else ""
    return "\n".join([
        f"📊 **stash this week** · {_day(u.since)} – {_day(u.until)}",
        f"servers: {u.servers}{new}",
        f"links saved: {u.links_saved} ({u.links_all_time} all time)",
        f"people active: {u.people_active}",
        f"picks marked read: {u.marked_read} · loved: {u.loved}",
        f"drops sent: {u.drops_sent} · notes written: {u.notes_written}",
    ])
