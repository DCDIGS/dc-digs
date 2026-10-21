"""A release you removed never comes back into the inbox, whatever way it turns up again."""
import pytest

from conftest import fixture_text
from dig import bandcamp, criteria, db, fetch
from dig.collectors import common, recommendations, references
from dig.models import Release, Track

ALBUM_URL = "https://example-label.bandcamp.com/album/first-ep"
TRACK_URL = "https://another-artist.bandcamp.com/track/single-track"
TRACK_ID = 825166777  # the track on release_track.html


@pytest.fixture
def crit():
    c = criteria._merge(criteria.DEFAULTS, {})
    c["filters"]["max_age_days"] = 100_000
    return c


def _remove(conn, rid):
    db.update_release(conn, rid, {"is_removed": True})


def _serve(monkeypatch, pages):
    monkeypatch.setattr(fetch, "get", lambda url, use_cache=True, timeout=30:
                        fetch.Page(url, url, pages[url], False))


def _collect(conn, crit, url, source="reference"):
    return common.collect_url(conn, url, crit, source, url, "test", [], ignore_age=True)


def test_same_link_is_never_added_again(conn, crit, monkeypatch):
    rid = db.add_release(conn, bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "reference"))
    conn.commit()
    _remove(conn, rid)
    _serve(monkeypatch, {ALBUM_URL: fixture_text("release_album.html")})
    assert db.is_known_url(conn, ALBUM_URL, ignore_age=True)          # collectors skip it outright
    assert _collect(conn, crit, ALBUM_URL) is None                     # and storing it again is refused
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
    assert db.get_release(conn, rid)["is_removed"] is True
    assert db.list_releases(conn, "inbox") == []


def test_same_release_under_another_link(conn, crit, monkeypatch):
    rid = db.add_release(conn, bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "reference"))
    conn.commit()
    _remove(conn, rid)
    other = "https://music.example-label.com/album/first-ep"   # e.g. the label's own domain
    html = fixture_text("release_album.html").replace(ALBUM_URL, other).replace("First EP", "First EP (repress)")
    _serve(monkeypatch, {other: html})
    assert _collect(conn, crit, other) is None                         # same Bandcamp id -> same release
    assert db.list_releases(conn, "inbox") == []


def test_track_page_of_a_removed_release(conn, crit, monkeypatch):
    album = Release(artist="Another Artist", title="Some EP", source="reference", source_url="x",
                    bandcamp_url="https://another-artist.bandcamp.com/album/some-ep", bandcamp_id=1, item_type="album",
                    tracks=[Track(1, "Single Track", 278.0, TRACK_ID, True), Track(2, "Other", 300.0, 42, True)])
    rid = db.add_release(conn, album)
    conn.commit()
    _remove(conn, rid)
    _serve(monkeypatch, {TRACK_URL: fixture_text("release_track.html")})
    assert _collect(conn, crit, TRACK_URL) is None
    assert conn.execute("SELECT outcome FROM seen_urls WHERE url = ?", (TRACK_URL,)).fetchone()[0] == "removed_by_user"
    assert db.list_releases(conn, "inbox") == []


def test_a_release_sharing_only_some_tracks_still_comes_in(conn, crit, monkeypatch):
    single = Release(artist="Someone", title="One track", source="reference", source_url="x",
                     bandcamp_url="https://x.bandcamp.com/track/one", bandcamp_id=2136434947, item_type="track",
                     tracks=[Track(1, "Track One", 375.0, 2136434947, True)])
    rid = db.add_release(conn, single)
    conn.commit()
    _remove(conn, rid)
    # the album has that track plus two others you never removed
    _serve(monkeypatch, {ALBUM_URL: fixture_text("release_album.html")})
    assert _collect(conn, crit, ALBUM_URL) is not None


def test_recommendations_and_references_skip_removed(conn, crit, monkeypatch):
    rid = db.add_release(conn, bandcamp.parse_release(fixture_text("release_track.html"), TRACK_URL, "reference"))
    conn.commit()
    _remove(conn, rid)
    # a favourite whose "you may also like" lists the removed release
    fav = db.add_release(conn, bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "bandcamp_follow"))
    conn.commit()
    db.update_release(conn, fav, {"is_favourite": True})
    page = fixture_text("release_album.html").replace(
        "https://reco-one.bandcamp.com/album/reco-1", TRACK_URL)
    _serve(monkeypatch, {ALBUM_URL: page})
    monkeypatch.setattr(common, "collect_url", lambda *a, **k: pytest.fail("fetched a removed release"))
    recommendations.run(conn, crit)
    # a reference whose buyers own it: its marker may update, it stays removed
    references.set_ref_marker(conn, rid, "Some Reference", "ref:9:Some Reference")
    conn.commit()
    r = db.get_release(conn, rid)
    assert r["is_removed"] is True and r["found_in"] == ["ref:9:Some Reference"]
    assert rid not in [x["id"] for x in db.list_releases(conn, "inbox")]


def test_restore_is_the_only_way_back(conn):
    rid = db.add_release(conn, bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "reference"))
    conn.commit()
    _remove(conn, rid)
    db.update_release(conn, rid, {"is_removed": False})
    assert [x["id"] for x in db.list_releases(conn, "inbox")] == [rid]


def test_removed_list_clears_after_a_day_but_stays_blocked(conn, crit, monkeypatch):
    rid = db.add_release(conn, bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "reference"))
    conn.commit()
    _remove(conn, rid)
    assert [x["id"] for x in db.list_releases(conn, "removed")] == [rid]
    assert db.counts(conn)["removed"] == 1
    # 25 hours later
    conn.execute("UPDATE releases SET removed_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-25 hours')")
    conn.commit()
    assert db.list_releases(conn, "removed") == [] and db.counts(conn)["removed"] == 0
    assert db.list_releases(conn, "inbox") == []                       # not back in the inbox either
    _serve(monkeypatch, {ALBUM_URL: fixture_text("release_album.html")})
    assert db.is_known_url(conn, ALBUM_URL, ignore_age=True)           # and still blocked
    assert _collect(conn, crit, ALBUM_URL) is None


def test_removed_favourite_stays_in_favourites_after_the_day(conn):
    rid = db.add_release(conn, bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "reference"))
    conn.commit()
    db.update_release(conn, rid, {"is_favourite": True, "is_removed": True})
    conn.execute("UPDATE releases SET removed_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-3 days')")
    conn.commit()
    assert [x["id"] for x in db.list_releases(conn, "favourites")] == [rid]
