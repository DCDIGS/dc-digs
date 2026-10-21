"""Reference tracks: buyers of a reference -> what several of them also own."""
import json

import pytest

from conftest import FIXTURES, fixture_text
from dig import bandcamp, criteria, db, fetch, ranking
from dig.collectors import references

REF = "https://third-artist.bandcamp.com/track/reference-track"
ALBUM_URL = "https://example-label.bandcamp.com/album/first-ep"


@pytest.fixture
def crit():
    c = criteria._merge(criteria.DEFAULTS, {})
    c["sources"]["reference_tracks"] = [REF]
    c["sources"]["reference_buyers"] = 10
    return c


def test_parse_supporters_real_track_page():
    info = bandcamp.parse_supporters(fixture_text("release_track_with_buyers.html"), REF)
    assert info["tralbum_type"] == "t" and info["tralbum_id"] == 3639478872
    assert len(info["fans"]) == 23 and all(isinstance(f["fan_id"], int) for f in info["fans"])
    assert info["more"] is False
    assert info["album_url"] == "https://third-artist.bandcamp.com/album/reference-ep"


def test_collection_fixture_shape():
    data = json.loads((FIXTURES / "fan_collection.json").read_text())
    assert all(i["item_url"].startswith("https://") for i in data["items"])


def test_shared_tags_ignores_broad_ones():
    assert references.shared_tags(["Electronic", "minimal"], ["electronic", "house"]) == set()
    assert references.shared_tags(["Deep-House", "electronic"], ["deep house"]) == {"deep house"}


def test_reference_url_validation():
    c = criteria.validate({"sources": {"reference_tracks": [REF + "?from=x", "third-artist.bandcamp.com/track/reference-track"]}})
    assert c["sources"]["reference_tracks"] == [REF]
    with pytest.raises(criteria.CriteriaError, match="track or album link"):
        criteria.validate({"sources": {"reference_tracks": ["https://third-artist.bandcamp.com"]}})


def _fake_web(monkeypatch, collections: dict[int, list[str]], pages: dict[str, str]):
    """Buyers 1..n of the reference; `collections[fan]` = release URLs they own."""
    ref_page = bandcamp.parse_supporters  # keep the real parser, fake only the network

    def fake_get(url, use_cache=True, timeout=30):
        if url in pages:
            return fetch.Page(url, url, pages[url], False)
        raise AssertionError(f"unexpected fetch {url}")

    def fake_post(url, body, use_cache=True, timeout=30, ttl=None, cache_key=None):
        assert url == references.COLLECTION_API
        return {"items": [{"item_url": u} for u in collections[body["fan_id"]]]}

    monkeypatch.setattr(fetch, "get", fake_get)
    monkeypatch.setattr(fetch, "post_json", fake_post)
    monkeypatch.setattr(references, "buyers", lambda url, want, errors: (
        list(collections) if url == REF else [], ref_page(pages[REF], REF) | {"album_url": None}))


def _page(url, title, tags, artist=None):
    """The saved album page, re-labelled as another release (own artist, title, tags)."""
    html = fixture_text("release_album.html").replace(ALBUM_URL, url).replace("First EP", title)
    html = html.replace("Example Artist", artist or f"Artist of {title}")
    html = html.replace("2927010148", str(1000 + sum(map(ord, url))))  # its own Bandcamp id
    return html.replace('"keywords":["Electronic"', '"keywords":[' + ",".join(f'"{t}"' for t in tags) + ',"Electronic"')


def test_reference_collector(conn, crit, monkeypatch):
    shared = "https://a.bandcamp.com/album/shared"      # owned by 3 buyers, shares a tag
    offgenre = "https://b.bandcamp.com/album/metal"      # owned by 2, no shared tag -> skipped
    lonely = "https://c.bandcamp.com/album/lonely"       # owned by 1 -> below min_buyers
    many = "https://d.bandcamp.com/album/many"           # owned by 4 -> genre check skipped
    collections = {1: [shared, offgenre, lonely, many], 2: [shared, offgenre, many],
                   3: [shared, many, REF], 4: [many]}
    ref_html = fixture_text("release_track_with_buyers.html")
    ref_tags = bandcamp.parse_release(ref_html, REF).genres
    pages = {REF: ref_html.replace('<li class="recommended-album', '<li class="x')}  # no recs here
    pages[shared] = _page(shared, "Shared", [ref_tags[3]])
    pages[offgenre] = _page(offgenre, "Metal", ["black metal"]).replace('"breaks",', "").replace('"house",', "") \
        .replace('"minimal techno",', "").replace('"minimal",', "").replace('"rominimal",', "")
    pages[many] = _page(many, "Many", ["noise"])
    _fake_web(monkeypatch, collections, pages)

    added, errors = references.run(conn, crit)
    assert errors == []
    urls = {r["bandcamp_url"]: r for r in db.list_releases(conn, "inbox")}
    assert set(urls) == {shared, many}
    assert conn.execute("SELECT outcome FROM seen_urls WHERE url = ?", (offgenre,)).fetchone()[0] == "other_genre"
    assert urls[shared]["found_in"] == ["ref:3:Third Artist – Reference Track"]

    ranking.rescore_inbox(conn, crit)
    reasons = db.get_release(conn, urls[many]["id"])["score_reasons"]
    assert "owned by 4 buyers of Third Artist – Reference Track +4" in reasons
    assert db.get_release(conn, urls[many]["id"])["score"] > db.get_release(conn, urls[shared]["id"])["score"]


