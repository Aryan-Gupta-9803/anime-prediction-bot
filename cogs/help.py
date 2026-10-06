from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

import backup
import season_rules
import storage
import style
from common import (
    REQUIRED_ANNOUNCE_PERMS,
    REQUIRED_BACKUP_PERMS,
    REQUIRED_PICKS_PERMS,
    get_text_channel,
    missing_permissions,
    reply,
)
from config import BACKUP_EVERY_DAYS
from permissions import has_admin_rights, is_admin
from textutil import clip


def _scoring_summary(rules: dict) -> str:
    if not storage.rules_complete(rules):
        return "see the announcement for scoring"
    first = rules[1]
    if all(rules[p] == first for p in range(1, 11)):
        return f"**{first[0]}** pts for the exact spot, **{first[1]}** pt(s) if it's on the chart in a different spot"
    return "points vary by position (see the announcement)"


async def _event_status(week) -> str:
    if week is None:
        return "No event is open right now. Hosts will announce the next one."

    week_id, kind = week["week_id"], week["type"]
    locked = storage.week_is_locked(week)
    count = await storage.aio.count_submissions(week)
    has_results = await storage.aio.week_has_results(week)

    scoring = _scoring_summary(await storage.aio.get_rules(week_id))

    if not locked:
        state = "**open**: use `/pick` to submit or change yours"
    elif has_results:
        state = "**finished**: results are posted, check `/my-score`"
    else:
        state = "**locked**: waiting for the host to enter results"

    rule = season_rules.get_rule(await storage.aio.get_season_rule())
    extra = "" if season_rules.is_standard(rule) else f"\nSeason rule: **{rule.name}** (see below)."
    text = f"**{week_id}** ({kind}) is {state}.\nScoring: {scoring}.{extra}\n{count} member(s) have submitted."
    if week.get("note"):
        text += f"\nNote: {week['note']}"
    return text


