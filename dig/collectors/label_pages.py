"""Bandcamp labels (or artists) listed in criteria.yaml, watched without following them.
Same /music page walk as your follows."""
from urllib.parse import urlsplit

from .. import config, db
from .bandcamp_follows import collect_account
from .common import log

SOURCE = "label_page"


def account_url(value: str) -> str:
    """'some-label', 'some-label.bandcamp.com' or any page URL on the account -> its base URL."""
    value = value.strip()
    if "://" not in value:
        value = "https://" + (value if "." in value else f"{value}.bandcamp.com")
    parts = urlsplit(value)
    return f"https://{parts.netloc.lower()}"


def account_name(url: str) -> str:
    host = urlsplit(url).netloc
    return host.removesuffix(".bandcamp.com")


def run(conn, crit: dict, per_label: int = config.RELEASES_PER_FOLLOW) -> tuple[int, list[str]]:
    labels = [account_url(u) for u in crit["sources"]["labels"] or []]
    if not labels:
        return 0, []
    run_id = db.start_run(conn, SOURCE)
    added, errors = 0, []
    try:
        for url in labels:
            account = {"name": account_name(url), "url": url}
            n = collect_account(conn, crit, account, per_label, errors, SOURCE, "label")
            log.info("label %s: %d new", account["name"], n)
            added += n
    except Exception as e:  # noqa: BLE001
        errors.append(f"collector crashed: {e!r}")
        raise
    finally:
        db.finish_run(conn, run_id, added, errors)
    return added, errors
