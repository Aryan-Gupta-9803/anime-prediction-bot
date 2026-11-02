"""Points by rank (rank_multipliers on /new-event, /event-template and /set-points) and the Gear 5 event."""
import asyncio

import pytest
from discord import app_commands

import event_types
import scoring
import storage
from conftest import RESULTS, TITLES
from validation import parse_rank_multipliers

GEAR_5 = app_commands.Choice(name="gear 5", value="gear5")
FLAT = {pos: (2, 1) for pos in range(1, 11)}


def run(coro):
    return asyncio.run(coro)


async def ready(env):
    await env.setup_channels()
    await env.start_season(TITLES)


# ---- reading what the host typed ------------------------------------------------------------------------------------------

def test_parser_reads_rank_colon_factor_in_a_few_spellings_and_rejects_nonsense():
    assert parse_rank_multipliers("") == ({}, [])
    assert parse_rank_multipliers("5:5") == ({5: 5}, [])
    assert parse_rank_multipliers("5:5, 1:2") == ({5: 5, 1: 2}, [])
    assert parse_rank_multipliers("rank 5 = 5x; 1:2\n3:0") == ({5: 5, 1: 2, 3: 0}, [])
    assert parse_rank_multipliers(" 10:100 ") == ({10: 100}, [])

    for bad, fragment in (("five", "should look like 5:5"), ("5", "should look like 5:5"), ("5:x", "should look like 5:5"),
                          ("0:2", "from 1 to 10"), ("11:2", "from 1 to 10"), ("5:101", "from 0 to 100"),
                          ("5:2, 5:3", "listed more than once"), ("5:2.5", "should look like 5:5")):
        values, errors = parse_rank_multipliers(bad)
        assert errors and fragment in errors[0], (bad, errors)
    values, errors = parse_rank_multipliers("5:5, nope, 1:2")
    assert values == {5: 5, 1: 2} and len(errors) == 1, "every problem is reported, good parts are still parsed"


# ---- opening weeks with a points matrix ---------------------------------------------------------------------------------------

def test_new_event_can_weight_chosen_ranks_and_says_so_in_the_announcement(env):
    async def scenario():
        await ready(env)
        i = env.inter(env.admin)
        await env.weeks.new_event.callback(env.weeks, i, 2, 1, None, "", "", "5:5, 1:2")
        assert storage.get_rules("week-1") == {**FLAT, 1: (4, 2), 5: (10, 5)}
        assert "rank 1 pays 2x (4 pts exact / 2 pts elsewhere on the chart)" in i.all_text and "rank 5 pays 5x" in i.all_text
        post = env.announce.sent[-1][1].description
        assert "Points by rank ::" in post and "rank 5 pays 5x (10 pts exact / 5 pts elsewhere on the chart)" in post
        assert "Scoring :: 2 pts for an exact spot" in post

        # an ordinary week has no such row and flat points
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        await env.weeks.new_event.callback(env.weeks, env.inter(env.admin), 2, 1, None, "", "", "")
        assert storage.get_rules("week-2") == FLAT and "Points by rank" not in env.announce.sent[-1][1].description
    run(scenario())


def test_event_template_takes_the_same_option_and_templates_stay_flat(env):
    async def scenario():
        await ready(env)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "", "3:3")
        assert storage.get_rules("week-1") == {**FLAT, 3: (6, 3)}
        assert storage.get_template("Standard")["exact_points"] == 2, "a template keeps just the two base numbers"
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        i = env.inter(env.admin)
        await env.weeks.new_event.callback(env.weeks, i, 3, 1, None, "", "Spicy", "2:2")
        assert "only the two base numbers, not the rank multipliers" in i.all_text
        assert storage.get_template("Spicy")["exact_points"] == 3
    run(scenario())


def test_bad_rank_text_starts_nothing(env):
    async def scenario():
        await ready(env)
        before = len(env.announce.sent)
        i = env.inter(env.admin)
        await env.weeks.new_event.callback(env.weeks, i, 2, 1, None, "", "", "5:5, 12:2")
        assert "Nothing was started" in i.all_text and "from 1 to 10" in i.all_text
        assert storage.list_weeks() == [] and len(env.announce.sent) == before
    run(scenario())


