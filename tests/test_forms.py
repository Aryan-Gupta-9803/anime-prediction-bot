import asyncio

import storage
from conftest import RESULTS, TITLES


def run(coro):
    return asyncio.run(coro)


async def open_pick(env, user):
    i = env.inter(user)
    await env.user_cog.pick.callback(env.user_cog, i)
    return i


async def start_week(env):
    await env.setup_channels()
    await env.start_season(TITLES)
    await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")


def test_the_form_is_ten_separate_boxes_over_two_steps_and_saves_only_at_the_end(env):
    async def scenario():
        await start_week(env)
        i = await open_pick(env, env.alice)
        assert [b.label for b in i.modal.inputs] == ["Rank #1 (top of the chart)", "Rank #2", "Rank #3", "Rank #4", "Rank #5"]
        assert all(not b.required for b in i.modal.inputs), "every box may be left empty"
        assert i.modal.title == "Ranks 1-5 of 10"

        for box, text in zip(i.modal.inputs, RESULTS[:5]):
            box._value = text
        first = env.inter(env.alice)
        await i.modal.on_submit(first)
        message = first.sent[-1]
        assert message.ephemeral and "Ranks 1-5 are good" in message.text and len(message.view.children) == 2
        assert storage.get_prediction("week-1", str(env.alice.id)) is None, "nothing may be saved after step 1"
        assert not env.picks.sent

        click = env.inter(env.alice)
        await message.view.children[0].callback(click)
        assert [b.label for b in click.modal.inputs] == ["Rank #6", "Rank #7", "Rank #8", "Rank #9", "Rank #10"]
        assert click.modal.title == "Ranks 6-10 of 10"

        for box, text in zip(click.modal.inputs, RESULTS[5:]):
            box._value = text
        last = env.inter(env.alice)
        await click.modal.on_submit(last)
        assert "Saved" in last.all_text and len(env.picks.sent) == 1
        assert storage.prediction_ranks(storage.get_prediction("week-1", str(env.alice.id))) == RESULTS
    run(scenario())


def test_boxes_may_be_empty_positions_are_kept_and_scoring_copes(env):
    async def scenario():
        await start_week(env)
        picks = ["One Piece", "", "Bleach", "", "", "", "Dandadan", "", "", "Vinland Saga"]
        i = await open_pick(env, env.alice)
        out = await env.submit_ranked(i, picks)
        assert "Saved" in out.all_text and "6 spot(s) left empty" in out.all_text
        assert storage.prediction_ranks(storage.get_prediction("week-1", str(env.alice.id))) == picks
        lines = [l for l in env.picks.sent[0][1].description.splitlines() if " :: " in l]
        assert lines[1] == "2 :: -" and lines[2] == "3 :: Bleach"

        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(RESULTS))
        # exact: One Piece, Bleach, Vinland Saga (2 each) + Dandadan on the chart but elsewhere (1)
        assert storage.score_points(storage.list_scores("week-1")[0]) == 7
    run(scenario())


def test_every_filled_box_must_be_valid_or_nothing_is_saved(env):
    async def scenario():
        await start_week(env)

        # a bad title in the first half is refused straight away, with no second step offered
        i = await open_pick(env, env.alice)
        out = await env.submit_ranked(i, ["One Piece", "Blech"] + [""] * 8)
        assert "rejected" in out.all_text and "Rank #2 ('Blech')" in out.all_text and "Did you mean 'Bleach'" in out.all_text
        assert out.sent[-1].view is None

        # a bad title in the second half is refused, and the first half is still there to retry
        i = await open_pick(env, env.alice)
        for box, text in zip(i.modal.inputs, RESULTS[:5]):
            box._value = text
        first = env.inter(env.alice)
        await i.modal.on_submit(first)
        view = first.sent[-1].view
        click = env.inter(env.alice)
        await view.children[0].callback(click)
        click.modal.inputs[0]._value = "Not A Real Show"
        bad = env.inter(env.alice)
        await click.modal.on_submit(bad)
        assert "rejected" in bad.all_text and "Rank #6 ('Not A Real Show')" in bad.all_text
        assert storage.get_prediction("week-1", str(env.alice.id)) is None

        again = env.inter(env.alice)
        await view.children[0].callback(again)               # same buttons still work
        for box, text in zip(again.modal.inputs, RESULTS[5:]):
            box._value = text
        ok = env.inter(env.alice)
        await again.modal.on_submit(ok)
        assert "Saved" in ok.all_text

        # the same title in both halves, and an all-empty form, are refused
        i = await open_pick(env, env.bob)
        out = await env.submit_ranked(i, ["One Piece", "Bleach", "", "", "", "Dandadan", "", "", "", "One Piece"])
        assert "Rank #10 ('One Piece') repeats rank #1" in out.all_text
        i = await open_pick(env, env.bob)
        out = await env.submit_ranked(i, [""] * 10)
        assert "Every box is empty" in out.all_text
        assert storage.get_prediction("week-1", str(env.bob.id)) is None
    run(scenario())


