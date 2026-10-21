"""New releases from the artists and labels a Bandcamp fan follows.

Fan page -> follows -> each follow's /music listing -> release pages we haven't
seen yet (first N per listing) -> keep those that pass the filters.
"""
from .. import bandcamp, config, db, fetch
from .common import collect_url, cutoff, log

SOURCE = "bandcamp_follow"


def run(conn, crit: dict, username: str | None = None,
        per_follow: int = config.RELEASES_PER_FOLLOW) -> tuple[int, list[str]]:
    if not crit["sources"]["bandcamp_follows"]:
        return 0, []
    username = username or crit["sources"].get("bandcamp_username") or config.BANDCAMP_USERNAME
    if not username:
        return 0, []  # no Bandcamp name set up: nothing to follow
    run_id = db.start_run(conn, SOURCE)
    added, errors = 0, []

    try:
        fan_page = fetch.get(f"https://bandcamp.com/{username}")
        follows = bandcamp.parse_follows(fan_page.text)
        log.info("%d follows", len(follows))
    except Exception as e:  # noqa: BLE001 - logged to the run table
        errors.append(f"fan page: {e}")
        db.finish_run(conn, run_id, 0, errors)
        return 0, errors

    try:
        for follow in follows:
            added += collect_account(conn, crit, follow, per_follow, errors, SOURCE, "follow")
    except Exception as e:  # noqa: BLE001 - unexpected; keep the run log honest
        errors.append(f"collector crashed: {e!r}")
        raise
    finally:
        db.finish_run(conn, run_id, added, errors)
    return added, errors


def collect_account(conn, crit: dict, follow: dict, per_follow: int, errors: list[str],
                    source: str, marker: str) -> int:
    """New releases on one artist/label account's /music page."""
    try:
        listing = fetch.get(follow["url"] + "/music")
    except Exception as e:  # noqa: BLE001
        errors.append(f"{follow['name']}: listing: {e}")
        return 0

    # An artist with a single release gets redirected straight to it.
    if "/album/" in listing.final_url or "/track/" in listing.final_url:
        urls = [bandcamp.clean_url(listing.final_url)]
    else:
        urls = bandcamp.parse_music_listing(listing.text, follow["url"])[:per_follow]

    added = 0
    for url in urls:
        if db.is_known_url(conn, url, cutoff(crit)):
            continue
        if collect_url(conn, url, crit, source, follow["url"], f"{marker}:{follow['name']}", errors):
            added += 1
    return added
