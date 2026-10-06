"""/pick and /end-week with rank fields: change only the ranks given, keep the rest."""
import asyncio

import pytest

import storage
from conftest import RESULTS, TITLES
from validation import CLEAR, merge_rank_edits, validate_slots

ALL_FIELDS = 10


def run(coro):
    return asyncio.run(coro)


async def start_week(env, template="Standard"):
    await env.setup_channels()
    await env.start_season(TITLES)
    await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), template, None, "")


async def full_pick(env, user, ranks=RESULTS):
    i = env.inter(user)
    await env.user_cog.pick.callback(env.user_cog, i)
    await env.submit_ranked(i, ranks)


async def quick_pick(env, user, **fields):
    """Runs /pick with only the given rank fields, e.g. quick_pick(env, alice, rank_3="Bleach")."""
    i = env.inter(user)
    values = [fields.get(f"rank_{n}", "") for n in range(1, ALL_FIELDS + 1)]
    await env.user_cog.pick.callback(env.user_cog, i, *values)
    return i


def saved(env, user, week="week-1"):
    return storage.prediction_ranks(storage.get_prediction(week, str(user.id)))


# ---- the merge rules on their own --------------------------------------------------------------------------

def test_merge_changes_only_the_ranks_given_and_moves_duplicates():
    base = ["One Piece", "Bleach", "Dandadan", "", "", "", "", "", "", ""]
    errors, canonical, notes = merge_rank_edits(base, {2: "frieren: beyond journey's end"}, TITLES)
    assert not errors and not notes
    assert canonical == ["One Piece", "Frieren: Beyond Journey's End", "Dandadan"] + [""] * 7

    errors, canonical, notes = merge_rank_edits(base, {1: "Dandadan"}, TITLES)
    assert canonical[:3] == ["Dandadan", "Bleach", ""], "the title moved, so its old rank is empty"
    assert notes == ["'Dandadan' moved from rank #3 to #1, so #3 is now empty."]

    errors, canonical, notes = merge_rank_edits(base, {1: "Dandadan", 3: "One Piece"}, TITLES)
    assert canonical[:3] == ["Dandadan", "Bleach", "One Piece"] and not notes, "swapping two ranks needs no empties"

    errors, canonical, _ = merge_rank_edits(base, {2: CLEAR}, TITLES)
    assert canonical[:3] == ["One Piece", "", "Dandadan"]
    errors, canonical, _ = merge_rank_edits(base, {2: "-"}, TITLES)
    assert canonical[1] == ""


def test_merge_refuses_unknown_titles_and_repeats_within_the_fields():
    base = [""] * 10
    errors, _, _ = merge_rank_edits(base, {4: "Blech"}, TITLES)
    assert len(errors) == 1 and "Rank #4 ('Blech')" in errors[0]
    errors, _, _ = merge_rank_edits(base, {1: "Bleach", 5: "bleach"}, TITLES)
    assert errors == ["Rank #5 ('Bleach') repeats rank #1."]
    # same rules as the form: the same text is judged the same way
    assert validate_slots(["Blech"] + [""] * 9, TITLES)[0]


# ---- /pick ---------------------------------------------------------------------------------------------------

def test_one_field_changes_one_rank_and_updates_the_post_in_place(env):
    async def scenario():
        await start_week(env)
        await full_pick(env, env.alice)
        post = env.picks.sent[0][2]

        out = await quick_pick(env, env.alice, rank_3="Mob Psycho 100")
        assert "Saved" in out.all_text and "Changed: #3 Mob Psycho 100" in out.all_text and "Kept 9 other" in out.all_text
        assert saved(env, env.alice) == RESULTS[:2] + ["Mob Psycho 100"] + RESULTS[3:]
        assert len(env.picks.sent) == 1 and post.embed.description.count(" :: ") >= 10, "edited in place, not reposted"
        assert "3 :: Mob Psycho 100" in post.embed.description
        assert out.deferred and out.sent[-1].ephemeral
    run(scenario())


