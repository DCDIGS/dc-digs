"""Web UI + JSON API on http://localhost:8765 — python -m dig.web"""
import http.server
import logging
import socketserver
import sys
import threading
import time
from urllib.parse import urlsplit

import requests
from flask import Flask, Response, abort, g, jsonify, request, send_from_directory, stream_with_context

from . import bandcamp, collect, config, criteria, db, fetch, ranking, schedule

app = Flask(__name__, static_folder=None)

# Stream URLs are valid for ~24 h after the page is fetched. Reuse a freshly fetched
# page's URLs for an hour so skipping between tracks of one release is instant.
STREAM_URL_TTL = 3600
_stream_cache: dict[str, tuple[float, dict[int, str]]] = {}
_stream_lock = threading.Lock()


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@app.before_request
def _only_local_pages():
    """Refuse requests that didn't come from this app's own pages: a foreign Host header
    (DNS rebinding) or a write that isn't JSON (another site's plain form/fetch can send
    text/plain cross-origin without the browser asking first)."""
    if urlsplit("//" + (request.host or "")).hostname not in LOCAL_HOSTS:
        abort(403)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.is_json:
        abort(415, "send JSON")


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object")
    return data


def conn():
    if "conn" not in g:
        g.conn = db.connect()
    return g.conn


@app.teardown_appcontext
def _close(_exc):
    c = g.pop("conn", None)
    if c is not None:
        c.close()


@app.get("/")
def index():
    return send_from_directory(config.STATIC_DIR, "index.html")


@app.get("/settings")
def settings_page():
    return send_from_directory(config.STATIC_DIR, "settings.html")


@app.get("/setup")
def setup_page():
    return send_from_directory(config.STATIC_DIR, "setup.html")


@app.get("/static/<path:name>")
def static_file(name):
    return send_from_directory(config.STATIC_DIR, name)


@app.get("/api/releases")
def releases():
    view = request.args.get("view", "inbox")
    if view not in db.VIEWS:
        abort(400, "unknown view")
    ranking.rescore_if_criteria_changed(conn())
    return jsonify(releases=db.list_releases(conn(), view), counts=db.counts(conn()))


@app.get("/api/releases/<int:release_id>")
def release(release_id):
    r = db.get_release(conn(), release_id)
    if r is None:
        abort(404)
    return jsonify(r)


@app.patch("/api/releases/<int:release_id>")
def update_release(release_id):
    if db.get_release(conn(), release_id) is None:
        abort(404)
    changes = _json_body()
    db.update_release(conn(), release_id, changes)
    if "is_favourite" in changes:  # favourites' labels and artists feed the ranking
        ranking.rescore_inbox(conn())
    return jsonify(release=db.get_release(conn(), release_id), counts=db.counts(conn()))


def _resolve_stream(track_id: int):
    """(stream_url, None) or (None, (json_body, status)). Reads the release page fresh
    (stream URLs expire), reusing a page fetched within the last hour."""
    t = db.track_for_stream(conn(), track_id)
    if t is None:
        abort(404)
    fallback = {"streamable": False, "bandcamp_url": t["bandcamp_url"]}
    if not t["streamable"] or not t["bandcamp_track_id"]:
        return None, (fallback, 409)

    page_url = t["bandcamp_url"]
    with _stream_lock:
        cached = _stream_cache.get(page_url)
    if cached and time.time() - cached[0] < STREAM_URL_TTL:
        urls = cached[1]
    else:
        try:
            urls = bandcamp.stream_urls(fetch.get(page_url, use_cache=False).text)
        except Exception as e:  # noqa: BLE001
            return None, (fallback | {"error": str(e)}, 502)
        with _stream_lock:
            now = time.time()
            for key in [k for k, (t0, _) in _stream_cache.items() if now - t0 > STREAM_URL_TTL]:
                del _stream_cache[key]  # keep the cache from growing forever
            _stream_cache[page_url] = (now, urls)

    url = urls.get(int(t["bandcamp_track_id"]))
    if not url:
        return None, (fallback | {"error": "track no longer streamable on Bandcamp"}, 409)
    return url, None


@app.get("/api/tracks/<int:track_id>/stream")
def stream(track_id):
    """The current stream URL. The browser streams straight from Bandcamp; nothing is stored."""
    url, err = _resolve_stream(track_id)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify(streamable=True, url=url)


PROXY_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")


@app.get("/api/tracks/<int:track_id>/audio")
def audio_proxy(track_id):
    """Pass the audio through (same origin, so the browser may decode it for the waveform).
    Bytes are relayed in chunks from memory; nothing is written to disk."""
    url, err = _resolve_stream(track_id)
    if err:
        return jsonify(err[0]), err[1]
    headers = {"User-Agent": config.USER_AGENT}
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    try:
        upstream = requests.get(url, headers=headers, stream=True, timeout=30)
    except requests.RequestException as e:
        return jsonify(error=f"couldn't reach Bandcamp: {e}"), 502
    if upstream.status_code not in (200, 206):
        upstream.close()
        return jsonify(error=f"Bandcamp answered {upstream.status_code}"), 502

    def relay():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()

    return Response(stream_with_context(relay()), status=upstream.status_code,
                    headers={h: upstream.headers[h] for h in PROXY_HEADERS if h in upstream.headers}
                    | {"Cache-Control": "no-store"})