async def _backup_status(client, me) -> str:
    try:
        channel = await backup.get_backup_channel(client)
    except backup.BackupError as e:
        return f"[todo] Backups: {e}"
    missing = missing_permissions(channel, me, REQUIRED_BACKUP_PERMS)
    if missing:
        return f"[todo] Backups {channel.mention}: I'm missing {', '.join(missing)}."
    if backup.state.last_error:
        return f"[todo] Backups {channel.mention}: the last attempt failed. {backup.state.last_error}"
    last = await storage.aio.last_backup_at()
    if last is None:
        return f"[ok] Backups {channel.mention}: none posted yet (the first one follows your first change)."
    stamp = last.replace("T", " ")[:16] + " UTC"
    if not await storage.aio.is_dirty():
        return f"[ok] Backups {channel.mention}: last {stamp}, up to date"
    due = datetime.fromisoformat(last) + timedelta(days=BACKUP_EVERY_DAYS)
    when_due = "within the hour" if due <= datetime.now(timezone.utc) else f"by {due:%Y-%m-%d}"
    return f"[ok] Backups {channel.mention}: last {stamp}; unsaved changes are backed up {when_due} (or run /backup)"


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="How the prediction game works and what's open right now")
    async def help_cmd(self, interaction: discord.Interaction):
        week = await storage.aio.get_current_week()
        ended = await storage.aio.season_ended()
        rule = season_rules.get_rule(await storage.aio.get_season_rule())
        embed = style.embed(
            "Weekly anime chart predictions",
            (
                "Guess how the weekly anime chart will look",
                ["You score for every title you got on the chart, and more when it's in the right position."],
            ),
        )
        status = (
            "**The season is over.** Final standings are posted in announcements; `/leaderboard` shows them too."
            if ended
            else await _event_status(week)
        )
        embed.add_field(name="Right now", value=clip(status, 1024), inline=False)
        embed.add_field(
            name="Picking your top 10",
            value=(
                "Run `/pick` and use the `rank-` fields. Each one is a spot on the chart: `rank-1` is the title "
                "you think will be **#1 (top of the chart)**, `rank-2` is #2, and so on down to `rank-10`.\n"
                "Click a field and start typing, and matching titles are suggested. Fill in as many as you like; a "
                "rank you skip keeps your current pick (or stays empty if you have none).\n"
                "To change just one later: `/pick rank-3: Bleach`. Choose `(leave empty)` to empty a rank.\n"
                "Prefer boxes? `/pick` with no fields opens a short form instead: ten boxes over two steps "
                "(1-5, then 6-10), filled in with your last picks."
            ),
            inline=False,
        )
        embed.add_field(
            name="The rules",
            value=(
                "1. `/anime-list` shows the titles you can pick from. Use a title as it's spelled there, and don't "
                "repeat one.\n"
                "2. A mistake rejects the whole submission and nothing is saved (an earlier pick of yours stays). "
                "I'll point at what's wrong.\n"
                "3. Your picks are posted in the picks channel and are public; your post updates in place.\n"
                "4. Picks close when a host runs `/lock` (there's no automatic deadline), then results get scored."
            ),
            inline=False,
        )
        if not season_rules.is_standard(rule):
            embed.add_field(name=f"Season rule: {rule.name}", value=clip(rule.explain, 1024), inline=False)
        embed.add_field(
            name="Commands",
            value=(
                "`/pick` submit or change your picks\n"
                "`/my-score` your points and what each pick earned (only you see it)\n"
                "`/leaderboard` season or per-event standings (only you see it)\n"
                "`/anime-list` the eligible titles\n"
                "`/help` this guide"
            ),
            inline=False,
        )
        if has_admin_rights(interaction.user):
            embed.set_footer(text="You're a host: /help-admin has the host guide.")
        await reply(interaction, embed=embed)

    @app_commands.command(name="help-admin", description="Host guide: setup status, weekly flow and fixing mistakes")
    @is_admin()
    async def help_admin(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        me = interaction.guild.me

        async def channel_status(key: str, label: str, needed) -> str:
            channel = await get_text_channel(interaction.client, key)
            if channel is None:
                return f"[todo] {label} channel: not set (or I can't see it). Run `/setup`."
            missing = missing_permissions(channel, me, needed)
            if missing:
                return f"[todo] {label} {channel.mention}: I'm missing {', '.join(missing)}."
            return f"[ok] {label} {channel.mention}"

        anime = await storage.aio.get_anime_list()
        rule = season_rules.get_rule(await storage.aio.get_season_rule())
        templates = await storage.aio.list_templates()
        week = await storage.aio.get_current_week()

        checklist = [
            await channel_status("announcement_channel_id", "Announcements", REQUIRED_ANNOUNCE_PERMS),
            await channel_status("user_channel_id", "Picks", REQUIRED_PICKS_PERMS),
            await _backup_status(interaction.client, me),
            f"[ok] Anime list: {len(anime)} titles" if anime else "[todo] Anime list is empty. Run `/start`.",
            f"[ok] Season rule: {rule.name}" if anime else "[todo] Season rule: choose one with `/start`.",
            f"[ok] Scoring templates: {len(templates)}" if templates else "[todo] No scoring templates (use `/new-event`).",
        ]
        if await storage.aio.season_ended():
            checklist.append("[ok] Season ended. Run `/reset` to archive it and start the next one.")
        elif week is None:
            checklist.append("[ok] No event open. Start one with `/event-template` or `/new-event`.")
        else:
            count = await storage.aio.count_submissions(week)
            if not storage.week_is_locked(week):
                checklist.append(f"[ok] {week['week_id']} ({week['type']}) is open, {count} submission(s). `/lock` when ready.")
            elif not await storage.aio.week_has_results(week):
                checklist.append(f"[todo] {week['week_id']} is locked, {count} submission(s), waiting for `/end-week`.")
            else:
                checklist.append(f"[ok] {week['week_id']} is scored. Start the next event whenever you like.")

        embed = style.embed("Host guide", ("Setup status, the season lifecycle and fixing mistakes", None))
        embed.add_field(name="Setup status", value=clip("\n".join(checklist), 1024), inline=False)
        embed.add_field(
            name="Once per season (about every 3 months)",
            value=(
                "1. `/setup` pick the announcements and picks channels (once).\n"
                "2. `/start` paste the season's titles, one per line (10 or more), and optionally choose a season rule (e.g. Minority Multiplier). Both are posted in announcements.\n"
                "3. Run the weekly flow below.\n"
                "4. `/end-season` posts the final leaderboard and closes the season.\n"
                "5. `/reset` posts a backup file, then clears it; then `/start` again. Channels and templates "
                "are kept, and `/restore` can bring the old season back."
            ),
            inline=False,
        )
        embed.add_field(
            name="Every week",
            value=(
                "1. Open it: `/event-template` (saved points, e.g. Standard 2/1) or `/new-event` (custom points; "
                "`save_as` keeps them as a template).\n"
                "2. Members use `/pick`; their picks appear in the picks channel.\n"
                "3. `/lock` when you're ready. Nothing closes automatically.\n"
                "4. `/end-week` enter the real top 10 in the same two-step form, or fix single ranks with the `rank-` fields "
                "Titles "
                "must be on the list: add a missing one with `/add-anime` first. It scores everyone and posts the "
                "results and standings."
            ),
            inline=False,
        )
        embed.add_field(
            name="Host commands",
            value=(
                "`/setup` `/start` `/add-anime` `/remove-anime` `/reset`\n"
                "`/event-template` `/new-event` `/set-points` `/list-templates`\n"
                "`/lock` `/unlock` `/end-week [week]` `/end-season`\n"
                "`/backup` `/restore [file]` `/export`\n"
                "`/help-admin` this guide"
            ),
            inline=False,
        )
        embed.add_field(
            name="Fixing mistakes",
            value=(
                "- Wrong results: run `/end-week` again. The form is pre-filled; it re-scores and re-posts.\n"
                "- Locked too early: `/unlock`.\n"
                "- Wrong points for a week: `/set-points`, then `/end-week` to re-score.\n"
                "- Title typo: fix it with `/remove-anime` + `/add-anime` before picks come in (picks keep "
                "the spelling they were saved with).\n"
                f"- Backups: a file is posted to the backup channel when a week is scored, at season end, before "
                f"a reset or restore, and every {BACKUP_EVERY_DAYS} days if anything else changed. `/backup` saves "
                "one now; `/restore` rolls back; `/export` gives an Excel copy."
            ),
            inline=False,
        )
        embed.add_field(
            name="Good to know",
            value=(
                "Picks are public as soon as they're submitted. "
                "Anything big asks you to confirm first. If the host ever loses its disk, a fresh start restores "
                "from the newest backup by itself. This bot only works in this server."
            ),
            inline=False,
        )
        await reply(interaction, embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
