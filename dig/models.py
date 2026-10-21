"""The common release format every collector produces."""
import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class Track:
    position: int
    title: str
    duration_seconds: float | None
    bandcamp_track_id: int | None
    streamable: bool
    stream_url: str | None = None  # never stored; expires after ~24 h


@dataclass
class Release:
    artist: str
    title: str
    source: str                      # e.g. "bandcamp_follow"
    source_url: str                  # where we found it
    bandcamp_url: str | None = None
    label: str | None = None
    format: str | None = None        # single / EP / LP (guessed)
    genres: list[str] = field(default_factory=list)
    art_url: str | None = None
    release_date: str | None = None  # ISO yyyy-mm-dd
    bandcamp_id: int | None = None
    item_type: str | None = None     # album / track
    reasons: list[str] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)  # URLs, used in phase 3


def normalise(text: str) -> str:
    """Loose form for de-duplication: no accents, case, brackets or punctuation."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[\[(][^\])]*[\])]", " ", text)  # [CAT001], (Original Mix)
    return re.sub(r"[\W_]+", " ", text).strip()


def dedup_key(artist: str, title: str) -> str:
    return normalise(artist) + " -- " + normalise(title)
