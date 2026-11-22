"""buttons under a drop, one row per pick: [✅ <title>] [👍🏿 love].

a reaction on a message can't say which of several picks it means, so drops use buttons named
after each pick. each button's custom_id carries the link id, so they keep working after a
restart. pressing one toggles it: the button turns green ("✅ read · <title>", "👍🏿 loved")
and the pick's grey line in the message says "✅ read" / "👍🏿 loved".
"""

import logging
from dataclasses import dataclass

import discord

from . import modes, reactions
from .metadata import display_title

log = logging.getLogger(__name__)

MAX_ROWS = 5  # discord allows 5 rows of buttons, so 5 picks get buttons
TITLE_IN_BUTTON = 45  # characters of the title shown on a button
READ_MARK = "✅ read"
LOVED_MARK = f"{reactions.LOVED} loved"


@dataclass(frozen=True)
class PickState:
    link_id: int
    title: str = ""
    read: bool = False
    loved: bool = False


def short(title: str) -> str:
    title = " ".join(title.split())
    return title if len(title) <= TITLE_IN_BUTTON else title[: TITLE_IN_BUTTON - 1].rstrip() + "…"


def label(action: str, title: str, on: bool) -> str:
    if action == "read":
        return f"✅ read · {short(title)}" if on else f"✅ {short(title)}"
    return LOVED_MARK if on else f"{reactions.LOVED} love"


class PickButton(discord.ui.DynamicItem[discord.ui.Button], template=r"stash:pick:(?P<link_id>\d+):(?P<action>read|loved)"):
    def __init__(self, link_id: int, action: str, title: str = "", on: bool = False, row: int | None = None):
        super().__init__(
            discord.ui.Button(
                label=label(action, title, on),
                style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
                custom_id=f"stash:pick:{link_id}:{action}",
                row=row,
            )
        )
        self.link_id = link_id
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["link_id"]), match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        pool = interaction.client.pool
        link = await pool.fetchrow(
            "SELECT user_id, status::text AS status, loved FROM links WHERE id = $1 AND guild_id = $2",
            self.link_id, interaction.guild_id,
        )
        if link is None:
            await interaction.response.send_message("that link no longer exists", ephemeral=True)
            return
        shared = await modes.is_shared(pool, interaction.guild_id)
        if link["user_id"] != interaction.user.id and not shared:
            await interaction.response.send_message("that's not your link", ephemeral=True)
            return

        currently_on = link["status"] == "read" if self.action == "read" else link["loved"]
        await reactions.set_state(pool, [self.link_id], self.action, not currently_on)
        log.info("guild %s: %s %s on link %s via button", interaction.guild_id,
                 "unset" if currently_on else "set", self.action, self.link_id)
        picks = await states_for_message(pool, interaction.message.id)
        await interaction.response.edit_message(
            content=with_status(interaction.message.content, picks), view=build_view(picks)
        )


def build_view(picks: list[PickState]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for row, pick in enumerate(picks[:MAX_ROWS]):
        view.add_item(PickButton(pick.link_id, "read", pick.title, pick.read, row))
        view.add_item(PickButton(pick.link_id, "loved", pick.title, pick.loved, row))
    return view


async def states_for_message(pool, message_id: int) -> list[PickState]:
    """each pick in a drop message, in order, with its current state."""
    rows = await pool.fetch(
        """SELECT l.id, l.title, l.url, l.status::text AS status, l.loved FROM drop_items i
             JOIN links l ON l.id = i.link_id
            WHERE i.discord_message_id = $1 ORDER BY i.id""",
        message_id,
    )
    return [PickState(r["id"], display_title(r["title"], r["url"]), r["status"] == "read", r["loved"]) for r in rows]


def with_status(content: str, picks: list[PickState]) -> str:
    """the drop message with each pick's grey line saying whether it's read and/or loved."""
    lines = content.split("\n")
    for n, pick in enumerate(picks, 1):
        heading = next((i for i, line in enumerate(lines) if line.startswith(f"**{n}. ")), None)
        if heading is None:
            continue
        marks = [m for m, on in ((READ_MARK, pick.read), (LOVED_MARK, pick.loved)) if on]
        has_details = heading + 1 < len(lines) and lines[heading + 1].startswith("-# ")
        # the grey line's parts, without any old marks (reading time, tags and saved-by stay)
        parts = [part for part in lines[heading + 1][3:].split(" · ") if part not in (READ_MARK, LOVED_MARK)] \
            if has_details else []
        new = parts + marks
        if has_details and new:
            lines[heading + 1] = "-# " + " · ".join(new)
        elif has_details:
            del lines[heading + 1]  # the grey line only held marks, now undone
        elif new:
            lines.insert(heading + 1, "-# " + " · ".join(new))
    return "\n".join(lines)
