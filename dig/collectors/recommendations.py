"""'If you like X, you may also like' from the pages of your favourites.

Favouriting a release queues it (db.update_release); this collects each queued
favourite's recommendations once. One hop only: releases found here are never
queued themselves.
"""
from .. import bandcamp, db, fetch
from .common import collect_url, cutoff, log

SOURCE = "recommendation"


def run(conn, crit: dict) -> tuple[int, list[str]]:
    if not crit["sources"]["recommendations_from_favourites"]:
        return 0, []
    queued = conn.execute(
        """SELECT r.id, r.artist, r.title, r.bandcamp_url, r.inbox FROM recommendation_queue q
           JOIN releases r ON r.id = q.release_id
           WHERE q.processed_at IS NULL AND r.is_favourite = 1""").fetchall()
    if not queued:
        return 0, []
    run_id = db.start_run(conn, SOURCE)
    ignore_age = bool(crit["filters"]["recommendations_ignore_max_age"])
    added, errors = 0, []
    try:
        for fav in queued:
            try:
                recs = bandcamp.parse_release(fetch.get(fav["bandcamp_url"]).text,
                                              fav["bandcamp_url"]).recommendations
            except Exception as e:  # noqa: BLE001 - stays queued for the next run
                errors.append(f"{fav['artist']} – {fav['title']}: {e}")
                continue
            log.info("recommendations from %s – %s: %d", fav["artist"], fav["title"], len(recs))
            for url in recs:
                if db.is_known_url(conn, url, cutoff(crit), ignore_age=ignore_age):
                    continue
                if collect_url(conn, url, crit, SOURCE, fav["bandcamp_url"], f"rec:{fav['id']}",
                               errors, discovered_via=fav["id"], ignore_age=ignore_age,
                               inbox=fav["inbox"]):
                    added += 1
            conn.execute("UPDATE recommendation_queue SET processed_at = ? WHERE release_id = ?",
                         (db.now(), fav["id"]))
            conn.commit()
    except Exception as e:  # noqa: BLE001
        errors.append(f"collector crashed: {e!r}")
        raise
    finally:
        db.finish_run(conn, run_id, added, errors)
    return added, errors
