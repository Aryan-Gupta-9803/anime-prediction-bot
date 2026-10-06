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

def test_multiplier_is_3x_for_a_lone_pick_and_1x_from_40_percent_of_players():
    assert [minority_multiplier(n, 10) for n in range(0, 11)] == [3.0, 3.0, 2.5, 1.5, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert minority_multiplier(2, 30) == 3.0 and minority_multiplier(9, 30) == 1.5 and minority_multiplier(12, 30) == 1.0
    assert minority_multiplier(1, 5) == 3.0 and minority_multiplier(2, 5) == 1.0, "2 of 5 is already 40%"
    assert minority_multiplier(0, 0) == 1.0
    for players in range(scoring.MIN_PLAYERS, 80):
        values = [minority_multiplier(n, players) for n in range(0, players + 1)]
        assert all(1.0 <= v <= 3.0 for v in values), players
        assert all(v * 2 == int(v * 2) for v in values), "multipliers come in steps of 0.5"
        assert all(a >= b for a, b in zip(values, values[1:])), "fewer pickers must never mean a smaller bonus"
        assert values[1] == 3.0 and values[-1] == 1.0
    assert scoring.MAX_MULTIPLIER == 3.0 and scoring.CROWD_SHARE == 0.40 and scoring.BONUS_RANKS == 3


# ---- the scoring ------------------------------------------------------------------------------------------------

FRIEREN = "Frieren: Beyond Journey's End"


def table():
    """Ten players. Their top 3s: player 0 One Piece / Frieren / Bleach (all three exactly right), players 1-2
    One Piece / Mob Psycho 100 / Bleach and Bleach / One Piece / Mob Psycho 100, the rest One Piece / Mob
    Psycho 100 / 86. Frieren is also at rank 5 for the last player (outside their top 3)."""
    tops = [["One Piece", FRIEREN, "Bleach"], ["One Piece", "Mob Psycho 100", "Bleach"],
            ["Bleach", "One Piece", "Mob Psycho 100"]] + [["One Piece", "Mob Psycho 100", "86"]] * 7
    out = []
    for i, top in enumerate(tops):
        picks = list(top)
        if i == 9:
            picks += ["Re:Zero", FRIEREN]
        picks += [t for t in FILLER if t not in picks and t != "Re:Zero x"]
        out.append(picks[:10])
    return out


def row_of(rows, title):
    return next(r for r in rows if r["predicted"] == title)


def test_a_players_top_3_earn_a_bonus_by_how_few_players_had_the_title_in_their_top_3():
    preds = table()
    results, notes = compute_minority_scores(preds, RESULTS, RULES)
    assert notes == [
        (FRIEREN, "x3 (1 of 10 players had it in their top 3)"),
        ("Bleach", "x1.5 (3 of 10 players had it in their top 3)"),
    ], "One Piece (everyone) is 1x and Mob Psycho/86 earn nothing, so neither is listed"

    plain = [scoring.compute_score(p, RESULTS, RULES) for p in preds]
    total, rows = results[0]
    assert row_of(rows, "One Piece")["points"] == 2 and "multiplier" not in row_of(rows, "One Piece")
    assert row_of(rows, FRIEREN)["points"] == 6 and row_of(rows, FRIEREN)["multiplier"] == 3.0     # exact 2 x 3
    assert row_of(rows, "Bleach")["points"] == 3 and row_of(rows, "Bleach")["multiplier"] == 1.5   # exact 2 x 1.5
    assert total == plain[0][0] + 4 + 1 == sum(r["points"] for r in rows)

    # a wrong-spot top-3 pick is multiplied too, and the half point is kept: Bleach at #1 is 1 pt x 1.5
    total, rows = results[2]
    assert row_of(rows, "Bleach")["points"] == 1.5 and row_of(rows, "Bleach")["multiplier"] == 1.5
    assert row_of(rows, "One Piece")["points"] == 1 and "multiplier" not in row_of(rows, "One Piece")
    assert total == plain[2][0] + 0.5

    # a top-3 pick that isn't on the chart earns nothing, so there is nothing to multiply
    assert row_of(results[1][1], "Mob Psycho 100")["points"] == 0
    # the same title outside the player's own top 3 is plain scoring, and does not count toward popularity
    total, rows = results[9]
    assert row_of(rows, FRIEREN)["points"] == 1 and "multiplier" not in row_of(rows, FRIEREN)
    assert total == plain[9][0], "ranks 4-10 are never multiplied"
    assert all(t == p for (t, _), (p, _) in zip(results[3:9], plain[3:9]))


def test_penalties_and_zero_point_rows_are_left_alone_and_half_points_are_kept():
    negative = {pos: (2, -1) for pos in range(1, 11)}
    results, _ = compute_minority_scores(table(), RESULTS, negative)
    bleach = row_of(results[2][1], "Bleach")              # wrong spot at #1: -1, never multiplied into a bigger penalty
    assert bleach["points"] == -1 and "multiplier" not in bleach
    for total, rows in results:
        assert total * 2 == int(total * 2) and all(r["points"] * 2 == int(r["points"] * 2) for r in rows)

    nothing = {pos: (0, 0) for pos in range(1, 11)}
    results, _ = compute_minority_scores(table(), RESULTS, nothing)
    assert all(total == 0 for total, _ in results)


def test_with_too_few_players_nothing_is_a_minority_so_everything_stays_1x():
    preds = table()[: scoring.MIN_PLAYERS - 1]
    results, notes = compute_minority_scores(preds, RESULTS, RULES)
    plain = [scoring.compute_score(p, RESULTS, RULES) for p in preds]
    assert [t for t, _ in results] == [t for t, _ in plain]
    assert notes == [("Multipliers", f"off this week: {len(preds)} player(s), needs {scoring.MIN_PLAYERS}")]

    results, notes = compute_minority_scores(table()[: scoring.MIN_PLAYERS], RESULTS, RULES)
    assert not any(label == "Multipliers" for label, _ in notes) and any(label == FRIEREN for label, _ in notes)


def test_no_players_and_players_with_nothing_in_their_top_3_do_not_break_scoring():
    assert compute_minority_scores([], RESULTS, RULES) == ([], [("Multipliers", "off this week: 0 player(s), needs 5")])
    empty_top = [""] * 3 + ["Dandadan", "Re:Zero"] + [""] * 5
    results, notes = compute_minority_scores([empty_top] * 5 + table()[:5], RESULTS, RULES)
    assert len(results) == 10 and results[0][0] == scoring.compute_score(empty_top, RESULTS, RULES)[0]


# ---- the registry -----------------------------------------------------------------------------------------------------

def test_the_minority_multiplier_announcement_reads_the_way_the_hosts_wrote_it():
    assert season_rules.MINORITY.name == "Minority Multiplier"
    assert season_rules.MINORITY.explain == (
        "For every week, your top 3 picks have the chance to earn a Bonus Multiplier based on how few players had it "
        "in their top 3 predictions.\n\n"
        "You can earn up to a 3x multiplier, and even overtake the regular points scorers by choosing an "
        "unconventional option!"
    )


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
        assert any("Season rule: Minority Multiplier" in e.description and "3x" in e.description for e in embeds)

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
        other = bot.tree.get_command("season-rule").to_dict(bot.tree)["options"][0]
        assert other["name"] == "rule" and other["required"] and [c["value"] for c in other["choices"]] == list(season_rules.RULES)
        await commands.Bot.close(bot)
    run(scenario())


def test_a_minority_multiplier_season_end_to_end_posts_multipliers_and_shows_them_in_my_score(env):
    async def scenario():
        await start_with(env, "minority-multiplier")
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        users = [env.alice, env.bob, env.carol, FakeUser("dave"), FakeUser("erin"), FakeUser("frank")]
        tops = [["One Piece", FRIEREN, "Bleach"], ["One Piece", "Mob Psycho 100", "Bleach"],
                ["One Piece", "Mob Psycho 100", "Bleach"]] + [["One Piece", "Mob Psycho 100", "86"]] * 3
        for user, top in zip(users, tops):
            out = await env.pick(user, ten(*top))
            assert "Saved" in out.all_text
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        out = await env.submit(j, "\n".join(RESULTS))
        assert "Results saved" in out.all_text

        # of 6 players: One Piece is in all 6 top 3s (1x), Frieren in 1 (3x), Bleach in 3 = 50% (1x)
        results_post = env.announce.sent[-1][1].description
        assert "### Minority Multiplier" in results_post
        assert f"**{FRIEREN}** :: x3 (1 of 6 players had it in their top 3)" in results_post
        assert "**Bleach**" not in results_post and "**One Piece**" not in results_post

        scores = {s["username"]: s for s in storage.list_scores("week-1")}
        plain = lambda top: scoring.compute_score(ten(*top), RESULTS, RULES)[0]
        assert storage.score_points(scores["alice"]) == plain(tops[0]) + 4, "Frieren exact: 2 pts become 6"
        assert storage.score_points(scores["frank"]) == plain(tops[5])

        k = env.inter(env.alice)
        await env.user_cog.my_score.callback(env.user_cog, k, "")
        assert "(exact x3)" in k.all_text

        # re-scoring after fixing the chart uses the rule again, not the plain arithmetic
        fix = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, fix, "", *([""] * 9), "Vinland Saga")
        assert f"x3 (1 of 6 players had it in their top 3)" in env.announce.sent[-1][1].description
    run(scenario())


