"""Seasonal rules: the registry, /start's rule choice, and the Minority Multiplier's multipliers."""
import asyncio

import pytest
from discord import app_commands

import scoring
import season_rules
import storage
from conftest import RESULTS, TITLES
from fakes import FakeUser
from scoring import compute_minority_scores, minority_multiplier

RULES = {pos: (2, 1) for pos in range(1, 11)}
TOP3 = RESULTS[:3]                       # One Piece, Frieren, Bleach
FILLER = ["Mob Psycho 100", "86", "Re:Zero", "Dandadan", "Spy x Family", "Demon Slayer", "Chainsaw Man",
          "Jujutsu Kaisen", "Vinland Saga", "Re:Zero x"]


def run(coro):
    return asyncio.run(coro)


def ten(*lead):
    """A full ten: the given titles first, then filler that isn't in the real top 3."""
    rest = [t for t in FILLER if t not in lead and t != "Re:Zero x"]
    return (list(lead) + rest)[:10]


# ---- the heuristic --------------------------------------------------------------------------------------------

def test_multiplier_is_4x_for_a_lone_pick_and_1x_from_40_percent_of_players():
    assert [minority_multiplier(n, 10) for n in range(0, 11)] == [4.0, 4.0, 3.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert minority_multiplier(2, 30) == 3.5 and minority_multiplier(12, 30) == 1.0 and minority_multiplier(11, 30) > 1.0
    assert minority_multiplier(1, 5) == 4.0 and minority_multiplier(2, 5) == 1.0, "2 of 5 is already 40%"
    assert minority_multiplier(0, 0) == 1.0
    for players in range(scoring.MIN_PLAYERS, 80):
        values = [minority_multiplier(n, players) for n in range(0, players + 1)]
        assert all(1.0 <= v <= 4.0 for v in values), players
        assert all(v * 2 == int(v * 2) for v in values), "multipliers come in steps of 0.5"
        assert all(a >= b for a, b in zip(values, values[1:])), "fewer pickers must never mean a smaller bonus"
        assert values[1] == 4.0 and values[-1] == 1.0
    assert scoring.MAX_MULTIPLIER == 4.0 and scoring.CROWD_SHARE == 0.40


# ---- the scoring ------------------------------------------------------------------------------------------------

def players(n_frieren, n_bleach, total=10):
    """`total` players who all have One Piece at #1. The first n_frieren have Frieren at #2 (its real spot), the
    first n_bleach have Bleach at #4 (on the chart, but not its real #3)."""
    frieren = "Frieren: Beyond Journey's End"
    out = []
    for i in range(total):
        picks = ["One Piece", frieren if i < n_frieren else "Mob Psycho 100", "86", "Bleach" if i < n_bleach else "Re:Zero"]
        picks += [t for t in FILLER if t not in picks and t != "Re:Zero x"]
        out.append(picks[:10])
    return out


def test_only_the_real_top_3_are_multiplied_by_how_few_players_had_them():
    preds = players(n_frieren=1, n_bleach=3)                       # of 10: One Piece 100%, Frieren 10%, Bleach 30%
    results, notes = compute_minority_scores(preds, RESULTS, RULES)
    assert [(label, text) for label, text in notes] == [
        ("One Piece", "x1 (10 of 10 players had it)"),
        ("Frieren: Beyond Journey's End", "x4 (1 of 10 players had it)"),
        ("Bleach", "x2 (3 of 10 players had it)"),
    ]

    total, rows = results[0]                                       # has all three; Frieren sits at rank 2 (exact)
    by_title = {r["predicted"]: r for r in rows}
    assert by_title["One Piece"]["points"] == 2 and "multiplier" not in by_title["One Piece"]
    assert by_title["Frieren: Beyond Journey's End"]["points"] == 8        # exact 2 x 4
    assert by_title["Frieren: Beyond Journey's End"]["multiplier"] == 4.0
    assert by_title["Bleach"]["points"] == 2 and by_title["Bleach"]["multiplier"] == 2.0   # wrong spot 1 x 2
    assert total == sum(r["points"] for r in rows)

    # a title that charted but outside the top 3 is never multiplied, however rare
    lone = ten("One Piece", "Dandadan")                            # Dandadan is real rank 4
    results, _ = compute_minority_scores(preds[:4] + [lone] * 1 + preds[5:], RESULTS, RULES)
    dandadan = next(r for r in results[4][1] if r["predicted"] == "Dandadan")
    assert dandadan["points"] in (1, 2) and "multiplier" not in dandadan


def test_penalties_and_zero_point_rows_are_left_alone_and_half_points_are_kept():
    negative = {pos: (2, -1) for pos in range(1, 11)}
    preds = players(n_frieren=1, n_bleach=1)
    results, _ = compute_minority_scores(preds, RESULTS, negative)
    bleach = next(r for r in results[0][1] if r["predicted"] == "Bleach")
    assert bleach["points"] == -1 and "multiplier" not in bleach, "a penalty must not be multiplied"
    for total, rows in results:
        assert total * 2 == int(total * 2) and all(r["points"] * 2 == int(r["points"] * 2) for r in rows)

    # 7 of 20 players had Bleach: 1.5x, so a 1-point wrong-spot match is worth exactly 1.5 (not rounded)
    results, _ = compute_minority_scores(players(1, 7, total=20), RESULTS, RULES)
    total, rows = results[0]
    bleach = next(r for r in rows if r["predicted"] == "Bleach")
    assert bleach["multiplier"] == 1.5 and bleach["points"] == 1.5
    assert total == 2 + 8 + 1.5 + sum(r["points"] for r in rows if r["predicted"] in FILLER and r["points"] > 0)

    nothing = {pos: (0, 0) for pos in range(1, 11)}
    results, _ = compute_minority_scores(preds, RESULTS, nothing)
    assert all(total == 0 for total, _ in results)


def test_with_too_few_players_nothing_is_a_minority_so_everything_stays_1x():
    preds = players(1, 1, total=scoring.MIN_PLAYERS - 1)
    results, notes = compute_minority_scores(preds, RESULTS, RULES)
    standard = [scoring.compute_score(p, RESULTS, RULES) for p in preds]
    assert [t for t, _ in results] == [t for t, _ in standard]
    assert notes == [("Multipliers", f"off this week: {len(preds)} player(s), needs {scoring.MIN_PLAYERS}")]

    results, notes = compute_minority_scores(players(1, 1, total=scoring.MIN_PLAYERS), RESULTS, RULES)
    assert any(label == "Frieren: Beyond Journey's End" for label, _ in notes)


def test_an_empty_top_3_spot_and_no_players_do_not_break_scoring():
    assert compute_minority_scores([], RESULTS, RULES) == ([], [("Multipliers", "off this week: 0 player(s), needs 5")])
    chart = ["One Piece", "", "Bleach"] + RESULTS[3:]
    results, notes = compute_minority_scores(players(1, 1), chart, RULES)
    assert [label for label, _ in notes] == ["One Piece", "Bleach"] and len(results) == 10


# ---- the registry -----------------------------------------------------------------------------------------------------

def test_registry_is_consistent_and_unknown_keys_mean_standard():
    assert season_rules.get_rule("") is season_rules.STANDARD
    assert season_rules.get_rule(None) is season_rules.STANDARD
    assert season_rules.get_rule("retired-rule") is season_rules.STANDARD
    assert season_rules.get_rule("minority-multiplier") is season_rules.MINORITY
    assert list(season_rules.RULES)[0] == season_rules.STANDARD_KEY, "standard stays first in /start's choices"
    assert 1 <= len(season_rules.RULES) <= 25
    for key, rule in season_rules.RULES.items():
        assert rule.key == key and 0 < len(rule.name) <= 100 and 0 < len(rule.explain) <= 1000
        assert not any(c in rule.name for c in "*_`~|"), "names are shown inside markdown"
    results, notes = season_rules.STANDARD.score([ten("One Piece")], RESULTS, RULES)
    assert results == [scoring.compute_score(ten("One Piece"), RESULTS, RULES)] and notes == []


# ---- through the bot ------------------------------------------------------------------------------------------------------

async def start_with(env, rule_key=None):
    await env.setup_channels()
    i = env.inter(env.admin)
    choice = app_commands.Choice(name=season_rules.RULES[rule_key].name, value=rule_key) if rule_key else None
    await env.season.start.callback(env.season, i, choice)
    out = await env.submit(i, "\n".join(TITLES))
    return out


def test_start_stores_the_rule_announces_it_and_reset_clears_it(env):
    async def scenario():
        out = await start_with(env, "minority-multiplier")
        assert storage.get_season_rule() == "minority-multiplier" and "Season rule: **Minority Multiplier**" in out.all_text
        embeds = [e for _, e, _ in env.announce.sent if e is not None]
        assert any("Season rule: Minority Multiplier" in e.description and "4x" in e.description for e in embeds)

        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        opened = env.announce.sent[-1][1].description
        assert "Season rule :: Minority Multiplier" in opened

        storage.reset_season()
        assert storage.get_season_rule() == "standard"
    run(scenario())


def test_start_without_a_rule_is_standard_and_says_nothing_extra(env):
    async def scenario():
        await start_with(env)
        assert storage.get_season_rule() == "standard"
        assert not any("Season rule" in (e.description or "") for _, e, _ in env.announce.sent if e is not None)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        assert "Season rule" not in env.announce.sent[-1][1].description
        i = env.inter(env.alice)
        await env.help.help_cmd.callback(env.help, i)
        assert "Season rule" not in i.all_text
    run(scenario())


def test_start_offers_the_registered_rules_as_choices(env):
    async def scenario():
        from bot import PredictionBot
        from discord.ext import commands
        bot = PredictionBot()
        for ext in ["cogs.season", "cogs.weeks", "cogs.user", "cogs.help", "cogs.backups"]:
            await bot.load_extension(ext)
        payload = bot.tree.get_command("start").to_dict(bot.tree)
        option = next(o for o in payload["options"] if o["name"] == "rule")
        assert not option.get("required")
        assert [c["value"] for c in option["choices"]] == list(season_rules.RULES)
        assert all(len(c["name"]) <= 100 for c in option["choices"])
        await commands.Bot.close(bot)
    run(scenario())


def test_a_minority_season_end_to_end_posts_multipliers_and_shows_them_in_my_score(env):
    async def scenario():
        await start_with(env, "minority-multiplier")
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        users = [env.alice, env.bob, env.carol, FakeUser("dave"), FakeUser("erin"), FakeUser("frank")]
        frieren = "Frieren: Beyond Journey's End"
        for n, user in enumerate(users):
            lead = ["One Piece"] + ([frieren] if n == 0 else []) + (["Bleach"] if n < 3 else [])
            i = env.inter(user)
            await env.user_cog.pick.callback(env.user_cog, i)
            out = await env.submit_ranked(i, ten(*lead))
            assert "Saved" in out.all_text
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        out = await env.submit(j, "\n".join(RESULTS))
        assert "Results saved" in out.all_text

        # of 6 players: One Piece 100% -> 1x, Frieren 1/6 (alone) -> 4x, Bleach 3/6 = 50% -> 1x
        results_post = env.announce.sent[-1][1].description
        assert "### Minority Multiplier" in results_post
        assert "**Frieren: Beyond Journey's End** :: x4 (1 of 6 players had it)" in results_post
        assert "**Bleach** :: x1 (3 of 6 players had it)" in results_post

        scores = {s["username"]: s for s in storage.list_scores("week-1")}
        top = storage.score_points(scores["alice"])
        plain = scoring.compute_score(ten("One Piece", frieren, "Bleach"), RESULTS, RULES)[0]
        assert top > plain, "the rare Frieren pick must earn more than normal scoring would give"
        assert storage.score_points(scores["frank"]) == scoring.compute_score(ten("One Piece"), RESULTS, RULES)[0]

        k = env.inter(env.alice)
        await env.user_cog.my_score.callback(env.user_cog, k, "")
        assert "(exact x4)" in k.all_text

        # re-scoring after fixing the chart uses the rule again, not the plain arithmetic
        fix = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, fix, "", *([""] * 9), "Vinland Saga")
        assert storage.score_points(storage.list_scores("week-1")[0]) >= 0
        assert "x4" in env.announce.sent[-1][1].description
    run(scenario())


def test_help_explains_the_season_rule_and_still_fits_discord_limits(env):
    async def scenario():
        await start_with(env, "minority-multiplier")
        i = env.inter(env.alice)
        await env.help.help_cmd.callback(env.help, i)
        embed = i.last.embed
        assert any(f.name == "Season rule: Minority Multiplier" for f in embed.fields)
        assert len(embed) < 6000 and all(len(f.value) <= 1024 and len(f.name) <= 256 for f in embed.fields)

        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        i = env.inter(env.alice)
        await env.help.help_cmd.callback(env.help, i)
        assert "Season rule: **Minority Multiplier**" in i.all_text

        j = env.inter(env.admin)
        await env.help.help_admin.callback(env.help, j)
        assert "[ok] Season rule: Minority Multiplier" in j.all_text
        assert len(j.last.embed) < 6000 and all(len(f.value) <= 1024 for f in j.last.embed.fields)
    run(scenario())
