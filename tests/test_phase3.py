"""Tag feeds, recommendations and ranking, against the made-up sample pages and responses in fixtures/."""
import json

import pytest

from conftest import FIXTURES, fixture_text
from dig import bandcamp, criteria, db, fetch, ranking
from dig.collectors import common, recommendations, tag_feeds

ALBUM_URL = "https://example-label.bandcamp.com/album/first-ep"


def fixture_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def crit():
    c = criteria._merge(criteria.DEFAULTS, {})
    c["filters"]["max_age_days"] = 100_000  # fixtures age; don't let dates decide the tests
    return c


def _add(conn, html="release_album.html", url=ALBUM_URL, source="bandcamp_follow", found_in=None):
    r = bandcamp.parse_release(fixture_text(html), url, source)
    rid = db.add_release(conn, r, found_in=found_in or [])
    conn.commit()
    return rid


# --- criteria -----------------------------------------------------------------

def test_no_criteria_file_means_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(criteria, "PATH", tmp_path / "criteria.yaml")   # a fresh install has none
    c = criteria.load()
    assert c["sources"]["reference_tracks"] == [] and c["sources"]["bandcamp_username"] == ""
    assert c["schedule"]["daily_at"] == "06:00"
    assert c["filters"]["max_age_days"] > 0


def test_merge_keeps_defaults_for_missing_keys():
    c = criteria._merge(criteria.DEFAULTS, {"ranking": {"boost_tags": {"breaks": 5}}})
    assert c["ranking"]["boost_tags"] == {"breaks": 5}
    assert c["ranking"]["boost_per_co_buyer"] == criteria.DEFAULTS["ranking"]["boost_per_co_buyer"]


# --- filters ------------------------------------------------------------------

def test_filters(crit):
    r = bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL)  # 2026-09-22, EP
    assert common.filter_reason(r, crit) is None
    crit["filters"]["exclude_tags"] = ["Minimal-Techno"]  # matches "minimal techno"
    assert common.filter_reason(r, crit) == "excluded_tag"
    crit["filters"]["exclude_tags"] = []
    crit["filters"]["formats"] = ["LP"]
    assert common.filter_reason(r, crit) == "format"
    crit["filters"]["formats"] = ["EP"]
    crit["filters"]["max_age_days"] = 1
    r.release_date = "2020-01-01"
    assert common.filter_reason(r, crit) == "too_old"
    assert common.filter_reason(r, crit, ignore_age=True) is None


def test_too_old_url_is_rechecked_when_max_age_grows(conn):
    db.mark_seen(conn, "https://x.bandcamp.com/album/old", "too_old", "2026-01-10")
    assert db.is_known_url(conn, "https://x.bandcamp.com/album/old", cutoff="2026-06-01")
    assert not db.is_known_url(conn, "https://x.bandcamp.com/album/old", cutoff="2026-01-01")


# --- tag feeds ----------------------------------------------------------------

def test_parse_discover_results():
    results = tag_feeds.parse_results(fixture_json("discover_deep_house.json"))
    assert len(results) == 60
    first = results[0]
    assert first["url"].startswith("https://") and "?" not in first["url"]
    assert len(first["release_date"]) == 10
    assert isinstance(first["preorder"], bool)


def test_tag_feed_collector(conn, crit, monkeypatch):
    feed = {"results": [
        {"item_url": ALBUM_URL + "?from=discover_page", "release_date": "2026-09-22 00:00:00 UTC",
         "is_album_preorder": False, "title": "First EP", "band_name": "x"},
        {"item_url": "https://old.bandcamp.com/album/ancient?from=discover_page",
         "release_date": "2001-12-01 00:00:00 UTC", "is_album_preorder": False, "title": "Ancient"},
    ]}
    crit["filters"]["max_age_days"] = (__import__("datetime").date.today()
                                       - __import__("datetime").date(2026, 1, 1)).days
    crit["sources"]["tag_feeds_enabled"] = True
    crit["sources"]["tag_feeds"] = ["minimal", "rominimal"]
    pages = []
    monkeypatch.setattr(fetch, "post_json", lambda url, body, use_cache=True: feed)
    monkeypatch.setattr(fetch, "get", lambda url, use_cache=True, timeout=30: (
        pages.append(url) or fetch.Page(url, url, fixture_text("release_album.html"), False)))

    added, errors = tag_feeds.run(conn, crit)
    assert (added, errors) == (1, [])
    assert pages == [ALBUM_URL]  # the 2001 release was skipped without fetching its page
    r = db.list_releases(conn, "inbox")[0]
    assert r["source"] == "tag_feed"
    assert r["found_in"] == ["tag:minimal", "tag:rominimal"]  # seen in both feeds, stored once