# ---- /season-rule -----------------------------------------------------------------------------------------------------

def choice(key):
    return app_commands.Choice(name=season_rules.RULES[key].name, value=key)


def test_season_rule_command_sets_the_rule_mid_season_and_announces_it(env):
    async def scenario():
        i = env.inter(env.admin)
        await env.season.season_rule.callback(env.season, i, choice("minority-multiplier"))
        assert "No season is running yet" in i.all_text and storage.get_season_rule() == "standard"

        await start_with(env)                                   # the season is already running, with no rule
        before = len(env.announce.sent)
        i = env.inter(env.admin)
        await env.season.season_rule.callback(env.season, i, choice("minority-multiplier"))
        assert storage.get_season_rule() == "minority-multiplier"
        assert "now **Minority Multiplier** (it was **Standard (no extra rule)**)" in i.all_text and i.sent[-1].ephemeral
        content, embed, _ = env.announce.sent[-1]
        assert len(env.announce.sent) == before + 1 and "season rule" in content and "Season rule: Minority Multiplier" in embed.description

        i = env.inter(env.admin)
        await env.season.season_rule.callback(env.season, i, choice("minority-multiplier"))
        assert "already **Minority Multiplier**" in i.all_text and len(env.announce.sent) == before + 1

        i = env.inter(env.admin)                                # and back again
        await env.season.season_rule.callback(env.season, i, choice("standard"))
        assert storage.get_season_rule() == "standard" and "Season rule: Standard" in env.announce.sent[-1][1].description
        assert "keep those scores" not in i.all_text, "nothing was scored yet"
    run(scenario())


