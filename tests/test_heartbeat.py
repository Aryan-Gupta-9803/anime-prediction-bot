"""The bot's check-in DMs to its owner: when it starts, every 24 hours, and when it shuts down cleanly."""
import asyncio
import types

import pytest

import backup
import storage
from cogs import heartbeat as hb
from conftest import RESULTS, TITLES
from fakes import FakeUser


def run(coro):
    return asyncio.run(coro)


async def open_week(env):
    await env.setup_channels()
    await env.start_season(TITLES)
    await env.weeks.event_template.callback(env.weeks, env.inter(env.admin), "Standard", None, "")


def test_a_check_in_goes_to_the_bots_owner_with_the_state_of_things(env):
    async def scenario():
        await open_week(env)
        await env.pick(env.alice, RESULTS)
        await env.pick(env.bob, RESULTS)
        assert await env.heartbeat.check_in("daily") is True
        assert len(env.admin.dms) == 1 and not env.alice.dms
        text = env.admin.dms[0]
        assert text.startswith("**Daily check-in: the prediction bot is online.**")
        assert "Up since <t:" in text and "Week 1 (standard) is open with 2 submission(s)." in text
        assert "If a check-in doesn't arrive within a day, the bot or its host is down." in text
        assert len(text) < 2000
    run(scenario())


def test_start_up_daily_and_shut_down_messages_read_differently(env):
    async def scenario():
        await env.heartbeat.check_in("started")
        await env.heartbeat.check_in("daily")
        await env.heartbeat.check_in("shutdown")
        started, daily, shutdown = env.admin.dms
        assert started.startswith("**The prediction bot just started.**") and "No event is open right now." in started
        assert "doesn't arrive" not in started
        assert daily.startswith("**Daily check-in")
        assert shutdown.startswith("**The prediction bot is shutting down.**") and "check the host's console" in shutdown
    run(scenario())


def test_a_backup_problem_is_mentioned_and_backup_age_is_shown(env):
    async def scenario():
        await env.setup_channels()
        await env.start_season(TITLES)
        await backup.post_backup(env.client, "test")
        await env.heartbeat.check_in("daily")
        assert "Last backup: <t:" in env.admin.dms[-1]
        backup.state.last_error = "I can't see the backup channel"
        await env.heartbeat.check_in("daily")
        assert "The last backup attempt failed: I can't see the backup channel" in env.admin.dms[-1]
    run(scenario())


def test_closed_dms_are_reported_not_raised_and_clear_once_delivery_works(env):
    async def scenario():
        env.admin.forbid_dm = True
        assert await env.heartbeat.check_in("daily") is False
        assert "wouldn't let me DM you" in env.heartbeat.last_error
        i = env.inter(env.admin)
        await env.help.help_admin.callback(env.help, i)
        assert "[todo] Check-in DM: Discord wouldn't let me DM you" in i.all_text

        env.admin.forbid_dm = False
        assert await env.heartbeat.check_in("daily") is True and env.heartbeat.last_error is None
        i = env.inter(env.admin)
        await env.help.help_admin.callback(env.help, i)
        assert "[ok] Check-in DM to host: last sent <t:" in i.all_text and "then every 24 hours" in i.all_text
    run(scenario())


def test_before_any_check_in_help_admin_says_when_the_first_one_comes(env):
    async def scenario():
        i = env.inter(env.admin)
        await env.help.help_admin.callback(env.help, i)
        assert "[ok] Check-in DM: the first one goes out as soon as the bot is ready, then every 24 hours." in i.all_text
        assert len(i.last.embed) < 6000 and all(len(f.value) <= 1024 for f in i.last.embed.fields)
    run(scenario())


