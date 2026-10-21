"""Parsers for Bandcamp pages. Structure verified against real pages
(samples in tests/fixtures/):

- release page: `data-tralbum` JSON attribute (tracks, durations, stream URLs,
  release date) plus an `application/ld+json` block (label = publisher, tags =
  keywords, cover image). "You may also like" is `li.recommended-album`.
- fan page: `#pagedata[data-blob]` JSON, follows in item_cache.following_bands.
- artist/label /music page: `#music-grid > li` links plus the remaining
  releases in the grid's `data-client-items` JSON attribute.
"""
import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .models import Release, Track


class ParseError(Exception):
    pass


def _json_attr(soup: BeautifulSoup, attr: str):
    el = soup.find(attrs={attr: True})
    if el is None:
        return None
    return json.loads(el[attr])


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d %b %Y %H:%M:%S %Z").date().isoformat()
    except ValueError:
        return None


def clean_url(url: str) -> str:
    """Drop query/fragment (?label=…&tab=music) so the same release has one URL."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path.rstrip("/"), "", ""))


def art_url(art_id: int | None, size: int = 10) -> str | None:
    return f"https://f4.bcbits.com/img/a{art_id:010d}_{size}.jpg" if art_id else None


def guess_format(item_type: str, title: str, n_tracks: int) -> str:
    if item_type == "track":
        return "single"
    words = set(re.findall(r"[a-z0-9]+", title.lower()))
    if "ep" in words:
        return "EP"
    if words & {"lp", "album"}:
        return "LP"
    if n_tracks <= 2:
        return "single"
    return "EP" if n_tracks <= 6 else "LP"


def parse_release(page_html: str, url: str, source: str = "", source_url: str = "") -> Release:
    soup = BeautifulSoup(page_html, "html.parser")
    tralbum = _json_attr(soup, "data-tralbum")
    if not tralbum:
        raise ParseError(f"no data-tralbum on {url}")

    ld = {}
    ld_tag = soup.find("script", type="application/ld+json")
    if ld_tag and ld_tag.string:
        try:
            ld = json.loads(ld_tag.string)
        except json.JSONDecodeError:
            ld = {}

    current = tralbum.get("current") or {}
    artist = tralbum.get("artist") or current.get("artist") or ""
    title = current.get("title") or ld.get("name") or ""
    item_type = tralbum.get("item_type") or current.get("type") or "album"

    # The account the release is on: a label, or the artist's own page (self-released).
    # These can't be told apart from the page, so label may equal the artist.
    label = (ld.get("publisher") or {}).get("name")

    tags, seen = [], set()
    for kw in ld.get("keywords") or []:
        k = kw.strip().lower()
        if k and k not in seen:
            seen.add(k)
            tags.append(k)

    info = tralbum.get("trackinfo") or []
    nums = [t.get("track_num") for t in info]
    use_nums = all(nums) and len(set(nums)) == len(nums)  # repeated/missing numbers happen
    tracks = []
    for i, t in enumerate(info, start=1):
        stream = (t.get("file") or {}).get("mp3-128")
        track_title = t.get("title") or ""
        if item_type == "track":
            track_title = title or track_title
        tracks.append(Track(
            position=t["track_num"] if use_nums else i,
            title=track_title,
            duration_seconds=t.get("duration") or None,
            bandcamp_track_id=t.get("id") or t.get("track_id"),
            streamable=bool(stream) and bool(t.get("streaming", 1)),
            stream_url=stream,
        ))

    recs = []
    for li in soup.select("li.recommended-album"):
        a = li.find("a", href=True)
        if a:
            recs.append(clean_url(a["href"]))

    return Release(
        artist=artist,
        title=title,
        source=source,
        source_url=source_url or url,
        bandcamp_url=clean_url(tralbum.get("url") or url),
        label=label,
        format=guess_format(item_type, title, len(tracks)),
        genres=tags,
        art_url=art_url(tralbum.get("art_id") or current.get("art_id")) or ld.get("image"),
        release_date=_date(tralbum.get("album_release_date") or current.get("release_date")),
        bandcamp_id=tralbum.get("id"),
        item_type=item_type,
        tracks=tracks,
        recommendations=recs,
    )


def stream_urls(page_html: str) -> dict[int, str]:
    """{bandcamp_track_id: current mp3-128 URL} for every streamable track."""
    soup = BeautifulSoup(page_html, "html.parser")
    tralbum = _json_attr(soup, "data-tralbum") or {}
    out = {}
    for t in tralbum.get("trackinfo") or []:
        url = (t.get("file") or {}).get("mp3-128")
        if url and t.get("id"):
            out[int(t["id"])] = url
    return out


def parse_follows(page_html: str) -> list[dict]:
    """Artists and labels a fan follows, from their public fan page."""
    soup = BeautifulSoup(page_html, "html.parser")
    el = soup.find(id="pagedata")
    if el is None or not el.get("data-blob"):
        raise ParseError("fan page has no #pagedata data-blob")
    blob = json.loads(el["data-blob"])
    cached = (blob.get("item_cache") or {}).get("following_bands") or {}
    expected = (blob.get("fan_data") or {}).get("following_bands_count")
    follows = []
    for band in cached.values():
        hints = band.get("url_hints") or {}
        if hints.get("custom_domain"):
            base = "https://" + hints["custom_domain"]
        elif hints.get("subdomain"):
            base = f"https://{hints['subdomain']}.bandcamp.com"
        else:
            continue
        follows.append({"band_id": band.get("band_id"), "name": band.get("name"), "url": base})
    return follows if expected is None else follows[:expected]


def parse_music_listing(page_html: str, base_url: str) -> list[str]:
    """Release URLs on an artist/label /music page, in page order (newest first by default)."""
    soup = BeautifulSoup(page_html, "html.parser")
    grid = soup.find(id="music-grid")
    urls = []
    if grid is not None:
        for li in grid.find_all("li", recursive=False):
            a = li.find("a", href=True)
            if a:
                urls.append(clean_url(urljoin(base_url + "/", a["href"])))
        extra = grid.get("data-client-items")
        if extra:
            for item in json.loads(extra):
                if item.get("page_url"):
                    urls.append(clean_url(urljoin(base_url + "/", item["page_url"])))
    seen, ordered = set(), []
    for u in urls:
        if u not in seen and re.search(r"/(album|track)/", u):
            seen.add(u)
            ordered.append(u)
    return ordered


def parse_supporters(page_html: str, page_url: str) -> dict:
    """Fans who bought this release, from the page's `#collectors-data` blob (up to 80;
    more via the tralbumcollectors endpoint). Also the ids needed to page further."""
    soup = BeautifulSoup(page_html, "html.parser")
    tralbum = _json_attr(soup, "data-tralbum") or {}
    el = soup.find(id="collectors-data")
    blob = json.loads(el["data-blob"]) if el is not None and el.get("data-blob") else {}
    parts = urlsplit(page_url)
    album_url = tralbum.get("album_url")
    return {
        "tralbum_id": tralbum.get("id"),
        "tralbum_type": "t" if tralbum.get("item_type") == "track" else "a",
        "fans": [{"fan_id": t["fan_id"], "token": t.get("token")} for t in blob.get("thumbs") or []
                 if t.get("fan_id")],
        "more": bool(blob.get("more_thumbs_available")),
        "album_url": clean_url(urljoin(f"{parts.scheme}://{parts.netloc}/", album_url)) if album_url else None,
    }
