"""The host's two-step results form (/end-week with no rank fields). Members use the rank fields on /pick."""
import asyncio

import storage
from conftest import RESULTS, TITLES


def run(coro):
    return asyncio.run(coro)


async def start_week(env):
    await env.setup_channels()
    await env.start_season(TITLES)
    await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")


async def open_results(env):
    i = env.inter(env.admin)
    await env.weeks.end_week.callback(env.weeks, i, "")
    return i


def test_the_results_form_is_ten_separate_boxes_over_two_steps_and_saves_only_at_the_end(env):
    async def scenario():
        await start_week(env)
        i = await open_results(env)
        assert [b.label for b in i.modal.inputs] == ["Rank #1 (top of the chart)", "Rank #2", "Rank #3", "Rank #4", "Rank #5"]
        assert all(not b.required for b in i.modal.inputs), "every box may be left empty"
        assert i.modal.title == "Ranks 1-5 of 10"

        for box, text in zip(i.modal.inputs, RESULTS[:5]):
            box._value = text
        first = env.inter(env.admin)
        await i.modal.on_submit(first)
        message = first.sent[-1]
        assert message.ephemeral and "Ranks 1-5 are good" in message.text and len(message.view.children) == 2
        assert storage.get_results("week-1") is None, "nothing may be saved after step 1"

        click = env.inter(env.admin)
        await message.view.children[0].callback(click)
        assert [b.label for b in click.modal.inputs] == ["Rank #6", "Rank #7", "Rank #8", "Rank #9", "Rank #10"]
        assert click.modal.title == "Ranks 6-10 of 10"
        for box, text in zip(click.modal.inputs, RESULTS[5:]):
            box._value = text
        last = env.inter(env.admin)
        await click.modal.on_submit(last)
        assert "Results saved" in last.all_text and storage.get_results("week-1") == RESULTS
    run(scenario())


def test_empty_boxes_are_allowed_positions_are_kept_and_scoring_copes(env):
    async def scenario():
        await start_week(env)
        picks = ["One Piece", "", "Bleach", "", "", "", "Dandadan", "", "", "Vinland Saga"]
        out = await env.pick(env.alice, picks)
        assert "Saved" in out.all_text
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))

        chart = ["One Piece", "", "Bleach", "Dandadan"] + [""] * 6          # the host leaves most spots empty
        j = await open_results(env)
        out = await env.submit_ranked(j, chart)
        assert "Results saved" in out.all_text and "7 chart spot(s) were left empty" in out.all_text
        assert storage.get_results("week-1") == chart
        # exact: One Piece, Bleach (2 each); Dandadan is on the chart but elsewhere (1)
        assert storage.score_points(storage.list_scores("week-1")[0]) == 5
    run(scenario())


def test_every_filled_box_must_be_valid_or_nothing_is_saved(env):
    async def scenario():
        await start_week(env)

        # a bad title in the first half is refused straight away, with no second step offered
        i = await open_results(env)
        out = await env.submit_ranked(i, ["One Piece", "Blech"] + [""] * 8)
        assert "rejected" in out.all_text and "Rank #2 ('Blech')" in out.all_text and "Did you mean 'Bleach'" in out.all_text
        assert out.sent[-1].view is None

        # a bad title in the second half is refused, and the first half is still there to retry
        i = await open_results(env)
        for box, text in zip(i.modal.inputs, RESULTS[:5]):
            box._value = text
        first = env.inter(env.admin)
        await i.modal.on_submit(first)
        view = first.sent[-1].view
        click = env.inter(env.admin)
        await view.children[0].callback(click)
        click.modal.inputs[0]._value = "Not A Real Show"
        bad = env.inter(env.admin)
        await click.modal.on_submit(bad)
        assert "rejected" in bad.all_text and "Rank #6 ('Not A Real Show')" in bad.all_text
        assert storage.get_results("week-1") is None

        again = env.inter(env.admin)
        await view.children[0].callback(again)               # same buttons still work
        for box, text in zip(again.modal.inputs, RESULTS[5:]):
            box._value = text
        ok = env.inter(env.admin)
        await again.modal.on_submit(ok)
        assert "Results saved" in ok.all_text and storage.get_results("week-1") == RESULTS

        # the same title in both halves, and an all-empty form, are refused
        i = await open_results(env)
        out = await env.submit_ranked(i, ["One Piece", "Bleach", "", "", "", "Dandadan", "", "", "", "One Piece"])
        assert "Rank #10 ('One Piece') repeats rank #1" in out.all_text
        i = await open_results(env)
        out = await env.submit_ranked(i, [""] * 10)
        assert "Every box is empty" in out.all_text
        assert storage.get_results("week-1") == RESULTS, "the earlier results are untouched"
    run(scenario())


def test_save_keeping_6_to_10_leaves_them_alone_but_still_catches_clashes(env):
    async def scenario():
        await start_week(env)
        i = await open_results(env)
        await env.submit_ranked(i, RESULTS)

        i = await open_results(env)
        assert i.modal.title == "Ranks 1-5 (saved results)"
        swapped = [RESULTS[1], RESULTS[0]] + RESULTS[2:5]
        out = await env.submit_ranked(i, swapped, keep_second_half=True)
        assert "Results saved" in out.all_text and storage.get_results("week-1") == swapped + RESULTS[5:]

        i = await open_results(env)                          # moving a title that is also in 6-10 clashes
        out = await env.submit_ranked(i, ["Vinland Saga"] + swapped[1:], keep_second_half=True)
        assert "rejected" in out.all_text and "repeats rank #1" in out.all_text
        assert storage.get_results("week-1") == swapped + RESULTS[5:]
    run(scenario())


def test_someone_elses_form_buttons_do_nothing(env):
    async def scenario():
        await start_week(env)
        i = await open_results(env)
        for box, text in zip(i.modal.inputs, RESULTS[:5]):
            box._value = text
        first = env.inter(env.admin)
        await i.modal.on_submit(first)
        stranger = env.inter(env.bob)
        assert await first.sent[-1].view.interaction_check(stranger) is False
        assert "belongs to someone else" in stranger.all_text
    run(scenario())
