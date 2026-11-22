"""the welcome stash posts when it joins a server, asking personal or shared."""

from types import SimpleNamespace

import discord

from stash import modes, welcome

GUILD = 7


class Channel:
    def __init__(self, id, can_post=True):
        self.id, self.mention, self.can_post = id, f"<#{id}>", can_post
        self.sent = []

    def permissions_for(self, member):
        return SimpleNamespace(view_channel=self.can_post, send_messages=self.can_post)

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))


def guild(read_later=None, system=None, others=()):
    text = [c for c in (read_later, system, *others) if c is not None]
    return SimpleNamespace(id=GUILD, name="friends", owner_id=99, me=object(),
                           system_channel=system, text_channels=text)


def test_welcome_names_the_read_later_channel_or_says_how_to_make_one():
    with_channel = welcome.welcome_text(Channel(5))
    assert "post links in <#5>" in with_channel and "/setup channel" in with_channel  # another channel is fine too
    text = welcome.welcome_text(None)
    assert "/setup channel" in text and "don't have one yet? make it" in text
    assert "**just me**" in text and "**our group**" in text and "/setup mode" in text


def test_two_buttons_that_keep_working_after_a_restart():
    view = welcome.build_view()
    assert [(i.item.custom_id, i.item.label) for i in view.children] == [
        ("stash:mode:personal", "just me"),
        ("stash:mode:shared", "our group"),
    ]
    assert view.timeout is None


def test_posts_in_read_later_then_system_channel_then_any_channel_it_can_use():
    read_later, system, general = Channel(1), Channel(2), Channel(3)
    assert welcome.where_to_post(guild(read_later, system, [general]), read_later) is read_later
    assert welcome.where_to_post(guild(None, system, [general]), None) is system
    blocked = Channel(2, can_post=False)
    assert welcome.where_to_post(guild(None, blocked, [general]), None) is general
    assert welcome.where_to_post(guild(None, blocked), None) is None


def test_choosing_a_mode_drops_the_buttons_and_says_who_chose():
    text = welcome.chosen_text("hi", modes.SHARED, 42)
    assert text == "hi\n\n**our group** it is (shared mode), chosen by <@42>."


def interaction(pool, manage):
    responses = []

    async def edit_message(**kwargs):
        responses.append(("edit", kwargs))

    async def send_message(content, **kwargs):
        responses.append(("send", content, kwargs))

    return responses, SimpleNamespace(
        client=SimpleNamespace(pool=pool), guild_id=GUILD, message=SimpleNamespace(content="hi"),
        user=SimpleNamespace(id=42, guild_permissions=SimpleNamespace(manage_guild=manage)),
        response=SimpleNamespace(edit_message=edit_message, send_message=send_message),
    )


def test_pressing_our_group_makes_the_server_shared(database):
    loop, pool = database
    responses, press = interaction(pool, manage=True)
    loop.run_until_complete(welcome.ModeButton(modes.SHARED).callback(press))
    assert loop.run_until_complete(modes.mode(pool, GUILD)) == modes.SHARED
    kind, kwargs = responses[0]
    assert kind == "edit" and kwargs["view"] is None and "chosen by <@42>" in kwargs["content"]


def test_only_server_managers_can_choose(database):
    loop, pool = database
    responses, press = interaction(pool, manage=False)
    loop.run_until_complete(welcome.ModeButton(modes.SHARED).callback(press))
    assert loop.run_until_complete(modes.mode(pool, GUILD)) == modes.PERSONAL
    assert responses[0][0] == "send" and responses[0][2] == {"ephemeral": True}


def test_welcome_is_posted_with_buttons(database):
    loop, pool = database
    general = Channel(3)

    async def find(g):
        return None

    bot = SimpleNamespace(read_later=SimpleNamespace(find=find))
    loop.run_until_complete(welcome.send(bot, guild(None, None, [general])))
    content, kwargs = general.sent[0]
    assert content.startswith("👋🏿 hi, i'm **stash**")
    assert isinstance(kwargs["view"], discord.ui.View)


def test_dms_the_owner_without_buttons_when_it_cant_post_anywhere(database):
    loop, pool = database
    dms = []

    async def find(g):
        return None

    async def fetch_user(uid):
        async def send(content, **kwargs):
            dms.append((uid, content, kwargs))
        return SimpleNamespace(send=send)

    bot = SimpleNamespace(read_later=SimpleNamespace(find=find), fetch_user=fetch_user)
    loop.run_until_complete(welcome.send(bot, guild(None, Channel(2, can_post=False))))
    uid, content, kwargs = dms[0]
    assert uid == 99 and content.startswith("(from **friends**)") and "view" not in kwargs
