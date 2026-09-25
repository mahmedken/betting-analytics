"""Africa Cup of Nations qualifying: results, fixtures, odds and markets.

Sources
  international results  github.com/martj42/international_results (every
                         men's international since 1872; updated by its
                         maintainer, lags by days to weeks)
  current campaign       ESPN's public API (league caf.nations_qual): fixtures,
                         live results, group standings, team colours/logos, and
                         the sportsbook odds ESPN displays (DraftKings now,
                         ESPN BET for the 2024 campaign)
  markets                Polymarket "AFCON 2027 Qualifying: Group X Winner"
"""

from __future__ import annotations

import io
import json
import re

import numpy as np
import pandas as pd

from .. import config
from . import http

RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/caf.nations_qual"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/caf.nations_qual/standings"
GAMMA = "https://gamma-api.polymarket.com"
RAW = config.RAW / "afcon"

HOSTS = {"Kenya", "Uganda", "Tanzania"}      # co-hosts of AFCON 2027, qualified automatically
CAMPAIGN_MONTHS = ("202603", "202609", "202610", "202611", "202703")
BACKTEST_MONTHS = ("202410", "202411")       # months with sportsbook odds retained by ESPN
QUAL_TOURNAMENT = "African Cup of Nations qualification"

_ESPN_NAMES = {"Congo DR": "DR Congo", "Sao Tome and Principe": "São Tomé and Príncipe"}


def name(n: str) -> str:
    return _ESPN_NAMES.get(n, n)


# ---------------------------------------------------------------------------
# historical international results
# ---------------------------------------------------------------------------

def load_results(refresh: bool = False) -> pd.DataFrame:
    path = RAW / "international_results.csv"
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(http.get(RESULTS_URL).content)
    d = pd.read_csv(path, parse_dates=["date"])
    d = d.dropna(subset=["home_score", "away_score"])
    d["neutral"] = d["neutral"].astype(str).str.upper().eq("TRUE")
    return d.rename(columns={"home_team": "home", "away_team": "away", "home_score": "hg", "away_score": "ag"})


# ---------------------------------------------------------------------------
# ESPN
# ---------------------------------------------------------------------------

def _american_to_decimal(ml) -> float:
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return np.nan
    if ml == 0:
        return np.nan
    return 1 + (ml / 100 if ml > 0 else 100 / -ml)


def espn_events(months=CAMPAIGN_MONTHS) -> pd.DataFrame:
    rows = []
    for m in months:
        for e in http.get(f"{ESPN}/scoreboard", params={"dates": m, "limit": 500}).json().get("events", []):
            c = e["competitions"][0]
            teams = {x["homeAway"]: x for x in c["competitors"]}
            h, a = teams["home"], teams["away"]
            state = e["status"]["type"]["state"]
            venue_country = (c.get("venue") or {}).get("address", {}).get("country")
            home = name(h["team"]["displayName"])
            rows.append({
                "event_id": e["id"], "kickoff_utc": pd.Timestamp(e["date"]), "home": home,
                "away": name(a["team"]["displayName"]), "state": state,
                "status": e["status"]["type"]["description"],
                "hg": float(h["score"]) if state == "post" else np.nan,
                "ag": float(a["score"]) if state == "post" else np.nan,
                "live_hg": float(h.get("score") or 0) if state == "in" else np.nan,
                "live_ag": float(a.get("score") or 0) if state == "in" else np.nan,
                "clock": e["status"].get("displayClock"),
                "venue": (c.get("venue") or {}).get("fullName"), "venue_country": venue_country,
                "neutral": bool(venue_country) and name(venue_country) != home,
                "home_code": h["team"].get("abbreviation"), "away_code": a["team"].get("abbreviation"),
                "home_color": "#" + (h["team"].get("color") or "6b7280"),
                "away_color": "#" + (a["team"].get("color") or "9ca3af"),
                "home_logo": h["team"].get("logo"), "away_logo": a["team"].get("logo"),
            })
    return pd.DataFrame(rows).drop_duplicates("event_id").sort_values("kickoff_utc").reset_index(drop=True)


