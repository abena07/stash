import io
import json
import zipfile
from datetime import datetime, timezone

from stash import drops, export, reactions

AMA, KOFI = 10, 20
NAMES = {AMA: "ama", KOFI: "kofi"}.get
SAVED = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
READ_AT = datetime(2026, 11, 22, 9, 30, tzinfo=timezone.utc)


def link(**overrides):
    base = dict(id=1, url="https://jvns.ca/blog/inside-git/", title="inside .git", status="read", loved=True,
                tags=["programming", "git"], created_at=SAVED, read_at=READ_AT, saver=AMA)
    return export.ExportLink(**{**base, **overrides})


def test_file_names_are_safe():
    assert export.file_name("inside .git") == "inside .git"
    assert export.file_name('a/b: "c"? <d> | e#f') == "a b c d e f"
    assert export.file_name("...") == "link"
    assert len(export.file_name("x" * 300)) == export.MAX_NAME


def test_markdown_has_frontmatter_and_notes():
    md = export.render(link(notes=[
        export.Note(AMA, "good intro", READ_AT),
        export.Note(KOFI, "skip part 3\nbut read the end", READ_AT),
    ]), NAMES)
    assert md == (
        "---\n"
        'url: "https://jvns.ca/blog/inside-git/"\n'
        'title: "inside .git"\n'
        "saved: 2026-11-20\n"
        "read: 2026-11-22\n"
        "status: read\n"
        "loved: true\n"
        'tags: ["programming", "git"]\n'
        "source: stash\n"
        "stash_id: 1\n"
        "---\n"
        "\n"
        "# inside .git\n"
        "\n"
        "https://jvns.ca/blog/inside-git/\n"
        "\n"
        "## notes\n"
        "\n"
        "- good intro (ama, 2026-11-22)\n"
        "- skip part 3\n  but read the end (kofi, 2026-11-22)\n"
    )


def test_awkward_titles_cant_break_the_frontmatter():
    md = export.render(link(title='she said "hi": a story\n---', tags=[]), NAMES)
    title_line = next(line for line in md.splitlines() if line.startswith("title: "))
    assert json.loads(title_line.removeprefix("title: ")) == 'she said "hi": a story ---'
    assert md.count("\n---\n") == 1  # only the real end of the frontmatter
    assert '\n# she said "hi": a story ---\n' in md


def test_no_title_or_read_date_and_saver_in_shared_mode():
    md = export.render(link(title=None, read_at=None, status="unread", loved=False), NAMES, show_saver=True)
    assert 'title: "jvns.ca"' in md and "\nread:" not in md
    assert 'saved_by: "ama"' in md and "loved: false" in md


def test_zip_has_one_file_per_link_in_a_folder():
    data = export.build_zip([link(id=1), link(id=2), link(id=3, title="other")], NAMES)
    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == ["stash/inside .git -2.md", "stash/inside .git.md", "stash/other.md"]


# ---- against postgres ----

async def seed(pool):
    await pool.execute("INSERT INTO guilds (guild_id) VALUES (1)")
    ids = {}
    for name, user, status, loved in [("read", AMA, "read", False), ("loved", AMA, "unread", True),
                                      ("plain", AMA, "unread", False), ("archived", AMA, "archived", False),
                                      ("kofis", KOFI, "read", False)]:
        ids[name] = await pool.fetchval(
            """INSERT INTO links (guild_id, user_id, url, url_normalized, title, status, loved)
               VALUES (1, $1, $2, $2, $3, $4::link_status, $5) RETURNING id""",
            user, f"https://example.com/{name}", name, status, loved,
        )
    for i, text in enumerate(["first", "second"]):
        await pool.execute(
            "INSERT INTO notes (link_id, author_id, discord_message_id, content) VALUES ($1, $2, $3, $4)",
            ids["read"], AMA, 100 + i, text,
        )
    return ids


def test_which_links_are_exported(database):
    loop, pool = database

    async def go():
        await seed(pool)
        titles = {}
        for which in (export.READ, export.LOVED, export.ALL):
            titles[which] = sorted(l.title for l in await export.fetch(pool, 1, AMA, which))
        titles["shared"] = sorted(l.title for l in await export.fetch(pool, 1, None, export.READ))
        notes = [n.content for n in (await export.fetch(pool, 1, AMA, export.READ))[0].notes]
        return titles, notes

    titles, notes = loop.run_until_complete(go())
    assert titles[export.READ] == ["read"]
    assert titles[export.LOVED] == ["loved"]
    assert titles[export.ALL] == ["archived", "loved", "plain", "read"]  # never kofi's in personal mode
    assert titles["shared"] == ["kofis", "read"]  # the whole pile in shared mode
    assert notes == ["first", "second"]


def test_reading_records_when_and_undo_clears_it(database):
    loop, pool = database

    async def go():
        ids = await seed(pool)
        plain = next(l for l in await drops.unread_links(pool, 1, AMA) if l.title == "plain")
        await drops.send_on_demand(pool, Pick(), 1, AMA, [plain], "📬 your picks")
        await reactions.apply(pool, 1, 4242, AMA, "read", True)
        read_at = await pool.fetchval("SELECT read_at FROM links WHERE id = $1", ids["plain"])
        await reactions.apply(pool, 1, 4242, AMA, "read", False)
        cleared = await pool.fetchval("SELECT read_at FROM links WHERE id = $1", ids["plain"])
        return read_at, cleared

    read_at, cleared = loop.run_until_complete(go())
    assert read_at is not None and cleared is None


class Pick:
    """posts the header, then the pick as message 4242."""

    def __init__(self):
        self.sent = 0

    async def send(self, content, **kwargs):
        self.sent += 1
        return type("Msg", (), {"id": 4242 if self.sent > 1 else 4241})()