def test_removed_releases_stay_out(conn, crit, monkeypatch):
    shared = "https://a.bandcamp.com/album/shared"
    db.mark_seen(conn, shared, "removed_by_user")
    conn.commit()
    ref_html = fixture_text("release_track_with_buyers.html").replace('<li class="recommended-album', '<li class="x')
    _fake_web(monkeypatch, {1: [shared], 2: [shared]}, {REF: ref_html})
    assert references.run(conn, crit) == (0, [])


def test_max_two_releases_per_artist(conn, crit, monkeypatch):
    urls = [f"https://same.bandcamp.com/album/r{i}" for i in range(4)] + ["https://other.bandcamp.com/album/x"]
    ref_html = fixture_text("release_track_with_buyers.html").replace('<li class="recommended-album', '<li class="x')
    pages = {REF: ref_html}
    for i, u in enumerate(urls[:4]):
        pages[u] = _page(u, f"Same {i}", ["house"], artist="Same Artist")
    pages[urls[4]] = _page(urls[4], "Other", ["house"])
    _fake_web(monkeypatch, {1: urls, 2: urls}, pages)

    added, errors = references.run(conn, crit)
    assert errors == [] and added == 3
    artists = [r["artist"] for r in db.list_releases(conn, "inbox")]
    assert artists.count("Same Artist") == 2
    # the capped ones aren't blocked for good: another reference may still bring them in
    assert conn.execute("SELECT COUNT(*) FROM seen_urls WHERE url LIKE 'https://same%'").fetchone()[0] == 2
    # and a second run doesn't add two more of the same artist
    assert references.run(conn, crit) == (0, [])


def test_ref_marker_replaced_not_duplicated(conn, crit, monkeypatch):
    many = "https://d.bandcamp.com/album/many"
    ref_html = fixture_text("release_track_with_buyers.html").replace('<li class="recommended-album', '<li class="x')
    pages = {REF: ref_html, many: _page(many, "Many", ["house"])}
    _fake_web(monkeypatch, {1: [many], 2: [many]}, pages)
    references.run(conn, crit)
    _fake_web(monkeypatch, {1: [many], 2: [many], 3: [many]}, pages)  # a third buyer next week
    references.run(conn, crit)
    r = db.list_releases(conn, "inbox")[0]
    assert r["found_in"] == ["ref:3:Third Artist – Reference Track"]


def test_known_candidates_dont_use_up_the_budget(conn, crit, monkeypatch):
    crit["sources"]["reference_max_new"] = 1   # budget: 3 page fetches
    seen = [f"https://s{i}.bandcamp.com/album/x" for i in range(5)]
    for u in seen:
        db.mark_seen(conn, u, "other_genre")
    conn.commit()
    fresh = "https://fresh.bandcamp.com/album/new"
    ref_html = fixture_text("release_track_with_buyers.html").replace('<li class="recommended-album', '<li class="x')
    pages = {REF: ref_html, fresh: _page(fresh, "Fresh", ["house"])}
    # the five known ones are shared by more buyers, so they come first
    _fake_web(monkeypatch, {1: seen + [fresh], 2: seen + [fresh], 3: seen}, pages)
    assert references.run(conn, crit) == (1, [])


def test_gone_pages_are_remembered_not_reported(conn, crit, monkeypatch):
    import requests as rq
    gone = "https://gone.bandcamp.com/album/deleted"
    ref_html = fixture_text("release_track_with_buyers.html").replace('<li class="recommended-album', '<li class="x')
    _fake_web(monkeypatch, {1: [gone], 2: [gone]}, {REF: ref_html})
    real_get = fetch.get

    def get(url, use_cache=True, timeout=30):
        if url == gone:
            resp = rq.Response()
            resp.status_code = 404
            raise rq.HTTPError(response=resp)
        return real_get(url, use_cache, timeout)

    monkeypatch.setattr(fetch, "get", get)
    assert references.run(conn, crit) == (0, [])
    assert conn.execute("SELECT outcome FROM seen_urls WHERE url = ?", (gone,)).fetchone()[0] == "gone"
