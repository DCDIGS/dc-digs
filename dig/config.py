"""Paths and settings. Most settings live in criteria.yaml (set up on first start);
.env is optional and only overrides the port and a few limits."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
STATIC_DIR = ROOT / "static"
DB_PATH = DATA_DIR / "dig.db"

# Normally set in the setup wizard (criteria.yaml); .env still works as a fallback.
BANDCAMP_USERNAME = os.getenv("BANDCAMP_USERNAME", "").strip()
PORT = int(os.getenv("DIG_PORT", "8765"))
RELEASES_PER_FOLLOW = int(os.getenv("DIG_RELEASES_PER_FOLLOW", "20"))

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
MIN_DELAY_SECONDS = 1.0
CACHE_TTL_SECONDS = 24 * 3600

LOG_DIR = ROOT / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
