"""Restarting after the Ups and Downs update: a backup made by the OLD version must load, and carry on working.

tests/fixtures/backup-before-updown.db is a real backup file made by the previous release (HEAD bdbf9e5): a season
with Week 1 scored, Week 2 open with one pick, and the Minority Multiplier set. It has no pick_extras table.
"""
import asyncio
import os
import shutil
import sqlite3

import pytest

import backup
import scoring
import storage
from conftest import RESULTS
from fakes import FakeAttachment
from test_updown import CHART2, UPDOWN, open_updown, score_week_one, ud

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "backup-before-updown.db")
OLD_NAME = "prediction-backup-20261016-120000.db"


def run(coro):
    return asyncio.run(coro)


def old_backup() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


def test_the_fixture_really_is_an_old_format_backup():
    path = FIXTURE
    tables = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "predictions" in tables and "pick_extras" not in tables, "the fixture must predate the new table"


def test_old_backups_stay_valid_because_the_new_table_is_not_required(env):
    assert "pick_extras" not in storage.EXPECTED_TABLES, "requiring it would reject every backup made before this update"
    info = storage.inspect_snapshot(old_backup())
    assert (info["weeks"], info["submissions"], info["titles"], info["scored_rows"]) == (2, 3, 13, 2)


def test_a_wiped_host_restores_the_old_backup_from_the_channel_and_keeps_going(env, tmp_path):
    async def scenario():
        env.backups_channel.plant(OLD_NAME, old_backup())
        path = str(tmp_path / "fresh-disk" / "prediction.db")                 # the server comes up with nothing
        assert await backup.prepare_storage(env.client, path) == "restored"

        # everything from before is there
        assert [w["week_id"] for w in storage.list_weeks()] == ["week-1", "week-2"]
        assert storage.get_season_rule() == "minority-multiplier"
        assert len(storage.get_anime_list()) == 13 and len(storage.list_scores("week-1")) == 2
        assert storage.get_results("week-1") == RESULTS
        week2 = storage.get_week("week-2")
        assert week2["type"] == "standard" and not storage.week_is_locked(week2)
        alice = next(p for p in storage.list_predictions("week-2"))
        assert storage.prediction_ranks(alice) == RESULTS[::-1]
        assert len(storage.list_templates()) == 2
        # ...and the new table exists, empty
        assert storage.list_pick_extras("week-2") == {} and ud("week-2", alice["user_id"]) is None
        assert await backup.prepare_storage(env.client, path) == "existing", "the next restart must not restore again"

        # carry on: finish week 2, then run the new Ups and Downs event as week 3
        await env.setup_channels()                    # (the fixture's channel ids belong to its own fake server)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        assert "Results saved" in (await env.submit(j, "\n".join(CHART2))).all_text
        assert storage.get_results("week-2") == CHART2

        i = await open_updown(env)
        assert "Started **Week 3** (Ups and Downs)" in i.all_text, i.all_text
        await env.pick(env.alice, CHART2[::-1], ups=2, downs=5)
        await env.pick(env.bob, CHART2, ups=4, downs=4)
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        week3 = RESULTS
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(week3))
        movement = scoring.chart_movement(CHART2, week3)                      # compared with WEEK 2, the restored one
        scores = {s["username"]: s for s in storage.list_scores("week-3")}
        assert set(scores) == {"alice", "bob"}
        rules = {p: (2, 1) for p in range(1, 11)}
        # the season rule survived the restore, so the ranked part still uses the Minority Multiplier (too few players: 1x)
        expected_bob = scoring.compute_score(CHART2, week3, rules)[0] + scoring.compute_updown_score((4, 4), movement)[0]
        assert storage.score_points(scores["bob"]) == expected_bob
    run(scenario())


def test_an_old_database_file_opened_directly_gets_the_new_table_and_keeps_its_data(tmp_path):
    path = str(tmp_path / "prediction.db")
    shutil.copy(FIXTURE, path)                                      # a host that still has its disk and just updated
    assert storage.open_db(path) is False, "it's an existing database, not a fresh install"
    assert [w["week_id"] for w in storage.list_weeks()] == ["week-1", "week-2"]
    assert storage.list_pick_extras("week-2") == {}
    storage.upsert_prediction("week-2", "42", "zed", RESULTS, 1, extras={"ups": 3, "downs": 4})
    assert ud("week-2", "42") == (3, 4)
    assert storage.get_revision() >= 1 and storage.get_season_rule() == "minority-multiplier"


def test_restore_command_accepts_an_old_backup_file_and_leaves_the_new_table_ready(env):
    async def scenario():
        await env.setup_channels()
        task, i = await env.start_command(
            lambda i: env.backups.restore.callback(env.backups, i, FakeAttachment(OLD_NAME, old_backup()))
        )
        if not task.done():
            await env.click(env.confirm_view(i), 0)
        await task
        assert "Restored" in i.original_edits[-1], i.original_edits
        assert [w["week_id"] for w in storage.list_weeks()] == ["week-1", "week-2"]
        storage.upsert_prediction("week-2", "42", "zed", RESULTS, 1, extras={"ups": 1, "downs": 2})
        assert storage.list_pick_extras("week-2") == {"42": {"ups": "1", "downs": "2"}}
    run(scenario())


def test_a_new_backup_with_ups_and_downs_restores_with_them_intact(env, tmp_path):
    async def scenario():
        await score_week_one(env)
        await open_updown(env)
        await env.pick(env.alice, CHART2, ups=3, downs=4)
        await backup.post_backup(env.client, "test", keep=True)              # the newest backup holds the Ups and Downs pick
        before = (ud("week-2", str(env.alice.id)), storage.data_summary())

        path = str(tmp_path / "fresh-disk" / "prediction.db")
        assert await backup.prepare_storage(env.client, path) == "restored"
        assert (ud("week-2", str(env.alice.id)), storage.data_summary()) == before
        assert storage.get_week("week-2")["type"] == "updown"
        # and the restored week still scores against Week 1's chart
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        j = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, j, "")
        await env.submit(j, "\n".join(CHART2))
        rows = storage.list_scores("week-2")
        assert len(rows) == 1 and "off by 1" in rows[0]["breakdown_json"]       # ups 3 vs actual 3 = exact; downs 4 vs 3
    run(scenario())
