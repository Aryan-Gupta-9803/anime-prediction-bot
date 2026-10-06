import asyncio
import json

import discord
import pytest
from discord import app_commands
from discord.ext import commands

import storage
from conftest import RESULTS, TITLES
from permissions import NotAdmin
from scoring import compute_score


def run(coro):
    return asyncio.run(coro)


# ---- storage layer --------------------------------------------------------------------------------

# ---- permissions --------------------------------------------------------------------------------

def test_admin_commands_reject_regular_members_and_accept_hosts(env):
    admin_only = [env.season.setup_channels, env.season.start, env.season.reset, env.weeks.new_event,
                  env.weeks.lock, env.weeks.end_week, env.help.help_admin,
                  env.weeks.set_points, env.backups.backup_now, env.backups.restore, env.backups.export,
                  env.weeks.end_season, env.season.season_rule]
    from fakes import FakeUser
    host_by_role = FakeUser("roleman", roles=["Event Host"])
    for cmd in admin_only:
        assert cmd.checks, f"{cmd.name} has no admin check"
        for check in cmd.checks:
            assert check(env.inter(env.admin)) is True
            assert check(env.inter(host_by_role)) is True
            with pytest.raises(NotAdmin):
                check(env.inter(env.alice))
    for cmd in (env.user_cog.pick, env.user_cog.leaderboard, env.user_cog.my_score, env.help.help_cmd):
        assert not cmd.checks


# ---- ranked flow ----------------------------------------------------------------------------------

