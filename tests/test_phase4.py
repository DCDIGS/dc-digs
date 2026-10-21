"""Settings (criteria editing), collector lock, waveform, next run, removed endpoints."""
import pytest
import yaml

from conftest import fixture_text
from dig import bandcamp, collect, config, criteria, db, web

ALBUM_URL = "https://example-label.bandcamp.com/album/first-ep"


@pytest.fixture
def crit_file(tmp_path, monkeypatch):
    path = tmp_path / "criteria.yaml"
    monkeypatch.setattr(criteria, "PATH", path)
    return path


@pytest.fixture
def client(conn):
    web.app.config["TESTING"] = True
    with web.app.test_client() as c:
        yield c


def _add(conn, **flags):
    r = bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "bandcamp_follow")
    rid = db.add_release(conn, r)
    conn.commit()
    if flags:
        db.update_release(conn, rid, flags)
    return rid


# --- criteria editing -----------------------------------------------------------

def test_validate_normalises_links_and_names():
    c = criteria.validate({
        "sources": {"tag_feeds": ["https://bandcamp.com/discover/Lo-Fi House?s=new", "deep house", "deep-house"]},
        "filters": {"max_age_days": "45", "exclude_tags": ["EDM"]},
    })
    assert c["sources"]["tag_feeds"] == ["lo-fi-house", "deep-house"]
    assert c["filters"]["max_age_days"] == 45
    assert c["filters"]["exclude_tags"] == ["edm"]


@pytest.mark.parametrize("bad,msg", [
    ({"filters": {"max_age_days": 0}}, "max_age_days"),
    ({"filters": {"max_age_days": "soon"}}, "whole number"),
    ({"filters": {"formats": ["EP", "cassette"]}}, "cassette"),
    ({"ranking": {"boost_tags": ["x"]}}, "mappings"),
    ({"sources": {"tag_feed_size": 500}}, "tag_feed_size"),
])
def test_validate_rejects(bad, msg):
    with pytest.raises(criteria.CriteriaError, match=msg):
        criteria.validate(bad)


def test_save_renders_file_that_loads_back_the_same(crit_file):
    saved = criteria.save({"sources": {"tag_feeds": ["minimal"]},
                           "ranking": {"boost_tags": {"minimal": 2}}})
    text = crit_file.read_text()
    assert text.startswith("# dig/inbox")
    assert "tag_feeds: [minimal]" in text
    assert criteria.load() == saved


def test_save_raw_keeps_comments_and_rejects_bad_yaml(crit_file):
    raw = "# my own note\nsources:\n  tag_feeds: [breaks]  # keep this comment\n"
    criteria.save_raw(raw)
    assert crit_file.read_text() == raw
    assert criteria.load()["sources"]["tag_feeds"] == ["breaks"]
    with pytest.raises(criteria.CriteriaError, match="YAML error"):
        criteria.save_raw("sources:\n  tag_feeds: [unclosed\n")
    assert crit_file.read_text() == raw  # unchanged after a failed save


def test_current_criteria_file_roundtrips():
    current = criteria.load()
    assert criteria.validate(yaml.safe_load(criteria.render(criteria.validate(current)))) == \
        criteria.validate(current)


def test_criteria_api(client, crit_file, monkeypatch):
    res = client.put("/api/criteria", json={"criteria": {"filters": {"max_age_days": 60}}})
    assert res.status_code == 200
    assert res.get_json()["criteria"]["filters"]["max_age_days"] == 60
    assert client.put("/api/criteria", json={"criteria": {"filters": {"max_age_days": -1}}}).status_code == 400
    assert client.get("/api/criteria").get_json()["criteria"]["filters"]["max_age_days"] == 60


def test_old_nts_settings_are_dropped():
    c = criteria.validate({"sources": {"nts_shows": ["four-tet"]},
                           "ranking": {"boost_played_on_nts": 2, "source_weight": {"nts": 2}}})
    assert "nts_shows" not in c["sources"] and "boost_played_on_nts" not in c["ranking"]
    assert "nts" not in c["ranking"]["source_weight"]
    import re
    assert not re.search(r"\bnts", criteria.render(c), re.I)


