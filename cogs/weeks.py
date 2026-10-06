import asyncio

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown as esc

import backup
import storage
from common import (
    ConfirmView,
    acknowledge,
    get_text_channel,
    given_ranks,
    rank_options,
    ranking,
    reply,
    season_totals,
    send_paged,
    week_autocomplete,
)
import season_rules
import style
from forms import RankedForm
from permissions import is_admin
from style import pts, row
from textutil import chunk_lines
from validation import merge_rank_edits

WEEK_TYPE_CHOICES = [
    app_commands.Choice(name="standard", value="standard"),
    app_commands.Choice(name="special", value="special"),
]


# ---- scoring (blocking; always run via asyncio.to_thread) -------------------------------------------

def _score_week(week_id: str, actual: list):
    """Scores every prediction under the season's rule. Returns (how many were scored, notes for the
    results post such as the minority multipliers)."""
    rules = storage.get_rules(week_id)
    rule = season_rules.get_rule(storage.get_season_rule())
    predictions = storage.list_predictions(week_id)
    results, notes = rule.score([storage.prediction_ranks(p) for p in predictions], actual, rules)
    entries = [
        {"user_id": p["user_id"], "username": p["username"], "points": total, "breakdown": breakdown}
        for p, (total, breakdown) in zip(predictions, results)
    ]
    storage.replace_scores(week_id, entries)
    return len(entries), notes


# ---- announcements -------------------------------------------------------------------------------------

def _leaderboard_lines(totals: dict, names: dict, limit: int = 15) -> list:
    rows = ranking(totals)
    lines = [row(rank, f"**{esc(names[uid])}** — {pts(points)}") for rank, uid, points in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"...and {len(rows) - limit} more. Use /leaderboard to see where you stand.")
    return lines


async def _post_results(client: discord.Client, week_id: str, notes: list = ()) -> bool:
    """Posts the week's results and standings to the announcement channel."""
    channel = await get_text_channel(client, "announcement_channel_id")
    if channel is None:
        return False

    week = await storage.aio.get_week(week_id)
    scores = await storage.aio.list_scores(week_id)
    totals, names = season_totals(scores)
    board = _leaderboard_lines(totals, names) or ["Nobody submitted a prediction for this one."]
    title = f"Results are in: {week_id}" + (f" ({week['type']})" if week else "")
    greeting = f"Hello everyone! The **{week_id}** results are in!"

    actual = await storage.aio.get_results(week_id) or []
    top10 = [row(i, esc(t) if t else "-") for i, t in enumerate(actual, start=1)]
    sections = [("Actual top 10", top10)]
    if notes:
        rule = season_rules.get_rule(await storage.aio.get_season_rule())
        sections.append((rule.name, [row(f"**{esc(label)}**", text) for label, text in notes]))
    await channel.send(content=greeting, embed=style.embed(title, *sections, ("Leaderboard", board)))

    all_scores = await storage.aio.list_scores()
    if len({s["week_id"] for s in all_scores}) > 1:
        season, season_names = season_totals(all_scores)
        await channel.send(embed=style.embed("Season standings", (None, _leaderboard_lines(season, season_names))))
    return True


# ---- result entry --------------------------------------------------------------------------------------

async def _finish_results(
    interaction: discord.Interaction, week_id: str, actual: list, from_command: bool = False, summary: str = ""
):
    """Saves the validated real chart (empty spots allowed), scores everyone and posts the results."""
    if not storage.rules_complete(await storage.aio.get_rules(week_id)):
        await reply(
            interaction,
            f"The scoring rules for {week_id} are incomplete, so scoring would give everyone 0. "
            "Set them with `/set-points` first.",
        )
        return

    await acknowledge(interaction, "Scoring...", from_command)
    await storage.aio.save_result(week_id, actual)
    await storage.aio.set_week_locked(week_id, True)
    scored, notes = await asyncio.to_thread(_score_week, week_id, actual)
    posted = await _post_results(interaction.client, week_id, notes)
    backup_problem = await backup.try_backup(interaction.client, f"{week_id} scored")

    lines = [f"Results saved and **{scored}** prediction(s) scored for {week_id}.{summary}"]
    empty = actual.count("")
    if empty:
        lines.append(f"{empty} chart spot(s) were left empty, so nobody can score on them. Run `/end-week` again to fill them in.")
    if not posted:
        lines.append("I couldn't post to the announcement channel. Check `/setup` and my permissions.")
    if backup_problem:
        lines.append(f"The backup after scoring didn't go through: {backup_problem}")
    await reply(interaction, "\n".join(lines))


# ---- commands ---------------------------------------------------------------------------------------------

