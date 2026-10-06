# Anime Prediction Bot

Discord bot for the weekly top-10 anime chart prediction game. Members guess the chart, hosts enter
the real result, and the bot scores everyone and posts standings. It replaces the manual Excel
workbook. Data lives in a small SQLite file on the host, and the bot posts a backup copy of that file
to a private Discord channel after changes. If the host ever loses its disk, a fresh start restores
itself from the newest backup, and `/restore` lets a host roll back by hand.

## How a season runs

1. **`/setup`** (once): pick the announcements channel and the picks channel.
2. **`/start [rule]`**: paste the season's anime titles (10 or more, one per line) and optionally choose a season rule
   (below). The list and the rule are posted in announcements.
3. **Open a week**: `/event-template` (saved points such as Standard 2/1), `/new-event` (custom points;
   `save_as` keeps them as a template).
4. **Members `/pick`**: ten separate boxes over two steps (1-5, then 6-10), pre-filled with their current pick, or
   their last picks from an earlier week. Or a quick edit: `/pick rank-3: Bleach` changes only that rank (see below).
   Valid picks are posted in the picks channel and updated in place when changed. Picks are public by design.
5. **`/lock`** when you're ready. Nothing closes automatically. `/unlock` undoes it.
6. **`/end-week`**: enter the real top 10 in the same two-step form (or fix single ranks with the `rank-` fields). Everyone is scored;
   results and season standings are posted, and a backup is saved. Run it again to correct a mistake.
7. Repeat 3 to 6 each week.
8. **`/end-season`** when the season is over: posts the full final leaderboard with the champion(s), locks everything
   and stops new weeks from opening. Then **`/reset`** (backs up first, asks to confirm) and `/start` the next one.

`/help` explains the game to members, with live status for the open event. `/help-admin` is the host guide: a
setup checklist (including backup status), the lifecycle, a command list and how to fix mistakes.

## Commands

| Command | Who | What |
|---|---|---|
| `/pick [rank-1 ... rank-10]` | anyone | Submit or change your picks: the form, or quick-edit single ranks |
| `/my-score [week]` | anyone | Your points with a per-pick breakdown (private) |
| `/leaderboard [week]` | anyone | Season or per-event standings with your rank (private) |
| `/anime-list` | anyone | The titles you can pick from (private) |
| `/help` | anyone | How to play and what's open right now |
| `/help-admin` | host | Setup checklist, lifecycle, commands, fixing mistakes |
| `/setup` | host | Choose announcements and picks channels; warns about missing bot permissions |
| `/start [rule]` | host | New season: paste the valid titles and optionally pick a season rule (refused if a season is already running) |
| `/add-anime` `/remove-anime` | host | Adjust the title list mid-season |
| `/event-template template [type] [note]` | host | Open a week with a saved scoring template |
| `/new-event exact_points partial_points [type] [note] [save_as]` | host | Open a week with custom scoring |
| `/set-points exact_points partial_points [week]` | host | Change a ranked week's scoring (then `/end-week` to re-score) |
| `/list-templates` | host | Show saved scoring templates |
| `/lock` `/unlock` | host | Close or reopen picks (posts a notice in announcements) |
| `/end-week [week]` | host | Enter the real chart (same ten-box form), score everyone, post results, back up |
| `/end-season` | host | Post the final leaderboard, lock the season, stop new weeks (asks first if a week is unscored) |
| `/backup` | host | Post a backup file to the backup channel right now |
| `/restore [file]` | host | Replace all data from the newest backup, or from a file you attach (asks to confirm) |
| `/export` | host | Get everything as an Excel file (private to you) |
| `/reset` | host | Back up, then clear the season for a new one (asks to confirm) |

"Host" means anyone with the Manage Server permission or the role named by `ADMIN_ROLE_NAME` (default `Event Host`).

### Validation