# ---- Gear 5 -------------------------------------------------------------------------------------------------------------------

def test_gear_5_pays_five_times_at_rank_5_and_is_announced(env):
    async def scenario():
        await ready(env)
        i = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, i, "Standard", GEAR_5, "")
        assert "Started **Week 1** (Gear 5)" in i.all_text and "rank 5 pays 5x" in i.all_text
        assert storage.get_week("week-1")["type"] == "gear5"
        assert storage.get_rules("week-1") == {**FLAT, 5: (10, 5)}
        post = env.announce.sent[-1][1].description
        assert "Type :: Gear 5" in post and "Gear 5 :: Rank 5 pays 5x this week" in post
        assert "rank 5 pays 5x (10 pts exact / 5 pts elsewhere on the chart)" in post

        h = env.inter(env.alice)
        await env.help.help_cmd.callback(env.help, h)
        assert "(Gear 5)" in h.all_text and "points vary by position" in h.all_text
    run(scenario())


def test_gear_5_uses_whatever_base_points_the_host_chooses_and_the_host_can_override_it(env):
    async def scenario():
        await ready(env)
        await env.weeks.new_event.callback(env.weeks, env.inter(env.admin), 3, 1, GEAR_5, "", "", "")
        assert storage.get_rules("week-1")[5] == (15, 5) and storage.get_rules("week-1")[4] == (3, 1)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        await env.weeks.new_event.callback(env.weeks, env.inter(env.admin), 2, 1, GEAR_5, "", "", "5:3, 1:2")
        assert storage.get_rules("week-2") == {**FLAT, 1: (4, 2), 5: (6, 3)}, "typed values win over the event's own"
    run(scenario())


def test_scoring_a_gear_5_week_end_to_end(env):
    async def scenario():
        await ready(env)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", GEAR_5, "")
        bob = list(RESULTS); bob[4] = "Mob Psycho 100"                     # rank 5 isn't on the chart at all
        carol = list(RESULTS); carol[4], carol[5] = carol[5], carol[4]     # ranks 5 and 6 swapped: both on the chart elsewhere
        await env.pick(env.alice, RESULTS)
        await env.pick(env.bob, bob)
        await env.pick(env.carol, carol)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(RESULTS))
        scores = {s["username"]: storage.score_points(s) for s in storage.list_scores("week-1")}
        assert scores["alice"] == 9 * 2 + 10                                # nine ordinary hits, and rank 5 pays 10
        assert scores["bob"] == 9 * 2 + 0                                   # misses the 10 and the 2
        assert scores["carol"] == 8 * 2 + 5 + 1                             # rank 5 on the chart elsewhere: 5, rank 6: 1
        reference = scoring.compute_score(RESULTS, RESULTS, {**FLAT, 5: (10, 5)})[0]
        assert scores["alice"] == reference

        k = env.inter(env.alice)
        await env.user_cog.my_score.callback(env.user_cog, k, "")
        assert "5 :: One Piece" not in k.all_text and "5 :: Re:Zero — 10 pts (exact)" in k.all_text
    run(scenario())


def test_the_minority_multiplier_only_touches_the_top_3_so_a_5x_rank_5_is_left_alone():
    rules = {**FLAT, 5: (10, 5)}
    predictions = [list(RESULTS)] + [RESULTS[:3] + ["Mob Psycho 100", "86"] + RESULTS[5:]] * 5     # only player 0 has rank 5 right
    results, _ = scoring.compute_minority_scores(predictions, RESULTS, rules)
    plain = scoring.compute_score(RESULTS, RESULTS, rules)
    row5 = next(r for r in results[0][1] if r["position"] == 5)
    assert row5["points"] == 10 and "multiplier" not in row5
    assert [r["points"] for r in results[0][1]][3:] == [r["points"] for r in plain[1]][3:]


# ---- changing the points afterwards ------------------------------------------------------------------------------------------------

