"""criteria.yaml: sources, hard filters and ranking weights. Missing keys fall back
to DEFAULTS, so an older or trimmed file keeps working."""
import copy
import re

import yaml

from . import config

PATH = config.ROOT / "criteria.yaml"

DEFAULTS = {
    "sources": {
        "bandcamp_username": "",
        "bandcamp_follows": True,
        "reference_tracks": [],
        "reference_tracks_2": [],
        "inbox_names": ["Inbox", "Inbox 2"],
        "reference_buyers": 60,
        "reference_items_per_buyer": 120,
        "reference_min_buyers": 2,
        "reference_strong_overlap": 4,
        "reference_max_new": 5,
        "reference_max_per_artist": 2,
        "tag_feeds_enabled": False,
        "tag_feeds": [],
        "tag_feed_size": 60,
        "recommendations_from_favourites": True,
        "labels": [],
        "boomkat_sections": [],
    },
    "schedule": {
        "daily_at": "06:00",
    },
    "filters": {
        "exclude_tags": [],
        "formats": ["EP", "LP", "single"],
        "max_age_days": 30,
        "recommendations_ignore_max_age": True,
        "references_ignore_max_age": True,
        "tag_feed_max_per_account": 3,
        "exclude_accounts": [],
    },
    "ranking": {
        "source_weight": {"reference": 2, "bandcamp_follow": 3, "label_page": 3, "recommendation": 2,
                          "boomkat": 1, "tag_feed": 1},
        "boost_per_co_buyer": 1,
        "max_co_buyer_boost": 10,
        "boost_tags": {},
        "boost_labels_of_favourites": 2,
        "boost_artists_of_favourites": 2,
        "boost_multiple_tag_feeds": 1,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif value is not None:
            out[key] = value
    return out


def load() -> dict:
    if not PATH.exists():
        return copy.deepcopy(DEFAULTS)
    with PATH.open(encoding="utf-8") as f:
        return _merge(DEFAULTS, yaml.safe_load(f) or {})


def mtime() -> float:
    return PATH.stat().st_mtime if PATH.exists() else 0.0


def tag_key(tag: str) -> str:
    """Bandcamp writes tags as 'deep-house' in URLs and 'deep house' on release pages."""
    return tag.strip().lower().replace("-", " ")


# --- editing from the Settings page ---------------------------------------------

class CriteriaError(ValueError):
    pass


def _slug(value: str, url_part: str) -> str:
    """'https://bandcamp.com/discover/Lo-Fi House' -> 'lo-fi-house'."""
    value = str(value).strip()
    if url_part in value:
        value = value.split(url_part, 1)[1]
    value = value.split("?")[0].strip("/ ").split("/")[0]
    return "-".join(value.lower().split())


def _str_list(value, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in value.replace("\n", ",").split(",")]
    if not isinstance(value, list):
        raise CriteriaError(f"{name} must be a list")
    out = []
    for v in value:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def _reference_url(value: str) -> str:
    """A Bandcamp track or album page, without ?query or #fragment."""
    from urllib.parse import urlsplit, urlunsplit
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if not re.search(r"/(track|album)/[^/]+", parts.path):
        raise CriteriaError(f"reference track must be a Bandcamp track or album link: {value}")
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _username(value) -> str:
    """'https://bandcamp.com/someone?from=x' or 'someone' -> 'someone'."""
    value = str(value or "").strip()
    if "bandcamp.com/" in value:
        value = value.split("bandcamp.com/", 1)[1]
    value = value.split("?")[0].strip("/ ").split("/")[0]
    if value and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
        raise CriteriaError(f"that doesn't look like a Bandcamp username: {value}")
    return value


def _hhmm(value) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(value or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise CriteriaError("daily time must look like 06:00")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _unique(values) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _int(value, name: str, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise CriteriaError(f"{name} must be a whole number") from None
    if not lo <= n <= hi:
        raise CriteriaError(f"{name} must be between {lo} and {hi}")
    return n


def validate(data: dict) -> dict:
    """Check and normalise criteria coming from the Settings page or a raw YAML edit."""
    if not isinstance(data, dict):
        raise CriteriaError("criteria must be a mapping")
    c = _merge(DEFAULTS, data)
    s, f, r = c["sources"], c["filters"], c["ranking"]
    s["bandcamp_username"] = _username(s["bandcamp_username"])
    c["schedule"]["daily_at"] = _hhmm(c["schedule"]["daily_at"])
    s.pop("nts_shows", None)                      # NTS was removed from the app
    r.pop("boost_played_on_nts", None)
    r["source_weight"].pop("nts", None)
    for key in ("bandcamp_follows", "recommendations_from_favourites", "tag_feeds_enabled"):
        s[key] = bool(s[key])
    s["reference_tracks"] = _unique(_reference_url(u) for u in _str_list(s["reference_tracks"], "reference_tracks"))
    s["reference_tracks_2"] = _unique(
        _reference_url(u) for u in _str_list(s["reference_tracks_2"], "reference_tracks_2"))
    names = s["inbox_names"] if isinstance(s["inbox_names"], list) else []
    names = [str(n).strip()[:30] for n in names[:2]] + ["", ""]
    s["inbox_names"] = [names[0] or "Inbox", names[1] or "Inbox 2"]
    s["reference_buyers"] = _int(s["reference_buyers"], "reference_buyers", 5, 400)
    s["reference_items_per_buyer"] = _int(s["reference_items_per_buyer"], "reference_items_per_buyer", 20, 500)
    s["reference_min_buyers"] = _int(s["reference_min_buyers"], "reference_min_buyers", 1, 50)
    s["reference_strong_overlap"] = _int(s["reference_strong_overlap"], "reference_strong_overlap", 1, 100)
    s["reference_max_new"] = _int(s["reference_max_new"], "reference_max_new", 1, 200)
    s["reference_max_per_artist"] = _int(s["reference_max_per_artist"], "reference_max_per_artist", 1, 50)
    s["tag_feeds"] = _unique(_slug(t, "/discover/") for t in _str_list(s["tag_feeds"], "tag_feeds"))
    s["tag_feed_size"] = _int(s["tag_feed_size"], "tag_feed_size", 1, 60)
    from .collectors.label_pages import account_url  # local: collectors import criteria users
    s["labels"] = _unique(account_url(u) for u in _str_list(s["labels"], "labels"))
    s["boomkat_sections"] = _str_list(s["boomkat_sections"], "boomkat_sections")

    f["exclude_tags"] = _unique(t.lower() for t in _str_list(f["exclude_tags"], "exclude_tags"))
    f["exclude_accounts"] = _str_list(f["exclude_accounts"], "exclude_accounts")
    f["formats"] = _str_list(f["formats"], "formats")
    unknown = set(f["formats"]) - {"EP", "LP", "single"}
    if unknown:
        raise CriteriaError(f"formats can only be EP, LP, single (not {', '.join(sorted(unknown))})")
    f["max_age_days"] = _int(f["max_age_days"], "max_age_days", 1, 3650)
    f["tag_feed_max_per_account"] = _int(f["tag_feed_max_per_account"], "tag_feed_max_per_account", 0, 60)
    f["recommendations_ignore_max_age"] = bool(f["recommendations_ignore_max_age"])
    f["references_ignore_max_age"] = bool(f["references_ignore_max_age"])

    for key in ("boost_labels_of_favourites", "boost_artists_of_favourites",
                "boost_multiple_tag_feeds", "boost_per_co_buyer", "max_co_buyer_boost"):
        r[key] = _int(r[key], key, -20, 20)
    if not isinstance(r["boost_tags"], dict) or not isinstance(r["source_weight"], dict):
        raise CriteriaError("boost_tags and source_weight must be mappings like {tag: points}")
    r["boost_tags"] = {_slug(k, "/discover/"): _int(v, f"boost_tags.{k}", -20, 20)
                       for k, v in r["boost_tags"].items() if str(k).strip()}
    r["source_weight"] = {str(k): _int(v, f"source_weight.{k}", -20, 20)
                          for k, v in r["source_weight"].items()}
    return c


def _block(values: list[str]) -> str:
    """A YAML list, one item per line, or ` []` on the key's own line when empty."""
    return "".join(f"\n    - {v}" for v in values) if values else " []"


def _flow(value) -> str:
    return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, sort_keys=False,
                          width=10_000).strip()


def render(c: dict) -> str:
    """criteria.yaml in its usual commented layout."""
    s, f, r = c["sources"], c["filters"], c["ranking"]
    b = lambda v: "true" if v else "false"  # noqa: E731
    return f"""# dig/inbox — sources, filters and ranking.
# Edit here or on the Settings page (http://localhost:8765/settings); the next
# collector run (or page load) picks up the changes.

sources:
  # Reference tracks (Bandcamp track or album links). The collector finds what their
  # buyers also bought: releases owned by several of them, in a genre the reference shares.
  reference_tracks:{_block(s['reference_tracks'])}
  # A second, separate list for another style. What it finds goes to the second inbox.
  reference_tracks_2:{_block(s['reference_tracks_2'])}
  inbox_names: {_flow(s['inbox_names'])}        # how the two inboxes are called in the app
  reference_buyers: {s['reference_buyers']}              # buyers read per reference
  reference_items_per_buyer: {s['reference_items_per_buyer']}   # most recent purchases read per buyer
  reference_min_buyers: {s['reference_min_buyers']}           # a release must be owned by at least this many of them
  reference_strong_overlap: {s['reference_strong_overlap']}       # from this many buyers on, skip the genre check
  reference_max_new: {s['reference_max_new']}             # new releases per reference per run
  reference_max_per_artist: {s['reference_max_per_artist']}       # releases per artist per reference, so other names get room

  # Your Bandcamp fan name (bandcamp.com/<name>): the artists and labels you follow
  # there bring their new releases into the first inbox. Leave empty to skip.
  bandcamp_username: "{s['bandcamp_username']}"
  bandcamp_follows: {b(s['bandcamp_follows'])}

  # Bandcamp "new arrivals" per tag (the name as in bandcamp.com/discover/<tag>).
  tag_feeds_enabled: {b(s['tag_feeds_enabled'])}
  tag_feeds: {_flow(s['tag_feeds'])}
  tag_feed_size: {s['tag_feed_size']}               # newest releases to look at per tag, max 60

  # When you favourite a release, the next run collects its "you may also like"
  # releases (one hop only, never recommendations of recommendations).
  recommendations_from_favourites: {b(s['recommendations_from_favourites'])}

  # Bandcamp labels or artists to watch without following them (their /music page).
  labels: {_flow(s['labels'])}

  # Not built:
  boomkat_sections: {_flow(s['boomkat_sections'])}

schedule:
  daily_at: "{c['schedule']['daily_at']}"             # the collector runs once a day at this time (while the app is running)

filters:                          # hard rules: matching releases never reach the inbox
  exclude_tags: {_flow(f['exclude_tags'])}                # e.g. [edm, big room]
  formats: {_flow(f['formats'])}
  max_age_days: {f['max_age_days']}                # by release date; pre-orders always pass
  recommendations_ignore_max_age: {b(f['recommendations_ignore_max_age'])}   # "you may also like" is often older music
  references_ignore_max_age: {b(f['references_ignore_max_age'])}        # what buyers of your references own can be any age
  tag_feed_max_per_account: {f['tag_feed_max_per_account']}     # per Bandcamp account per run; stops mass uploaders flooding the inbox
  exclude_accounts: {_flow(f['exclude_accounts'])}            # Bandcamp account names to ignore in tag feeds, e.g. [SomeMassUploader]

ranking:                          # soft rules: ordering only
  source_weight: {_flow(r['source_weight'])}
  boost_tags: {_flow(r['boost_tags'])}
  boost_labels_of_favourites: {r['boost_labels_of_favourites']}
  boost_artists_of_favourites: {r['boost_artists_of_favourites']}
  boost_multiple_tag_feeds: {r['boost_multiple_tag_feeds']}     # per extra tag feed the release showed up in
  boost_per_co_buyer: {r['boost_per_co_buyer']}           # per buyer of a reference who also owns it
  max_co_buyer_boost: {r['max_co_buyer_boost']}          # cap per reference
"""


def save(data: dict) -> dict:
    c = validate(data)
    _write(render(c))
    return c


def save_raw(text: str) -> dict:
    """Save hand-edited YAML as-is (keeps your comments) after checking it."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise CriteriaError(f"YAML error: {e}") from None
    c = validate(data)
    _write(text)
    return c


def _write(text: str) -> None:
    tmp = PATH.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(PATH)