Members' and hosts' forms work the same way. **A box may be left empty, but anything typed must be a title from the
season list, and no title may appear twice.** If any filled box is wrong the whole submission is refused and nothing
is saved (an earlier pick stays untouched); mistakes get a "Did you mean...?" hint and name the box. Matching ignores
case, spacing, curly quotes and a leading `1.`. The first step is checked immediately, the halves are checked against
each other at the end, and "Save, keep 6-10 as they are" skips the second form for small edits. A form needs at least
one filled box.

The host's real chart is held to the same rule, so a typo can't reach the leaderboard. If a title genuinely charted
but isn't on the list, run `/add-anime` and then `/end-week` again. Empty chart spots are allowed (nobody can score on
them) and are reported back.

Discord allows at most five text boxes in one form, which is why ten boxes means two steps.

### Quick edit

`/pick` and `/end-week` also have ten optional fields, `rank-1` to `rank-10`. Click a field and start typing: matching
titles are suggested (any part of the title, any case, 25 at a time) along with `(leave empty)`. Fill in only the
ranks you want to change and the rest keep their title, taken from the member's pick this week, or their last picks
if they haven't picked yet (for `/end-week`: the saved results). Placing a title that is already at another rank moves
it and leaves the old rank empty; the private reply lists what changed and what moved. The same validation applies
(every filled field must be a listed title, no repeats, at least one rank must remain), so a typo refuses the whole
edit. With no fields, the commands open the form as before.

### Remembered picks

Each member's most recent picks are remembered, separately from the season, so the form opens pre-filled with them in
a new week and after a `/reset`. Titles that are no longer on the list are left blank. This week's own pick always
wins over the remembered one.

### Scoring

Each week is two flat numbers: points for an exact position match and points for a title that is on the chart but
in a different spot. This is the same arithmetic as the original workbook. Two templates ship, read from the real
Summer 2026 sheet:

| Template | Exact | Wrong spot |
|---|---|---|
| Standard | 2 | 1 |
| First Week Advantage | 5 | 2 |

### Season rules

`/start` takes an optional **rule** that applies to every week of that season (`/reset` clears it). It's announced with
the list, shown on each week's announcement and in `/help`, and used whenever a week is scored or re-scored. Rules live
in `season_rules.py`: add a scoring function and one entry in `RULES` and it appears in `/start` by itself.

**Minority Multiplier** (the first one): for the real top 3, each title gets a multiplier from how few players had it in
their ten (anywhere). 40% of players or more: 1x. Only one player: 4x. In between it slides in a straight line, rounded to
the nearest 0.5x; with 10 players that is 1 player 4x, 2 players 3x, 3 players 2x, 4 or more 1x. The multiplier applies to the points a player earns on that title (exact or wrong spot) and is not rounded, so a
score can have a half point (a 1-point wrong-spot match at 1.5x is 1.5). It never touches penalties or titles outside the top 3. Weeks with fewer than 5 players have no multipliers.
The results post lists each top-3 title's multiplier, and `/my-score` shows it on the rows it changed. The knobs
(`MAX_MULTIPLIER`, `CROWD_SHARE`, `MIN_PLAYERS`) are at the top of the minority section in `scoring.py`.

Not built: the joker weeks, the unused "RESERVED" upper/lower-half idea from the original workbook, and (switched off
for now) the awards ballot. The ballot's storage and scoring code is still in the project so old backups restore and
it can be brought back, but there is no command to start one.

## Backups

- **What:** a consistent copy of the whole database, posted as a file (`prediction-backup-YYYYMMDD-HHMMSS.db`) to the
  channel in `BACKUP_CHANNEL_ID`. It's a normal SQLite file. For something you can open in Excel, use `/export`.
- **When:** the moments that matter, plus a weekly safety net. A backup is posted when a week is scored
  (`/end-week`), at `/end-season`, before `/reset` and `/restore`, on `/backup`, on a clean shutdown, and **once every
  7 days** if anything else changed since the last one (`BACKUP_EVERY_DAYS`). Check `/help-admin` for the last backup
  and when the next is due, or a `[todo]` if the channel can't be written to.
- **Kept:** the newest 50 automatic backups (`BACKUP_KEEP`). Backups from `/backup`, `/end-season`, `/reset` and
  `/restore` are never deleted automatically.
