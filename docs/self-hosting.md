# self-hosting stash

most people can just [add the public bot](https://stash.bennett-eghan.com). run your own copy if you want your links, notes and keys in a database you control, want to change how stash works, or want it to keep running whatever happens to the public one.

you'll need:
- a discord account (to create the bot)
- a host that runs python 3.13 and postgres. this guide uses [railway](https://railway.com), but any host works
- about 20 minutes

## 1. create the discord bot

1. go to the [discord developer portal](https://discord.com/developers/applications) and click **new application**. name it whatever you like.
2. **bot** tab:
   - click **reset token** and copy the token. you'll need it in step 3. keep it secret.
   - turn on **message content intent**, then save. without it, stash can't see the links people post.
3. **installation** tab, under **guild install**:
   - scopes: `bot`, `applications.commands`
   - permissions: view channels, send messages, add reactions, read message history, create public threads, send messages in threads, mention everyone (only needed for shared mode)
4. copy the install link and open it to add the bot to your server.
5. optional, if other people will add your copy: under **general information**, fill in a privacy policy and terms of service url.

## 2. deploy on railway

1. create a project from your fork of this repo (**new project → deploy from github repo**).
2. add a database: **new → database → postgres**.
3. on the bot service, open **variables** and add:

| variable | value | needed? |
|---|---|---|
| `DISCORD_TOKEN` | the token from step 1 | yes |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | yes |
| `STASH_SECRET_KEY` | run `openssl rand -base64 32 \| tr '+/' '-_'` and paste the result | for `/setkey` |
| `ALERT_WEBHOOK_URL` | a discord webhook url (channel settings → integrations → webhooks) | optional |
| `LOG_LEVEL` | `INFO` | optional |

don't set `DEV_GUILD_ID` here. it's only for development (see below).

4. deploy. `railway.json` sets the start command (`python -m stash`). the database tables are created on first start, and every later change to them is applied automatically.
5. check the deploy logs for `logged in as <your bot>`. slash commands can take up to an hour to appear the first time.

**keep `STASH_SECRET_KEY` somewhere safe**, like a password manager. it encrypts the ai keys servers add with `/setkey`. if it's lost or changed, those keys can't be read any more and each server has to run `/setkey` again. nothing else breaks.

### other hosts

stash is one long-running python process plus postgres. on any host:

```sh
pip install -r requirements.txt
export DISCORD_TOKEN=... DATABASE_URL=postgresql://... STASH_SECRET_KEY=...
python -m stash
```

stash is tested with postgres 16. it doesn't need a public address unless you use the obsidian plugin (below).

## 3. set up your server

1. make a text channel called `#read-later`, or run `/setup channel:#any-channel` to use another one.
2. make sure the bot has the permissions from step 1 in that channel.
3. post a link. stash reacts 🔖 and opens a notes thread.

| command | what it does |
|---|---|
| `/setup` | read-later channel, personal or shared mode, and where failed-drop alerts go |
| `/schedule` | drop days, hour and timezone (default wednesday and sunday, 09:00 utc) |
| `/picks`, `/random` | picks right now |
| `/export` | a zip of your links as markdown |
| `/setkey` | bring your own ai key (claude, openai, gemini, deepseek or openrouter) to tag links |
| `/connect obsidian` | a code for the obsidian plugin |
| `/ping` | check the bot and database are up |

## 4. optional: the obsidian plugin

the plugin pulls links from a small http api the bot serves when the `PORT` variable is set. railway sets `PORT` for you.

1. on the bot service, **settings → networking → generate domain** (or add your own domain).
2. in obsidian's stash plugin settings, set **server** to that address, e.g. `https://your-bot.up.railway.app`.
3. run `/connect obsidian` in discord and paste the code into the plugin.

the api only answers requests carrying a code, and each code only sees its owner's links.

## updating

pull the latest changes into your fork. railway redeploys on every push to your main branch. database changes in `migrations/` apply on startup.

## development

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # add your test bot's token
createdb stash
set -a && source .env && set +a
python -m stash
```

- use a **separate test bot** and a **separate test server**, and set `DEV_GUILD_ID` to that server's id. slash commands then appear instantly, and your test bot never doubles up with the real one.
- set `PORT=8787` to serve the plugin api locally.

tests:

```sh
createdb stash_test   # the database tests use; they're skipped if it's missing
pytest
cd obsidian && npm install && npm test
```

## troubleshooting

- **no reaction when posting a link:** check the message content intent is on, and that the bot can view, send and react in the channel. `/setup` shows which channel it's watching.
- **slash commands missing:** global commands take up to an hour the first time. in development, set `DEV_GUILD_ID`.
- **no notes thread:** the bot needs create public threads and send messages in threads.
- **@everyone doesn't notify in shared mode:** the bot needs mention @everyone, @here, and all roles.
- **drops aren't arriving:** `/schedule` shows when they're due. a drop only includes links saved before its time. failed drops alert the server owner after 3 retries.
