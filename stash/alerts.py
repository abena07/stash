"""telling people when a drop has failed for good (#16)."""

import logging
from datetime import datetime

import aiohttp
import discord

from .drops import DAY_NAMES

log = logging.getLogger(__name__)


def owner_message(guild_name: str, slot: datetime, reason: str) -> str:
    day = DAY_NAMES[slot.isoweekday()]
    return (
        f"heads up: i couldn't send the {day} drop in **{guild_name}**, even after retrying. "
        f"{reason}.\n"
        "once that's fixed, `/picks` sends one straight away, or the next drop goes out as usual."
    )


def host_message(guild_name: str, guild_id: int, user_id: int, slot: datetime, reason: str) -> str:
    return (
        f"drop failed · {guild_name} ({guild_id}) · user {user_id} · "
        f"slot {slot.isoformat()} · {reason}"
    )


async def tell_owner(guild: discord.Guild, alert_channel_id: int | None, text: str) -> str:
    """post in the server's alert channel if one is set, else dm the owner. returns where it went."""
    if alert_channel_id:
        channel = guild.get_channel(alert_channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(text)
                return f"#{channel.name}"
            except discord.HTTPException as e:
                log.warning("guild %s: alert channel failed, dming the owner: %s", guild.id, e)
    owner = guild.owner or await guild.fetch_member(guild.owner_id)
    await owner.send(text)
    return "owner dm"


async def tell_host(session: aiohttp.ClientSession, webhook_url: str | None, text: str) -> None:
    if not webhook_url:
        return  # host alerts are optional
    webhook = discord.Webhook.from_url(webhook_url, session=session)
    await webhook.send(text, username="stash alerts", allowed_mentions=discord.AllowedMentions.none())
