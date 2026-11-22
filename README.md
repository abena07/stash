# stash

a small discord bot that saves the links you post and brings two back every wednesday and sunday, so they actually get read.

**[add stash to discord](https://stash.bennett-eghan.com)** · [obsidian plugin](https://github.com/abena07/stash-obsidian) · [self-host it](docs/self-hosting.md)

![stash system design](docs/system-design.png)

## what it does

- **saves links** posted in `#read-later` (or any channel you pick with `/setup channel`): 🔖 when saved, 🔁 if it's already in your queue.
- **opens a notes thread** on each saved post, named after the page with its reading time. anything written there is kept as a note.
- **drops picks** on your schedule (default wednesday and sunday, 09:00 utc), favouring older and shorter reads.
- **✅ marks read, 👍 marks loved.** links sent three times without being read are archived.
- **personal or shared:** your own queue, or one pile for a group server. when stash joins a server it posts a hello asking which ("just me" or "our group"); change it later with `/setup mode`.
- **optional ai tags** with your own key (`/setkey`: claude, openai, gemini, deepseek or openrouter), stored encrypted.
- **export:** `/export` for a markdown zip, or the [obsidian plugin](https://github.com/abena07/stash-obsidian) to sync into your vault and make a monthly roundup of what you loved.

## commands

| command | what it does |
|---|---|
| `/picks` | two picks from your queue, right now |
| `/random` | one random unread link |
| `/schedule` | see or change drop days, time and timezone |
| `/setup` | read-later channel, personal or shared mode, and where alerts go |
| `/export` | your links and notes as markdown |
| `/setkey` | bring your own ai key for tagging |
| `/connect obsidian` | a code for the obsidian plugin |

## self-hosting and development

see **[docs/self-hosting.md](docs/self-hosting.md)**: creating the discord bot, deploying on railway (or anywhere with python and postgres), every setting, running it locally, and the tests.

## how it's built

one python service (discord.py + asyncpg) and one postgres database. everything is stored per server and per person. [`docs/system-design.md`](docs/system-design.md) has the design notes behind the drawing above.

| folder | what's in it |
|---|---|
| `stash/` | the bot: saving, drops, reactions, notes, tagging, export, the plugin api |
| `migrations/` | database changes, applied on startup |
| `obsidian/` | the obsidian plugin (mirrored to [stash-obsidian](https://github.com/abena07/stash-obsidian)) |
| `site/` | the landing page, privacy policy and terms |
| `tests/` | `pytest`, run in ci on every pull request |

## license

[mit](LICENSE)
