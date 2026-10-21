"""Run the collectors: python -m dig.collect"""
import argparse
import logging
import os

from . import config, criteria, db, fetch, ranking
from .collectors import bandcamp_follows, label_pages, recommendations, references, tag_feeds

COLLECTORS = {
    "references": references,
    "bandcamp_follows": bandcamp_follows,
    "label_pages": label_pages,
    "recommendations": recommendations,
    "tag_feeds": tag_feeds,
}


LOCK_PATH = config.DATA_DIR / "collect.lock"


def start_in_background() -> None:
    """Run `python -m dig.collect` detached, logging to logs/collect.log."""
    import subprocess
    import sys
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        flags["start_new_session"] = True
    with (config.LOG_DIR / "collect.log").open("a", encoding="utf-8") as log:
        subprocess.Popen([sys.executable, "-m", "dig.collect"], cwd=config.ROOT,
                         stdout=log, stderr=subprocess.STDOUT, **flags)


def _lock():
    """An exclusive lock held while collecting, or None if another run holds it.
    The OS releases it when the process ends, even after a crash."""
    f = LOCK_PATH.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def is_running() -> bool:
    f = _lock()
    if f is None:
        return True
    f.close()
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="Collect new releases into data/dig.db")
    p.add_argument("--source", choices=COLLECTORS, action="append",
                   help="only run this collector (repeatable); default: all")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    lock = _lock()
    if lock is None:
        print("another collector run is in progress; skipping")
        return 0
    crit = criteria.load()
    fetch.prune_cache()
    conn = db.connect()
    failed = False
    for name in args.source or COLLECTORS:
        try:
            added, errors = COLLECTORS[name].run(conn, crit)
        except Exception as e:  # noqa: BLE001 - one broken source mustn't stop the others
            added, errors = 0, [repr(e)]
        print(f"{name}: {added} new, {len(errors)} error(s)")
        for e in errors:
            print("  !", e)
        failed |= bool(errors) and added == 0
    ranking.rescore_inbox(conn, crit)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