def test_a_title_moves_when_placed_at_another_rank(env):
    async def scenario():
        await start_week(env)
        await full_pick(env, env.alice)
        out = await quick_pick(env, env.alice, rank_1="Bleach")        # Bleach was rank 3
        assert saved(env, env.alice) == ["Bleach", RESULTS[1], ""] + RESULTS[3:]
        assert "'Bleach' moved from rank #3 to #1" in out.all_text
    run(scenario())


def test_leave_empty_clears_a_rank(env):
    async def scenario():
        await start_week(env)
        await full_pick(env, env.alice)
        out = await quick_pick(env, env.alice, rank_10=CLEAR)
        assert saved(env, env.alice) == RESULTS[:9] + [""] and "#10 emptied" in out.all_text
    run(scenario())


def test_a_bad_field_refuses_everything_and_keeps_the_earlier_pick(env):
    async def scenario():
        await start_week(env)
        await full_pick(env, env.alice)
        out = await quick_pick(env, env.alice, rank_2="Bleach", rank_3="Not A Show")
        assert "rejected" in out.all_text and "Rank #3 ('Not A Show')" in out.all_text
        assert saved(env, env.alice) == RESULTS, "nothing from the refused edit may be saved"

        out = await quick_pick(env, env.alice, rank_1="One Piece", rank_2="One Piece")
        assert "repeats rank #1" in out.all_text and saved(env, env.alice) == RESULTS

        out = await quick_pick(env, env.alice, rank_4="Blech")
        assert "Did you mean 'Bleach'" in out.all_text
    run(scenario())


def test_first_time_quick_pick_leaves_the_other_ranks_empty(env):
    async def scenario():
        await start_week(env)
        out = await quick_pick(env, env.bob, rank_1="One Piece", rank_2="Bleach")
        assert saved(env, env.bob) == ["One Piece", "Bleach"] + [""] * 8
        assert "Saved" in out.all_text and "Kept" not in out.all_text
        assert len(env.picks.sent) == 1

        out = await quick_pick(env, env.carol, rank_1=CLEAR)            # nothing left at all
        assert "Every rank would be empty" in out.all_text
        assert storage.get_prediction("week-1", str(env.carol.id)) is None
    run(scenario())


def test_quick_edit_starts_from_last_weeks_picks_when_this_week_has_none(env):
    async def scenario():
        await start_week(env)
        await full_pick(env, env.alice)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(RESULTS))
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")

        out = await quick_pick(env, env.alice, rank_2="86")
        assert "copied from week-1" in out.all_text
        assert saved(env, env.alice, "week-2") == [RESULTS[0], "86"] + RESULTS[2:]

        # titles that left the season list are not carried over
        await env.season.remove_anime.callback(env.season, env.inter(env.admin), "Vinland Saga")
        out = await quick_pick(env, env.alice, rank_1="Bleach")
        assert saved(env, env.alice, "week-2")[9] == ""
    run(scenario())


def test_quick_edit_respects_locks_and_missing_events(env):
    async def scenario():
        out = await quick_pick(env, env.alice, rank_1="Bleach")
        assert "No event is open" in out.all_text
        await start_week(env)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        out = await quick_pick(env, env.alice, rank_1="Bleach")
        assert "locked" in out.all_text and storage.get_prediction("week-1", str(env.alice.id)) is None
    run(scenario())


def test_no_fields_still_opens_the_form_and_ballots_refuse_rank_fields(env):
    async def scenario():
        await start_week(env)
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        assert i.modal is not None and not i.sent

        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        k = env.inter(env.admin)
        await env.weeks.new_ballot.callback(env.weeks, k, "Awards", "")
        await env.submit(k, "Anime | 10\nMovie | 5")
        out = await quick_pick(env, env.alice, rank_1="Bleach")
        assert "awards ballot" in out.all_text and out.modal is None
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        assert i.modal is not None, "a ballot with no fields still opens its form"
    run(scenario())


# ---- /end-week -----------------------------------------------------------------------------------------------