@app.put("/api/tracks/<int:track_id>/peaks")
def put_peaks(track_id):
    """Store a waveform the browser computed: loudness per slice, 0..1. Not audio."""
    peaks = _json_body().get("peaks")
    if (not isinstance(peaks, list) or not 10 <= len(peaks) <= 400
            or not all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in peaks)):
        abort(400, "peaks must be 10-400 numbers between 0 and 1")
    if db.track_for_stream(conn(), track_id) is None:
        abort(404)
    db.set_peaks(conn(), track_id, [round(float(p), 3) for p in peaks])
    return jsonify(ok=True)


@app.get("/api/meta")
def meta():
    settings = db.get_settings(conn())
    return jsonify(counts=db.counts(conn()), last_run=db.last_run(conn()),
                   settings=settings,
                   setup_needed=settings.get("setup_done") != "1",
                   next_run=schedule.next_run().isoformat(),
                   collecting=collect.is_running(),
                   inbox_names=criteria.load()["sources"]["inbox_names"])


@app.get("/api/criteria")
def get_criteria():
    raw = criteria.PATH.read_text(encoding="utf-8") if criteria.PATH.exists() else ""
    return jsonify(criteria=criteria.load(), raw=raw)


def _save_criteria(saver, payload):
    try:
        after = saver(payload)
    except criteria.CriteriaError as e:
        return jsonify(error=str(e)), 400
    ranking.rescore_inbox(conn(), after)
    return jsonify(criteria=after, warnings=[], raw=criteria.PATH.read_text(encoding="utf-8"))


@app.put("/api/criteria")
def put_criteria():
    return _save_criteria(criteria.save, _json_body().get("criteria"))


@app.put("/api/criteria/raw")
def put_criteria_raw():
    return _save_criteria(criteria.save_raw, _json_body().get("raw", ""))


@app.post("/api/collect")
def start_collect():
    """Start a collector run in the background (same as the 06:00 job)."""
    if collect.is_running():
        return jsonify(started=False, reason="a collector run is already in progress"), 409
    collect.start_in_background()
    return jsonify(started=True), 202


@app.get("/api/collect")
def collect_status():
    return jsonify(running=collect.is_running())


@app.get("/api/setup")
def get_setup():
    c = criteria.load()
    return jsonify(bandcamp_username=c["sources"]["bandcamp_username"] or config.BANDCAMP_USERNAME,
                   inbox_names=c["sources"]["inbox_names"],
                   reference_tracks=c["sources"]["reference_tracks"],
                   reference_tracks_2=c["sources"]["reference_tracks_2"],
                   daily_at=c["schedule"]["daily_at"])


def _check_fan_page(username: str) -> str | None:
    """An error message if bandcamp.com/<username> doesn't exist; None if it does (or if
    Bandcamp can't be reached right now: then the first run will tell)."""
    try:
        fetch.get(f"https://bandcamp.com/{username}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return f"There's no Bandcamp fan page at bandcamp.com/{username}. Check the spelling."
    except Exception:  # noqa: BLE001 - offline: don't block the setup over it
        pass
    return None


@app.post("/api/setup")
def save_setup():
    data = _json_body()
    c = criteria.load()
    c["sources"]["bandcamp_username"] = data.get("bandcamp_username", "")
    c["sources"]["inbox_names"] = data.get("inbox_names") or c["sources"]["inbox_names"]
    c["sources"]["reference_tracks"] = data.get("reference_tracks", [])
    c["sources"]["reference_tracks_2"] = data.get("reference_tracks_2", [])
    c["schedule"]["daily_at"] = data.get("daily_at") or c["schedule"]["daily_at"]
    try:
        checked = criteria.validate(c)
    except criteria.CriteriaError as e:
        return jsonify(error=str(e)), 400
    s = checked["sources"]
    if not s["reference_tracks"] and not s["reference_tracks_2"] and not s["bandcamp_username"]:
        return jsonify(error="Add at least one reference track, or your Bandcamp name, so there's something to dig from."), 400
    if s["bandcamp_username"] and (problem := _check_fan_page(s["bandcamp_username"])):
        return jsonify(error=problem), 400
    criteria.save(checked)
    db.set_setting(conn(), "setup_done", "1")
    started = False
    if data.get("collect_now", True) and not collect.is_running():
        collect.start_in_background()
        started = True
    return jsonify(ok=True, collecting=started)


@app.get("/api/runs")
def runs():
    return jsonify(runs=db.recent_runs(conn(), 20))


@app.put("/api/settings")
def settings():
    data = _json_body()
    if "start_at" in data:
        try:
            value = float(data["start_at"])
        except (TypeError, ValueError):
            abort(400, "start_at must be a number")
        if not 0 <= value < 1:
            abort(400, "start_at must be between 0 and 1")
        db.set_setting(conn(), "start_at", str(value))
    for key in ("waveform", "autonext"):
        if key in data:
            db.set_setting(conn(), key, "1" if data[key] else "0")
    return jsonify(db.get_settings(conn()))


def _server_bind_without_fqdn(self):
    # http.server calls socket.getfqdn() on startup, a reverse-DNS lookup that can
    # hang for a long time on macOS. We only serve 127.0.0.1, so skip it.
    socketserver.TCPServer.server_bind(self)
    self.server_name, self.server_port = self.server_address[:2]


def main():
    if sys.stdout is None or sys.stderr is None:  # pythonw.exe (Windows, no console window)
        sys.stdout = sys.stderr = open(config.LOG_DIR / "web.log", "a", buffering=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    http.server.HTTPServer.server_bind = _server_bind_without_fqdn
    db.connect().close()
    schedule.start()
    print(f"DC Digs is running at http://localhost:{config.PORT}", flush=True)
    app.run(host="127.0.0.1", port=config.PORT, threaded=True)


if __name__ == "__main__":
    main()
