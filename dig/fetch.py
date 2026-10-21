"""Polite HTTP fetching: real user agent, >=1 s between requests to the same
site, and a one-day HTML cache on disk. Only page HTML is ever cached."""
import hashlib
import json
import threading
import time
from urllib.parse import urlparse

import requests

from . import config

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT,
                         "Accept-Language": "en-GB,en;q=0.9"})
_last_request: dict[str, float] = {}
_lock = threading.Lock()


def _site(url: str) -> str:
    """artist.bandcamp.com and label.bandcamp.com are the same site for rate limiting."""
    host = urlparse(url).hostname or ""
    return ".".join(host.split(".")[-2:])


def _wait_turn(url: str) -> None:
    site = _site(url)
    with _lock:
        now = time.monotonic()
        wait = _last_request.get(site, 0) + config.MIN_DELAY_SECONDS - now
        _last_request[site] = max(now, now + wait)
    if wait > 0:
        time.sleep(wait)


def _cache_path(url: str):
    return config.CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")


def prune_cache() -> int:
    """Delete cached responses past their lifetime. Returns how many were removed."""
    removed = 0
    for path in config.CACHE_DIR.glob("*.json"):
        if time.time() - path.stat().st_mtime < config.CACHE_TTL_SECONDS:
            continue  # nothing is cached for less than a day
        try:
            ttl = json.loads(path.read_text(encoding="utf-8")).get("ttl", config.CACHE_TTL_SECONDS)
        except (OSError, ValueError):
            ttl = 0
        if time.time() - path.stat().st_mtime > ttl:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


class Page:
    def __init__(self, url: str, final_url: str, text: str, from_cache: bool):
        self.url, self.final_url, self.text, self.from_cache = url, final_url, text, from_cache


def _cached(key: str, use_cache: bool, ttl: int = config.CACHE_TTL_SECONDS):
    path = _cache_path(key)
    if use_cache and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _store(key: str, final_url: str, text: str, ttl: int = config.CACHE_TTL_SECONDS) -> None:
    _cache_path(key).write_text(json.dumps({"final_url": final_url, "text": text, "ttl": ttl}),
                                encoding="utf-8")


THROTTLED = (429, 503)
RETRY_AFTER_SECONDS = 20


def _send(method, url: str, **kwargs):
    """One request with the polite delay; waits and retries once if the site throttles us."""
    for attempt in (1, 2):
        _wait_turn(url)
        resp = method(url, **kwargs)
        if resp.status_code not in THROTTLED or attempt == 2:
            resp.raise_for_status()
            return resp
        time.sleep(RETRY_AFTER_SECONDS)


def get(url: str, use_cache: bool = True, timeout: int = 30) -> Page:
    data = _cached(url, use_cache)
    if data:
        return Page(url, data["final_url"], data["text"], True)

    resp = _send(_session.get, url, timeout=timeout)
    _store(url, resp.url, resp.text)
    return Page(url, resp.url, resp.text, False)


def get_json(url: str, use_cache: bool = True):
    return json.loads(get(url, use_cache).text)


def post_json(url: str, body: dict, use_cache: bool = True, timeout: int = 30,
              ttl: int = config.CACHE_TTL_SECONDS, cache_key: str | None = None):
    """POST a JSON body, return the decoded JSON. Cached (a day by default) like pages;
    `cache_key` lets a request whose body changes every time (a timestamp) still be cached."""
    key = cache_key or url + "\n" + json.dumps(body, sort_keys=True)
    data = _cached(key, use_cache, ttl)
    if data:
        return json.loads(data["text"])

    resp = _send(_session.post, url, json=body, timeout=timeout)
    _store(key, resp.url, resp.text, ttl)
    return resp.json()
