"""buttons under a drop: ✅ and 👍🏿 for each pick, so a whole drop fits in one message.

a reaction on a message can't say which of several picks it means, so drops with more than
one pick use buttons. each button's custom_id carries the link id, so they keep working after
a restart. pressing one toggles it, and the buttons redraw to show what's been done.
"""

import logging
from dataclasses import dataclass

import discord

from . import modes, reactions

log = logging.getLogger(__name__)

PICKS_PER_ROW = 2  # ✅ and 👍🏿 for two picks per row of buttons (discord allows 5 rows)


@dataclass(frozen=True)
class PickState:
    link_id: int
    read: bool = False
    loved: bool = False


def label(action: str, n: int, on: bool) -> str:
    if action == "read":
        return f"✅ read {n}" if on else f"✅ {n}"
    return f"{reactions.LOVED} loved {n}" if on else f"{reactions.LOVED} {n}"


class PickButton(discord.ui.DynamicItem[discord.ui.Button], template=r"stash:pick:(?P<link_id>\d+):(?P<action>read|loved)"):
    def __init__(self, link_id: int, action: str, n: int = 0, on: bool = False, row: int | None = None):
        super().__init__(
            discord.ui.Button(
                label=label(action, n, on),
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
        await interaction.response.edit_message(view=await view_for_message(pool, interaction.message.id))


def build_view(picks: list[PickState]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for n, pick in enumerate(picks, 1):
        row = min((n - 1) // PICKS_PER_ROW, 4)
        view.add_item(PickButton(pick.link_id, "read", n, pick.read, row))
        view.add_item(PickButton(pick.link_id, "loved", n, pick.loved, row))
    return view


async def view_for_message(pool, message_id: int) -> discord.ui.View:
    """the buttons for a drop message, showing each pick's current state."""
    rows = await pool.fetch(
        """SELECT l.id, l.status::text AS status, l.loved FROM drop_items i
             JOIN links l ON l.id = i.link_id
            WHERE i.discord_message_id = $1 ORDER BY i.id""",
        message_id,
    )
    return build_view([PickState(r["id"], r["status"] == "read", r["loved"]) for r in rows])