# --- collector lock ----------------------------------------------------------------

def test_collect_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "LOCK_PATH", tmp_path / "collect.lock")
    held = collect._lock()
    assert held is not None
    assert collect.is_running()
    assert collect._lock() is None
    held.close()
    assert not collect.is_running()


# --- waveform ----------------------------------------------------------------------

def _track_id(conn):
    rid = _add(conn)
    return db.get_release(conn, rid)["tracks"][0]["id"]


def test_peaks_saved_and_returned(conn, client):
    tid = _track_id(conn)
    assert client.put(f"/api/tracks/{tid}/peaks", json={"peaks": [0.5] * 120}).status_code == 200
    track = db.get_release(conn, 1)["tracks"][0]
    assert track["peaks"] == [0.5] * 120


@pytest.mark.parametrize("bad", [[0.5] * 5, [2.0] * 120, ["x"] * 120, "nope"])
def test_peaks_validation(conn, client, bad):
    tid = _track_id(conn)
    assert client.put(f"/api/tracks/{tid}/peaks", json={"peaks": bad}).status_code == 400


class _FakeUpstream:
    def __init__(self, status, body, headers):
        self.status_code, self._body, self.headers, self.closed = status, body, headers, False

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


def test_audio_proxy_relays_range_without_touching_disk(conn, client, monkeypatch, tmp_path):
    tid = _track_id(conn)
    monkeypatch.setattr(web, "_resolve_stream", lambda track_id: ("https://t4.bcbits.com/stream/x", None))
    seen = {}

    def fake_get(url, headers, stream, timeout):
        seen.update(headers)
        return _FakeUpstream(206, b"\xff\xfb" * 50, {"Content-Type": "audio/mpeg", "Content-Length": "100",
                                                     "Content-Range": "bytes 0-99/6000000", "Set-Cookie": "x"})

    monkeypatch.setattr(web.requests, "get", fake_get)
    before = {p for p in config.ROOT.rglob("*") if ".venv" not in p.parts}
    res = client.get(f"/api/tracks/{tid}/audio", headers={"Range": "bytes=0-99"})
    assert res.status_code == 206
    assert res.data == b"\xff\xfb" * 50
    assert seen["Range"] == "bytes=0-99"
    assert res.headers["Content-Range"] == "bytes 0-99/6000000"
    assert "Set-Cookie" not in res.headers and res.headers["Cache-Control"] == "no-store"
    after = {p for p in config.ROOT.rglob("*") if ".venv" not in p.parts}
    assert not {p for p in after - before if p.suffix not in (".pyc",)}, "the proxy wrote files"


def test_audio_proxy_unstreamable(conn, client):
    tid = _track_id(conn)
    conn.execute("UPDATE tracks SET streamable = 0 WHERE id = ?", (tid,))
    conn.commit()
    assert client.get(f"/api/tracks/{tid}/audio").status_code == 409


def test_waveform_setting(client):
    assert client.put("/api/settings", json={"waveform": False}).get_json()["waveform"] == "0"
    assert client.put("/api/settings", json={"waveform": True}).get_json()["waveform"] == "1"


# --- audit fixes -------------------------------------------------------------------

