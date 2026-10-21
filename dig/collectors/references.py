"""Releases bought by the same people as your reference tracks.

For each reference (a Bandcamp track or album page):
1. its buyers: the `#collectors-data` blob on the page (and, for a track, on its album),
   paged further via /api/tralbumcollectors/2/thumbs if needed;
2. each buyer's most recent purchases via /api/fancollection/1/collection_items
   (cached for 7 days; only counts are kept, never fan names or collections);
3. releases owned by at least `reference_min_buyers` of them, most-shared first;
4. a genre check against the reference's tags, then the usual filters.
Also collects the reference's own "you may also like". Inspected 2026-09-23.
"""
import json
import time
from collections import Counter
from urllib.parse import urlsplit

from .. import bandcamp, db, fetch
from ..models import normalise
from .common import collect_url, cutoff, log

SOURCE = "reference"
COLLECTORS_API = "/api/tralbumcollectors/2/thumbs"
COLLECTION_API = "https://bandcamp.com/api/fancollection/1/collection_items"
COLLECTION_TTL = 7 * 24 * 3600
# Tags nearly every electronic release carries; sharing only these says nothing.
BROAD_TAGS = {"electronic", "electronica", "electronic music", "music", "dance", "club",
              "underground", "vinyl", "dj", "edm", "instrumental", "beats"}


def ref_label(release) -> str:
    return f"{release.artist} – {release.title}"


def buyers(page_url: str, want: int, errors: list[str]) -> tuple[list[int], dict]:
    """Fan ids who bought this page's release (up to `want`), plus the parsed page info."""
    info = bandcamp.parse_supporters(fetch.get(page_url).text, page_url)
    fans = list(info["fans"])
    host = "{0.scheme}://{0.netloc}".format(urlsplit(page_url))
    while info["more"] and len(fans) < want and fans and info["tralbum_id"]:
        try:
            data = fetch.post_json(host + COLLECTORS_API, {
                "tralbum_type": info["tralbum_type"], "tralbum_id": info["tralbum_id"],
                "token": fans[-1]["token"], "count": 80})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{page_url}: more buyers: {e}")
            break
        batch = [{"fan_id": r["fan_id"], "token": r.get("token")} for r in data.get("results") or []
                 if r.get("fan_id")]
        if not batch:
            break
        fans += batch
        info["more"] = bool(data.get("more_available"))
    return [f["fan_id"] for f in fans[:want]], info


def collection(fan_id: int, count: int) -> list[str]:
    """Release URLs a fan bought most recently. Cached a week: collections change slowly."""
    data = fetch.post_json(COLLECTION_API, {
        "fan_id": fan_id, "older_than_token": f"{int(time.time()) + 86400}::a::", "count": count},
        ttl=COLLECTION_TTL, cache_key=f"{COLLECTION_API}\nfan={fan_id}&count={count}")
    urls = []
    for item in data.get("items") or []:
        if item.get("item_url"):
            urls.append(bandcamp.clean_url(item["item_url"]))
    return urls


def shared_tags(candidate_tags, reference_tags) -> set[str]:
    norm = lambda tags: {t.strip().lower().replace("-", " ") for t in tags} - BROAD_TAGS  # noqa: E731
    return norm(candidate_tags) & norm(reference_tags)


def found_via(release_found_in: list[str], label: str) -> bool:
    return any(m == f"refrec::{label}" or (m.startswith("ref:") and m.split(":", 2)[2] == label)
               for m in release_found_in)


def set_ref_marker(conn, release_id: int, label: str, marker: str) -> None:
    """Replace this reference's "ref:<n>:<label>" marker; the buyer count changes between runs."""
    row = conn.execute("SELECT found_in FROM releases WHERE id = ?", (release_id,)).fetchone()
    found = [m for m in json.loads(row["found_in"] or "[]")
             if not (m.startswith("ref:") and m.split(":", 2)[2] == label)]
    found.append(marker)
    conn.execute("UPDATE releases SET found_in = ? WHERE id = ?", (json.dumps(found), release_id))


def artists_so_far(conn, label: str) -> Counter:
    """How many releases per artist this reference has already brought in."""
    counts: Counter = Counter()
    for r in conn.execute("SELECT artist, found_in FROM releases WHERE source = ?", (SOURCE,)):
        if found_via(json.loads(r["found_in"] or "[]"), label):
            counts[normalise(r["artist"])] += 1
    return counts


