import asyncio

import storage
from conftest import RESULTS, TITLES


def run(coro):
    return asyncio.run(coro)


def final_embeds(env):
    return [e for _, e, _ in env.announce.sent if e is not None and (e.description or "").startswith("# Season complete")]


def backup_files(env):
    return [m for m in sorted(env.backups_channel.messages.values(), key=lambda m: m.id) if m.attachments]


def test_end_season_posts_the_full_final_standings_and_closes_the_season(env):
    async def scenario():
        await env.play_week()                                    # alice and bob tie on 20
        i = env.inter(env.admin)
        await env.weeks.end_season.callback(env.weeks, i)
        assert "season is over" in i.all_text and "A backup of the final state was saved" in i.all_text

        embed = final_embeds(env)[-1]
        assert "### Champions: alice, bob with 20 pts each" in embed.description
        assert "1 :: **alice** — 20 pts" in embed.description and "1 :: **bob** — 20 pts" in embed.description
        assert embed.colour.value == 0xE7CA4D
        assert embed.footer.text == "1 event(s) scored, 2 player(s)"
        assert storage.season_ended()

        milestone = [m for m in backup_files(env) if "season end" in m.content]
        assert len(milestone) == 1 and milestone[0].attachments[0].filename.endswith("-keep.db")

        j = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, j, "Standard", None, "")
        assert "season has ended" in j.all_text and j.modal is None

        j = env.inter(env.alice)
        await env.help.help_cmd.callback(env.help, j)
        assert "The season is over" in j.all_text
        j = env.inter(env.admin)
        await env.help.help_admin.callback(env.help, j)
        assert "[ok] Season ended" in j.all_text and "/end-season" in j.all_text

        # running it again simply re-posts; /reset clears the flag so a new season can begin
        before = len(final_embeds(env))
        await env.weeks.end_season.callback(env.weeks, env.inter(env.admin))
        assert len(final_embeds(env)) == before + 1
        task, k = await env.start_command(lambda k: env.season.reset.callback(env.season, k))
        await env.click(env.confirm_view(k), 0)
        await task
        assert not storage.season_ended()
        await env.start_season(TITLES)
        j = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, j, "Standard", None, "")
        assert "Week 1" in j.all_text
    run(scenario())


def test_a_single_champion_and_nothing_to_show_yet(env):
    async def scenario():
        await env.setup_channels()
        i = env.inter(env.admin)
        await env.weeks.end_season.callback(env.weeks, i)
        assert "Nothing has been scored" in i.all_text and not storage.season_ended()

        storage.create_week("week-1", "standard", "", 2, 1)
        storage.save_result("week-1", RESULTS)
        storage.replace_scores("week-1", [
            {"user_id": "1", "username": "alice", "points": 20, "breakdown": []},
            {"user_id": "2", "username": "bob", "points": 15, "breakdown": []},
            {"user_id": "3", "username": "carol", "points": 15, "breakdown": []},
        ])
        storage.set_week_locked("week-1", True)
        await env.weeks.end_season.callback(env.weeks, env.inter(env.admin))
        text = final_embeds(env)[-1].description
        assert "### Champion: alice with 20 pts" in text
        assert "2 :: **bob** — 15 pts" in text and "2 :: **carol** — 15 pts" in text, "ties share a rank"
    run(scenario())


def test_unscored_weeks_need_a_confirmation_and_open_ones_get_locked(env):
    async def scenario():
        await env.play_week()
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")     # week-2, never scored

        task, i = await env.start_command(lambda i: env.weeks.end_season.callback(env.weeks, i))
        assert "week-2" in i.sent[0].text and "won't count" in i.sent[0].text
        await env.click(env.confirm_view(i), 1)
        await task
        assert not storage.season_ended() and not final_embeds(env)

        task, i = await env.start_command(lambda i: env.weeks.end_season.callback(env.weeks, i))
        await env.click(env.confirm_view(i), 0)
        await task
        assert storage.season_ended() and final_embeds(env)
        assert storage.week_is_locked(storage.get_week("week-2")), "an open week must not stay open after the season ends"
        assert "season is over" in i.original_edits[-1]
    run(scenario())


def test_a_long_final_leaderboard_is_split_into_pages_that_fit_discord(env):
    async def scenario():
        await env.setup_channels()
        storage.create_week("week-1", "standard", "", 2, 1)
        storage.save_result("week-1", RESULTS)
        storage.replace_scores("week-1", [
            {"user_id": str(1000 + n), "username": f"player_with_a_fairly_long_name_{n}", "points": 200 - n, "breakdown": []}
            for n in range(150)
        ])
        storage.set_week_locked("week-1", True)
        await env.weeks.end_season.callback(env.weeks, env.inter(env.admin))
        pages = final_embeds(env)
        assert len(pages) >= 2 and all(len(p.description) <= 4096 for p in pages)
        assert pages[0].description.startswith("# Season complete: final standings (1/%d)" % len(pages))
        assert "150 :: **" in pages[-1].description and "— 51 pts" in pages[-1].description
        assert "player\\_with\\_a" in pages[-1].description, "names are escaped so underscores don't turn into italics"
    run(scenario())


def test_scoring_a_week_backs_it_up_and_a_backup_failure_does_not_undo_the_scoring(env):
    async def scenario():
        await env.play_week()
        assert any("week-1 scored" in m.content for m in backup_files(env))

        # next week, with the backup channel unwritable: results are still saved and posted, with a heads-up
        env.backups_channel.forbid_send = True
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        await env.pick(env.alice, RESULTS)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        out = await env.submit(j, "\n".join(RESULTS))
        assert "Results saved" in out.all_text and "backup after scoring didn't go through" in out.all_text
        assert storage.list_scores("week-2") and storage.get_results("week-2") == RESULTS
    run(scenario())
