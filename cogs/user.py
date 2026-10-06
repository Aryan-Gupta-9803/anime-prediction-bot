import json

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown as esc

import storage
from common import (
    SafeModal,
    get_text_channel,
    post_or_edit_embed,
    ranking,
    reply,
    season_totals,
    week_autocomplete,
)
import style
from style import pts, row
from textutil import clip
from forms import RankedForm
from validation import parse_category_pairs, validate_ballot_pick

REJECTED = "Submission rejected, nothing was saved (any earlier pick of yours is unchanged):"


def _jump_url(channel, message_id) -> str:
    return f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{message_id}"


async def _open_week_or_explain(interaction: discord.Interaction, week_id: str):
    """Re-checks at submit time that this event is still the open one (a host may have
    locked it, or started a new one, while the form was open)."""
    week = await storage.aio.get_current_week()
    if week is None or week["week_id"] != week_id:
        await reply(interaction, "That event has ended or been replaced. Nothing was saved; run `/pick` again.")
        return None
    if storage.week_is_locked(week):
        await reply(interaction, f"Predictions for {week_id} were locked before you submitted. Nothing was saved.")
        return None
    return week


async def _picks_channel_or_explain(interaction: discord.Interaction):
    channel = await get_text_channel(interaction.client, "user_channel_id")
    if channel is None:
        await reply(
            interaction,
            "I can't reach the picks channel right now, so nothing was saved. A host needs to re-run `/setup`.",
        )
    return channel


async def _publish(interaction, channel, existing_message_id, embed, save):
    """Posts/edits the member's embed, then saves it; a brand-new embed is removed
    again if the save fails, so the channel never shows a pick that wasn't stored."""
    try:
        message_id = await post_or_edit_embed(channel, existing_message_id, embed)
    except discord.Forbidden:
        await reply(interaction, "I'm not allowed to post in the picks channel, so nothing was saved. Tell a host.")
        return None
    try:
        await save(message_id)
    except Exception:
        if str(message_id) != str(existing_message_id):
            try:
                await (await channel.fetch_message(message_id)).delete()
            except discord.HTTPException:
                pass
        raise
    return message_id


async def _finish_picks(interaction: discord.Interaction, week_id: str, canonical: list):
    """Saves a validated ten-slot pick (empty slots allowed) and posts/updates the member's embed."""
    if await _open_week_or_explain(interaction, week_id) is None:
        return
    channel = await _picks_channel_or_explain(interaction)
    if channel is None:
        return

    await interaction.response.edit_message(content="Saving your picks...", view=None)
    user_id = str(interaction.user.id)
    existing = await storage.aio.get_prediction(week_id, user_id)
    embed = style.embed(
        f"{esc(interaction.user.display_name)}'s picks",
        (week_id, [row(i, esc(t) if t else "-") for i, t in enumerate(canonical, start=1)]),
        footer="Change them any time before the lock with /pick",
    )

    async def save(message_id):
        await storage.aio.upsert_prediction(week_id, user_id, str(interaction.user), canonical, message_id)

    message_id = await _publish(interaction, channel, existing["message_id"] if existing else None, embed, save)
    if message_id is not None:
        empty = canonical.count("")
        note = f" ({empty} spot(s) left empty.)" if empty else ""
        await reply(
            interaction,
            f"Saved. [See your picks]({_jump_url(channel, message_id)}).{note} "
            "You can change them until the host locks the week.",
        )


async def _prefill_ranks(week_id: str, user_id: str, valid_titles: list):
    """What to pre-fill the form with: this week's pick if there is one, otherwise the member's
    last submission from any earlier week. Titles that are no longer on the list are dropped.
    Returns (ranks, note) where note says where carried-over picks came from."""
    existing = await storage.aio.get_prediction(week_id, user_id)
    if existing:
        ranks, note = storage.prediction_ranks(existing), ""
    else:
        last = await storage.aio.get_last_picks(user_id)
        ranks, note = (storage.prediction_ranks(last), f"copied from {last['week_id']}") if last else ([""] * 10, "")
    by_norm = {storage.normalize(t): t for t in valid_titles}
    cleaned = [by_norm.get(storage.normalize(t), "") if t else "" for t in ranks]
    return cleaned, (note if any(cleaned) else "")


class BallotPickModal(SafeModal, title="Submit your awards ballot"):
    entries = discord.ui.TextInput(
        label="One guess per line: Category: Your guess",
        style=discord.TextStyle.paragraph,
        placeholder="Anime: Frieren\nMovie: Suzume\n...",
        required=True,
        max_length=4000,
    )

    def __init__(self, week_id: str, prefill: str = ""):
        super().__init__()
        self.week_id = week_id
        self.entries.default = prefill[: self.entries.max_length] or None

    async def on_submit(self, interaction: discord.Interaction):
        pairs, parse_errors = parse_category_pairs(self.entries.value)
        categories = await storage.aio.get_ballot_categories(self.week_id)
        ok, errors, canonical = validate_ballot_pick(pairs, [c["category"] for c in categories])
        errors = parse_errors + errors
        if errors or not ok:
            await reply(interaction, clip(REJECTED + "\n" + "\n".join(f"- {e}" for e in errors), 1900))
            return
        if not canonical:
            await reply(interaction, "I found no guesses, so nothing was saved. Use `Category: Guess`, one per line.")
            return
        if await _open_week_or_explain(interaction, self.week_id) is None:
            return
        channel = await _picks_channel_or_explain(interaction)
        if channel is None:
            return

        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        existing = await storage.aio.get_ballot_submission(self.week_id, user_id)
        lines = [row(f"**{esc(cat)}**", esc(guess)) for cat, guess in canonical.items()]
        embed = style.embed(
            f"{esc(interaction.user.display_name)}'s ballot",
            (self.week_id, [clip("\n".join(lines), 3500)]),
            footer="Change it any time before the lock with /pick",
        )

        async def save(message_id):
            await storage.aio.set_ballot_picks(self.week_id, user_id, str(interaction.user), canonical)
            await storage.aio.upsert_ballot_submission(self.week_id, user_id, str(interaction.user), message_id)

        message_id = await _publish(interaction, channel, existing["message_id"] if existing else None, embed, save)
        if message_id is not None:
            await reply(interaction, f"Saved. [See your ballot]({_jump_url(channel, message_id)}). You can change it until the host locks the event.")