def run(conn, crit: dict) -> tuple[int, list[str]]:
    s = crit["sources"]
    # Each inbox has its own list of reference tracks; what a list finds goes to its inbox.
    refs = [(1, u) for u in s["reference_tracks"] or []] + [(2, u) for u in s["reference_tracks_2"] or []]
    if not refs:
        return 0, []
    run_id = db.start_run(conn, SOURCE)
    ignore_age = bool(crit["filters"]["references_ignore_max_age"])
    added, errors = 0, []
    try:
        for inbox, ref_url in refs:
            try:
                added += _collect_reference(conn, crit, ref_url, ignore_age, errors, inbox)
            except Exception as e:  # noqa: BLE001 - one broken reference mustn't stop the rest
                errors.append(f"{ref_url}: {e}")
    finally:
        db.finish_run(conn, run_id, added, errors)
    return added, errors


def _collect_reference(conn, crit, ref_url, ignore_age, errors, inbox=1) -> int:
    s = crit["sources"]
    ref = bandcamp.parse_release(fetch.get(ref_url).text, ref_url)
    label = ref_label(ref)
    own = {ref.bandcamp_url}
    fan_ids, info = buyers(ref_url, s["reference_buyers"], errors)
    ref_tags = set(ref.genres)
    # A track's album buyers own the track too, and there are usually more of them.
    if info["album_url"] and len(fan_ids) < s["reference_buyers"]:
        album_fans, _ = buyers(info["album_url"], s["reference_buyers"] - len(fan_ids), errors)
        fan_ids += [f for f in album_fans if f not in fan_ids]
        own.add(info["album_url"])
        try:
            ref_tags |= set(bandcamp.parse_release(fetch.get(info["album_url"]).text, info["album_url"]).genres)
        except Exception:  # noqa: BLE001 - the track's own tags are enough
            pass
    log.info("reference %s: %d buyers", label, len(fan_ids))

    counts: Counter = Counter()
    failed = []
    for fan_id in fan_ids:
        try:
            counts.update(set(collection(fan_id, s["reference_items_per_buyer"])) - own)
        except Exception as e:  # noqa: BLE001 - private or vanished collections happen
            failed.append(str(e))
    if failed:  # one line, not one per buyer
        errors.append(f"{label}: {len(failed)} of {len(fan_ids)} buyer collections unreadable ({failed[0]})")

    added = 0
    marker = lambda n: f"ref:{n}:{label}"  # noqa: E731 - "ref:<co-buyers>:<reference>"
    per_artist = artists_so_far(conn, label)

    # Its own "you may also like" first: Bandcamp's own idea of what's close.
    for url in ref.recommendations[:8]:
        added += _consider(conn, crit, url, f"refrec::{label}", ref_tags, 0, ignore_age, errors, per_artist, inbox)

    fetched = 0
    for url, n in counts.most_common():
        if n < s["reference_min_buyers"] or added >= s["reference_max_new"] or fetched >= 3 * s["reference_max_new"]:
            break
        existing = conn.execute("SELECT id FROM releases WHERE bandcamp_url = ?", (url,)).fetchone()
        if existing:
            set_ref_marker(conn, existing["id"], label, marker(n))
            conn.commit()
            continue
        # Only pages actually fetched count against the budget; known ones are free,
        # otherwise the top candidates, once all seen, would block everything below them.
        if db.is_known_url(conn, url, cutoff(crit), ignore_age=ignore_age):
            continue
        fetched += 1
        added += _consider(conn, crit, url, marker(n), ref_tags, n, ignore_age, errors, per_artist, inbox)
    log.info("reference %s: %d candidates shared by >=%d buyers, %d new", label,
             sum(1 for c in counts.values() if c >= s["reference_min_buyers"]), s["reference_min_buyers"], added)
    return added


def _consider(conn, crit, url, marker, ref_tags, co_buyers, ignore_age, errors, per_artist, inbox=1) -> int:
    if db.is_known_url(conn, url, cutoff(crit), ignore_age=ignore_age):
        return 0
    s = crit["sources"]
    strong = co_buyers >= s["reference_strong_overlap"]

    def accept(release):
        # Buyers of an artist own more of that artist; leave room for other names.
        if per_artist[normalise(release.artist)] >= s["reference_max_per_artist"]:
            return "artist_cap"
        # Genre check: must share a real tag with the reference, unless many buyers overlap.
        if not strong and not shared_tags(release.genres, ref_tags):
            return "other_genre"
        return None

    new_id = collect_url(conn, url, crit, SOURCE, url, marker, errors, ignore_age=ignore_age, accept=accept,
                         inbox=inbox)
    if new_id:
        artist = conn.execute("SELECT artist FROM releases WHERE id = ?", (new_id,)).fetchone()["artist"]
        per_artist[normalise(artist)] += 1
    return 1 if new_id else 0
