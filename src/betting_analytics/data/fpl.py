"""Official fixture list and kick-off times from the Fantasy Premier League API."""

from __future__ import annotations

import pandas as pd

from . import http
from .teams import canonical

BASE = "https://fantasy.premierleague.com/api"


def load_fixtures() -> pd.DataFrame:
    teams = {t["id"]: canonical(t["name"]) for t in http.get(f"{BASE}/bootstrap-static/").json()["teams"]}
    rows = []
    for f in http.get(f"{BASE}/fixtures/").json():
        rows.append({
            "fpl_id": f["id"],
            "gameweek": f["event"],
            "kickoff_utc": pd.to_datetime(f["kickoff_time"], utc=True) if f["kickoff_time"] else pd.NaT,
            "home": teams[f["team_h"]],
            "away": teams[f["team_a"]],
            "started": bool(f["started"]),
            "finished": bool(f["finished"]),
            "hg": f["team_h_score"],
            "ag": f["team_a_score"],
        })
    return pd.DataFrame(rows)