def _breakdown_lines(week_type: str, raw: str) -> list:
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return []
    lines = []
    for r in rows:
        if week_type == "ballot":
            detail = f", answer: {esc(r['correct'])}" if r["reason"] == "incorrect" else ""
            lines.append(row(f"**{esc(r['category'])}**", f"{esc(r['guess']) or '-'} — {pts(r['points'])} ({r['reason']}{detail})"))
        else:
            lines.append(row(r["position"], f"{esc(r['predicted']) or '-'} — {pts(r['points'])} ({r['reason']})"))
    return lines


class UserCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="pick", description="Submit or change your prediction for the active event")
    async def pick(self, interaction: discord.Interaction):
        week = await storage.aio.get_current_week()
        if week is None:
            await reply(interaction, "No event is open right now.")
            return
        if storage.week_is_locked(week):
            await reply(interaction, f"Predictions are locked for {week['week_id']}.")
            return

        user_id = str(interaction.user.id)
        if week["type"] == "ballot":
            picks = await storage.aio.get_ballot_picks(week["week_id"], user_id)
            prefill = "\n".join(f"{p['category']}: {p['guess']}" for p in picks)
            await interaction.response.send_modal(BallotPickModal(week["week_id"], prefill))
        else:
            week_id = week["week_id"]
            ranks, note = await _prefill_ranks(week_id, user_id, await storage.aio.get_anime_list())

            async def precheck(i: discord.Interaction) -> bool:
                return await _open_week_or_explain(i, week_id) is not None

            async def finish(i: discord.Interaction, canonical: list):
                await _finish_picks(i, week_id, canonical)

            form = RankedForm(
                noun="Rank", prefill=ranks, source_note=note, reject_prefix=REJECTED, finish=finish, precheck=precheck
            )
            await form.open_first(interaction)

    @app_commands.command(name="my-score", description="See how you scored (only you can see this)")
    @app_commands.describe(week="Leave empty for the active event")
    @app_commands.autocomplete(week=week_autocomplete)
    async def my_score(self, interaction: discord.Interaction, week: str = ""):
        week_id = week or await storage.aio.get_current_week_id()
        wk = await storage.aio.get_week(week_id) if week_id else None
        if wk is None:
            await reply(interaction, "No such event, and none is active right now.")
            return

        user_id = str(interaction.user.id)
        scores = await storage.aio.list_scores()
        mine = next((s for s in scores if s["week_id"] == week_id and str(s["user_id"]) == user_id), None)
        if mine is None:
            submitted = (
                await storage.aio.get_ballot_submission(week_id, user_id)
                if wk["type"] == "ballot"
                else await storage.aio.get_prediction(week_id, user_id)
            )
            await reply(
                interaction,
                f"Your picks for {week_id} are in, but it hasn't been scored yet."
                if submitted
                else f"You didn't submit picks for {week_id}.",
            )
            return

        totals, _ = season_totals(scores)
        footer = None
        if user_id in totals:
            rank = next(r for r, uid, _ in ranking(totals) if uid == user_id)
            footer = f"Season total: {totals[user_id]} pts (rank {rank} of {len(totals)})"
        lines = _breakdown_lines(wk["type"], mine["breakdown_json"])
        embed = style.embed(
            f"Your score: {week_id}",
            (pts(storage.score_points(mine)), [clip("\n".join(lines), 3500)] if lines else None),
            footer=footer,
        )
        await reply(interaction, embed=embed)

    @app_commands.command(name="leaderboard", description="Season standings, or one event's (only you can see this)")
    @app_commands.describe(week="Leave empty for the season standings")
    @app_commands.autocomplete(week=week_autocomplete)
    async def leaderboard(self, interaction: discord.Interaction, week: str = ""):
        scores = await storage.aio.list_scores(week or None)
        totals, names = season_totals(scores)
        rows = ranking(totals)
        if not rows:
            await reply(interaction, "No scores yet.")
            return

        lines = [row(rank, f"**{esc(names[uid])}** — {pts(points)}") for rank, uid, points in rows[:20]]
        mine = next(((rank, pts) for rank, uid, pts in rows if uid == str(interaction.user.id)), None)
        embed = style.embed(
            f"Leaderboard: {week}" if week else "Season leaderboard",
            (None, lines),
            footer=f"You: rank {mine[0]} with {mine[1]} pts" if mine else "You have no points yet",
        )
        await reply(interaction, embed=embed)

    @app_commands.command(name="anime-list", description="The titles you can pick from this season")
    async def anime_list(self, interaction: discord.Interaction):
        titles = await storage.aio.get_anime_list()
        if not titles:
            await reply(interaction, "No season has been started yet.")
            return
        pages = style.paged(
            "Eligible anime",
            [row(i, esc(t)) for i, t in enumerate(titles, start=1)],
            subheading=f"{len(titles)} titles to choose from",
        )
        for page in pages:
            await reply(interaction, embed=page)


async def setup(bot: commands.Bot):
    await bot.add_cog(UserCommands(bot))