async def _post_final_standings(client: discord.Client) -> bool:
    channel = await get_text_channel(client, "announcement_channel_id")
    if channel is None:
        return False
    scores = await storage.aio.list_scores()
    totals, names = season_totals(scores)
    rows = ranking(totals)
    top = rows[0][2]
    champions = [esc(names[uid]) for _, uid, pts in rows if pts == top]
    if len(champions) == 1:
        lead = f"Champion: {champions[0]} with {top} pts"
    else:
        lead = f"Champions: {', '.join(champions)} with {top} pts each"
    lines = [row(rank, f"**{esc(names[uid])}** — {pts(points)}") for rank, uid, points in rows]
    events = len({s["week_id"] for s in scores})
    await send_paged(
        channel,
        "Season complete: final standings",
        lines,
        subheading=lead,
        footer=f"{events} event(s) scored, {len(rows)} player(s)",
        content="Hello everyone! That's a wrap on the season. Here are the **final standings**!",
    )
    return True


async def template_autocomplete(interaction: discord.Interaction, current: str):
    templates = [t for t in await storage.aio.list_templates() if current.lower() in t["name"].lower()]
    return [
        app_commands.Choice(name=f"{t['name']} ({t['exact_points']}/{t['partial_points']})"[:100], value=t["name"])
        for t in templates
    ][:25]


