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
4. **Members `/pick`**: fill in the `rank-1` to `rank-10` fields (titles are suggested as you type). A rank you leave
   out keeps the member's current pick, or their last picks from an earlier week, so `/pick rank-3: Bleach` changes
   only that rank (see below). Valid picks are posted in the picks channel and updated in place when changed. Picks
   are public by design.
5. **`/lock`** when you're ready. Nothing closes automatically. `/unlock` undoes it.
6. **`/end-week`**: enter the real top 10 in a two-step form (or fix single ranks with the `rank-` fields). Everyone is scored;
   results and season standings are posted, and a backup is saved. Run it again to correct a mistake.
7. Repeat 3 to 6 each week.
8. **`/end-season`** when the season is over: posts the full final leaderboard with the champion(s), locks everything
   and stops new weeks from opening. Then **`/reset`** (backs up first, asks to confirm) and `/start` the next one.

`/help` explains the game to members, with live status for the open event. `/help-admin` is the host guide: a
setup checklist (including backup status), the lifecycle, a command list and how to fix mistakes.

## Commands

| Command | Who | What |
|---|---|---|
| `/pick [rank-1 ... rank-10]` | anyone | Submit or change your picks with the rank fields; with none filled in it shows your current picks |
| `/my-score [week]` | anyone | Your points with a per-pick breakdown (private) |
| `/leaderboard [week]` | anyone | Season or per-event standings with your rank (private) |
| `/anime-list` | anyone | The titles you can pick from (private) |
| `/help` | anyone | How to play and what's open right now |
| `/help-admin` | host | Setup checklist, lifecycle, commands, fixing mistakes |
| `/setup` | host | Choose announcements and picks channels; warns about missing bot permissions |
| `/start [rule]` | host | New season: paste the valid titles and optionally pick a season rule (refused if a season is already running) |
| `/season-rule rule` | host | Set or change the season's rule mid-season (applies to weeks scored from then on) |
| `/add-anime` `/remove-anime` | host | Adjust the title list mid-season |
| `/event-template template [type] [note]` | host | Open a week with a saved scoring template (type: standard, special, or ups and downs) |
| `/new-event exact_points partial_points [type] [note] [save_as]` | host | Open a week with custom scoring (same types) |
| `/set-points exact_points partial_points [week]` | host | Change a ranked week's scoring (then `/end-week` to re-score) |
| `/list-templates` | host | Show saved scoring templates |
| `/lock` `/unlock` | host | Close or reopen picks (posts a notice in announcements) |
| `/end-week [week]` | host | Enter the real chart (ten-box form or rank fields), score everyone, post results, back up |
| `/end-season` | host | Post the final leaderboard, lock the season, stop new weeks (asks first if a week is unscored) |
| `/backup` | host | Post a backup file to the backup channel right now |
| `/restore [file]` | host | Replace all data from the newest backup, or from a file you attach (asks to confirm) |
| `/export` | host | Get everything as an Excel file (private to you) |
| `/reset` | host | Back up, then clear the season for a new one (asks to confirm) |

"Host" means anyone with the Manage Server permission or the role named by `ADMIN_ROLE_NAME` (default `Event Host`).

### Validation

Members' rank fields and the host's results form follow the same rules. **A rank may be left empty, but anything
filled in must be a title from the season list, and no title may appear twice.** If any filled field is wrong the whole
submission is refused and nothing is saved (an earlier pick stays untouched); mistakes get a "Did you mean...?" hint and
name the rank. Matching ignores case, spacing, curly quotes and a leading `1.`. At least one rank must remain filled.
In the host's two-step results form the first step is checked immediately, the halves are checked against each other at
the end, and "Save, keep 6-10 as they are" skips the second form for small edits.

The host's real chart is held to the same rule, so a typo can't reach the leaderboard. If a title genuinely charted
but isn't on the list, run `/add-anime` and then `/end-week` again. Empty chart spots are allowed (nobody can score on
them) and are reported back.

Discord allows at most five text boxes in one form, which is why the results form has two steps.

### Quick edit

`/pick` and `/end-week` also have ten optional fields, `rank-1` to `rank-10`. Click a field and start typing: matching
titles are suggested (any part of the title, any case, 25 at a time) along with `(leave empty)`. Fill in only the
ranks you want to change and the rest keep their title, taken from the member's pick this week, or their last picks
if they haven't picked yet (for `/end-week`: the saved results). Placing a title that is already at another rank moves
it and leaves the old rank empty; the private reply lists what changed and what moved. The same validation applies
(every filled field must be a listed title, no repeats, at least one rank must remain), so a typo refuses the whole
edit. `/pick` with no fields shows the member's current picks and how the fields work; `/end-week` with no fields opens
the two-step results form, pre-filled with the saved results.

### Remembered picks

Each member's most recent picks are remembered, separately from the season, so a new week (and the week after a
`/reset`) starts from them: any rank you leave out of `/pick` keeps that title. Titles that are no longer on the list are left blank. This week's own pick always
wins over the remembered one.

### Scoring

