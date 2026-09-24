"""Expected goals (xG) per match from Understat (EPL, 2014-15 onward)."""

from __future__ import annotations

import json

import pandas as pd

from .. import config
from . import http
from .teams import canonical

BASE = "https://understat.com"


def _raw_path(start_year: int):
    return config.RAW / "understat" / f"EPL_{start_year}.json"


def download_season(start_year: int, force: bool = False) -> dict:
    path = _raw_path(start_year)
    if path.exists() and not force:
        return json.loads(path.read_text())
    r = http.get(
        f"{BASE}/getLeagueData/EPL/{start_year}",
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Referer": f"{BASE}/league/EPL/{start_year}"},
    )
    data = r.json()
    # Keep only the match list; team and player tables are large and unused.
    slim = {"dates": data["dates"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slim, separators=(",", ":")))
    return slim


def load_season(start_year: int, refresh: bool = False) -> pd.DataFrame:
    data = download_season(start_year, force=refresh)
    rows = []
    for m in data["dates"]:
        if not m.get("isResult"):
            continue
        rows.append({
            "season": start_year,
            "home": canonical(m["h"]["title"]),
            "away": canonical(m["a"]["title"]),
            "us_hg": int(m["goals"]["h"]),
            "us_ag": int(m["goals"]["a"]),
            "us_hxg": float(m["xG"]["h"]),
            "us_axg": float(m["xG"]["a"]),
        })
    return pd.DataFrame(rows)


def load_history(first: int = config.FIRST_XG_SEASON, last: int | None = None) -> pd.DataFrame:
    last = last if last is not None else config.current_season()
    frames = []
    for year in range(first, last + 1):
        try:
            frames.append(load_season(year, refresh=(year == config.current_season())))
        except Exception as exc:
            print(f"understat: {config.season_label(year)} unavailable ({exc})")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