class Weeks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _guard_new_event(self, interaction: discord.Interaction, needs_anime: bool) -> "tuple[bool, str]":
        """Checks everything that must be true before a new event opens.
        Returns (ok, reminder); when not ok the user has already been told why."""
        if not (await storage.aio.get_config("announcement_channel_id") and await storage.aio.get_config("user_channel_id")):
            await reply(interaction, "Run `/setup` first so I know which channels to use.")
            return False, ""
        if needs_anime and not await storage.aio.get_anime_list():
            await reply(interaction, "No anime list yet. Run `/start` first.")
            return False, ""
        if await storage.aio.season_ended():
            await reply(
                interaction,
                "The season has ended. Run `/reset` to archive it and start a new one, or `/restore` to undo.",
            )
            return False, ""
        current = await storage.aio.get_current_week()
        if current and not storage.week_is_locked(current):
            await reply(
                interaction,
                f"**{current['week_id']}** is still open. Run `/lock` or `/end-week` before starting another.",
            )
            return False, ""
        reminder = ""
        if current and not await storage.aio.week_has_results(current):
            reminder = (
                f"\nReminder: **{current['week_id']}** is locked but has no results yet. "
                f"Score it any time with `/end-week week:{current['week_id']}`."
            )
        return True, reminder

    async def _start_week(
        self,
        interaction: discord.Interaction,
        week_type: str,
        note: str,
        exact_points: int,
        partial_points: int,
        save_as: str = "",
    ):
        ok, reminder = await self._guard_new_event(interaction, needs_anime=True)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)

        replaced = None
        if save_as:
            replaced = await storage.aio.save_template(
                save_as.strip(), exact_points, partial_points, f"Saved from /new-event ({week_type})"
            )
        week_id = await storage.aio.next_week_id()
        await storage.aio.create_week(week_id, week_type, note, exact_points, partial_points)

        channel = await get_text_channel(interaction.client, "announcement_channel_id")
        if channel is not None:
            lines = [
                row("Type", week_type),
                row("Scoring", f"{pts(exact_points)} for an exact spot, {pts(partial_points)} if it's on the chart elsewhere"),
            ]
            rule = season_rules.get_rule(await storage.aio.get_season_rule())
            if not season_rules.is_standard(rule):
                lines.append(row("Season rule", rule.name))
            if note:
                lines.append(row("Note", esc(note)))
            await channel.send(
                content=f"Hello everyone! **{week_id}** predictions are open!",
                embed=style.embed(
                    f"{week_id} predictions are open",
                    ("Use /pick to submit your ranked top 10, with titles from /anime-list.", lines),
                ),
            )

        text = f"Started **{week_id}** ({week_type}): {exact_points} pts exact / {partial_points} pts wrong spot."
        if save_as:
            text += f" Template **{save_as.strip()}** {'updated' if replaced else 'saved'}."
        if channel is None:
            text += "\nI couldn't post the announcement. Check `/setup` and my permissions."
        await reply(interaction, text + reminder)

    @app_commands.command(name="event-template", description="Start a new week using a saved scoring template")
    @is_admin()
    @app_commands.describe(
        template="Saved scoring template to use",
        week_type="standard or special",
        note="Shown in the announcement, e.g. a suggested deadline (the bot never closes picks by itself)",
    )
    @app_commands.choices(week_type=WEEK_TYPE_CHOICES)
    @app_commands.autocomplete(template=template_autocomplete)
    async def event_template(
        self,
        interaction: discord.Interaction,
        template: str,
        week_type: app_commands.Choice[str] = None,
        note: app_commands.Range[str, 0, 500] = "",
    ):
        tmpl = await storage.aio.get_template(template)
        if tmpl is None:
            await reply(interaction, f"No template named **{template}**. See `/list-templates`.")
            return
        await self._start_week(
            interaction,
            week_type.value if week_type else "standard",
            note,
            tmpl["exact_points"],
            tmpl["partial_points"],
        )

    @app_commands.command(name="new-event", description="Start a new week with custom scoring, optionally saved as a template")
    @is_admin()
    @app_commands.describe(
        exact_points="Points for an exact position match",
        partial_points="Points if the anime charts but in a different position",
        week_type="standard or special",
        note="Shown in the announcement, e.g. a suggested deadline (the bot never closes picks by itself)",
        save_as="Also save these values as a reusable template with this name",
    )
    @app_commands.choices(week_type=WEEK_TYPE_CHOICES)
    async def new_event(
        self,
        interaction: discord.Interaction,
        exact_points: app_commands.Range[int, -100, 1000],
        partial_points: app_commands.Range[int, -100, 1000],
        week_type: app_commands.Choice[str] = None,
        note: app_commands.Range[str, 0, 500] = "",
        save_as: app_commands.Range[str, 0, 60] = "",
    ):
        await self._start_week(
            interaction,
            week_type.value if week_type else "standard",
            note,
            exact_points,
            partial_points,
            save_as,
        )

    @app_commands.command(name="set-points", description="Change a ranked week's scoring (then re-run /end-week to re-score)")
    @is_admin()
    @app_commands.describe(
        exact_points="Points for an exact position match",
        partial_points="Points if the anime charts but in a different position",
        week="Leave empty for the active event",
    )
    @app_commands.autocomplete(week=week_autocomplete)
    async def set_points(
        self,
        interaction: discord.Interaction,
        exact_points: app_commands.Range[int, -100, 1000],
        partial_points: app_commands.Range[int, -100, 1000],
        week: str = "",
    ):
        week_id = week or await storage.aio.get_current_week_id()
        wk = await storage.aio.get_week(week_id) if week_id else None
        if wk is None:
            await reply(interaction, "No such event. Pass a week id, or open one first.")
            return
        await storage.aio.set_week_points(week_id, exact_points, partial_points)
        text = f"**{week_id}** now scores {exact_points} pts exact / {partial_points} pts wrong spot."
        if await storage.aio.week_has_results(wk):
            text += " Results are already in, so run `/end-week` to re-score everyone."
        await reply(interaction, text)

    @app_commands.command(name="list-templates", description="Show saved scoring templates")
    @is_admin()
    async def list_templates_cmd(self, interaction: discord.Interaction):
        templates = await storage.aio.list_templates()
        if not templates:
            await reply(interaction, "No templates saved yet. `/new-event` with `save_as` creates one.")
            return
        lines = []
        for t in templates:
            line = f"**{t['name']}**: {t['exact_points']} exact / {t['partial_points']} wrong spot"
            if t.get("description"):
                line += f" ({t['description']})"
            lines.append(line)
        await reply(interaction, chunk_lines(lines, 1900)[0])

    @app_commands.command(name="lock", description="Close predictions for the active event")
    @is_admin()
    async def lock(self, interaction: discord.Interaction):
        week = await storage.aio.get_current_week()
        if week is None:
            await reply(interaction, "There's no active event.")
            return
        if storage.week_is_locked(week):
            await reply(interaction, f"**{week['week_id']}** is already locked.")
            return
        await interaction.response.defer(ephemeral=True)
        await storage.aio.set_week_locked(week["week_id"], True)
        count = await storage.aio.count_submissions(week)

        channel = await get_text_channel(interaction.client, "announcement_channel_id")
        if channel is not None:
            await channel.send(
                embed=style.embed(
                    f"{week['week_id']} is locked",
                    ("Predictions are closed.", [row("Submissions", count)]),
                )
            )
        await reply(interaction, f"Locked **{week['week_id']}** with {count} submission(s).")

    @app_commands.command(name="unlock", description="Reopen predictions for the active event (undo a /lock)")
    @is_admin()
    async def unlock(self, interaction: discord.Interaction):
        week = await storage.aio.get_current_week()
        if week is None:
            await reply(interaction, "There's no active event.")
            return
        if not storage.week_is_locked(week):
            await reply(interaction, f"**{week['week_id']}** isn't locked.")
            return
        await interaction.response.defer(ephemeral=True)
        await storage.aio.set_week_locked(week["week_id"], False)
        extra = ""
        if await storage.aio.week_has_results(week):
            extra = " Results were already entered, so re-run `/end-week` after any changes to re-score."
        channel = await get_text_channel(interaction.client, "announcement_channel_id")
        if channel is not None:
            await channel.send(
                embed=style.embed(f"{week['week_id']} is open again", ("You can /pick again until the next lock.", None))
            )
        await reply(interaction, f"Reopened **{week['week_id']}**.{extra}")

    @app_commands.command(
        name="end-week", description="Enter the real results and score everyone (re-run to correct mistakes)"
    )
    @is_admin()
    @app_commands.describe(week="Leave empty for the active event")
    @app_commands.autocomplete(week=week_autocomplete)
    @rank_options("Real chart rank")
    async def end_week(
        self,
        interaction: discord.Interaction,
        week: str = "",
        rank_1: str = "",
        rank_2: str = "",
        rank_3: str = "",
        rank_4: str = "",
        rank_5: str = "",
        rank_6: str = "",
        rank_7: str = "",
        rank_8: str = "",
        rank_9: str = "",
        rank_10: str = "",
    ):
        week_id = week or await storage.aio.get_current_week_id()
        wk = await storage.aio.get_week(week_id) if week_id else None
        if wk is None:
            await reply(interaction, "No such event. Pass a week id, or open one first.")
            return

        given = given_ranks(rank_1, rank_2, rank_3, rank_4, rank_5, rank_6, rank_7, rank_8, rank_9, rank_10)
        if wk["type"] == "ballot":
            await reply(interaction, f"{week_id} is an awards ballot, and ballots are switched off for now.")
            return

        if not storage.rules_complete(await storage.aio.get_rules(week_id)):
            await reply(
                interaction,
                f"The scoring rules for {week_id} are incomplete. Set them with `/set-points` first.",
            )
            return
        existing = await storage.aio.get_results(week_id) or []
        reject_prefix = (
            "Results rejected, nothing was saved. (A title that really charted but isn't on the list needs "
            "`/add-anime` first, then run `/end-week` again.)"
        )

        if given:
            errors, canonical, notes = merge_rank_edits(existing, given, await storage.aio.get_anime_list())
            if not errors and not any(canonical):
                errors.append("Every rank would be empty. Fill in at least one.")
            if errors:
                await reply(interaction, reject_prefix + "\n" + "\n".join(f"- {e}" for e in errors))
                return
            summary = "\nChanged: " + ", ".join(f"#{p} {canonical[p - 1] or 'emptied'}" for p in sorted(given)) + "."
            if existing and any(existing):
                summary += " The other ranks stayed as saved."
            if notes:
                summary += "\n" + "\n".join(notes)
            await _finish_results(interaction, week_id, canonical, from_command=True, summary=summary)
            return

        async def finish(i: discord.Interaction, canonical: list):
            await _finish_results(i, week_id, canonical)

        form = RankedForm(
            noun="Rank", prefill=existing, source_note="saved results", reject_prefix=reject_prefix, finish=finish
        )
        await form.open_first(interaction)

    @app_commands.command(name="end-season", description="Close the season and post the final leaderboard")
    @is_admin()
    async def end_season(self, interaction: discord.Interaction):
        scores = await storage.aio.list_scores()
        if not scores:
            await reply(interaction, "Nothing has been scored this season, so there's no leaderboard yet. Score a week with `/end-week` first.")
            return

        weeks = await storage.aio.list_weeks()
        unscored = [w["week_id"] for w in weeks if not await storage.aio.week_has_results(w)]
        confirmed_flow = bool(unscored)
        if unscored:
            view = ConfirmView(interaction.user.id, "End the season anyway")
            await interaction.response.send_message(
                f"{', '.join(unscored)} haven't been scored, so their points won't count in the final standings "
                "(open ones will be locked). End the season anyway?",
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.confirmed:
                if view.timed_out:
                    await interaction.edit_original_response(content="Timed out. Nothing changed.", view=None)
                return
        else:
            await interaction.response.defer(ephemeral=True)

        for w in weeks:
            if not storage.week_is_locked(w):
                await storage.aio.set_week_locked(w["week_id"], True)
        await storage.aio.mark_season_ended()
        posted = await _post_final_standings(interaction.client)
        backup_problem = await backup.try_backup(interaction.client, "season end", keep=True)

        text = "The season is over and the final standings are posted. Run `/reset` when you're ready for the next one."
        if not posted:
            text = "The season is marked as over, but I couldn't post to the announcement channel. Check `/setup` and my permissions."
        text += " A backup of the final state was saved." if backup_problem is None else f" The final backup didn't go through: {backup_problem}"
        if confirmed_flow:
            await interaction.edit_original_response(content=text, view=None)
        else:
            await reply(interaction, text)


async def setup(bot: commands.Bot):
    await bot.add_cog(Weeks(bot))
