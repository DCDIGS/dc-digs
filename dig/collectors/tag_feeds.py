"""New arrivals per Bandcamp tag, from the JSON endpoint bandcamp.com/discover/<tag>
loads its grid from (inspected 2026-09-23; sample in tests/fixtures/discover_deep_house.json).

The listing already has release dates, so too-old releases are skipped without
fetching their page. A release seen in several tag feeds gets each tag recorded.
"""
from .. import bandcamp, db, fetch
from .common import collect_url, cutoff, is_too_old, log

SOURCE = "tag_feed"
DISCOVER_URL = "https://bandcamp.com/api/discover/1/discover_web"


def discover_body(tag: str, size: int) -> dict:
    return {"category_id": 0, "tag_norm_names": [tag], "geoname_id": 0, "slice": "new",
            "time_facet_id": None, "cursor": "*", "size": size,
            "include_result_types": ["a", "s"], "followed_bands": False}


def parse_results(data: dict) -> list[dict]:
    out = []
    for item in data.get("results") or []:
        if not item.get("item_url"):
            continue
        out.append({
            "url": bandcamp.clean_url(item["item_url"]),
            "release_date": (item.get("release_date") or "")[:10] or None,
            "preorder": bool(item.get("is_album_preorder")),
            "artist": item.get("album_artist") or item.get("band_name"),
            "account": item.get("band_name") or "",
            "title": item.get("title"),
        })
    return out


def run(conn, crit: dict) -> tuple[int, list[str]]:
    tags = crit["sources"]["tag_feeds"] or []
    if not crit["sources"]["tag_feeds_enabled"] or not tags:
        return 0, []
    size = max(1, min(60, int(crit["sources"]["tag_feed_size"])))
    run_id = db.start_run(conn, SOURCE)
    added, errors = 0, []
    cap = int(crit["filters"]["tag_feed_max_per_account"] or 0)
    excluded = {a.strip().casefold() for a in crit["filters"]["exclude_accounts"] or []}
    per_account: dict[str, int] = {}
    try:
        for tag in tags:
            try:
                results = parse_results(fetch.post_json(DISCOVER_URL, discover_body(tag, size)))
            except Exception as e:  # noqa: BLE001
                errors.append(f"tag {tag}: {e}")
                continue
            log.info("tag %s: %d results", tag, len(results))
            for item in results:
                url, marker = item["url"], f"tag:{tag}"
                existing = conn.execute("SELECT id FROM releases WHERE bandcamp_url = ?", (url,)).fetchone()
                if existing:
                    db.add_found_in(conn, existing["id"], marker)
                    conn.commit()
                    continue
                if db.is_known_url(conn, url, cutoff(crit)):
                    continue
                if not item["preorder"] and is_too_old(item["release_date"], crit):
                    db.mark_seen(conn, url, "too_old", item["release_date"])
                    conn.commit()
                    continue
                account = item["account"].casefold()
                if account in excluded:
                    db.mark_seen(conn, url, "excluded_account", item["release_date"])
                    conn.commit()
                    continue
                # Not marked seen: the rest of a big upload batch gets its turn on later runs.
                if cap and per_account.get(account, 0) >= cap:
                    continue
                per_account[account] = per_account.get(account, 0) + 1
                if collect_url(conn, url, crit, SOURCE, f"https://bandcamp.com/discover/{tag}",
                               marker, errors):
                    added += 1
    except Exception as e:  # noqa: BLE001
        errors.append(f"collector crashed: {e!r}")
        raise
    finally:
        db.finish_run(conn, run_id, added, errors)
    return added, errors
