"""SQLite storage for the prediction bot.

Everything lives in one file (DATA_DIR/prediction.db). Backups are consistent snapshots of that
file (SQLite's online backup API), which the bot posts to a private Discord channel; restoring
loads a snapshot back into the live database in one atomic step.

Discord IDs are stored as TEXT everywhere so they can never be rounded.
All functions are synchronous and serialised by one lock; async callers use `aio`, which runs
each call in a worker thread.
"""
import asyncio
import contextlib
import functools
import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from textutil import normalize

SCHEMA_VERSION = 1
RANK_COLS = [f"rank{i}" for i in range(1, 11)]

# Taken from the community's actual Summer 2026 sheet: most weeks score a flat 2 pts exact /
# 1 pt wrong-spot; the season opener used a boosted 5/2.
DEFAULT_TEMPLATES = [
    ("Standard", 2, 1, "Regular week: 2 pts for an exact position match, 1 pt if it charted but in the wrong spot."),
    ("First Week Advantage", 5, 2, "Season-opener bonus: 5 pts exact, 2 pts wrong-spot."),
]

_RANK_DDL = ", ".join(f"{c} TEXT NOT NULL DEFAULT ''" for c in RANK_COLS)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS anime (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, title_norm TEXT NOT NULL UNIQUE, added_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS weeks (
    week_id TEXT PRIMARY KEY, type TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS point_rules (
    week_id TEXT NOT NULL, position INTEGER NOT NULL, exact_points INTEGER NOT NULL,
    partial_points INTEGER NOT NULL, PRIMARY KEY (week_id, position));
CREATE TABLE IF NOT EXISTS templates (
    name_norm TEXT PRIMARY KEY, name TEXT NOT NULL, exact_points INTEGER NOT NULL,
    partial_points INTEGER NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS predictions (
    week_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT NOT NULL, {_RANK_DDL},
    message_id TEXT NOT NULL DEFAULT '', submitted_at TEXT NOT NULL, PRIMARY KEY (week_id, user_id));
CREATE TABLE IF NOT EXISTS last_picks (
    user_id TEXT PRIMARY KEY, week_id TEXT NOT NULL, {_RANK_DDL}, saved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS results (
    week_id TEXT PRIMARY KEY, {_RANK_DDL}, entered_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scores (
    week_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT NOT NULL, points INTEGER NOT NULL,
    breakdown_json TEXT NOT NULL, computed_at TEXT NOT NULL, PRIMARY KEY (week_id, user_id));
CREATE TABLE IF NOT EXISTS ballot_categories (
    week_id TEXT NOT NULL, ord INTEGER NOT NULL, category TEXT NOT NULL, points INTEGER NOT NULL,
    correct_answer TEXT NOT NULL DEFAULT '', PRIMARY KEY (week_id, ord));
CREATE TABLE IF NOT EXISTS ballot_picks (
    week_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT NOT NULL, category TEXT NOT NULL,
    guess TEXT NOT NULL, PRIMARY KEY (week_id, user_id, category));
CREATE TABLE IF NOT EXISTS ballot_submissions (
    week_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT NOT NULL, message_id TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL, PRIMARY KEY (week_id, user_id));
"""

EXPECTED_TABLES = {
    "meta", "config", "anime", "weeks", "point_rules", "templates", "predictions", "results", "scores",
    "ballot_categories", "ballot_picks", "ballot_submissions",
}
# Tables wiped by /reset. Config (channels) and templates survive.
SEASON_TABLES = [
    "anime", "weeks", "point_rules", "predictions", "results", "scores",
    "ballot_categories", "ballot_picks", "ballot_submissions",
]
# What /export writes, in sheet order.
EXPORT_TABLES = [
    "weeks", "predictions", "results", "scores", "anime", "point_rules", "templates",
    "ballot_categories", "ballot_picks", "ballot_submissions",
]

_lock = threading.RLock()
_conn: "sqlite3.Connection | None" = None
_path: "str | None" = None


class InvalidBackup(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- connection ------------------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("Database isn't open yet; call storage.open_db() first.")
    return _conn


def _connect(path: str) -> sqlite3.Connection:
    # Autocommit mode: transactions are explicit (see _tx), so nothing is left half-open.
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_SQL)
    for key, value in (("schema_version", str(SCHEMA_VERSION)), ("revision", "0"), ("backed_up_revision", "0"),
                       ("created_at", now_iso())):
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (key, value))


def open_db(path: "str | None" = None) -> bool:
    """Opens (creating if needed) the database. Returns True when it is brand new, which is the
    moment to look for a backup to restore. A damaged file is moved aside and counts as new."""
    global _conn, _path
    path = path or os.path.join(os.environ.get("DATA_DIR", "data"), "prediction.db")
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fresh = not os.path.exists(path)
        try:
            conn = _connect(path)
            if not fresh and conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("quick_check failed")
            _init_schema(conn)
        except sqlite3.DatabaseError:
            with contextlib.suppress(Exception):
                conn.close()
            os.replace(path, f"{path}.corrupt-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}")
            fresh = True
            conn = _connect(path)
            _init_schema(conn)
        _conn, _path = conn, path
        return fresh


def close_db():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def discard_db():
    """Closes and deletes the database file. Used when startup must abort before the database
    holds anything, so the next start still counts as a fresh install."""
    with _lock:
        path = _path
        close_db()
        for suffix in ("", "-journal", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                os.remove((path or "") + suffix)


def _q(sql: str, params=()) -> list:
    with _lock:
        return [dict(r) for r in _db().execute(sql, params).fetchall()]


def _one(sql: str, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else None


@contextlib.contextmanager
def _tx():
    """One atomic write. Bumps the revision counter so the backup loop knows something changed."""
    with _lock:
        conn = _db()
        conn.execute("BEGIN IMMEDIATE")
        changes_before = conn.total_changes
        try:
            yield conn
            if conn.total_changes != changes_before:
                conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def _set_config(conn, key: str, value):
    conn.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# ---- Config --------------------------------------------------------------------------------------------

def get_config(key: str, default=None):
    row = _one("SELECT value FROM config WHERE key = ?", (key,))
    return row["value"] if row else default


def set_config(key: str, value):
    with _tx() as conn:
        _set_config(conn, key, value)


# ---- Anime list ----------------------------------------------------------------------------------------

def get_anime_list() -> list:
    return [r["title"] for r in _q("SELECT title FROM anime ORDER BY id")]


def get_anime_set() -> set:
    return {normalize(t) for t in get_anime_list()}


def add_anime(title: str) -> bool:
    """Returns False if the title is already on the list."""
    with _tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO anime(title, title_norm, added_at) VALUES (?, ?, ?)",
            (title.strip(), normalize(title), now_iso()),
        )
        return cur.rowcount == 1


def remove_anime(title: str) -> "str | None":
    """Returns the removed title as stored, or None if it wasn't on the list."""
    with _tx() as conn:
        row = conn.execute("SELECT id, title FROM anime WHERE title_norm = ?", (normalize(title),)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM anime WHERE id = ?", (row["id"],))
        return row["title"]


def replace_anime_list(titles: list) -> int:
    seen, rows, stamp = set(), [], now_iso()
    for title in titles:
        n = normalize(title)
        if n and n not in seen:
            seen.add(n)
            rows.append((title.strip(), n, stamp))
    with _tx() as conn:
        conn.execute("DELETE FROM anime")
        conn.executemany("INSERT INTO anime(title, title_norm, added_at) VALUES (?, ?, ?)", rows)
    return len(rows)


# ---- Weeks / events -------------------------------------------------------------------------------------

def week_is_locked(week: dict) -> bool:
    return str(week.get("locked", "")).strip().upper() in ("1", "TRUE")


def get_week(week_id: str):
    return _one("SELECT * FROM weeks WHERE week_id = ?", (str(week_id),))


def list_weeks() -> list:
    return _q("SELECT * FROM weeks ORDER BY rowid")


def get_current_week_id():
    return get_config("current_week_id") or None


def get_current_week():
    week_id = get_current_week_id()
    return get_week(week_id) if week_id else None


def next_week_id() -> str:
    existing = {w["week_id"] for w in list_weeks()}
    n = 1
    while f"week-{n}" in existing:
        n += 1
    return f"week-{n}"


def create_week(week_id: str, week_type: str, note: str, exact_points: int, partial_points: int):
    """Point values are flat across all 10 positions, matching how every week in the
    reference sheet scores."""
    with _tx() as conn:
        conn.execute(
            "INSERT INTO weeks(week_id, type, note, locked, created_at) VALUES (?, ?, ?, 0, ?)",
            (week_id, week_type, note, now_iso()),
        )
        conn.executemany(
            "INSERT INTO point_rules(week_id, position, exact_points, partial_points) VALUES (?, ?, ?, ?)",
            [(week_id, pos, exact_points, partial_points) for pos in range(1, 11)],
        )
        _set_config(conn, "current_week_id", week_id)


def set_week_locked(week_id: str, locked: bool):
    with _tx() as conn:
        cur = conn.execute("UPDATE weeks SET locked = ? WHERE week_id = ?", (1 if locked else 0, week_id))
        if cur.rowcount == 0:
            raise ValueError(f"Unknown week {week_id}")


def get_rules(week_id: str) -> dict:
    return {
        r["position"]: (r["exact_points"], r["partial_points"])
        for r in _q("SELECT position, exact_points, partial_points FROM point_rules WHERE week_id = ?", (week_id,))
    }


def rules_complete(rules: dict) -> bool:
    return all(pos in rules for pos in range(1, 11))


def set_week_points(week_id: str, exact_points: int, partial_points: int):
    """Replaces the scoring rules of a ranked week (re-run /end-week afterwards to re-score)."""
    with _tx() as conn:
        if conn.execute("SELECT 1 FROM weeks WHERE week_id = ?", (week_id,)).fetchone() is None:
            raise ValueError(f"Unknown week {week_id}")
        conn.execute("DELETE FROM point_rules WHERE week_id = ?", (week_id,))
        conn.executemany(
            "INSERT INTO point_rules(week_id, position, exact_points, partial_points) VALUES (?, ?, ?, ?)",
            [(week_id, pos, exact_points, partial_points) for pos in range(1, 11)],
        )


# ---- Templates ------------------------------------------------------------------------------------------

def list_templates() -> list:
    return _q("SELECT name, exact_points, partial_points, description, created_at FROM templates ORDER BY rowid")


def get_template(name: str):
    return _one(
        "SELECT name, exact_points, partial_points, description, created_at FROM templates WHERE name_norm = ?",
        (normalize(name),),
    )


def save_template(name: str, exact_points: int, partial_points: int, description: str = "") -> bool:
    """Creates or overwrites a template. Returns True if one with that name was replaced."""
    with _tx() as conn:
        replaced = conn.execute("SELECT 1 FROM templates WHERE name_norm = ?", (normalize(name),)).fetchone() is not None
        conn.execute(
            "INSERT INTO templates(name_norm, name, exact_points, partial_points, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name_norm) DO UPDATE SET name = excluded.name, "
            "exact_points = excluded.exact_points, partial_points = excluded.partial_points, "
            "description = excluded.description",
            (normalize(name), name, exact_points, partial_points, description, now_iso()),
        )
        return replaced


def seed_default_templates():
    """Adds the built-in templates once. Deleting them later won't bring them back."""
    if get_config("templates_seeded") == "TRUE":
        return
    if not list_templates():
        for name, exact_points, partial_points, description in DEFAULT_TEMPLATES:
            save_template(name, exact_points, partial_points, description)
    set_config("templates_seeded", "TRUE")


# ---- Ranked predictions -----------------------------------------------------------------------------------

def get_prediction(week_id: str, user_id: str):
    return _one("SELECT * FROM predictions WHERE week_id = ? AND user_id = ?", (week_id, str(user_id)))


def upsert_prediction(week_id: str, user_id: str, username: str, ranks: list, message_id):
    ranks = (list(ranks) + [""] * 10)[:10]
    columns = ", ".join(RANK_COLS)
    marks = ", ".join("?" for _ in RANK_COLS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in RANK_COLS)
    stamp = now_iso()
    with _tx() as conn:
        conn.execute(
            f"INSERT INTO predictions(week_id, user_id, username, {columns}, message_id, submitted_at) "
            f"VALUES (?, ?, ?, {marks}, ?, ?) ON CONFLICT(week_id, user_id) DO UPDATE SET "
            f"username = excluded.username, {updates}, message_id = excluded.message_id, "
            "submitted_at = excluded.submitted_at",
            (week_id, str(user_id), username, *ranks, str(message_id or ""), stamp),
        )
        conn.execute(
            f"INSERT INTO last_picks(user_id, week_id, {columns}, saved_at) VALUES (?, ?, {marks}, ?) "
            f"ON CONFLICT(user_id) DO UPDATE SET week_id = excluded.week_id, {updates}, saved_at = excluded.saved_at",
            (str(user_id), week_id, *ranks, stamp),
        )


def get_last_picks(user_id: str):
    """The member's most recent ranked picks from any week or season, for pre-filling the form."""
    return _one("SELECT * FROM last_picks WHERE user_id = ?", (str(user_id),))


def list_predictions(week_id: str) -> list:
    return _q("SELECT * FROM predictions WHERE week_id = ? ORDER BY rowid", (week_id,))


def prediction_ranks(prediction: dict) -> list:
    return [prediction.get(c, "") for c in RANK_COLS]


# ---- Ranked results -------------------------------------------------------------------------------------------

def save_result(week_id: str, ranks: list):
    ranks = (list(ranks) + [""] * 10)[:10]
    columns = ", ".join(RANK_COLS)
    marks = ", ".join("?" for _ in RANK_COLS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in RANK_COLS)
    with _tx() as conn:
        conn.execute(
            f"INSERT INTO results(week_id, {columns}, entered_at) VALUES (?, {marks}, ?) "
            f"ON CONFLICT(week_id) DO UPDATE SET {updates}, entered_at = excluded.entered_at",
            (week_id, *ranks, now_iso()),
        )


def get_results(week_id: str):
    row = _one("SELECT * FROM results WHERE week_id = ?", (week_id,))
    return [row[c] for c in RANK_COLS] if row else None


# ---- Scores (shared by ranked weeks and ballots) -----------------------------------------------------------------

def replace_scores(week_id: str, entries: list):
    """entries: [{user_id, username, points, breakdown}, ...]. Replaces the week's scores."""
    stamp = now_iso()
    with _tx() as conn:
        conn.execute("DELETE FROM scores WHERE week_id = ?", (week_id,))
        conn.executemany(
            "INSERT INTO scores(week_id, user_id, username, points, breakdown_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(week_id, str(e["user_id"]), e["username"], int(e["points"]), json.dumps(e["breakdown"]), stamp)
             for e in entries],
        )


def list_scores(week_id: "str | None" = None) -> list:
    if week_id is None:
        return _q("SELECT * FROM scores ORDER BY rowid")
    return _q("SELECT * FROM scores WHERE week_id = ? ORDER BY rowid", (week_id,))


def score_points(record: dict) -> int:
    try:
        return int(float(record.get("points", 0)))
    except (TypeError, ValueError):
        return 0


# ---- Awards-ballot events ------------------------------------------------------------------------------------------

def create_ballot_event(week_id: str, note: str, categories: list):
    """categories: [(name, points), ...] in display order."""
    with _tx() as conn:
        conn.execute(
            "INSERT INTO weeks(week_id, type, note, locked, created_at) VALUES (?, 'ballot', ?, 0, ?)",
            (week_id, note, now_iso()),
        )
        conn.executemany(
            "INSERT INTO ballot_categories(week_id, ord, category, points, correct_answer) VALUES (?, ?, ?, ?, '')",
            [(week_id, i, name, points) for i, (name, points) in enumerate(categories, 1)],
        )
        _set_config(conn, "current_week_id", week_id)


def get_ballot_categories(week_id: str) -> list:
    return _q("SELECT * FROM ballot_categories WHERE week_id = ? ORDER BY ord", (week_id,))


def set_ballot_correct_answers(week_id: str, answers: list):
    """answers: [(category, answer), ...]. Returns (applied_count, unknown_categories)."""
    with _tx() as conn:
        by_name = {
            normalize(r["category"]): r["ord"]
            for r in conn.execute("SELECT ord, category FROM ballot_categories WHERE week_id = ?", (week_id,))
        }
        unknown, applied = [], 0
        for category, answer in answers:
            ord_ = by_name.get(normalize(category))
            if ord_ is None:
                unknown.append(category)
                continue
            conn.execute(
                "UPDATE ballot_categories SET correct_answer = ? WHERE week_id = ? AND ord = ?",
                (answer, week_id, ord_),
            )
            applied += 1
        return applied, unknown


def set_ballot_picks(week_id: str, user_id: str, username: str, picks: dict):
    """picks: {category: guess}. Replaces this user's previous picks for the event."""
    with _tx() as conn:
        conn.execute("DELETE FROM ballot_picks WHERE week_id = ? AND user_id = ?", (week_id, str(user_id)))
        conn.executemany(
            "INSERT INTO ballot_picks(week_id, user_id, username, category, guess) VALUES (?, ?, ?, ?, ?)",
            [(week_id, str(user_id), username, cat, guess) for cat, guess in picks.items() if guess],
        )


def get_ballot_picks(week_id: str, user_id: "str | None" = None) -> list:
    if user_id is None:
        return _q("SELECT * FROM ballot_picks WHERE week_id = ? ORDER BY rowid", (week_id,))
    return _q("SELECT * FROM ballot_picks WHERE week_id = ? AND user_id = ? ORDER BY rowid", (week_id, str(user_id)))


def get_ballot_submission(week_id: str, user_id: str):
    return _one("SELECT * FROM ballot_submissions WHERE week_id = ? AND user_id = ?", (week_id, str(user_id)))


def upsert_ballot_submission(week_id: str, user_id: str, username: str, message_id):
    with _tx() as conn:
        conn.execute(
            "INSERT INTO ballot_submissions(week_id, user_id, username, message_id, submitted_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(week_id, user_id) DO UPDATE SET username = excluded.username, "
            "message_id = excluded.message_id, submitted_at = excluded.submitted_at",
            (week_id, str(user_id), username, str(message_id or ""), now_iso()),
        )


def count_submissions(week: dict) -> int:
    table = "ballot_submissions" if week["type"] == "ballot" else "predictions"
    return _one(f"SELECT COUNT(*) AS n FROM {table} WHERE week_id = ?", (week["week_id"],))["n"]


def week_has_results(week: dict) -> bool:
    if week["type"] == "ballot":
        return any(c["correct_answer"].strip() for c in get_ballot_categories(week["week_id"]))
    return get_results(week["week_id"]) is not None


# ---- Season reset ---------------------------------------------------------------------------------------------------

def season_ended() -> bool:
    return get_config("season_ended") == "TRUE"


def mark_season_ended():
    set_config("season_ended", "TRUE")


def reset_season():
    """Empties the season tables. Callers must have saved a backup first. Channels (config),
    templates and each member's remembered last picks are kept."""
    with _tx() as conn:
        for table in SEASON_TABLES:
            conn.execute(f"DELETE FROM {table}")
        _set_config(conn, "current_week_id", "")
        _set_config(conn, "season_ended", "")


# ---- Backups ---------------------------------------------------------------------------------------------------------

def _meta(key: str, default="0") -> str:
    row = _one("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else default


def get_revision() -> int:
    return int(_meta("revision"))


def is_dirty() -> bool:
    """True when something changed since the last backup was posted."""
    return get_revision() > int(_meta("backed_up_revision"))


def last_backup_at() -> "str | None":
    return _meta("last_backup_at", "") or None


def mark_backed_up(revision: int):
    """Records that a snapshot taken at `revision` is safely posted. Doesn't count as a change."""
    with _lock:
        conn = _db()
        conn.execute("UPDATE meta SET value = ? WHERE key = 'backed_up_revision' AND CAST(value AS INTEGER) < ?",
                     (str(revision), revision))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_backup_at', ?)", (now_iso(),))


def data_summary(conn=None) -> dict:
    """Headline numbers shown when deciding whether to restore or reset."""
    with _lock:
        conn = conn or _db()
        count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "weeks": count("SELECT COUNT(*) FROM weeks"),
            "submissions": count("SELECT COUNT(*) FROM predictions") + count("SELECT COUNT(*) FROM ballot_submissions"),
            "titles": count("SELECT COUNT(*) FROM anime"),
            "scored_rows": count("SELECT COUNT(*) FROM scores"),
        }


def has_data() -> bool:
    summary = data_summary()
    return any(summary.values())


def snapshot() -> tuple:
    """A consistent copy of the whole database as bytes, plus the revision it contains."""
    with _lock:
        revision = get_revision()
        fd, tmp = tempfile.mkstemp(suffix=".db", dir=os.path.dirname(os.path.abspath(_path)))
        os.close(fd)
        try:
            dest = sqlite3.connect(tmp)
            try:
                _db().backup(dest)
                dest.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('snapshot_at', ?)", (now_iso(),))
                dest.commit()
            finally:
                dest.close()
            return Path(tmp).read_bytes(), revision
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def inspect_snapshot(data: bytes) -> dict:
    """Checks that `data` is a usable backup and describes it. Raises InvalidBackup otherwise."""
    if not data.startswith(b"SQLite format 3\x00"):
        raise InvalidBackup("it isn't a backup file from this bot (not a SQLite database)")
    with _lock:
        fd, tmp = tempfile.mkstemp(suffix=".db", dir=os.path.dirname(os.path.abspath(_path)))
        os.close(fd)
        try:
            Path(tmp).write_bytes(data)
            conn = sqlite3.connect(Path(tmp).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise InvalidBackup("the file is damaged")
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                missing = EXPECTED_TABLES - tables
                if missing:
                    raise InvalidBackup("it's missing data this bot needs (" + ", ".join(sorted(missing)) + ")")
                meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
                if int(meta.get("schema_version", 0)) > SCHEMA_VERSION:
                    raise InvalidBackup("it was made by a newer version of the bot")
                info = data_summary(conn)
                info["snapshot_at"] = meta.get("snapshot_at") or meta.get("last_backup_at") or "unknown time"
                return info
            finally:
                conn.close()
        except sqlite3.DatabaseError as e:
            raise InvalidBackup(f"it couldn't be read as a database ({e})") from e
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def restore_snapshot(data: bytes) -> dict:
    """Replaces ALL data with the backup's contents, atomically. Returns the backup's summary."""
    info = inspect_snapshot(data)
    with _lock:
        fd, tmp = tempfile.mkstemp(suffix=".db", dir=os.path.dirname(os.path.abspath(_path)))
        os.close(fd)
        try:
            Path(tmp).write_bytes(data)
            source = sqlite3.connect(tmp)
            try:
                source.backup(_db())
            finally:
                source.close()
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        _init_schema(_db())
        with _tx():
            pass  # bump the revision so the restored state is backed up again as the newest snapshot
    return info


def dump_tables() -> dict:
    """{table: (headers, rows)} for /export, ordered for reading."""
    result = {}
    with _lock:
        for table in EXPORT_TABLES:
            cursor = _db().execute(f"SELECT * FROM {table} ORDER BY rowid")
            headers = [d[0] for d in cursor.description]
            result[table] = (headers, [tuple(r) for r in cursor.fetchall()])
    return result


# ---- async access ------------------------------------------------------------------------------------------------------

class _AsyncAccess:
    """`await aio.get_current_week()` runs storage.get_current_week() in a worker thread."""

    def __getattr__(self, name):
        fn = globals()[name]

        @functools.wraps(fn)
        async def runner(*args, **kwargs):
            return await asyncio.to_thread(fn, *args, **kwargs)

        return runner


aio = _AsyncAccess()