Each week is two flat numbers: points for an exact position match and points for a title that is on the chart but
in a different spot. This is the same arithmetic as the original workbook. Two templates ship, read from the real
Summer 2026 sheet:

| Template | Exact | Wrong spot |
|---|---|---|
| Standard | 2 | 1 |
| First Week Advantage | 5 | 2 |

### Ups and Downs weeks

An event type for a one-off week: open it with `/event-template` or `/new-event` and `type: ups and downs`. Members make
their normal ranked picks **and** guess how many titles will climb (`ups`) and how many will fall (`downs`) compared
with the previous week's chart: `/pick rank-1: Title ... ups: 3 downs: 4`. Each guess is worth 5 points when exact, 4 if
off by 1, 3 if off by 2, and so on down to 0, separately for ups and for downs. The ranked picks score as usual (the
template's points, plus the season rule if one is set) and the Ups and Downs points are added on top.

- **What counts:** a title is an *up* if it sits at a better rank than on last week's chart and a *down* if it sits at a
  worse one; it has to be on both charts. New entries and titles that dropped off don't count (two switches at the top of
  the Ups and Downs section in `scoring.py` change that, and the announcement text follows them).
- **Last week must be scored first.** The week it compares with is the event right before it. Starting the event, and
  `/end-week`, both refuse until that week has results, and the announcement shows that chart for reference.
- **`/pick`:** Discord can't hide options, so `ups` and `downs` are always listed (0-10, optional). In an ordinary week
  they're refused; in this week both are needed the first time, and after that a member can change either on its own.
  Together they can't exceed 10. They aren't carried over to other weeks.
- The results post lists which titles went up, down, stayed, are new or dropped off, and `/my-score` shows each guess
  against the actual count.

### Season rules

`/start` takes an optional **rule** that applies to every week of that season (`/reset` clears it). It's announced with
the list, shown on each week's announcement and in `/help`, and used whenever a week is scored or re-scored. Rules live
in `season_rules.py`: add a scoring function and one entry in `RULES` and it appears in `/start` by itself.

**Minority Multiplier** (the first one): each player's own top 3 picks (ranks 1-3) can earn a bonus multiplier from
how few players had that same title in their own top 3. 40% of players or more: 1x. Only one player: 3x. In between
it slides in a straight line, rounded to the nearest 0.5x; with 10 players that is 1 player 3x, 2 players 2.5x,
3 players 1.5x, 4 or more 1x. The multiplier applies to the points that pick earns (exact spot or wrong spot) and is
not rounded, so a score can have a half point (a 1-point wrong-spot match at 1.5x is 1.5). It never touches
penalties, picks that earn nothing, or ranks 4-10, and a title in someone's ranks 4-10 doesn't count toward its
popularity. Weeks with fewer than 5 players have no multipliers. The results post lists each title that earned a bonus
and its multiplier, and `/my-score` shows it on the rows it changed. The knobs (`MAX_MULTIPLIER`, `CROWD_SHARE`,
`BONUS_RANKS`, `MIN_PLAYERS`) are at the top of the Minority Multiplier section in `scoring.py`.

`/season-rule` changes the rule mid-season (it posts the new rule in announcements). Weeks already scored keep their
scores until you re-run `/end-week week:<id>` for them; weeks scored from then on use the new rule.

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
4. `/pick rank-1: ...` again with just one field (the other ranks stay, and the post should update, not duplicate), `/lock`, `/end-week`.
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

## Adding to the bot later

**A new field on `/pick`** (for example a "hot take" title in a special week):
1. Declare it as a `PickField` in `event_types.py` and list it in `fields=` of the event type(s) that use it. A field has
   a name, a label, a one-line help text and a kind: a whole `int` (with min and max), a season `title`, one of a fixed
   list of `choice`s, or free `text`. It can be required (needed the first time a member picks in that event) or optional.
2. Add a parameter with the same name to `/pick` in `cogs/user.py`, with a matching type (`app_commands.Range[int, lo, hi]`
   for a number, `str` for the rest). Discord needs every option written into the command, so this one line can't be
   skipped; its description, title suggestions and fixed choices come from the declaration.

Everything else is automatic: checking what the member typed (the whole `/pick` is refused if anything is wrong), keeping
a value when they edit something else, saving it, showing it on their post and in the bare-`/pick` guide, and refusing it
in weeks that don't use it. The values live in one generic table (`pick_extras`), so a new field never needs a database
change, and older backups still restore. `tests/test_pick_fields.py` fails if the declarations and `/pick` disagree.
Discord allows 25 options on a command; `/pick` has the ten ranks plus the extra fields.

**A new event type** (an `EventType` in `event_types.py`): its name, its fields, and optional hooks for a rule across
fields (`check`), reasons it can't start or be scored yet (`start_problem`, `score_problem`), extra announcement text
(`announce`) and extra points plus a results-post section (`score`). It then shows up in `/event-template` and
`/new-event` by itself. `tests/test_pick_fields.py` has a made-up "Bonus Round" event doing all of this as a template.

**A new season rule** goes in `season_rules.py` (see Season rules above).

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