def test_full_ranked_week_lifecycle(env):
    async def scenario():
        # setup + season
        i = await env.setup_channels()
        assert "announcements will post" in i.all_text.lower() and "missing" not in i.all_text.lower()
        out = await env.start_season(TITLES)
        assert "13" in out.all_text
        assert any("New season" in (e.description or "") for _, e, _ in env.announce.sent if e is not None)
        # a second /start is refused
        i = env.inter(env.admin)
        await env.season.start.callback(env.season, i)
        assert "already running" in i.all_text

        # open a week from the Standard template
        i = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, i, "Standard", None, "picks close Sunday")
        assert "week-1" in i.all_text and "2 pts exact / 1 pts" in i.all_text
        ann = env.announce.sent[-1][1]
        assert "Scoring :: 2 pts for an exact spot, 1 pt if it's on the chart elsewhere" in ann.description and "Note :: picks close Sunday" in ann.description
        assert ann.description.startswith("# week-1 predictions are open") and ann.colour.value == 0xE7CA4D
        assert storage.get_rules("week-1") == {p: (2, 1) for p in range(1, 11)}

        # opening another while open is refused
        i = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, i, "Standard", None, "")
        assert "still open" in i.all_text

        # alice: a typo disqualifies the whole submission and saves nothing
        bad = list(RESULTS)
        bad[1] = "Friren: Beyond Journeys End"
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        out = await env.submit(i, "\n".join(bad))
        assert "rejected" in out.all_text and "Did you mean 'Frieren: Beyond Journey's End'" in out.all_text
        assert storage.get_prediction("week-1", str(env.alice.id)) is None
        assert not env.picks.sent

        # alice submits properly, with list numbering and curly quotes (phone keyboard)
        good = [f"{n}. {t}" for n, t in enumerate(RESULTS, 1)]
        good[1] = "2. Frieren: Beyond Journey’s End"
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        out = await env.submit(i, "\n".join(good))
        assert "Saved" in out.all_text and len(env.picks.sent) == 1
        first_msg_id = env.picks.sent[0][2].id
        stored = storage.get_prediction("week-1", str(env.alice.id))
        assert stored["rank2"] == "Frieren: Beyond Journey's End"          # canonical spelling saved
        assert stored["message_id"] == str(first_msg_id)

        # resubmitting prefills the form and edits the same embed instead of posting again
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        assert [b.default for b in i.modal.inputs] == stored_titles(stored)[:5]
        swapped = list(RESULTS); swapped[0], swapped[1] = swapped[1], swapped[0]
        out = await env.submit(i, "\n".join(swapped))
        assert len(env.picks.sent) == 1, "should edit in place, not post a second embed"
        first_row = next(l for l in env.picks.messages[first_msg_id].embed.description.splitlines() if l.startswith("1 ::"))
        assert first_row.endswith("Frieren: Beyond Journey's End")

        # a deleted embed is re-posted rather than crashing
        del env.picks.messages[first_msg_id]
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        await env.submit(i, "\n".join(RESULTS))
        assert len(env.picks.sent) == 2

        # bob picks a numeric-looking title and a title that starts with digits + dot
        bob_pick = ["86", "2.43: Seiin High School Boys Volleyball Team"] + RESULTS[:8]
        i = env.inter(env.bob)
        await env.user_cog.pick.callback(env.user_cog, i)
        out = await env.submit(i, "\n".join(bob_pick))
        assert "Saved" in out.all_text, out.all_text
        assert storage.get_prediction("week-1", str(env.bob.id))["rank2"].startswith("2.43")

        # lock: late submissions are refused, including a form opened before the lock
        late = env.inter(env.carol)
        await env.user_cog.pick.callback(env.user_cog, late)
        i = env.inter(env.admin)
        await env.weeks.lock.callback(env.weeks, i)
        assert "2 submission" in i.all_text
        lock_post = env.announce.sent[-1][1].description
        assert lock_post.startswith("# week-1 is locked") and "Submissions :: 2" in lock_post
        out = await env.submit(late, "\n".join(RESULTS))
        assert "locked before you submitted" in out.all_text
        assert storage.get_prediction("week-1", str(env.carol.id)) is None
        i = env.inter(env.carol)
        await env.user_cog.pick.callback(env.user_cog, i)
        assert "locked" in i.all_text

        # end-week: score + post
        i = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, i, "")
        out = await env.submit(i, "\n".join(RESULTS))
        assert "2" in out.all_text and "scored" in out.all_text
        alice = next(s for s in storage.list_scores("week-1") if s["user_id"] == str(env.alice.id))
        expected, _ = compute_score(RESULTS, RESULTS, {p: (2, 1) for p in range(1, 11)})
        assert storage.score_points(alice) == expected == 20
        bob = next(s for s in storage.list_scores("week-1") if s["user_id"] == str(env.bob.id))
        exp_bob, _ = compute_score(bob_pick, RESULTS, {p: (2, 1) for p in range(1, 11)})
        assert storage.score_points(bob) == exp_bob
        posted = env.announce.sent[-1][1]
        assert posted.description.startswith("# Results are in") and "### Actual top 10" in posted.description
        assert posted.description.index("alice") < posted.description.index("bob")

        # users see their own breakdown, ephemeral
        i = env.inter(env.alice)
        await env.user_cog.my_score.callback(env.user_cog, i, "")
        assert i.last.ephemeral and "### 20 pts" in i.last.embed.description and "(exact)" in i.last.embed.description
        i = env.inter(env.carol)
        await env.user_cog.my_score.callback(env.user_cog, i, "")
        assert "didn't submit" in i.all_text
        i = env.inter(env.alice)
        await env.user_cog.leaderboard.callback(env.user_cog, i, "")
        assert i.last.ephemeral and "alice" in i.last.embed.description and "rank 1" in i.last.embed.footer.text

        # correcting results: form is prefilled and re-scores
        i = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, i, "")
        assert [b.default for b in i.modal.inputs] == RESULTS[:5]
        wrong = list(RESULTS); wrong[0], wrong[1] = wrong[1], wrong[0]
        await env.submit(i, "\n".join(wrong))
        alice2 = next(s for s in storage.list_scores("week-1") if s["user_id"] == str(env.alice.id))
        assert storage.score_points(alice2) == 18  # two exact spots swapped -> now wrong spot (1 each)
        assert len(storage.list_scores("week-1")) == 2, "re-scoring must replace, not append"

        # next week can start; the finished one needs no reminder
        i = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, i, "First Week Advantage", None, "")
        assert "week-2" in i.all_text and "5 pts exact / 2 pts" in i.all_text

    run(scenario())


