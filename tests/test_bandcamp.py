"""Parser tests against the made-up sample pages in fixtures/ (see make_fixtures.py)."""
import pytest

from conftest import fixture_text
from dig import bandcamp
from dig.models import dedup_key, normalise

ALBUM_URL = "https://example-label.bandcamp.com/album/first-ep"


@pytest.fixture(scope="module")
def album():
    return bandcamp.parse_release(fixture_text("release_album.html"), ALBUM_URL, "bandcamp_follow")


def test_album_metadata(album):
    assert album.artist == "Example Artist"
    assert album.title == "First EP"
    assert album.label == "Example Label"
    assert album.release_date == "2026-09-22"
    assert album.item_type == "album"
    assert album.format == "EP"  # 3 tracks
    assert album.bandcamp_url == ALBUM_URL
    assert album.bandcamp_id == 2927010148
    assert album.art_url == "https://f4.bcbits.com/img/a1000000001_10.jpg"
    assert album.genres[:3] == ["electronic", "breaks", "house"]
    assert all(g == g.lower() for g in album.genres)


def test_album_tracks(album):
    assert [t.title for t in album.tracks] == ["Track One", "Track Two", "Track Three"]
    assert [t.position for t in album.tracks] == [1, 2, 3]
    assert album.tracks[0].duration_seconds == pytest.approx(375.385)
    assert album.tracks[0].bandcamp_track_id == 2136434947
    assert all(t.streamable for t in album.tracks)
    assert album.tracks[0].stream_url.startswith("https://t4.bcbits.com/stream/")


def test_album_recommendations(album):
    assert len(album.recommendations) >= 3
    assert all("bandcamp.com/album/" in u or "/track/" in u for u in album.recommendations)
    assert all("?" not in u for u in album.recommendations)


def test_single_track_page():
    r = bandcamp.parse_release(fixture_text("release_track.html"),
                               "https://another-artist.bandcamp.com/track/single-track")
    assert r.artist == "Another Artist"
    assert r.title == "Single Track"
    assert r.item_type == "track"
    assert r.format == "single"
    assert r.release_date == "2026-02-04"
    assert len(r.tracks) == 1
    # track pages title the track "Artist - Title"; we use the release title instead
    assert r.tracks[0].title == "Single Track"
    assert r.tracks[0].position == 1
    # duplicated keywords with different case are collapsed
    assert r.genres.count("deep house") == 1


def test_stream_urls():
    urls = bandcamp.stream_urls(fixture_text("release_album.html"))
    assert set(urls) == {2136434947, 1496176132, 2905989917}
    assert all("/mp3-128/" in u for u in urls.values())


def test_page_without_tralbum_raises():
    with pytest.raises(bandcamp.ParseError):
        bandcamp.parse_release("<html><body>nope</body></html>", "https://x.bandcamp.com/album/y")


def test_parse_follows():
    follows = bandcamp.parse_follows(fixture_text("fan_page.html"))
    assert len(follows) == 3
    assert follows[0] == {"band_id": 1001, "name": "Example Records",
                          "url": "https://example-records.bandcamp.com"}
    assert all(f["url"].startswith("https://") for f in follows)


def test_parse_music_listing():
    urls = bandcamp.parse_music_listing(fixture_text("label_music.html"), "https://example-label.bandcamp.com")
    assert len(urls) == 85  # 16 grid items + 69 in data-client-items
    assert urls[0] == "https://example-label.bandcamp.com/album/first-release-cat001"
    # releases hosted on another artist's subdomain keep that host, minus the query string
    assert "https://other-artist.bandcamp.com/album/other-release" in urls
    assert len(set(urls)) == len(urls)
    assert all("?" not in u for u in urls)


@pytest.mark.parametrize("item_type,title,n,expected", [
    ("track", "Anything", 1, "single"),
    ("album", "We Call It EP [MICRDIG007]", 8, "EP"),
    ("album", "Debut LP", 3, "LP"),
    ("album", "Two Tracker", 2, "single"),
    ("album", "Four Tracker", 4, "EP"),
    ("album", "Big One", 10, "LP"),
    ("album", "Sleepy", 3, "EP"),  # "ep" only counts as a whole word
])
def test_guess_format(item_type, title, n, expected):
    assert bandcamp.guess_format(item_type, title, n) == expected


def test_clean_url():
    assert bandcamp.clean_url("https://a.bandcamp.com/album/x/?label=1&tab=music#t") == \
        "https://a.bandcamp.com/album/x"


def test_normalise_and_dedup_key():
    assert normalise("Café Del Mar [CAT001] (Original Mix)") == "cafe del mar"
    assert dedup_key("Overmono", "Good Lies") == dedup_key("OVERMONO", "Good  Lies!")
    assert dedup_key("A", "B") != dedup_key("A", "C")
