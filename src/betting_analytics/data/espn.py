"""ESPN's public soccer API: fixtures, live scores and match events.

The scoreboard accepts one day (YYYYMMDD) or one month (YYYYMM); date ranges
are rejected for soccer leagues. Responses allow cross-origin requests, so
the site polls the same endpoint from the browser for live scores.
"""

from __future__ import annotations

import pandas as pd

from . import http

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def scoreboard(league: str, dates: str) -> list[dict]:
    return http.get(f"{BASE}/{league}/scoreboard", params={"dates": dates, "limit": 500}).json().get("events", [])


def summary(league: str, event_id: str) -> dict:
    return http.get(f"{BASE}/{league}/summary", params={"event": event_id}).json()


def parse_minute(display_clock: str | None) -> float | None:
    """ESPN clock text to match minute: "17'" -> 17, "45'+2'" -> 45 + 2, "90'+6'" -> 96."""
    if not display_clock:
        return None
    parts = [p for p in display_clock.replace("'", "").split("+") if p.strip()]
    try:
        return float(sum(float(p) for p in parts))
    except ValueError:
        return None


def goal_events(event: dict) -> list[dict]:
    """Goals in an event as [{minute, base, added, side}] with side 'home' or 'away' (own goals credited to the beneficiary)."""
    c = event["competitions"][0]
    side_of = {x["team"]["id"]: x["homeAway"] for x in c["competitors"]}
    out = []
    for d in c.get("details") or []:
        if not d.get("scoringPlay"):
            continue
        side = side_of.get((d.get("team") or {}).get("id"))
        if side is None:
            continue
        txt = (d.get("clock") or {}).get("displayValue", "")
        parts = [p for p in txt.replace("'", "").split("+") if p.strip()]
        try:
            base, added = float(parts[0]), float(parts[1]) if len(parts) > 1 else 0.0
        except (IndexError, ValueError):
            continue
        out.append({"minute": base + added, "base": base, "added": added, "side": side})
    return out


def event_ids(league: str, fixtures: pd.DataFrame, canonical) -> dict[tuple[str, str], str]:
    """ESPN event id for each (home, away) in `fixtures` (needs a kickoff_utc column), matched by team names."""
    out: dict[tuple[str, str], str] = {}
    if fixtures.empty:
        return out
    days = sorted({t.strftime("%Y%m%d") for t in pd.to_datetime(fixtures["kickoff_utc"], utc=True)})
    wanted = set(zip(fixtures["home"], fixtures["away"]))
    for d in days:
        for e in scoreboard(league, d):
            teams = {x["homeAway"]: x["team"]["displayName"] for x in e["competitions"][0]["competitors"]}
            try:
                key = (canonical(teams["home"]), canonical(teams["away"]))
            except (KeyError, ValueError):
                continue
            if key in wanted:
                out[key] = e["id"]
    return out