def stored_titles(prediction):
    return [t for t in storage.prediction_ranks(prediction) if t]


def test_results_validation_and_missing_rules(env):
    async def scenario():
        await env.setup_channels()
        await env.start_season(TITLES)
        i = env.inter(env.admin)
        await env.weeks.new_event.callback(env.weeks, i, 3, 1, None, "", "Triple")
        assert "Template **Triple** saved" in i.all_text
        assert storage.get_template("triple")["exact_points"] == 3
        i = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, i, "")
        out = await env.submit(i, "\n".join(RESULTS[:9] + [RESULTS[0]]))
        assert "rejected" in out.all_text and "repeats rank #1" in out.all_text
        assert storage.get_results("week-1") is None
        i = env.inter(env.admin); await env.weeks.end_week.callback(env.weeks, i, "")
        out = await env.submit(i, "\n".join(RESULTS[:9] + ["Brand New Show"]))
        assert "rejected" in out.all_text and "Brand New Show" in out.all_text and "/add-anime" in out.all_text
        assert storage.get_results("week-1") is None, "a title that isn't on the list must not be accepted"
        i = env.inter(env.admin); await env.weeks.end_week.callback(env.weeks, i, "")
        out = await env.submit(i, "")
        assert "Every box is empty" in out.all_text
        i = env.inter(env.admin); await env.weeks.end_week.callback(env.weeks, i, "")
        out = await env.submit(i, "\n".join(RESULTS[:9]))
        assert "1 chart spot(s) were left empty" in out.all_text
        assert storage.get_results("week-1")[9] == "" and storage.get_results("week-1")[:9] == RESULTS[:9]
        assert "10 :: -" in env.announce.sent[-1][1].description
        # missing scoring rules are caught before anything is scored
        storage._db().execute("DELETE FROM point_rules WHERE position > 5")
        i = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, i, "")
        assert "incomplete" in i.all_text and "/set-points" in i.all_text and i.modal is None
    run(scenario())


def test_guards_before_setup_and_start(env):
    async def scenario():
        i = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, i, "Standard", None, "")
        assert "/setup" in i.all_text
        await env.setup_channels()
        i = env.inter(env.admin)
        await env.weeks.event_template.callback(env.weeks, i, "Standard", None, "")
        assert "/start" in i.all_text
        i = env.inter(env.admin)
        await env.season.start.callback(env.season, i)
        out = await env.submit(i, "\n".join(TITLES[:5]))
        assert "at least 10" in out.all_text and storage.get_anime_list() == []
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        assert "No event is open" in i.all_text
        i = env.inter(env.alice)
        await env.user_cog.anime_list.callback(env.user_cog, i)
        assert "No season" in i.all_text
    run(scenario())


def test_setup_warns_about_missing_permissions_and_unreachable_channels(env):
    async def scenario():
        from fakes import FakeChannel, FakePerms
        weak = env.client.add_channel(FakeChannel(env.guild, "weak", perms=FakePerms(embed_links=False, read_message_history=False)))
        i = env.inter(env.admin)
        await env.season.setup_channels.callback(env.season, i, env.announce, weak)
        assert "embed links" in i.all_text and "read message history" in i.all_text
        # picks channel vanishes: members get a clear message, nothing is half-saved
        await env.start_season(TITLES)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        env.client.channels.pop(weak.id)
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        out = await env.submit(i, "\n".join(RESULTS))
        assert "can't reach the picks channel" in out.all_text
        assert storage.get_prediction("week-1", str(env.alice.id)) is None
    run(scenario())


