import { test } from "node:test";
import assert from "node:assert/strict";
import { fileName, mdxText, monthOf, parseStashFile, pick, postDate, render, type Entry } from "../src/roundup.ts";

// a file exactly as stash writes it (see stash/export.py)
const STASH_FILE = `---
url: "https://www.paulgraham.com/greatwork.html"
title: "How to Do Great Work"
saved: 2026-11-22
read: 2026-11-23
status: read
loved: true
tags: ["career", "productivity"]
source: stash
stash_id: 4
---

# How to Do Great Work

https://www.paulgraham.com/greatwork.html

## notes

- wow paul (abena, 2026-11-22)
- second thought (abena, 2026-11-23)
`;

test("reads stash's own files back", () => {
  assert.deepEqual(parseStashFile(STASH_FILE), {
    url: "https://www.paulgraham.com/greatwork.html",
    title: "How to Do Great Work",
    read: "2026-11-23",
    loved: true,
    tags: ["career", "productivity"],
    note: "wow paul",
  });
});

test("ignores other notes in the folder and links never read", () => {
  assert.equal(parseStashFile("# my own note\n\nnot from stash"), null);
  assert.equal(parseStashFile("---\ntitle: x\n---\nbody"), null);
  const unread = parseStashFile(STASH_FILE.replace("read: 2026-11-23\n", "").replace("status: read", "status: unread"));
  assert.equal(unread?.read, null);
});

const e = (title: string, read: string | null, loved = false, tags: string[] = [], note: string | null = null): Entry =>
  ({ url: `https://example.com/${title}`, title, read, loved, tags, note });

test("picks the month's reads, loved first, then most recent", () => {
  const entries = [
    e("old", "2026-10-30", true), e("a", "2026-11-02"), e("b", "2026-11-20"),
    e("loved", "2026-11-01", true), e("never read", null, true),
  ];
  assert.deepEqual(pick(entries, "2026-11", 7).map((x) => x.title), ["loved", "b", "a"]);
  assert.deepEqual(pick(entries, "2026-11", 2).map((x) => x.title), ["loved", "b"]);
});

test("renders a post in the website's format, grouped by tag", () => {
  const post = render([
    e("how-to-do-great-work", "2026-11-23", true, ["career"], "wow paul"),
    e("inside-git", "2026-11-21", false, ["programming"]),
    e("untagged", "2026-11-05"),
  ], "2026-11", "november 30, 2026", true);
  assert.equal(post, `export const meta = {
  title: "stash roundup: november 2026",
  date: "november 30, 2026",
  tags: ["stash", "reading"]
}

my favourite reads from november.

### career

- [how-to-do-great-work](https://example.com/how-to-do-great-work): wow paul

### programming

- [inside-git](https://example.com/inside-git)

### everything else

- [untagged](https://example.com/untagged)
`);
});

test("spaces and brackets in urls are encoded so the markdown link holds", () => {
  const post = render([{ ...e("x", "2026-11-02"), url: "https://en.wikipedia.org/wiki/Foo_(bar) baz" }],
    "2026-11", "november 30, 2026", true);
  assert.ok(post.includes("(https://en.wikipedia.org/wiki/Foo_%28bar%29%20baz)"));
});

test("no headings when nothing is tagged, and notes can be left out", () => {
  const post = render([e("a", "2026-11-02", false, [], "hidden")], "2026-11", "november 30, 2026", false);
  assert.ok(post.endsWith("my favourite reads from november.\n\n- [a](https://example.com/a)\n"));
});

test("titles and notes can't break the site's mdx", () => {
  assert.equal(mdxText("react {hooks} <div> [x]"), "react \\{hooks\\} \\<div\\> \\[x\\]");
  assert.equal(mdxText("two\n lines"), "two lines");
});

test("months, file names and dates", () => {
  const today = new Date(Date.UTC(2026, 10, 22)); // 22 november 2026
  assert.equal(monthOf(today, 0), "2026-11");
  assert.equal(monthOf(today, -1), "2026-10");
  assert.equal(monthOf(new Date(Date.UTC(2026, 0, 5)), -1), "2025-12");
  assert.equal(fileName("2026-11"), "stash-roundup-2026-11.md");
  assert.equal(postDate("2026-10", today), "october 31, 2026"); // a finished month ends on its last day
  assert.equal(postDate("2026-11", today), "november 22, 2026"); // this month so far: today
});
