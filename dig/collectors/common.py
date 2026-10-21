"""Shared by the Bandcamp collectors: hard filters, and fetch -> parse -> filter -> store."""
import logging
from datetime import date, timedelta

import requests

from .. import bandcamp, criteria as criteria_mod, db, fetch

log = logging.getLogger("dig.collect")


RETRY_LATER = {"artist_cap"}


def cutoff(crit: dict) -> str:
    return (date.today() - timedelta(days=crit["filters"]["max_age_days"])).isoformat()


def is_too_old(release_date: str | None, crit: dict) -> bool:
    """Pre-orders (future dates) and undated releases always pass."""
    return bool(release_date) and release_date < cutoff(crit)


def filter_reason(release, crit: dict, ignore_age: bool = False) -> str | None:
    """Why a release fails the hard filters in criteria.yaml, or None if it passes."""
    f = crit["filters"]
    if not ignore_age and is_too_old(release.release_date, crit):
        return "too_old"
    excluded = {criteria_mod.tag_key(t) for t in f["exclude_tags"]}
    if excluded & {criteria_mod.tag_key(g) for g in release.genres}:
        return "excluded_tag"
    formats = {x.lower() for x in f["formats"]}
    if formats and (release.format or "").lower() not in formats:
        return "format"
    return None


def collect_url(conn, url: str, crit: dict, source: str, source_url: str, found_in: str,
                errors: list[str], discovered_via: int | None = None,
                ignore_age: bool = False, accept=None, inbox: int = 1) -> int | None:
    """Fetch one release page and store it if it passes the filters and `accept(release)`,
    a source's own check returning None (ok) or a reason. Reasons in RETRY_LATER aren't
    remembered, so the release can still come in another way. Returns the new release
    id, or None (known, filtered, duplicate or error)."""
    try:
        page = fetch.get(url)
        release = bandcamp.parse_release(page.text, url, source, source_url)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (404, 410):
            db.mark_seen(conn, url, "gone")  # deleted from Bandcamp; collections still list it
            conn.commit()
            return None
        errors.append(f"{url}: {e}")  # retried next run (not marked seen)
        return None
    except bandcamp.ParseError:
        db.mark_seen(conn, url, "not_a_release")
        conn.commit()
        return None
    except Exception as e:  # noqa: BLE001 - retried next run (not marked seen)
        errors.append(f"{url}: {e}")
        return None

    if db.is_removed_music(conn, release):
        db.mark_seen(conn, url, "removed_by_user", release.release_date)
        conn.commit()
        return None
    reason = filter_reason(release, crit, ignore_age)
    if not reason and accept is not None:
        reason = accept(release)
        if reason in RETRY_LATER:
            return None
    if reason:
        db.mark_seen(conn, url, reason, release.release_date)
        conn.commit()
        return None
    try:
        new_id = db.add_release(conn, release, found_in=[found_in], discovered_via=discovered_via,
                                inbox=inbox)
    except Exception as e:  # noqa: BLE001 - not marked seen, so it's retried next run
        errors.append(f"{url}: storing failed: {e!r}")
        return None
    db.mark_seen(conn, url, "added" if new_id else "duplicate", release.release_date)
    conn.commit()
    if new_id:
        log.info("+ [%s] %s — %s (%s)", found_in, release.artist, release.title, release.release_date)
    return new_id