def test_end_week_can_correct_one_rank_and_rescores(env):
    async def scenario():
        await start_week(env)
        await full_pick(env, env.alice)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))

        first = RESULTS[:9] + ["Mob Psycho 100"]                         # entered with a mistake at rank 10
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(first))
        before = storage.score_points(storage.list_scores("week-1")[0])

        fix = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, fix, "", *([""] * 9), "Vinland Saga")
        assert "Results saved" in fix.all_text and "Changed: #10 Vinland Saga" in fix.all_text
        assert storage.get_results("week-1") == RESULTS
        assert storage.score_points(storage.list_scores("week-1")[0]) == before + 2

        bad = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, bad, "", "Blech")
        assert "Results rejected" in bad.all_text and storage.get_results("week-1") == RESULTS

        fresh = env.inter(env.admin)                                       # no fields: the form, pre-filled
        await env.weeks.end_week.callback(env.weeks, fresh, "")
        assert fresh.modal.title == "Ranks 1-5 (saved results)"
    run(scenario())


def test_end_week_fields_need_results_to_start_from_or_fill_in_the_chart(env):
    async def scenario():
        await start_week(env)
        i = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, i, "", "One Piece", "Bleach")
        assert storage.get_results("week-1") == ["One Piece", "Bleach"] + [""] * 8
        assert "2 chart spot" not in i.all_text and "8 chart spot(s) were left empty" in i.all_text

        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "", CLEAR, CLEAR)
        assert "Every rank would be empty" in j.all_text
    run(scenario())


# ---- the suggestions, and how the command looks to Discord ----------------------------------------------------

def test_title_suggestions_match_as_you_type_and_offer_leave_empty(env):
    from common import title_autocomplete

    async def scenario():
        await start_week(env)
        i = env.inter(env.alice)

        names = [c.name for c in await title_autocomplete(i, "")]
        assert names[0] == CLEAR and len(names) == len(TITLES) + 1

        names = [c.name for c in await title_autocomplete(i, "b")]
        assert names[0] == "Bleach", "titles starting with the text come first"
        assert "Chainsaw Man" not in names
        assert [c.name for c in await title_autocomplete(i, "SAGA")] == ["Vinland Saga"]      # anywhere, any case
        assert [c.name for c in await title_autocomplete(i, "zzz")] == []
        assert [c.name for c in await title_autocomplete(i, "leave")] == [CLEAR]
        assert [c.value for c in await title_autocomplete(i, "frieren")] == ["Frieren: Beyond Journey's End"]

        for n in range(40):                                                 # more titles than Discord shows
            storage.add_anime(f"Extra Show {n}")
        assert len(await title_autocomplete(i, "")) == 25
        assert len(await title_autocomplete(i, "extra")) == 25
        storage.add_anime("L" * 150)
        assert all(len(c.name) <= 100 and len(c.value) <= 100 for c in await title_autocomplete(i, "LLL"))
    run(scenario())


def test_pick_and_end_week_offer_ten_optional_suggesting_rank_fields(env):
    async def scenario():
        from bot import PredictionBot
        from discord.ext import commands
        bot = PredictionBot()
        for ext in ["cogs.season", "cogs.weeks", "cogs.user", "cogs.help", "cogs.backups"]:
            await bot.load_extension(ext)
        for name in ("pick", "end-week"):
            payload = bot.tree.get_command(name).to_dict(bot.tree)
            ranks = [o for o in payload["options"] if o["name"].startswith("rank-")]
            assert [o["name"] for o in ranks] == [f"rank-{n}" for n in range(1, 11)]
            assert all(o.get("autocomplete") and not o.get("required") for o in ranks)
            assert all(len(o["description"]) <= 100 for o in ranks)
        week = [o for o in bot.tree.get_command("end-week").to_dict(bot.tree)["options"] if o["name"] == "week"][0]
        assert week.get("autocomplete"), "the week field keeps its own suggestions"
        await commands.Bot.close(bot)
    run(scenario())