def espn_odds(event_id: str) -> dict:
    """Sportsbook 1X2 and over/under odds shown by ESPN for one event.

    For finished events the main line is the last price ESPN recorded (the
    close); `open` holds the opening price when present.
    """
    sm = http.get(f"{ESPN}/summary", params={"event": event_id}).json()
    pc = sm.get("pickcenter") or sm.get("odds") or []
    if not pc:
        return {}
    o = pc[0]
    ho, ao, do = o.get("homeTeamOdds", {}), o.get("awayTeamOdds", {}), o.get("drawOdds", {})
    out = {"provider": o.get("provider", {}).get("name"),
           "odds_h": _american_to_decimal(ho.get("moneyLine")), "odds_d": _american_to_decimal(do.get("moneyLine")),
           "odds_a": _american_to_decimal(ao.get("moneyLine")),
           "ou_line": o.get("overUnder"), "odds_over": _american_to_decimal(o.get("overOdds")),
           "odds_under": _american_to_decimal(o.get("underOdds"))}
    return out


def espn_standings() -> pd.DataFrame:
    s = http.get(ESPN_STANDINGS).json()
    rows = []
    for g in s.get("children", []):
        grp = g["name"].replace("Group ", "")
        for e in g["standings"]["entries"]:
            t = e["team"]
            rows.append({"group": grp, "team": name(t["displayName"]), "code": t.get("abbreviation"),
                         "logo": (t.get("logos") or [{}])[0].get("href"), "espn_id": t.get("id")})
    return pd.DataFrame(rows)


def espn_team_meta() -> dict:
    """Colour and logo per team from the campaign's events."""
    ev = espn_events()
    meta = {}
    for r in ev.itertuples():
        meta.setdefault(r.home, {"code": r.home_code, "color": r.home_color, "logo": r.home_logo})
        meta.setdefault(r.away, {"code": r.away_code, "color": r.away_color, "logo": r.away_logo})
    return meta


# ---------------------------------------------------------------------------
# Polymarket group-winner markets
# ---------------------------------------------------------------------------

def _yes_token(m: dict) -> str | None:
    try:
        ids = json.loads(m.get("clobTokenIds") or "[]")
    except (TypeError, ValueError):
        return None
    return str(ids[0]) if ids else None


def polymarket_groups() -> pd.DataFrame:
    rows = []
    for g in "abcdefghijkl":
        evs = http.get(f"{GAMMA}/events", params={"slug": f"afcon-2027-qualifying-group-{g}-winner"}).json()
        if not evs:
            continue
        for m in evs[0].get("markets", []):
            if m.get("closed"):
                continue
            t = m.get("groupItemTitle") or re.sub(r"^Will (.+?) win.*$", r"\1", m.get("question", ""))
            rows.append({"group": g.upper(), "team": name(t.strip()),
                         "bid": float(m["bestBid"]) if m.get("bestBid") is not None else np.nan,
                         "ask": float(m["bestAsk"]) if m.get("bestAsk") is not None else np.nan,
                         "last": float(m["lastTradePrice"]) if m.get("lastTradePrice") is not None else np.nan,
                         "volume": float(m.get("volume") or 0),
                         "fee_rate": ((m.get("feeSchedule") or {}).get("rate") if m.get("feesEnabled") else 0.0),
                         "slug": m.get("slug"), "token": _yes_token(m)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# combined match history for modelling
# ---------------------------------------------------------------------------

def match_history(results: pd.DataFrame, campaign: pd.DataFrame) -> pd.DataFrame:
    """International results plus campaign results ESPN has that the results file does not yet."""
    r = results[["date", "home", "away", "hg", "ag", "tournament", "neutral"]].copy()
    r["kickoff_utc"] = pd.to_datetime(r["date"]).dt.tz_localize("UTC") + pd.Timedelta(hours=15)
    c = campaign[campaign["state"] == "post"].copy()
    c["tournament"] = QUAL_TOURNAMENT
    c["date"] = c["kickoff_utc"].dt.tz_convert(None).dt.normalize()
    have = set(zip(r["date"].dt.strftime("%Y-%m-%d"), r["home"], r["away"]))
    c = c[[(d.strftime("%Y-%m-%d"), h, a) not in have for d, h, a in zip(c["date"], c["home"], c["away"])]]
    out = pd.concat([r, c[["date", "kickoff_utc", "home", "away", "hg", "ag", "tournament", "neutral"]]], ignore_index=True)
    return out.sort_values("kickoff_utc").reset_index(drop=True)
