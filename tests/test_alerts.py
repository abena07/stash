import asyncio
from datetime import datetime, timezone

import discord

from stash import alerts

SLOT = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)  # a wednesday


def test_owner_message_is_plain():
    text = alerts.owner_message("my server", SLOT, "i don't have permission to post in #read-later")
    assert text.startswith("heads up: i couldn't send the wednesday drop in **my server**")
    assert "i don't have permission to post in #read-later." in text
    assert "`/picks`" in text


def test_host_message_has_what_the_host_needs():
    text = alerts.host_message("my server", 123, 456, SLOT, "something went wrong on my side")
    assert text == (
        "drop failed · my server (123) · user 456 · slot 2026-11-18T09:00:00+00:00 · "
        "something went wrong on my side"
    )


def test_host_alert_is_skipped_without_a_webhook():
    asyncio.run(alerts.tell_host(None, None, "anything"))  # no session needed, nothing sent


class FakeChannel(discord.TextChannel):
    def __init__(self, name, fail=False):  # skip discord.py's own setup
        self.name = name
        self.fail = fail
        self.sent = []

    async def send(self, text):
        if self.fail:
            response = type("Response", (), {"status": 403, "reason": "nope"})()
            raise discord.Forbidden(response, "nope")
        self.sent.append(text)


class FakeOwner:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


class FakeGuild:
    id = 1
    owner_id = 99

    def __init__(self, channel=None):
        self.owner = FakeOwner()
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel


def test_alert_goes_to_the_alert_channel_when_set():
    channel = FakeChannel("alerts")
    guild = FakeGuild(channel)
    assert asyncio.run(alerts.tell_owner(guild, 555, "oops")) == "#alerts"
    assert channel.sent == ["oops"] and guild.owner.sent == []


def test_alert_dms_the_owner_by_default():
    guild = FakeGuild()
    assert asyncio.run(alerts.tell_owner(guild, None, "oops")) == "owner dm"
    assert guild.owner.sent == ["oops"]


def test_alert_falls_back_to_a_dm_when_the_channel_fails():
    guild = FakeGuild(FakeChannel("alerts", fail=True))
    assert asyncio.run(alerts.tell_owner(guild, 555, "oops")) == "owner dm"
    assert guild.owner.sent == ["oops"]