def test_recipient_is_the_application_owner_the_team_owner_or_the_configured_user(env, monkeypatch):
    async def scenario():
        # a bot owned by a team: the team's owner gets it
        leader = FakeUser("teamlead")
        env.client.team = types.SimpleNamespace(owner=leader)
        await env.heartbeat.check_in("daily")
        assert leader.dms and not env.admin.dms
        env.client.team = None

        # ALERT_USER_ID wins over the owner
        friend = FakeUser("friend")
        env.client.users[friend.id] = friend
        monkeypatch.setattr(hb, "ALERT_USER_ID", friend.id)
        fresh = hb.Heartbeat(env.client)
        await fresh.check_in("daily")
        assert friend.dms and not env.admin.dms

        # an ID Discord doesn't know is a reported failure, not a crash
        monkeypatch.setattr(hb, "ALERT_USER_ID", 1)
        broken = hb.Heartbeat(env.client)
        assert await broken.check_in("daily") is False and "NotFound" in broken.last_error
    run(scenario())


def test_the_schedule_starts_now_then_waits_24_hours_and_stops_with_the_cog(env):
    async def scenario():
        cog = hb.Heartbeat(env.client)
        assert hb.Heartbeat.daily.hours == 24
        await cog.cog_load()                              # what loading the extension does
        for _ in range(100):
            if env.admin.dms:
                break
            await asyncio.sleep(0.01)
        assert len(env.admin.dms) == 1 and env.admin.dms[0].startswith("**The prediction bot just started.**")
        await asyncio.sleep(0.05)
        assert len(env.admin.dms) == 1, "the next one is a day away"
        gap = (cog.daily.next_iteration - __import__("datetime").datetime.now(__import__("datetime").timezone.utc)).total_seconds()
        assert 24 * 3600 - 60 < gap <= 24 * 3600
        assert cog._first is False, "later runs are the daily ones"
        cog.cog_unload()
        await asyncio.sleep(0.02)
        assert not cog.daily.is_running()
    run(scenario())


def test_the_switch_turns_everything_off(env, monkeypatch):
    async def scenario():
        monkeypatch.setattr(hb, "HEARTBEAT_DM", False)
        cog = hb.Heartbeat(env.client)
        await cog.cog_load()
        assert not cog.daily.is_running()
        await cog.notify_shutdown()
        assert not env.admin.dms
        assert cog.status_line() == "[ok] Check-in DMs: switched off (HEARTBEAT_DM=off)."
    run(scenario())


def test_shutdown_sends_a_goodbye_but_never_blocks_or_breaks_the_stop(env, monkeypatch):
    async def scenario():
        await env.heartbeat.notify_shutdown()
        assert env.admin.dms[-1].startswith("**The prediction bot is shutting down.**")

        async def slow(self, kind):
            await asyncio.sleep(60)
        monkeypatch.setattr(hb, "SHUTDOWN_TIMEOUT", 0.05)
        monkeypatch.setattr(hb.Heartbeat, "check_in", slow)
        await asyncio.wait_for(env.heartbeat.notify_shutdown(), 2)         # gives up quietly

        async def boom(self, kind):
            raise RuntimeError("discord is down")
        monkeypatch.setattr(hb.Heartbeat, "check_in", boom)
        await env.heartbeat.notify_shutdown()                                # swallowed
    run(scenario())


def test_the_real_bot_loads_the_cog_and_says_goodbye_through_it_even_if_that_fails(env):
    async def scenario():
        import bot as bot_module
        from discord.ext import commands
        assert "cogs.heartbeat" in bot_module.EXTENSIONS
        b = bot_module.PredictionBot()

        async def never_ready():                          # this bot never logs in, so the daily loop just waits
            await asyncio.Event().wait()
        b.wait_until_ready = never_ready
        await b.load_extension("cogs.heartbeat")
        cog = b.get_cog("Heartbeat")
        assert cog is not None and cog.daily.is_running(), "the schedule starts when the extension loads"

        said = []

        async def fake_shutdown():
            said.append("bye")
        cog.notify_shutdown = fake_shutdown
        await b._say_goodbye()
        assert said == ["bye"]

        tried = []

        async def broken():
            tried.append(1)
            raise RuntimeError("discord is down")
        b._say_goodbye = broken
        await b.close()                                   # the stop still goes through, backup and all
        assert tried == [1], "closing the bot sends the goodbye"
        assert not cog.daily.is_running()
    run(scenario())
