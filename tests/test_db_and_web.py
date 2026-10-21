import pytest

from conftest import fixture_text
from dig import bandcamp, criteria, db, fetch, web
from dig.collectors import bandcamp_follows

ALBUM_URL = "https://example-label.bandcamp.com/album/first-ep"


def _crit(max_age_days):
    c = criteria._merge(criteria.DEFAULTS, {})
    c["filters"]["max_age_days"] = max_age_days
    return c


def _add_album(conn):
    r = bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "bandcamp_follow")
    rid = db.add_release(conn, r)
    conn.commit()
    return rid


def test_add_release_dedups_by_url_and_artist_title(conn):
    assert _add_album(conn) is not None
    assert _add_album(conn) is None  # same URL
    r = bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "tag_feed")
    r.bandcamp_url = "https://elsewhere.bandcamp.com/album/wz016"
    assert db.add_release(conn, r) is None  # same artist + title from another source
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3


def test_favourite_survives_removal_and_restore(conn):
    rid = _add_album(conn)
    db.update_release(conn, rid, {"is_favourite": True})
    db.update_release(conn, rid, {"is_removed": True})
    assert [r["id"] for r in db.list_releases(conn, "favourites")] == [rid]
    assert [r["id"] for r in db.list_releases(conn, "removed")] == [rid]
    assert db.list_releases(conn, "inbox") == []
    db.update_release(conn, rid, {"is_removed": False})
    r = db.get_release(conn, rid)
    assert r["is_favourite"] and not r["is_removed"] and r["removed_at"] is None
    assert db.counts(conn) == {"inbox": 1, "inbox2": 0, "favourites": 1, "removed": 0}


def test_favouriting_queues_recommendations(conn):
    rid = _add_album(conn)
    db.update_release(conn, rid, {"is_favourite": True})
    assert conn.execute("SELECT release_id FROM recommendation_queue").fetchall()[0][0] == rid


def test_collector_with_saved_pages(conn, monkeypatch):
    pages = {
        "https://bandcamp.com/examplefan": fixture_text("fan_page.html"),
    }
    listing = fixture_text("label_music.html")
    album = fixture_text("release_album.html")
    requested = []

    def fake_get(url, use_cache=True, timeout=30):
        requested.append(url)
        if url in pages:
            text = pages[url]
        elif url.endswith("/music"):
            text = listing
        else:
            text = album  # every release page is the same album -> dedup kicks in
        return fetch.Page(url, url, text, False)

    monkeypatch.setattr(fetch, "get", fake_get)
    added, errors = bandcamp_follows.run(conn, _crit(10_000), username="examplefan", per_follow=2)
    assert errors == []
    assert added == 1
    outcomes = dict(conn.execute("SELECT outcome, COUNT(*) FROM seen_urls GROUP BY outcome").fetchall())
    # 3 follows x 2 URLs, all the same album: one added, the rest caught as duplicates
    assert outcomes == {"added": 1, "duplicate": 5}
    run = db.last_run(conn)
    assert run["finished_at"] and run["items_found"] == 1

    # A second run fetches no release pages at all.
    requested.clear()
    added, _ = bandcamp_follows.run(conn, _crit(10_000), username="examplefan", per_follow=2)
    assert added == 0
    assert not any("/album/" in u for u in requested)


def test_collector_skips_old_releases(conn, monkeypatch):
    monkeypatch.setattr(fetch, "get", lambda url, use_cache=True, timeout=30: fetch.Page(
        url, url, fixture_text("fan_page.html") if url.endswith("examplefan")
        else fixture_text("label_music.html") if url.endswith("/music")
        else fixture_text("release_track.html"), False))  # released 2026-02-04
    added, errors = bandcamp_follows.run(conn, _crit(1), username="examplefan", per_follow=1)
    assert added == 0 and errors == []
    assert conn.execute("SELECT outcome FROM seen_urls").fetchone()[0] == "too_old"


@pytest.fixture
def client(conn):
    web.app.config["TESTING"] = True
    with web.app.test_client() as c:
        yield c


def test_api_flags_and_note(conn, client):
    rid = _add_album(conn)
    res = client.patch(f"/api/releases/{rid}", json={"is_favourite": True, "note": "opener"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["release"]["is_favourite"] is True
    assert body["release"]["note"] == "opener"
    assert body["counts"]["favourites"] == 1
    assert client.get("/api/releases?view=favourites").get_json()["releases"][0]["id"] == rid
    assert client.get("/api/releases?view=nope").status_code == 400


def test_stream_endpoint_resolves_fresh_url(conn, client, monkeypatch):
    rid = _add_album(conn)
    track_id = db.get_release(conn, rid)["tracks"][1]["id"]
    calls = []

    def fake_get(url, use_cache=True, timeout=30):
        calls.append((url, use_cache))
        return fetch.Page(url, url, fixture_text("release_album.html"), False)

    monkeypatch.setattr(fetch, "get", fake_get)
    web._stream_cache.clear()
    res = client.get(f"/api/tracks/{track_id}/stream")
    assert res.status_code == 200
    assert "/mp3-128/1496176132" in res.get_json()["url"]
    assert calls == [(ALBUM_URL, False)]  # page fetched fresh, not from the day cache


def test_stream_endpoint_unstreamable(conn, client):
    rid = _add_album(conn)
    track_id = db.get_release(conn, rid)["tracks"][0]["id"]
    conn.execute("UPDATE tracks SET streamable = 0 WHERE id = ?", (track_id,))
    conn.commit()
    res = client.get(f"/api/tracks/{track_id}/stream")
    assert res.status_code == 409
    assert res.get_json() == {"streamable": False, "bandcamp_url": ALBUM_URL}


def test_settings_validation(client):
    assert client.put("/api/settings", json={"start_at": 0.3}).get_json()["start_at"] == "0.3"
    assert client.put("/api/settings", json={"start_at": 1.5}).status_code == 400