def test_foreign_host_is_refused(client):
    assert client.get("/api/meta").status_code == 200
    assert client.get("/api/meta", headers={"Host": "evil.example:8765"}).status_code == 403
    assert client.get("/api/meta", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_writes_must_be_json(conn, client):
    rid = _add(conn)
    # a cross-site form or text/plain fetch needs no CORS preflight; refuse it
    assert client.post("/api/collect", data="x", content_type="text/plain").status_code == 415
    assert client.patch(f"/api/releases/{rid}", data='{"is_removed": true}',
                        content_type="text/plain").status_code == 415
    assert client.patch(f"/api/releases/{rid}", json=["not", "an", "object"]).status_code == 400
    assert db.get_release(conn, rid)["is_removed"] is False


def test_bad_settings_value_is_400(client):
    assert client.put("/api/settings", json={"start_at": "half"}).status_code == 400


# --- next run ------------------------------------------------------------------------

def _crit(daily_at="06:00"):
    c = criteria._merge(criteria.DEFAULTS, {})
    c["schedule"]["daily_at"] = daily_at
    return c


def test_next_run_is_the_daily_time():
    from datetime import datetime, timezone
    from dig import schedule
    tz = timezone.utc
    assert schedule.next_run(datetime(2026, 9, 24, 5, 30, tzinfo=tz), _crit()) == datetime(2026, 9, 24, 6, 0, tzinfo=tz)
    assert schedule.next_run(datetime(2026, 9, 24, 6, 0, tzinfo=tz), _crit()) == datetime(2026, 9, 25, 6, 0, tzinfo=tz)
    assert schedule.next_run(datetime(2026, 9, 24, 20, 15, tzinfo=tz), _crit("21:30")) == \
        datetime(2026, 9, 24, 21, 30, tzinfo=tz)


def test_daily_run_is_due_once_and_catches_up(conn):
    from datetime import datetime, timezone
    from dig import schedule
    tz = timezone.utc
    morning, noon = datetime(2026, 9, 24, 5, 0, tzinfo=tz), datetime(2026, 9, 24, 12, 0, tzinfo=tz)
    assert not schedule.is_due(conn, noon, _crit())             # never before the setup wizard
    db.set_setting(conn, "setup_done", "1")
    assert not schedule.is_due(conn, morning, _crit())          # not yet 06:00
    assert schedule.is_due(conn, noon, _crit())                 # computer was off at 06:00: catch up
    conn.execute("INSERT INTO runs (source, started_at) VALUES ('reference', '2026-09-24T06:00:30+00:00')")
    conn.commit()
    assert not schedule.is_due(conn, noon, _crit())             # it ran today


def test_daily_time_validation():
    assert criteria.validate({"schedule": {"daily_at": "7:05"}})["schedule"]["daily_at"] == "07:05"
    with pytest.raises(criteria.CriteriaError):
        criteria.validate({"schedule": {"daily_at": "25:00"}})


# --- setup wizard -------------------------------------------------------------------

def test_setup_wizard(client, crit_file, monkeypatch):
    started = []
    monkeypatch.setattr(collect, "start_in_background", lambda: started.append(1))
    monkeypatch.setattr(web, "_check_fan_page", lambda name: None if name == "realfan" else "no such fan")
    assert client.get("/api/meta").get_json()["setup_needed"] is True
    # nothing to dig from
    assert client.post("/api/setup", json={"reference_tracks": []}).status_code == 400
    # a fan name that doesn't exist
    res = client.post("/api/setup", json={"bandcamp_username": "nobody", "reference_tracks": []})
    assert res.status_code == 400 and "no such fan" in res.get_json()["error"]
    # a good setup
    res = client.post("/api/setup", json={
        "bandcamp_username": "https://bandcamp.com/realfan", "inbox_names": ["House", "Breaks"],
        "reference_tracks": ["https://a.bandcamp.com/track/x?from=y"], "reference_tracks_2": [],
        "daily_at": "7:30"})
    assert res.get_json() == {"ok": True, "collecting": True} and started == [1]
    c = criteria.load()
    assert c["sources"]["bandcamp_username"] == "realfan"
    assert c["sources"]["reference_tracks"] == ["https://a.bandcamp.com/track/x"]
    assert c["sources"]["inbox_names"] == ["House", "Breaks"] and c["schedule"]["daily_at"] == "07:30"
    assert client.get("/api/meta").get_json()["setup_needed"] is False
    assert client.get("/api/setup").get_json()["bandcamp_username"] == "realfan"


def test_meta_has_next_run(client):
    body = client.get("/api/meta").get_json()
    assert "next_run" in body and "collecting" in body


def test_autonext_setting(client):
    assert client.get("/api/meta").get_json()["settings"]["autonext"] == "1"   # on by default
    assert client.put("/api/settings", json={"autonext": False}).get_json()["autonext"] == "0"
    assert client.put("/api/settings", json={"autonext": True}).get_json()["autonext"] == "1"


def test_crate_and_digest_are_gone(client):
    for path in ("/api/crate/export?format=txt", "/api/digest", "/digest"):
        assert client.get(path).status_code == 404
    assert "crate" not in client.get("/api/meta").get_json()["counts"]