def test_set_points_keeps_gear_5s_rank_5_and_can_set_other_ranks(env):
    async def scenario():
        await ready(env)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", GEAR_5, "")
        i = env.inter(env.admin)
        await env.weeks.set_points.callback(env.weeks, i, 4, 2, "")
        assert storage.get_rules("week-1") == {pos: (4, 2) for pos in range(1, 11)} | {5: (20, 10)}
        assert "rank 5 pays 5x (20 pts exact / 10 pts elsewhere on the chart)" in i.all_text

        i = env.inter(env.admin)
        await env.weeks.set_points.callback(env.weeks, i, 2, 1, "", "1:3")
        assert storage.get_rules("week-1") == {**FLAT, 1: (6, 3), 5: (10, 5)}

        i = env.inter(env.admin)
        await env.weeks.set_points.callback(env.weeks, i, 2, 1, "", "20:2")
        assert "Nothing was changed" in i.all_text and storage.get_rules("week-1")[1] == (6, 3), "bad text changes nothing"
    run(scenario())


def test_set_points_on_a_custom_matrix_week_warns_when_it_resets_it(env):
    async def scenario():
        await ready(env)
        await env.weeks.new_event.callback(env.weeks, env.inter(env.admin), 2, 1, None, "", "", "2:4")
        i = env.inter(env.admin)
        await env.weeks.set_points.callback(env.weeks, i, 3, 1, "")
        assert storage.get_rules("week-1") == {pos: (3, 1) for pos in range(1, 11)}
        assert "rank multipliers were reset" in i.all_text
        i = env.inter(env.admin)
        await env.weeks.set_points.callback(env.weeks, i, 3, 1, "")          # flat to flat: nothing to warn about
        assert "reset" not in i.all_text
    run(scenario())


def test_rescoring_after_set_points_uses_the_new_matrix(env):
    async def scenario():
        await ready(env)
        await env.weeks.new_event.callback(env.weeks, env.inter(env.admin), 2, 1, None, "", "", "")
        await env.pick(env.alice, RESULTS)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(RESULTS))
        assert storage.score_points(storage.list_scores("week-1")[0]) == 20
        await env.weeks.set_points.callback(env.weeks, env.inter(env.admin), 2, 1, "", "5:5")
        k = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, k, "", *([""] * 9), "Vinland Saga")     # re-run to re-score
        assert storage.score_points(storage.list_scores("week-1")[0]) == 28
    run(scenario())


# ---- plumbing --------------------------------------------------------------------------------------------------------------------------

def test_the_option_is_offered_on_the_three_commands_and_gear_5_is_a_choice(env):
    async def scenario():
        from bot import PredictionBot
        from discord.ext import commands
        bot = PredictionBot()
        for ext in ["cogs.season", "cogs.weeks", "cogs.user", "cogs.help", "cogs.backups"]:
            await bot.load_extension(ext)
        for name in ("new-event", "event-template", "set-points"):
            options = {o["name"]: o for o in bot.tree.get_command(name).to_dict(bot.tree)["options"]}
            o = options["rank_multipliers"]
            assert o["type"] == 3 and not o.get("required") and 0 < len(o["description"]) <= 100 and o["max_length"] == 100
        week_type = next(o for o in bot.tree.get_command("new-event").to_dict(bot.tree)["options"] if o["name"] == "week_type")
        assert [(c["name"], c["value"]) for c in week_type["choices"]][-1] == ("gear 5", "gear5")
        await commands.Bot.close(bot)
    run(scenario())


def test_a_points_matrix_survives_backup_and_restore(env):
    async def scenario():
        await ready(env)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", GEAR_5, "", "1:2")
        before = storage.get_rules("week-1")
        data, _ = storage.snapshot()
        storage.reset_season()
        assert storage.get_rules("week-1") == {}
        storage.restore_snapshot(data)
        assert storage.get_rules("week-1") == before == {**FLAT, 1: (4, 2), 5: (10, 5)}
        assert storage.get_week("week-1")["type"] == "gear5" and event_types.type_label("gear5") == "Gear 5"
    run(scenario())


def test_flat_weeks_are_created_exactly_as_before():
    # the storage default: no multipliers means every position gets the same two numbers
    assert storage._rule_rows("w", 2, 1) == [("w", pos, 2, 1) for pos in range(1, 11)]
    assert storage._rule_rows("w", 2, 1, {5: 5})[4] == ("w", 5, 10, 5)
    assert storage._rule_rows("w", 2, 1, {11: 9}) == storage._rule_rows("w", 2, 1), "ranks outside 1-10 are ignored"