- **Restore by hand:** `/restore` uses the newest backup that has data in it (so it skips the empty one posted after a
  `/reset`). To roll back further, download an older file from the channel and run `/restore` with it attached. A
  damaged or unrelated file is refused, and what you're replacing is saved first, so a restore can itself be undone.
- **Restore automatically:** if the bot starts with no database (a new host, or the disk was wiped), it restores the
  newest backup before doing anything else. If it can't read the backup channel at that moment it refuses to start
  rather than running empty, so nothing gets silently lost.
- **The trade-off of a weekly schedule:** everything is safe once a week is scored, but picks and edits made since the
  last backup exist only on the host's disk. If the disk is lost mid-week, they are gone (a week at most; less if you
  ran `/backup`). Set `BACKUP_EVERY_DAYS=1` for a daily safety net.
- **Keep the channel private.** Backups contain usernames, Discord IDs and everyone's picks.

## Setup

You need Python 3.10 or newer.

### 1. Discord bot
1. In the [Developer Portal](https://discord.com/developers/applications), create an application, add a Bot, copy its
   token. No privileged intents are needed.
2. Invite it with this link (replace `YOUR_APP_ID`); it asks for View Channels, Send Messages, Embed Links, Attach
   Files and Read Message History:
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot%20applications.commands&permissions=117760`
3. Give your hosts the `Event Host` role (or Manage Server).
4. Create a **private** channel (for example `#bot-backups`) that only hosts and the bot can see. Make sure the bot
   has View Channel, Send Messages, Attach Files and Read Message History there.
5. Turn on Developer Mode (Settings, Advanced), then right-click your server and the backup channel and choose Copy ID.

### 2. Configure and run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the three IDs/tokens
python bot.py
```
`.env` needs `DISCORD_TOKEN`, `GUILD_ID` (the bot only works in that one server) and `BACKUP_CHANNEL_ID`.
`DATA_DIR` (default `./data`) is where the database file lives; `BACKUP_EVERY_DAYS` (default 7) sets the weekly safety net. The bot logs what it did at startup, e.g.
`Fresh install with no backups yet: starting empty` or `Synced 23 slash commands to the server`.

### 3. First-run checklist (in a test server or channel)
1. `/setup`, then `/help-admin`: everything under Setup status should read `[ok]` except the anime list.
2. `/start` with 10 or more titles, then `/event-template Standard`.
3. `/pick` with a deliberate typo (expect rejection), then correctly (expect a post in the picks channel).
4. `/pick` again (form is pre-filled; the post should update, not duplicate), `/lock`, `/end-week`.
5. `/end-week` already posted a backup of the scored week to the backup channel; run `/backup` for another. `/help-admin` shows the last one.
6. `/export` for the Excel copy, then `/reset` and `/restore` to see the round trip.

## Deploying (Kerit Cloud / Space-Node)

1. Push this folder to a private GitHub repo (`.env` and the `data/` folder are gitignored).
2. Connect the host to the repo and set the start command to `python bot.py` (Python 3.10 or newer).
3. Add `DISCORD_TOKEN`, `GUILD_ID` and `BACKUP_CHANNEL_ID` as environment variables (and optionally
   `ADMIN_ROLE_NAME`). If the host offers a persistent folder or volume, set `DATA_DIR` to it; if not, that's fine,
   because a wiped disk restores from the backup channel on the next start.
4. Run exactly one instance, and stop any copy on your own machine first. Two instances would each keep their own
   database and both post backups.

## Limits worth knowing

- One server, one season at a time. Two hosts opening a week in the same instant could both succeed.
- Discord must be answered within 3 seconds when a form opens; the database is local, so this isn't a concern.
- Backups are limited by the server's upload size (10 MB or more). A season is a few hundred KB.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
The suite runs offline against a temporary database and fake Discord objects: full ranked seasons,
backups, pruning, restore (by command and automatic on a wiped host), reset, permissions, the Excel export, and
Discord's size limits.