def test_save_keeping_6_to_10_leaves_them_alone_but_still_catches_clashes(env):
    async def scenario():
        await start_week(env)
        i = await open_pick(env, env.alice)
        await env.submit_ranked(i, RESULTS)

        i = await open_pick(env, env.alice)
        changed = ["Mob Psycho 100"] + RESULTS[1:5]
        out = await env.submit_ranked(i, changed, keep_second_half=True)
        assert "Saved" in out.all_text
        assert storage.prediction_ranks(storage.get_prediction("week-1", str(env.alice.id))) == changed + RESULTS[5:]

        i = await open_pick(env, env.alice)                   # moving a title that is also in 6-10 clashes
        out = await env.submit_ranked(i, ["Vinland Saga"] + changed[1:], keep_second_half=True)
        assert "rejected" in out.all_text and "repeats rank #1" in out.all_text
        assert storage.prediction_ranks(storage.get_prediction("week-1", str(env.alice.id)))[0] == "Mob Psycho 100"
    run(scenario())


def test_someone_elses_form_buttons_do_nothing(env):
    async def scenario():
        await start_week(env)
        i = await open_pick(env, env.alice)
        for box, text in zip(i.modal.inputs, RESULTS[:5]):
            box._value = text
        first = env.inter(env.alice)
        await i.modal.on_submit(first)
        stranger = env.inter(env.bob)
        assert await first.sent[-1].view.interaction_check(stranger) is False
        assert "belongs to someone else" in stranger.all_text
    run(scenario())


def test_last_picks_carry_over_to_new_weeks_and_seasons(env):
    async def scenario():
        await start_week(env)
        i = await open_pick(env, env.alice)
        await env.submit_ranked(i, RESULTS)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(RESULTS))
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")   # week-2

        i = await open_pick(env, env.alice)                    # alice has not picked in week-2 yet
        assert i.modal.title == "Ranks 1-5 (copied from week-1)"
        assert [b.default for b in i.modal.inputs] == RESULTS[:5]
        i2 = await open_pick(env, env.bob)                      # bob has never picked
        assert i2.modal.title == "Ranks 1-5 of 10" and [b.default for b in i2.modal.inputs] == [None] * 5

        # a title that left the season list is dropped from the pre-fill, the rest stays
        await env.season.remove_anime.callback(env.season, env.inter(env.admin), "Bleach")
        i = await open_pick(env, env.alice)
        assert [b.default for b in i.modal.inputs] == [RESULTS[0], RESULTS[1], None, RESULTS[3], RESULTS[4]]

        # this week's own pick wins over last week's
        await env.season.add_anime.callback(env.season, env.inter(env.admin), "Bleach")
        mine = ["Mob Psycho 100", "86"] + [""] * 8
        i = await open_pick(env, env.alice)
        await env.submit_ranked(i, mine)
        i = await open_pick(env, env.alice)
        assert [b.default for b in i.modal.inputs] == ["Mob Psycho 100", "86", None, None, None]
        assert "copied" not in i.modal.title

        # the memory outlives a /reset, so next season's first form is pre-filled too
        storage.reset_season()
        await env.start_season(TITLES)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        i = await open_pick(env, env.alice)
        assert i.modal.title == "Ranks 1-5 (copied from week-2)"
        assert [b.default for b in i.modal.inputs][:2] == ["Mob Psycho 100", "86"]
    run(scenario())


def test_results_use_the_same_form_and_keep_6_to_10_on_request(env):
    async def scenario():
        await start_week(env)
        i = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, i, "")
        assert [b.label for b in i.modal.inputs][:2] == ["Rank #1 (top of the chart)", "Rank #2"]
        assert i.modal.title == "Ranks 1-5 of 10"
        await env.submit_ranked(i, RESULTS)

        i = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, i, "")
        assert i.modal.title == "Ranks 1-5 (saved results)"
        swapped = [RESULTS[1], RESULTS[0]] + RESULTS[2:5]
        out = await env.submit_ranked(i, swapped, keep_second_half=True)
        assert "Results saved" in out.all_text
        assert storage.get_results("week-1") == swapped + RESULTS[5:]
    run(scenario())
