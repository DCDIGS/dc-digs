"""SQLite storage in data/dig.db. Favourite and removed are independent flags."""
import json
import sqlite3
from datetime import datetime, timezone

from . import config
from .models import Release, dedup_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    id              INTEGER PRIMARY KEY,
    artist          TEXT NOT NULL,
    title           TEXT NOT NULL,
    label           TEXT,
    format          TEXT,
    genres          TEXT NOT NULL DEFAULT '[]',   -- JSON list of tags
    source          TEXT NOT NULL,
    source_url      TEXT,
    discovered_via  INTEGER REFERENCES releases(id),
    score           REAL NOT NULL DEFAULT 0,
    score_reasons   TEXT NOT NULL DEFAULT '[]',   -- JSON list of strings
    bandcamp_url    TEXT UNIQUE,
    bandcamp_id     INTEGER,
    item_type       TEXT,
    release_date    TEXT,
    art_url         TEXT,
    dedup_key       TEXT NOT NULL UNIQUE,
    found_at        TEXT NOT NULL,
    is_new          INTEGER NOT NULL DEFAULT 1,
    is_favourite    INTEGER NOT NULL DEFAULT 0,
    favourited_at   TEXT,
    is_removed      INTEGER NOT NULL DEFAULT 0,
    removed_at      TEXT,
    in_crate        INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tracks (
    id                INTEGER PRIMARY KEY,
    release_id        INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    title             TEXT NOT NULL,
    duration_seconds  REAL,
    bandcamp_track_id INTEGER,
    streamable        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (release_id, position)
);
CREATE TABLE IF NOT EXISTS recommendation_queue (
    release_id    INTEGER PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
    queued_at     TEXT NOT NULL,
    processed_at  TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    items_found  INTEGER NOT NULL DEFAULT 0,
    errors       TEXT NOT NULL DEFAULT '[]'       -- JSON list of strings
);
-- Release URLs already looked at (too old, or added), so they're never refetched.
CREATE TABLE IF NOT EXISTS seen_urls (
    url         TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    outcome     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracks_release ON tracks(release_id);
"""

DEFAULT_SETTINGS = {"start_at": "0.5", "waveform": "1", "autonext": "1"}

# Columns added after phase 1: (table, column, definition)
MIGRATIONS = [
    ("releases", "found_in", "TEXT NOT NULL DEFAULT '[]'"),  # JSON: ["follow:Name", "tag:deep-house", "rec:12"]
    ("seen_urls", "release_date", "TEXT"),                   # lets a larger max_age re-check old ones
    ("tracks", "peaks", "TEXT"),
    ("releases", "inbox", "INTEGER NOT NULL DEFAULT 1"),      # 1 or 2: which inbox it was found for                             # JSON waveform (loudness per slice); no audio
]


def _migrate(conn) -> None:
    # NTS was removed from the app; its tracklist tables go with it.
    conn.execute("DROP TABLE IF EXISTS nts_tracks")
    conn.execute("DROP TABLE IF EXISTS nts_episodes")
    for table, column, definition in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_initialised: set[str] = set()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Create tables once per process, so ordinary web requests never need a write lock.
    if str(config.DB_PATH) not in _initialised:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        _initialised.add(str(config.DB_PATH))
    return conn


# --- collector side -------------------------------------------------------

def is_known_url(conn, url: str, cutoff: str | None = None, ignore_age: bool = False) -> bool:
    """True if this release URL needs no fetch: already a release, or seen before.
    A release skipped as too old counts as unknown again once it falls inside `cutoff`,
    and always for sources that ignore age (`ignore_age`)."""
    if conn.execute("SELECT 1 FROM releases WHERE bandcamp_url = ?", (url,)).fetchone():
        return True
    row = conn.execute("SELECT outcome, release_date FROM seen_urls WHERE url = ?", (url,)).fetchone()
    if row is None:
        return False
    if row["outcome"] == "too_old" and (
            ignore_age or (cutoff and row["release_date"] and row["release_date"] >= cutoff)):
        return False
    return True


def mark_seen(conn, url: str, outcome: str, release_date: str | None = None) -> None:
    conn.execute("""INSERT INTO seen_urls (url, first_seen, outcome, release_date) VALUES (?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET outcome = excluded.outcome,
                                                   release_date = excluded.release_date""",
                 (url, now(), outcome, release_date))


def add_found_in(conn, release_id: int, marker: str) -> None:
    row = conn.execute("SELECT found_in FROM releases WHERE id = ?", (release_id,)).fetchone()
    found = json.loads(row["found_in"] or "[]")
    if marker not in found:
        found.append(marker)
        conn.execute("UPDATE releases SET found_in = ? WHERE id = ?", (json.dumps(found), release_id))


def add_release(conn, r: Release, score: float = 0, found_in: list[str] | None = None,
                discovered_via: int | None = None, inbox: int = 1) -> int | None:
    """Insert a release and its tracks. Returns the new id, or None if it's a duplicate:
    same Bandcamp URL, same Bandcamp id (the URL can change, e.g. a label's own domain),
    or same normalised artist + title from any source."""
    key = dedup_key(r.artist, r.title)
    dup = conn.execute(
        """SELECT id FROM releases WHERE dedup_key = ? OR bandcamp_url = ?
           OR (bandcamp_id = ? AND item_type = ?)""",
        (key, r.bandcamp_url, r.bandcamp_id, r.item_type)).fetchone()
    if dup:
        return None
    conn.execute("SAVEPOINT add_release")
    try:
        release_id = _insert_release(conn, r, score, key, found_in, discovered_via, inbox)
    except Exception:
        conn.execute("ROLLBACK TO add_release")
        conn.execute("RELEASE add_release")
        raise
    conn.execute("RELEASE add_release")
    return release_id


def _insert_release(conn, r: Release, score, key, found_in, discovered_via, inbox=1) -> int:
    cur = conn.execute(
        """INSERT INTO releases (artist, title, label, format, genres, source, source_url,
               score, score_reasons, bandcamp_url, bandcamp_id, item_type, release_date,
               art_url, dedup_key, found_at, found_in, discovered_via, inbox)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (r.artist, r.title, r.label, r.format, json.dumps(r.genres), r.source, r.source_url,
         score, json.dumps(r.reasons), r.bandcamp_url, r.bandcamp_id, r.item_type,
         r.release_date, r.art_url, key, now(), json.dumps(found_in or []), discovered_via, inbox))
    release_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO tracks (release_id, position, title, duration_seconds,
               bandcamp_track_id, streamable) VALUES (?, ?, ?, ?, ?, ?)""",
        [(release_id, t.position, t.title, t.duration_seconds, t.bandcamp_track_id,
          int(t.streamable)) for t in r.tracks])
    return release_id


def is_removed_music(conn, r: Release) -> bool:
    """True if you removed this music before, even if it now comes under another page:
    a track page whose track is on a release you removed, or a release whose every
    track is (e.g. an album re-uploaded under a new link)."""
    track_ids = [t.bandcamp_track_id for t in r.tracks if t.bandcamp_track_id]
    if r.item_type == "track" and r.bandcamp_id:
        track_ids = [r.bandcamp_id]
    if not track_ids:
        return False
    marks = ",".join("?" * len(track_ids))
    removed = conn.execute(
        f"""SELECT COUNT(DISTINCT t.bandcamp_track_id) FROM tracks t JOIN releases x ON x.id = t.release_id
            WHERE x.is_removed = 1 AND t.bandcamp_track_id IN ({marks})""", track_ids).fetchone()[0]
    return removed == len(set(track_ids))


def start_run(conn, source: str) -> int:
    cur = conn.execute("INSERT INTO runs (source, started_at) VALUES (?, ?)", (source, now()))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, items_found: int, errors: list[str]) -> None:
    conn.execute("UPDATE runs SET finished_at = ?, items_found = ?, errors = ? WHERE id = ?",
                 (now(), items_found, json.dumps(errors), run_id))
    conn.commit()


# --- web side ---------------------------------------------------------------

# The Removed list shows only the last day's removals (to undo a slip). Older ones drop
# out of the list but stay in the database, which is what keeps them from coming back.
REMOVED_VISIBLE_HOURS = 24

VIEWS = {
    "inbox": "is_removed = 0 AND inbox = 1",
    "inbox2": "is_removed = 0 AND inbox = 2",
    "favourites": "is_favourite = 1",
    # removed_at is stored as UTC "YYYY-MM-DDTHH:MM:SS+00:00", so strings compare in time order
    "removed": "is_removed = 1 AND removed_at >= "
               f"strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-{REMOVED_VISIBLE_HOURS} hours')",
}


def release_dict(row) -> dict:
    d = dict(row)
    d["genres"] = json.loads(d["genres"] or "[]")
    d["score_reasons"] = json.loads(d["score_reasons"] or "[]")
    d["found_in"] = json.loads(d.get("found_in") or "[]")
    for flag in ("is_new", "is_favourite", "is_removed", "in_crate"):
        d[flag] = bool(d[flag])
    return d


def list_releases(conn, view: str) -> list[dict]:
    where = VIEWS[view]
    order = {"favourites": "favourited_at DESC",
             "removed": "removed_at DESC"}.get(view, "score DESC, found_at DESC")
    rows = conn.execute(f"SELECT * FROM releases WHERE {where} ORDER BY {order}").fetchall()
    return [release_dict(r) for r in rows]


def get_release(conn, release_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
    if not row:
        return None
    d = release_dict(row)
    d["tracks"] = [dict(t) | {"streamable": bool(t["streamable"]),
                              "peaks": json.loads(t["peaks"]) if t["peaks"] else None}
                   for t in conn.execute(
        "SELECT id, position, title, duration_seconds, streamable, peaks FROM tracks "
        "WHERE release_id = ? ORDER BY position", (release_id,))]
    return d


def counts(conn) -> dict:
    return {view: conn.execute(f"SELECT COUNT(*) FROM releases WHERE {where}").fetchone()[0]
            for view, where in VIEWS.items()}


def update_release(conn, release_id: int, changes: dict) -> None:
    ts = now()
    sets, args = [], []
    for flag, stamp in (("is_favourite", "favourited_at"), ("is_removed", "removed_at")):
        if flag in changes:
            on = bool(changes[flag])
            sets += [f"{flag} = ?", f"{stamp} = ?"]
            args += [int(on), ts if on else None]
    for flag in ("in_crate", "is_new"):
        if flag in changes:
            sets.append(f"{flag} = ?")
            args.append(int(bool(changes[flag])))
    if "note" in changes:
        sets.append("note = ?")
        args.append(str(changes["note"]))
    if not sets:
        return
    conn.execute(f"UPDATE releases SET {', '.join(sets)} WHERE id = ?", (*args, release_id))
    # Phase 3: favouriting queues the release's "you may also like" for the next collector run.
    # One hop only: releases that came from a recommendation don't get queued themselves.
    if changes.get("is_favourite"):
        conn.execute("""INSERT OR IGNORE INTO recommendation_queue (release_id, queued_at)
                        SELECT id, ? FROM releases WHERE id = ? AND source != 'recommendation'""",
                     (ts, release_id))
    conn.commit()


def track_for_stream(conn, track_id: int):
    return conn.execute(
        """SELECT t.id, t.bandcamp_track_id, t.streamable, r.bandcamp_url
           FROM tracks t JOIN releases r ON r.id = t.release_id WHERE t.id = ?""",
        (track_id,)).fetchone()


def recent_runs(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) | {"errors": json.loads(r["errors"] or "[]")} for r in rows]


def set_peaks(conn, track_id: int, peaks: list[float]) -> None:
    conn.execute("UPDATE tracks SET peaks = ? WHERE id = ?", (json.dumps(peaks), track_id))
    conn.commit()


def last_run(conn) -> dict | None:
    runs = recent_runs(conn, 1)
    return runs[0] if runs else None


def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def set_setting(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()

