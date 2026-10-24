# stash

a discord bot that saves links from a `#read-later` channel and drops a couple of picks on a schedule.

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

## deploy to railway

1. create a project from this repo and add a postgres service.
2. on the bot service, set `DISCORD_TOKEN` and `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. deploy. `railway.json` sets the start command (`python -m stash`).

full self-hosting docs come with issue #13.
