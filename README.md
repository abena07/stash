# stash

a discord bot that saves links from a `#read-later` channel and drops a couple of picks on a schedule.

![stash system design](docs/system-design.png)

## run locally

needs python 3.13 and postgres.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN
createdb stash
set -a && source .env && set +a
python -m stash
```

migrations in `migrations/` apply automatically on startup.

in the discord developer portal, enable the **message content** intent for the bot, and invite it with the `bot` and `applications.commands` scopes. set `DEV_GUILD_ID` to your test server so slash commands show up instantly.

make a channel named `#read-later` (or pick any channel with `/setup channel`) and give the bot **view channel**, **send messages**, **add reactions**, **read message history**, **create public threads** and **send messages in threads** there. any link posted in it is saved: 🔖 means saved, 🔁 means it's already in your queue. each saved post gets a thread named after the page; anything you write in it is kept as a note on the link.

by default everyone in a server has their own queue. `/setup mode:shared` turns the server into one reading pile: each drop is one set of picks for the group, starting with @everyone and showing who saved each link. for that @everyone to notify people, also give the bot **mention @everyone, @here, and all roles** in `#read-later`.

## tests

```sh
pip install -r requirements-dev.txt
pytest
```

## deploy to railway

1. create a project from this repo and add a postgres service.
2. on the bot service, set `DISCORD_TOKEN` and `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. deploy. `railway.json` sets the start command (`python -m stash`).

full self-hosting docs come with issue #13.
