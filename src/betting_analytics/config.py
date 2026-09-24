"""Paths, season helpers and shared constants."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
LIVE = DATA / "live"
LEDGER = DATA / "ledger"
SITE = ROOT / "site"
SITE_DATA = SITE / "data"

USER_AGENT = "betting-analytics/0.1 (+https://github.com/mahmedken/betting-analytics)"

# First season (start year) loaded from football-data.co.uk. Earlier seasons have
# no average/max odds columns and some malformed rows.
FIRST_SEASON = 2005
# Understat xG starts in 2014-15.
FIRST_XG_SEASON = 2014


def season_label(start_year: int) -> str:
    """2025 -> '2025-26'."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def fd_code(start_year: int) -> str:
    """2025 -> '2526' (football-data.co.uk directory name)."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def current_season(today: dt.date | None = None) -> int:
    """Start year of the season in progress. Seasons roll over on 1 July."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    return today.year if today.month >= 7 else today.year - 1


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