# --- recommendations ----------------------------------------------------------

def test_recommendations_one_hop(conn, crit, monkeypatch):
    fav = _add(conn)
    db.update_release(conn, fav, {"is_favourite": True})
    recs = bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL).recommendations
    track_page = fixture_text("release_track.html")

    def fake_get(url, use_cache=True, timeout=30):
        return fetch.Page(url, url, fixture_text("release_album.html") if url == ALBUM_URL else track_page, False)

    monkeypatch.setattr(fetch, "get", fake_get)
    added, errors = recommendations.run(conn, crit)
    # every recommendation page is the same saved track page -> one release, rest duplicates
    assert (added, errors) == (1, [])
    rec = next(r for r in db.list_releases(conn, "inbox") if r["source"] == "recommendation")
    assert rec["discovered_via"] == fav
    assert conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0] == len(recs)
    assert conn.execute("SELECT processed_at FROM recommendation_queue").fetchone()[0]

    # favouriting a recommended release does not queue *its* recommendations
    db.update_release(conn, rec["id"], {"is_favourite": True})
    assert conn.execute("SELECT COUNT(*) FROM recommendation_queue").fetchone()[0] == 1
    assert recommendations.run(conn, crit) == (0, [])


def test_recommendations_ignore_max_age(conn, crit, monkeypatch):
    fav = _add(conn)
    db.update_release(conn, fav, {"is_favourite": True})
    crit["filters"]["max_age_days"] = 1  # the saved track page is from 2026-02-04
    monkeypatch.setattr(fetch, "get", lambda url, use_cache=True, timeout=30: fetch.Page(
        url, url, fixture_text("release_album.html" if url == ALBUM_URL else "release_track.html"), False))
    crit["filters"]["recommendations_ignore_max_age"] = False
    assert recommendations.run(conn, crit)[0] == 0
    conn.execute("UPDATE recommendation_queue SET processed_at = NULL")
    conn.execute("DELETE FROM seen_urls")
    crit["filters"]["recommendations_ignore_max_age"] = True
    assert recommendations.run(conn, crit)[0] == 1


# --- ranking ------------------------------------------------------------------

def test_artist_names():
    assert ranking.artist_names("Takuya Nakamura & Tim Reaper") == {"takuya nakamura", "tim reaper"}
    assert ranking.artist_names("Sam Binga, Machinedrum feat. Cesco") == {"sam binga", "machinedrum", "cesco"}
    assert ranking.artist_names("DJ") == set()
    assert ranking.artist_names("Unknown Artist") == set()
    assert ranking.artist_names("Various Artists") == set()


def test_score_reasons(conn, crit):
    crit["ranking"]["boost_tags"] = {"rominimal": 3, "breaks": 1}
    rid = _add(conn, found_in=["tag:minimal", "tag:rominimal"], source="tag_feed")
    ranking.rescore_inbox(conn, crit)
    r = db.get_release(conn, rid)
    # only the best tag boost counts (rominimal +3, not also breaks +1)
    assert r["score_reasons"] == ["tag feed: minimal, rominimal +1", "in 2 tag feeds +1", "tag rominimal +3"]
    assert r["score"] == 5


