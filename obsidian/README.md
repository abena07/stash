# stash for obsidian

syncs the links and notes you save with the [stash](https://stash.bennett-eghan.com) discord bot into a folder in your vault.

## set up
1. in discord, run `/connect obsidian`. stash shows you a code that only you can see.
2. install this plugin, open its settings and paste the code.
3. that's it. it syncs when obsidian opens, every 30 minutes while it's open, and whenever you run **stash: sync now**.

## what it does
- one markdown file per link in `stash/` (you can change the folder), with frontmatter (url, title, saved and read dates, loved, tags) and your notes from the link's discord thread.
- by default only links you've marked ✅ read. you can pick loved or all.
- **it never overwrites a file you've edited, and never brings back one you've deleted.**
- if a link's title changes, its file is renamed (obsidian keeps links to it working).
- files you downloaded with `/export` are picked up if they're unchanged.

## privacy
the code works like a password and only ever shows your own links (or your server's shared pile). `/disconnect obsidian` turns it off. see stash's [privacy policy](https://stash.bennett-eghan.com/privacy.html).

## self-hosting
if you run your own copy of stash, set **server** in the plugin's settings to your bot's address.

## developing
```sh
npm install
npm test        # the sync rules
npm run build   # main.js
```
copy `main.js` and `manifest.json` into `<your vault>/.obsidian/plugins/stash-sync/` to try it.
