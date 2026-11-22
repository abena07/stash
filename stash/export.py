"""export saved links and their notes as markdown for an obsidian vault (#17).

one file per link: yaml frontmatter (url, title, dates, loved, tags) and a
body with the link and its notes in order. everything is zipped in memory.
"""

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import asyncpg

from .metadata import display_title

READ, ALL, LOVED = "read", "all", "loved"
MAX_NAME = 80  # characters in a file name, before ".md"


@dataclass
class Note:
    author_id: int
    content: str
    created_at: datetime


@dataclass
class ExportLink:
    id: int
    url: str
    title: str | None
    status: str
    loved: bool
    tags: list[str]
    created_at: datetime
    read_at: datetime | None
    saver: int
    loved_at: datetime | None = None
    notes: list[Note] = field(default_factory=list)


async def fetch(pool: asyncpg.Pool, guild_id: int, user_id: int | None, which: str) -> list[ExportLink]:
    """one person's links (user_id), or the whole server's pile (None), with their notes."""
    # "read" includes loved links: anything you loved is worth keeping, read or not
    where = {READ: "status = 'read' OR loved", LOVED: "loved", ALL: "true"}[which]
    rows = await pool.fetch(
        f"""SELECT id, url, title, status::text AS status, loved, tags, created_at, read_at, loved_at, user_id AS saver
            FROM links
            WHERE guild_id = $1 AND ($2::bigint IS NULL OR user_id = $2) AND ({where})
            ORDER BY created_at""",
        guild_id, user_id,
    )
    links = {r["id"]: ExportLink(**{**dict(r), "tags": list(r["tags"])}) for r in rows}
    if links:
        for n in await pool.fetch(
            """SELECT link_id, author_id, content, created_at FROM notes
               WHERE link_id = ANY($1::bigint[]) ORDER BY created_at, id""",
            list(links),
        ):
            links[n["link_id"]].notes.append(Note(n["author_id"], n["content"], n["created_at"]))
    return list(links.values())


def file_name(title: str) -> str:
    """a safe file name from a title: no slashes or characters obsidian or filesystems dislike."""
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .") or "link"
    return name[:MAX_NAME].rstrip(" .")


def _yaml(value: str) -> str:
    # json strings are valid yaml and escape everything that could break the frontmatter
    return json.dumps(value, ensure_ascii=False)


def render(link: ExportLink, name_of: Callable[[int], str], show_saver: bool = False) -> str:
    title = " ".join(display_title(link.title, link.url).split())  # one line, so it can't break the heading
    lines = [
        "---",
        f"url: {_yaml(link.url)}",
        f"title: {_yaml(title)}",
        f"saved: {link.created_at:%Y-%m-%d}",
    ]
    if link.read_at:
        lines.append(f"read: {link.read_at:%Y-%m-%d}")
    if link.loved and link.loved_at:
        lines.append(f"loved_on: {link.loved_at:%Y-%m-%d}")
    lines.append(f"status: {link.status}")
    lines.append(f"loved: {'true' if link.loved else 'false'}")
    lines.append(f"tags: [{', '.join(_yaml(t) for t in link.tags)}]")
    if show_saver:
        lines.append(f"saved_by: {_yaml(name_of(link.saver))}")
    lines += ["source: stash", f"stash_id: {link.id}", "---", "", f"# {title}", "", link.url, ""]
    if link.notes:
        lines += ["## notes", ""]
        for note in link.notes:
            text = note.content.strip().replace("\n", "\n  ")  # keep multi-line notes inside their bullet
            lines.append(f"- {text} ({name_of(note.author_id)}, {note.created_at:%Y-%m-%d})")
        lines.append("")
    return "\n".join(lines)


def files(links: list[ExportLink], name_of: Callable[[int], str],
          show_saver: bool = False) -> list[tuple[str, str, ExportLink]]:
    """(file name, markdown, link) for each link. same-titled links get -2, -3…

    the zip and the obsidian plugin both use this, so their files match exactly.
    """
    used: dict[str, int] = {}
    out = []
    for link in links:
        base = file_name(display_title(link.title, link.url))
        key = base.lower()
        used[key] = used.get(key, 0) + 1
        name = base if used[key] == 1 else f"{base} -{used[key]}"
        out.append((f"{name}.md", render(link, name_of, show_saver), link))
    return out


def build_zip(links: list[ExportLink], name_of: Callable[[int], str], show_saver: bool = False,
              folder: str = "stash") -> bytes:
    """a zip with one markdown file per link inside `folder/`."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content, _ in files(links, name_of, show_saver):
            zf.writestr(f"{folder}/{name}", content)
    return buffer.getvalue()
