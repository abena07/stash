"""one-message drops with ✅/👍🏿 buttons per pick."""

import itertools
from types import SimpleNamespace

from stash import buttons, drops, reactions

_ids = itertools.count(70_000)


class Channel:
    def __init__(self):
        self.sent, self.kwargs = [], []

    async def send(self, content, **kwargs):
        self.sent.append(content)
        self.kwargs.append(kwargs)
        return SimpleNamespace(id=next(_ids))


def ids_and_labels(view):
    return [(item.item.custom_id, item.item.label, item.item.style.name, item.item.row) for item in view.children]


def test_one_row_per_pick_named_after_it():
    view = buttons.build_view([buttons.PickState(11, "How to Do Great Work"),
                               buttons.PickState(12, "How to Work Hard", read=True, loved=True)])
    assert ids_and_labels(view) == [
        ("stash:pick:11:read", "✅ How to Do Great Work", "secondary", 0),
        ("stash:pick:11:loved", "👍🏿 love", "secondary", 0),
        ("stash:pick:12:read", "✅ read · How to Work Hard", "success", 1),
        ("stash:pick:12:loved", "👍🏿 loved", "success", 1),
    ]
    assert view.timeout is None  # buttons don't expire


def test_every_pick_in_a_drop_gets_buttons():
    assert drops.PICKS_PER_DROP <= buttons.MAX_ROWS


def test_long_titles_are_shortened_on_buttons():
    view = buttons.build_view([buttons.PickState(1, "x" * 200)])
    assert len(view.children[0].item.label) <= 2 + buttons.TITLE_IN_BUTTON


def test_at_most_five_picks_get_buttons():
    view = buttons.build_view([buttons.PickState(i, f"p{i}") for i in range(7)])
    assert [item.item.row for item in view.children] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]


MESSAGE = (
    "📬 your picks\n\n"
    "**1. How to Do Great Work**\n-# ⏱️ 52 min read\nhttps://a.com/1\n\n"
    "**2. No details**\nhttps://a.com/2"
)


def test_status_shows_on_each_pick_and_can_be_undone():
    both = buttons.with_status(MESSAGE, [buttons.PickState(1, read=True, loved=True), buttons.PickState(2, loved=True)])
    assert "-# ⏱️ 52 min read · ✅ read · 👍🏿 loved\nhttps://a.com/1" in both
    assert "**2. No details**\n-# 👍🏿 loved\nhttps://a.com/2" in both  # a grey line appears when needed
    undone = buttons.with_status(both, [buttons.PickState(1), buttons.PickState(2)])
    assert "-# ⏱️ 52 min read\nhttps://a.com/1" in undone and "· ✅ read" not in undone
    assert "-# 👍🏿 loved" not in undone


async def seed(pool, links=2):
    await pool.execute("INSERT INTO guilds (guild_id) VALUES (1)")
    for i in range(links):
        await pool.execute(
            """INSERT INTO links (guild_id, user_id, url, url_normalized, title)
               VALUES (1, 10, $1, $1, $2)""",
            f"https://example.com/{i}", f"post {i}",
        )


def test_a_drop_is_one_message_with_buttons_and_no_previews(database):
    loop, pool = database
    channel = Channel()

    async def go():
        await seed(pool)
        links = await drops.unread_links(pool, 1, 10)
        await drops.send_on_demand(pool, channel, 1, 10, links, "📬 your picks")
        return await pool.fetch("SELECT link_id, discord_message_id FROM drop_items ORDER BY id")

    items = loop.run_until_complete(go())
    assert len(channel.sent) == 1
    kwargs = channel.kwargs[0]
    assert kwargs["suppress_embeds"] is True  # no big preview cards
    assert len(kwargs["view"].children) == 4
    assert len({i["discord_message_id"] for i in items}) == 1  # both picks point at the one message


def test_pressing_a_button_redraws_with_the_new_state(database):
    loop, pool = database
    channel = Channel()

    async def go():
        await seed(pool)
        links = await drops.unread_links(pool, 1, 10)
        await drops.send_on_demand(pool, channel, 1, 10, links, "📬 your picks")
        message_id = await pool.fetchval("SELECT discord_message_id FROM drop_items LIMIT 1")
        first = await pool.fetchval("SELECT link_id FROM drop_items ORDER BY id LIMIT 1")
        await reactions.set_state(pool, [first], "loved", True)  # what the 👍🏿 1 button does
        return buttons.build_view(await buttons.states_for_message(pool, message_id))

    labels = [item.item.label for item in loop.run_until_complete(go()).children]
    assert labels == ["✅ post 0", "👍🏿 loved", "✅ post 1", "👍🏿 love"]


def test_a_reaction_on_a_multi_pick_message_changes_nothing(database):
    loop, pool = database
    channel = Channel()

    async def go():
        await seed(pool)
        links = await drops.unread_links(pool, 1, 10)
        await drops.send_on_demand(pool, channel, 1, 10, links, "📬 your picks")
        message_id = await pool.fetchval("SELECT discord_message_id FROM drop_items LIMIT 1")
        return await reactions.apply(pool, 1, message_id, 10, "read", True)

    assert loop.run_until_complete(go()) == 0  # it can't tell which pick; the buttons handle that


def test_a_reaction_on_a_single_pick_still_works(database):
    loop, pool = database
    channel = Channel()

    async def go():
        await seed(pool, links=1)
        links = await drops.unread_links(pool, 1, 10)
        await drops.send_on_demand(pool, channel, 1, 10, links, "🎲 a random pick")
        message_id = await pool.fetchval("SELECT discord_message_id FROM drop_items")
        return await reactions.apply(pool, 1, message_id, 10, "read", True)

    assert loop.run_until_complete(go()) == 1
