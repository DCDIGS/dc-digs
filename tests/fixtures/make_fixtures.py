"""Writes the made-up sample pages the tests read: python tests/fixtures/make_fixtures.py

They copy the structure of real Bandcamp pages (only the parts DC Digs reads: the
`data-tralbum` JSON, the ld+json block, "you may also like", the `#collectors-data`
buyer list, a fan page's `#pagedata`, a label's `#music-grid`, the discover endpoint),
but every artist, label, title, fan and link in them is invented.
"""
import json
from html import escape
from pathlib import Path

HERE = Path(__file__).parent


def attr(obj) -> str:
    return escape(json.dumps(obj, ensure_ascii=False), quote=True)


def stream(track_id: int) -> str:
    return f"https://t4.bcbits.com/stream/0000/mp3-128/{track_id}?p=0&ts=1&t=x&token=1_x"


def recommendations(prefix: str) -> str:
    urls = [f"https://reco-one.bandcamp.com/album/reco-1"] if prefix == "album" else []
    urls += [f"https://{prefix}-reco-{i}.bandcamp.com/{'album' if i % 2 else 'track'}/pick-{i}" for i in range(2, 9)]
    urls = urls[:7]
    items = "\n".join(f'  <li class="recommended-album"><a href="{u}?from=footer-nn">Pick</a></li>' for u in urls)
    return f'<p>If you like this, you may also like:</p>\n<ul class="horizontal">\n{items}\n</ul>'


def buyers(n: int) -> str:
    fans = [{"fan_id": 1000 + i, "username": f"fan{i}", "name": f"Fan {i}", "image_id": None,
             "token": f"1:1700000000:{1000 + i}:0:1:0"} for i in range(1, n + 1)]
    blob = {"thumbs": fans, "shown_thumbs": fans, "more_thumbs_available": False,
            "reviews": [], "shown_reviews": [], "more_reviews_available": False, "band_thanks_text": None}
    return f'<div id="collectors-data" data-blob="{attr(blob)}"></div>'


def release_page(*, url, artist, title, label, item_type, release_id, art_id, release_date, keywords,
                 tracks, album_url=None, buyer_count=0, recs="album") -> str:
    tralbum = {
        "id": release_id, "item_type": item_type, "artist": artist, "url": url, "art_id": art_id,
        "album_release_date": release_date if item_type == "album" else None, "album_url": album_url,
        "current": {"title": title, "release_date": release_date, "art_id": art_id,
                    "type": item_type, "id": release_id},
        "trackinfo": [{"track_num": num, "title": t, "duration": dur, "id": tid, "track_id": tid,
                       "streaming": 1, "file": {"mp3-128": stream(tid)}} for num, t, dur, tid in tracks],
    }
    ld = {"@type": "MusicAlbum" if item_type == "album" else "MusicRecording", "name": title,
          "byArtist": {"@type": "MusicGroup", "name": artist},
          "publisher": {"@type": "MusicGroup", "name": label},
          "keywords": keywords, "image": f"https://f4.bcbits.com/img/a{art_id:010d}_10.jpg"}
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(title)} | {escape(artist)}</title>
<!-- Made-up sample page with the structure of a Bandcamp {item_type} page. -->
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False, separators=(',', ':'))}</script>
</head><body>
<script data-tralbum="{attr(tralbum)}"></script>
{buyers(buyer_count)}
{recommendations(recs)}
</body></html>
"""


def main():
    (HERE / "release_album.html").write_text(release_page(
        url="https://example-label.bandcamp.com/album/first-ep", artist="Example Artist", title="First EP",
        label="Example Label", item_type="album", release_id=2927010148, art_id=1000000001,
        release_date="22 Sep 2026 00:00:00 GMT",
        keywords=["Electronic", "breaks", "house", "minimal", "minimal techno", "rominimal", "Placeville"],
        tracks=[(1, "Track One", 375.385, 2136434947), (2, "Track Two", 369.38, 1496176132),
                (3, "Track Three", 367.241, 2905989917)],
        buyer_count=10), encoding="utf-8")

    (HERE / "release_track.html").write_text(release_page(
        url="https://another-artist.bandcamp.com/track/single-track", artist="Another Artist",
        title="Single Track", label="ANOTHER ARTIST", item_type="track", release_id=825166777,
        art_id=1000000002, release_date="04 Feb 2026 00:00:00 GMT",
        keywords=["Deep House", "Electronic", "House", "Ukhouse", "deep house", "electronic music", "house",
                  "Thirdcity"],
        tracks=[(None, "Another Artist - Single Track", 278.095, 825166777)], recs="track"), encoding="utf-8")

    (HERE / "release_track_with_buyers.html").write_text(release_page(
        url="https://third-artist.bandcamp.com/track/reference-track", artist="Third Artist",
        title="Reference Track", label="Third Artist", item_type="track", release_id=3639478872,
        art_id=1000000003, release_date="01 Mar 2026 00:00:00 GMT",
        keywords=["electronic", "electronica", "house", "melodic house", "progressive-house", "Othertown"],
        tracks=[(1, "Reference Track", 412.0, 3639478872)], album_url="/album/reference-ep",
        buyer_count=23, recs="ref"), encoding="utf-8")

    grid = [("/album/first-release-cat001", "First Release"), ("/album/second-release-cat002", "Second Release"),
            ("https://other-artist.bandcamp.com/album/other-release?label=1001&tab=music", "Other Release")]
    grid += [(f"/album/release-{i}", f"Release {i}") for i in range(4, 17)]
    items = "\n".join(f'  <li><a href="{escape(h)}"><p class="title">{t}</p></a></li>' for h, t in grid)
    more = [{"id": 5000 + j, "title": f"Back Catalogue {j}", "artist": "Example Label", "type": "album",
             "page_url": f"/album/back-catalogue-{j}", "art_id": None} for j in range(1, 70)]
    (HERE / "label_music.html").write_text(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Music | Example Label</title></head><body>
<!-- Made-up label page with the structure of <label>.bandcamp.com/music: the first releases
     are grid items, the rest are in the grid's data-client-items JSON. -->
<ol id="music-grid" data-client-items="{attr(more)}">
{items}
</ol>
</body></html>
""", encoding="utf-8")

    results = [{"item_id": 7000 + i, "item_type": "a", "title": f"New Arrival {i}",
                "item_url": f"https://new-artist-{i}.bandcamp.com/album/new-arrival-{i}?from=discover_page",
                "band_name": f"New Artist {i}", "album_artist": None,
                "release_date": f"2026-09-{1 + i % 28:02d} 00:00:00 UTC", "is_album_preorder": i % 10 == 0}
               for i in range(1, 61)]
    (HERE / "discover_deep_house.json").write_text(json.dumps({"results": results, "result_count": 60}, indent=1))


if __name__ == "__main__":
    main()
