"""The daily collector run. The app starts it itself at `schedule.daily_at` (criteria.yaml),
so it works the same on Mac, Windows and Linux without any system scheduler.

If the computer was off or asleep at that time, the run happens as soon as the app is
running again that day ("catch-up"). It never runs before the setup wizard is done.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import collect, criteria, db

log = logging.getLogger("dig.schedule")
CHECK_EVERY_SECONDS = 60


def _daily_time(crit: dict | None = None) -> tuple[int, int]:
    hh, mm = (crit or criteria.load())["schedule"]["daily_at"].split(":")
    return int(hh), int(mm)


def todays_slot(now: datetime | None = None, crit: dict | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    hh, mm = _daily_time(crit)
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def next_run(now: datetime | None = None, crit: dict | None = None) -> datetime:
    """The next time the daily run is due (local time)."""
    now = now or datetime.now().astimezone()
    slot = todays_slot(now, crit)
    return slot if slot > now else slot + timedelta(days=1)


def is_due(conn, now: datetime | None = None, crit: dict | None = None) -> bool:
    """True once today's time has passed and no run has started since then."""
    settings = db.get_settings(conn)
    if settings.get("setup_done") != "1":
        return False
    now = now or datetime.now().astimezone()
    slot = todays_slot(now, crit)
    if now < slot:
        return False
    since = slot.astimezone(timezone.utc).isoformat(timespec="seconds")
    started = conn.execute("SELECT 1 FROM runs WHERE started_at >= ? LIMIT 1", (since,)).fetchone()
    return not started and settings.get("last_scheduled_run", "") < since


def _loop() -> None:
    while True:
        try:
            conn = db.connect()
            try:
                if is_due(conn) and not collect.is_running():
                    slot = todays_slot().astimezone(timezone.utc).isoformat(timespec="seconds")
                    db.set_setting(conn, "last_scheduled_run", slot)   # don't start it twice
                    log.info("starting the daily collector run")
                    collect.start_in_background()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - the scheduler must keep going
            log.exception("scheduler check failed")
        time.sleep(CHECK_EVERY_SECONDS)


def start() -> None:
    threading.Thread(target=_loop, name="daily-collector", daemon=True).start()