def test_forbidden_picks_channel_and_unlock(env):
    async def scenario():
        await env.setup_channels()
        await env.start_season(TITLES)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")
        env.picks.forbid_send = True
        i = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, i)
        out = await env.submit(i, "\n".join(RESULTS))
        assert "not allowed to post" in out.all_text
        assert storage.get_prediction("week-1", str(env.alice.id)) is None
        env.picks.forbid_send = False

        i = env.inter(env.admin); await env.weeks.lock.callback(env.weeks, i)
        i = env.inter(env.admin); await env.weeks.lock.callback(env.weeks, i)
        assert "already locked" in i.all_text
        i = env.inter(env.admin); await env.weeks.unlock.callback(env.weeks, i)
        assert "Reopened" in i.all_text
        i = env.inter(env.alice); await env.user_cog.pick.callback(env.user_cog, i)
        assert i.modal is not None
    run(scenario())


# ---- reset ---------------------------------------------------------------------------------------------

# ---- help --------------------------------------------------------------------------------------------------

def test_help_and_help_admin_reflect_state_and_fit_discord_limits(env):
    async def scenario():
        i = env.inter(env.alice)
        await env.help.help_cmd.callback(env.help, i)
        assert "No event is open" in i.all_text and i.last.ephemeral
        assert i.last.embed.footer.text is None, "regular members shouldn't be pointed at host commands"

        i = env.inter(env.admin)
        await env.help.help_admin.callback(env.help, i)
        text = i.all_text
        assert "[todo] Announcements channel" in text and "[todo] Anime list is empty" in text
        assert len(i.last.embed) < 6000

        await env.setup_channels()
        await env.start_season(TITLES)
        await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "deadline Sunday")
        i = env.inter(env.alice); await env.user_cog.pick.callback(env.user_cog, i)
        await env.submit(i, "\n".join(RESULTS))

        i = env.inter(env.admin); await env.help.help_admin.callback(env.help, i)
        text = i.all_text
        assert "[ok] Announcements" in text and "[ok] Picks" in text and "[ok] Anime list: 13 titles" in text
        assert "week-1 (standard) is open, 1 submission" in text
        assert len(i.last.embed) < 6000 and all(len(f.value) <= 1024 for f in i.last.embed.fields)
        for cmd in ("/setup", "/start", "/end-week", "/unlock", "/help-admin", "/reset"):
            assert cmd in text

        i = env.inter(env.alice); await env.help.help_cmd.callback(env.help, i)
        text = i.all_text
        assert "week-1" in text and "open" in text and "2" in text and "1 member(s)" in text and "deadline Sunday" in text
        assert len(i.last.embed) < 6000 and all(len(f.value) <= 1024 for f in i.last.embed.fields)

        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        i = env.inter(env.admin); await env.help.help_admin.callback(env.help, i)
        assert "[todo] week-1 is locked" in i.all_text
        i = env.inter(env.alice); await env.help.help_cmd.callback(env.help, i)
        assert "waiting for the host" in i.all_text

    run(scenario())


def test_anime_list_pages_and_is_ephemeral(env):
    async def scenario():
        await env.setup_channels()
        big = [f"Show number {n} with a reasonably long descriptive title" for n in range(150)]
        out = await env.start_season(big)
        assert "150" in out.all_text
        assert len(env.announce.sent) >= 2, "a long list should be split across embeds"
        assert all(len(e.description) <= 4096 for _, e, _ in env.announce.sent)
        i = env.inter(env.alice)
        await env.user_cog.anime_list.callback(env.user_cog, i)
        assert i.sent and all(s.ephemeral for s in i.sent)
        assert all(len(s.embed.description) <= 4096 for s in i.sent)
        i = env.inter(env.admin)
        await env.season.add_anime.callback(env.season, i, "  Extra Show ")
        assert "Added" in i.all_text
        i = env.inter(env.admin)
        await env.season.add_anime.callback(env.season, i, "extra   show")
        assert "already" in i.all_text
        i = env.inter(env.admin)
        await env.season.remove_anime.callback(env.season, i, "EXTRA SHOW")
        assert "Removed **Extra Show**" in i.all_text
        i = env.inter(env.admin)
        await env.season.remove_anime.callback(env.season, i, "nothing here")
        assert "isn't on the list" in i.all_text
    run(scenario())