def test_season_rule_change_leaves_scored_weeks_alone_and_applies_to_the_next_scoring(env):
    async def scenario():
        await env.play_week()                                    # week-1 scored under standard rules, 2 players
        scored = {s["username"]: storage.score_points(s) for s in storage.list_scores("week-1")}
        i = env.inter(env.admin)
        await env.season.season_rule.callback(env.season, i, choice("minority-multiplier"))
        assert "week-1 already scored under the old rule and keep those scores" in i.all_text
        assert {s["username"]: storage.score_points(s) for s in storage.list_scores("week-1")} == scored

        # re-scoring now uses the new rule (2 players is too few, so it says multipliers are off)
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "week-1")
        await env.submit(j, "\n".join(RESULTS))
        assert "off this week: 2 player(s)" in env.announce.sent[-1][1].description
    run(scenario())


def test_season_rule_command_refuses_after_the_season_ended_and_is_host_only(env):
    async def scenario():
        await env.play_week()
        await env.weeks.end_season.callback(env.weeks, env.inter(env.admin))
        i = env.inter(env.admin)
        await env.season.season_rule.callback(env.season, i, choice("minority-multiplier"))
        assert "season has ended" in i.all_text and storage.get_season_rule() == "standard"

        from permissions import NotAdmin
        assert env.season.season_rule.checks
        for check in env.season.season_rule.checks:
            assert check(env.inter(env.admin)) is True
            with pytest.raises(NotAdmin):
                check(env.inter(env.alice))
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
