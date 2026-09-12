# Himyar Levels

XP and levels for Discord, from messages and voice.

Part of the [Himyar](https://himyar.org) bot suite. Built with `discord.py`.

---

## What it does

Members earn XP by chatting and by being in voice calls, level up, and earn roles
at levels you choose. `/rank` shows progress, `/leaderboard` shows the top of the
server split by text, voice or combined.

## Multi-server by design

No guild, channel, or role id is hardcoded. Every setting and every member's XP
lives in SQLite keyed by `guild_id`, so a member's level on one server has nothing
to do with their level on another.

**There is no dashboard and no website.** All configuration happens in Discord.

## The curve

Going from level n to n+1 costs `5n² + 50n + 100` XP — the curve most Discord
users already know. Familiarity matters here: members arrive with an intuition for
what level 10 means, and a novel curve makes the number meaningless to them.

At the default 15–25 XP per message:

| Level | Messages |
| --- | --- |
| 5 | ~58 |
| 10 | ~234 |
| 20 | ~1,192 |
| 50 | ~13,400 |

`/config text rate:` scales all of it. The command shows you the new message
counts when you change it, so you can see what you've done before members do.

## Voice XP and anti-farm

Voice XP is the part people try hardest to cheat. Four protections, all on by
default and each independently switchable:

- **Needs company** — no XP alone in a channel. Kills the overnight-idle exploit.
- **No XP muted or deafened** — self-muted *or* server-muted by a moderator.
- **AFK channel ignored.**
- **Daily cap** — default 2,000 XP per member per day, so the leaderboard isn't
  just whoever leaves Discord open longest.

All 256 combinations of these rules are verified by test.

XP is awarded by a ticking loop that looks at who's in voice right now, rather
than by remembering when people joined. A redeploy in the middle of a busy call
costs nobody anything.

## Commands

### Everyone

| Command | What it does |
| --- | --- |
| `/rank` | Level, XP, progress bar, rank, message and voice stats |
| `/leaderboard` | Top members — combined, text only, or voice only, paged |
| `/levelroles` | Which roles can be earned, and roughly how much chat each takes |

### Staff

| Command | What it does |
| --- | --- |
| `/setup` | One command to configure the server |
| `/config view` | Every setting at a glance |
| `/config text` | On/off, XP per message, cooldown, overall rate |
| `/config voice` | On/off, XP per minute, daily cap, and each anti-farm rule |
| `/config levelup` | In-channel, one dedicated channel, DM, or off |
| `/config roles` | Whether level roles stack or replace |
| `/config exclude add` · `remove` · `list` | Channels that earn no XP — text, voice or a whole category |
| `/config multiplier add` · `remove` · `list` | Bonus XP for roles |
| `/config messages` | Rewrite **any** text the bot sends, in any language |
| `/levels role add` · `remove` · `list` | Level role rewards |
| `/levels xp add` · `remove` · `set` · `setlevel` | Adjust a member |
| `/levels reset` | One member, or the whole server (with confirmation) |
| `/levels sync` | Re-apply reward roles to everyone at their current level |
| `/levels seed` | Give members the XP for a tier role they already hold |
| `/levels event` | Double-XP events with an optional announcement |

## Migrating from another leveling bot

If your members already wear tier roles from a previous bot, `/levels seed` reads
them and grants the matching XP so nobody loses the rank they earned:

1. Map each existing tier role to a level with `/levels role add`.
2. Run `/levels seed`. It shows a preview of who would change and by how much, and
   does nothing until you confirm.
3. `/leaderboard` to sanity-check the result.

By default it only ever raises someone's XP. Pass `overwrite:true` to also lower
members who have more XP than their role implies.

## Behaviour worth knowing

- **Role rewards replace by default.** Reaching Diamond removes Amethyst — a tier
  ladder implies one rung at a time. `/config roles stack:true` changes it.
- **Role multipliers don't stack.** Booster (1.5×) plus VIP (2×) gives 2×, not 3×.
  Stacking multipliers is how a leaderboard quietly becomes meaningless.
- **Voice level-ups have no chat channel of their own.** If level-ups are set to
  reply in-channel, set a level-up channel too and voice level-ups land there
  instead of being dropped.
- **`/rank` on someone who's never spoken creates no record** — looking a member up
  doesn't put them on the board.
- **Message *content* is never read.** The bot counts messages; it doesn't need
  and doesn't request the Message Content intent.

## Requirements

- Python 3.10+
- Privileged intents: **Server Members**. (Message Content is *not* needed.)
- Bot permissions: Send Messages, Embed Links, Read Message History, View Channels,
  Connect (to see voice state), and Manage Roles for level rewards.

## Running it

```bash
git clone https://github.com/GXO-gg/himyar-levels.git
cd himyar-levels
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then put your token in it
python bot.py
```

### As a service (Ubuntu / systemd)

```bash
sudo cp himyar-levels.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now himyar-levels
journalctl -u himyar-levels -f
```

### Environment

| Variable | Required | Default |
| --- | --- | --- |
| `DISCORD_TOKEN` | yes | — |
| `HIMYAR_DB_PATH` | no | `data/levels.db` |
| `DEV_GUILD_ID` | no | — (sync commands to one server instantly while testing) |
| `LOG_LEVEL` | no | `INFO` |

## Layout

```
bot.py                  entry point and command sync
core/levels.py          the XP curve — pure, testable, no Discord objects
core/rules.py           who earns XP and when — every anti-farm predicate
core/db.py              SQLite schema and queries, keyed by guild_id
core/strings.py         every user-facing string + per-guild override layer
core/views.py           leaderboard paging and confirmations
core/util.py            durations, embeds, permission checks
cogs/xp.py              message XP, the voice tick, level-ups and reward roles
cogs/ranks.py           /rank, /leaderboard, /levelroles
cogs/config.py          /setup, /config, /levels, /help
```

## Backups

Everything is in one file — `data/levels.db`. This one holds every member's
progress, so it's the one most worth backing up.

```bash
sqlite3 data/levels.db ".backup '/home/himyar/backups/levels-$(date +%F).db'"
```

---

MIT licensed. Built for the Himyar bot suite.