# ---- command registration ------------------------------------------------------------------------------------

def test_every_command_is_valid_for_discord(env):
    async def scenario():
        import discord
        from bot import PredictionBot
        bot = PredictionBot()
        for ext in ["cogs.season", "cogs.weeks", "cogs.user", "cogs.help", "cogs.backups"]:
            await bot.load_extension(ext)
        names = sorted(c.name for c in bot.tree.get_commands())
        assert names == sorted([
            "setup", "start", "add-anime", "remove-anime", "reset", "event-template", "new-event", "season-rule",
            "list-templates", "lock", "unlock", "end-week", "pick", "my-score", "leaderboard", "anime-list",
            "help", "help-admin", "set-points", "backup", "restore", "export", "end-season"]), names
        for c in bot.tree.get_commands():
            payload = c.to_dict(bot.tree)
            assert len(payload["description"]) <= 100, c.name
        # wrong-server interactions are turned away before any command runs
        from fakes import FakeGuild, FakeInteraction
        other = FakeInteraction(env.client, FakeGuild(1), env.admin)
        assert await bot.tree.interaction_check(other) is False
        assert "only works inside its own server" in other.all_text
        mine = FakeInteraction(env.client, env.guild, env.admin)
        assert await bot.tree.interaction_check(mine) is True
        await commands.Bot.close(bot)
    run(scenario())


# ---- Discord hard limits (Discord answers 400 and the user sees nothing useful) -------------------------

def _walk_options(options, path):
    for o in options:
        assert len(o["name"]) <= 32, f"{path}/{o['name']}"
        assert len(o["description"]) <= 100, f"{path}/{o['name']}: {len(o['description'])} chars"
        for c in o.get("choices", []):
            assert len(str(c["name"])) <= 100
        _walk_options(o.get("options", []), f"{path}/{o['name']}")


def test_commands_and_modals_respect_discord_limits(env):
    import warnings
    warnings.simplefilter("ignore")
    from cogs import season, user, weeks

    async def scenario():
        from bot import PredictionBot
        bot = PredictionBot()
        for ext in ["cogs.season", "cogs.weeks", "cogs.user", "cogs.help", "cogs.backups"]:
            await bot.load_extension(ext)
        for c in bot.tree.get_commands():
            payload = c.to_dict(bot.tree)
            assert len(payload["name"]) <= 32 and len(payload["description"]) <= 100, c.name
            _walk_options(payload.get("options", []), c.name)
        assert len(bot.tree.get_commands()) <= 100
        await commands.Bot.close(bot)

        from forms import RankedForm, SlotModal
        big = "x" * 9000
        title100 = "T" * 100
        picks = RankedForm(noun="Rank", prefill=[title100] * 10, source_note="copied from week-12", reject_prefix="", finish=None)
        chart = RankedForm(noun="Rank", prefill=[title100] * 10, source_note="saved results", reject_prefix="", finish=None)
        modals = [season.StartModal(), SlotModal(picks, 1), SlotModal(picks, 2),
                  SlotModal(chart, 1), SlotModal(chart, 2)]
        for m in modals:
            assert len(m.title) <= 45, m.title
            boxes = getattr(m, "inputs", None) or [m.entries]
            assert len(boxes) <= 5, "Discord allows at most 5 boxes per form"
            for t in boxes:
                assert len(t.label) <= 45, f"{type(m).__name__} label is {len(t.label)} chars: {t.label!r}"
                assert len(t.placeholder or "") <= 100
                assert t.max_length <= 4000
                assert len(t.default or "") <= t.max_length, f"{type(m).__name__} prefill exceeds max_length"

    run(scenario())


def test_overlong_replies_are_clipped(env):
    async def scenario():
        from common import reply
        i = env.inter(env.admin)
        await reply(i, "y" * 5000)
        assert len(i.last.content) <= 2000
    run(scenario())
