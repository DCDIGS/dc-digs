"""Scores with human-readable reasons. Ordering only; filters live in the collectors.

Scores are recomputed for everything still in the inbox whenever criteria.yaml
or your favourites change."""
import json
import re

from . import criteria as criteria_mod
from .models import normalise

_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|\bx\b|\bvs\.?|\bfeat\.?|\bft\.?|/)\s*", re.I)
_last_criteria_mtime = None
# Placeholders in tracklists and compilations, not artists.
_NOT_ARTISTS = {"unknown", "unknown artist", "unknown artists", "various", "various artists",
                "va", "v a", "untitled", "unreleased", "id", "tba", "dubplate", "white label"}


def artist_names(text: str) -> set[str]:
    """'Takuya Nakamura & Tim Reaper' -> {'takuya nakamura', 'tim reaper'} (too-short names dropped)."""
    names = {normalise(p) for p in _ARTIST_SPLIT.split(text or "")}
    return {n for n in names if len(n) >= 3 and n not in _NOT_ARTISTS}


# Big "compilations" are often DJ-pool bootlegs that name-drop famous artists.
MAX_TRACKS_FOR_TRACK_ARTISTS = 8


def release_artists(release: dict, track_titles: list[str]) -> set[str]:
    """The release artist plus 'Artist - Title' prefixes from compilation track titles
    (only on releases with at most MAX_TRACKS_FOR_TRACK_ARTISTS tracks)."""
    names = artist_names(release["artist"])
    if len(track_titles) > MAX_TRACKS_FOR_TRACK_ARTISTS:
        return names
    for title in track_titles:
        if " - " in title:
            names |= artist_names(title.split(" - ", 1)[0])
    return names


def build_context(conn) -> dict:
    favs = conn.execute("SELECT id, artist, title, label FROM releases WHERE is_favourite = 1").fetchall()
    return {"favs": {f["id"]: dict(f) for f in favs}}


def score(release: dict, track_titles: list[str], crit: dict, ctx: dict) -> tuple[float, list[str]]:
    rank = crit["ranking"]
    total, reasons = 0.0, []

    def add(points, text):
        nonlocal total
        if points:
            total += points
            reasons.append(f"{text} {points:+g}")

    found_in = release.get("found_in") or []
    source = release["source"]
    detail = {
        "bandcamp_follow": "followed on Bandcamp" + "".join(
            f": {m[7:]}" for m in found_in[:1] if m.startswith("follow:")),
        "tag_feed": "tag feed",
        "recommendation": "recommended from a favourite",
        "label_page": "watched label" + "".join(
            f": {m[6:]}" for m in found_in[:1] if m.startswith("label:")),
        "boomkat": "Boomkat",
    }.get(source, source)
    if source == "tag_feed":
        tags = [m[4:] for m in found_in if m.startswith("tag:")]
        detail = "tag feed: " + ", ".join(tags) if tags else detail
    if source == "recommendation" and release.get("discovered_via"):
        fav = ctx["favs"].get(release["discovered_via"])
        if fav:
            detail = f"recommended from a favourite: {fav['artist']} – {fav['title']}"
    if source == "reference":
        detail = "found via your reference tracks"
    add(rank["source_weight"].get(source, 0), detail)

    # Co-purchases: "ref:<n>:<reference>" per reference whose buyers own it.
    for m in found_in:
        if m.startswith("ref:"):
            n, ref = m[4:].split(":", 1)
            points = min(int(n) * rank["boost_per_co_buyer"], rank["max_co_buyer_boost"])
            add(points, f"owned by {n} buyers of {ref}")
        elif m.startswith("refrec::"):
            add(rank["source_weight"].get("recommendation", 0), f"“you may also like” on {m[8:]}")

    tag_feeds = [m for m in found_in if m.startswith("tag:")]
    if len(tag_feeds) > 1:
        add(rank["boost_multiple_tag_feeds"] * (len(tag_feeds) - 1), f"in {len(tag_feeds)} tag feeds")

    # Only the best-matching tag counts, so piling on popular tags doesn't pay.
    genres = {criteria_mod.tag_key(g) for g in release.get("genres") or []}
    tag_hits = [(points, tag) for tag, points in (rank.get("boost_tags") or {}).items()
                if criteria_mod.tag_key(tag) in genres]
    if tag_hits:
        points, tag = max(tag_hits)
        add(points, f"tag {tag}")

    others = [f for fid, f in ctx["favs"].items() if fid != release["id"]]
    label = normalise(release.get("label") or "")
    own_artist = normalise(release["artist"])
    if label and label != own_artist:
        match = next((f for f in others if normalise(f["label"] or "") == label), None)
        if match:
            add(rank["boost_labels_of_favourites"], f"label of a favourite: {release['label']}")
    artists = release_artists(release, track_titles)
    fav_artists = set().union(*(artist_names(f["artist"]) for f in others)) if others else set()
    hit = sorted(artists & fav_artists)
    if hit:
        add(rank["boost_artists_of_favourites"], f"artist of a favourite: {hit[0]}")


    return total, reasons


def rescore_inbox(conn, crit: dict | None = None) -> int:
    crit = crit or criteria_mod.load()
    ctx = build_context(conn)
    rows = conn.execute("SELECT * FROM releases WHERE is_removed = 0").fetchall()
    titles = {}
    for t in conn.execute("SELECT release_id, title FROM tracks"):
        titles.setdefault(t["release_id"], []).append(t["title"])
    for row in rows:
        r = dict(row)
        r["genres"] = json.loads(r["genres"] or "[]")
        r["found_in"] = json.loads(r["found_in"] or "[]")
        total, reasons = score(r, titles.get(r["id"], []), crit, ctx)
        conn.execute("UPDATE releases SET score = ?, score_reasons = ? WHERE id = ?",
                     (total, json.dumps(reasons), r["id"]))
    conn.commit()
    return len(rows)


def rescore_if_criteria_changed(conn) -> bool:
    global _last_criteria_mtime
    current = criteria_mod.mtime()
    if current == _last_criteria_mtime:
        return False
    rescore_inbox(conn)
    _last_criteria_mtime = current
    return True
