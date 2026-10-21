"""Two inboxes, each fed by its own list of reference tracks."""
import pytest

from conftest import fixture_text
from dig import bandcamp, criteria, db, fetch, ranking
from dig.collectors import recommendations, references
from test_references import REF, _fake_web, _page

REF2 = "https://example-artist.bandcamp.com/track/second-reference"
ALBUM_URL = "https://example-label.bandcamp.com/album/first-ep"


@pytest.fixture
def crit():
    c = criteria._merge(criteria.DEFAULTS, {})
    c["sources"]["reference_buyers"] = 10
    return c


def test_each_reference_list_feeds_its_own_inbox(conn, crit, monkeypatch):
    house = "https://a.bandcamp.com/album/house-thing"
    breaks = "https://b.bandcamp.com/album/breaks-thing"
    ref_html = fixture_text("release_track_with_buyers.html").replace('<li class="recommended-album', '<li class="x')
    pages = {REF: ref_html, REF2: ref_html.replace("Reference Track", "Second Reference"),
             house: _page(house, "House Thing", ["house"]), breaks: _page(breaks, "Breaks Thing", ["house"])}
    crit["sources"]["reference_tracks"] = [REF]
    crit["sources"]["reference_tracks_2"] = [REF2]

    def fake_buyers(url, want, errors):
        fans = [1, 2] if url == REF else [3, 4] if url == REF2 else []
        return fans, bandcamp.parse_supporters(ref_html, url) | {"album_url": None}

    _fake_web(monkeypatch, {1: [house], 2: [house], 3: [breaks], 4: [breaks]}, pages)
    monkeypatch.setattr(references, "buyers", fake_buyers)
    added, errors = references.run(conn, crit)
    assert (added, errors) == (2, [])
    assert [r["bandcamp_url"] for r in db.list_releases(conn, "inbox")] == [house]
    assert [r["bandcamp_url"] for r in db.list_releases(conn, "inbox2")] == [breaks]
    assert db.counts(conn)["inbox"] == 1 and db.counts(conn)["inbox2"] == 1
    ranking.rescore_inbox(conn, crit)                       # both inboxes get scored
    assert db.list_releases(conn, "inbox2")[0]["score"] > 0


def test_shared_views_cover_both_inboxes(conn):
    r = bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "reference")
    rid = db.add_release(conn, r, inbox=2)
    conn.commit()
    db.update_release(conn, rid, {"is_favourite": True})
    assert [x["id"] for x in db.list_releases(conn, "favourites")] == [rid]
    db.update_release(conn, rid, {"is_removed": True})
    assert db.list_releases(conn, "inbox2") == []
    assert [x["id"] for x in db.list_releases(conn, "removed")] == [rid]
    db.update_release(conn, rid, {"is_removed": False})      # restore goes back to its own inbox
    assert [x["id"] for x in db.list_releases(conn, "inbox2")] == [rid] and db.list_releases(conn, "inbox") == []


def test_recommendations_follow_the_favourites_inbox(conn, crit, monkeypatch):
    fav = db.add_release(conn, bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "reference"),
                         inbox=2)
    conn.commit()
    db.update_release(conn, fav, {"is_favourite": True})
    monkeypatch.setattr(fetch, "get", lambda url, use_cache=True, timeout=30: fetch.Page(
        url, url, fixture_text("release_album.html" if url == ALBUM_URL else "release_track.html"), False))
    assert recommendations.run(conn, crit)[0] == 1
    rec = next(r for r in db.list_releases(conn, "inbox2") if r["source"] == "recommendation")
    assert rec["inbox"] == 2


def test_inbox_names_and_second_list_in_settings():
    c = criteria.validate({"sources": {"reference_tracks_2": [REF2 + "?x=1"], "inbox_names": [" House ", ""]}})
    assert c["sources"]["reference_tracks_2"] == [REF2]
    assert c["sources"]["inbox_names"] == ["House", "Inbox 2"]
    text = criteria.render(c)
    assert "reference_tracks_2:" in text and REF2 in text
    import yaml
    assert criteria.validate(yaml.safe_load(text))["sources"]["inbox_names"] == ["House", "Inbox 2"]