def test_favourite_label_and_artist_boost(conn, crit):
    fav = _add(conn)  # label "Example Label"
    db.update_release(conn, fav, {"is_favourite": True})
    other = bandcamp.parse_release(fixture_text("release_track.html"), "https://x.bandcamp.com/track/y", "tag_feed")
    conn.execute("UPDATE releases SET artist = 'Ex Artist' WHERE id = ?", (fav,))
    other.label = "Example Label"
    other.artist = "Ex Artist & Friend"
    other.title = "Something else"
    rid = db.add_release(conn, other)
    conn.commit()
    ranking.rescore_inbox(conn, crit)
    reasons = db.get_release(conn, rid)["score_reasons"]
    assert "label of a favourite: Example Label +2" in reasons
    assert any(x.startswith("artist of a favourite") for x in reasons)
    # a favourite doesn't boost itself
    assert not any("favourite" in x for x in db.get_release(conn, fav)["score_reasons"])


def test_tag_feed_caps_mass_uploaders(conn, crit, monkeypatch):
    feed = {"results": [{"item_url": f"https://dub.bandcamp.com/album/r{i}", "band_name": "MASSUPLOADER",
                         "release_date": "2027-01-01 00:00:00 UTC", "is_album_preorder": False,
                         "title": f"R{i}"} for i in range(8)]
                       + [{"item_url": "https://spam.bandcamp.com/album/x", "band_name": "Spam Co",
                           "release_date": "2026-09-20 00:00:00 UTC", "is_album_preorder": False,
                           "title": "X"}]}
    crit["sources"]["tag_feeds_enabled"] = True
    crit["sources"]["tag_feeds"] = ["deep-house"]
    crit["filters"]["exclude_accounts"] = ["spam co"]
    fetched = []

    def fake_get(url, use_cache=True, timeout=30):
        fetched.append(url)
        # distinct URL + title per fake release
        r = fixture_text("release_album.html").replace(ALBUM_URL, url).replace("First EP", url[-2:])
        r = r.replace("2927010148", str(1000 + sum(map(ord, url))))  # its own Bandcamp id
        return fetch.Page(url, url, r, False)

    monkeypatch.setattr(fetch, "post_json", lambda url, body, use_cache=True: feed)
    monkeypatch.setattr(fetch, "get", fake_get)
    added, errors = tag_feeds.run(conn, crit)
    assert errors == [] and added == 3 and len(fetched) == 3
    assert conn.execute("SELECT outcome FROM seen_urls WHERE url LIKE '%spam%'").fetchone()[0] == "excluded_account"


def test_track_artists_only_count_on_small_releases():
    small = ranking.release_artists({"artist": "Various Artists"}, ["Fouk - Kill Frenzy", "Kerri Chandler - Rain"])
    assert small == {"fouk", "kerri chandler"}
    big = ranking.release_artists({"artist": "DJ Tools"}, [f"Famous {i} - Hit" for i in range(20)])
    assert big == {"dj tools"}


# --- label pages ---------------------------------------------------------------

def test_label_account_url():
    from dig.collectors.label_pages import account_url
    assert account_url("example-label") == "https://example-label.bandcamp.com"
    assert account_url("Example-Label.bandcamp.com") == "https://example-label.bandcamp.com"
    assert account_url("https://example-label.bandcamp.com/album/x?y=1") == "https://example-label.bandcamp.com"
    assert account_url("https://music.somelabel.com/music") == "https://music.somelabel.com"
    assert criteria.validate({"sources": {"labels": ["example-label", "https://example-label.bandcamp.com/music"]}}) \
        ["sources"]["labels"] == ["https://example-label.bandcamp.com"]


def test_label_pages_collector(conn, crit, monkeypatch):
    from dig.collectors import label_pages
    crit["sources"]["labels"] = ["example-label"]
    requested = []

    def fake_get(url, use_cache=True, timeout=30):
        requested.append(url)
        page = "label_music.html" if url.endswith("/music") else "release_album.html"
        return fetch.Page(url, url, fixture_text(page), False)

    monkeypatch.setattr(fetch, "get", fake_get)
    added, errors = label_pages.run(conn, crit, per_label=2)
    assert (added, errors) == (1, [])  # both URLs serve the same saved album -> 1 added, 1 duplicate
    assert requested[0] == "https://example-label.bandcamp.com/music"
    r = db.list_releases(conn, "inbox")[0]
    assert r["source"] == "label_page" and r["found_in"] == ["label:example-label"]
    ranking.rescore_inbox(conn, crit)
    assert db.get_release(conn, r["id"])["score_reasons"][0] == "watched label: example-label +3"
