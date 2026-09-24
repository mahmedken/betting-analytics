"""Kalshi prediction-market prices for EPL matches and season outcomes.

Prices are dollars per contract that pays $1, i.e. directly comparable with a
probability. Taker fee per contract is 0.07 * P * (1 - P) (Kalshi fee schedule,
`quadratic` fee type with multiplier 1), rounded up to the cent per order; the
per-contract figure is used here, which is exact for large orders.
"""

from __future__ import annotations

import re

import pandas as pd

from .. import config
from . import http
from .teams import canonical

BASE = "https://api.elections.kalshi.com/trade-api/v2"
TAKER_FEE_RATE = 0.07

MATCH_SERIES = {
    "KXEPLGAME": "1x2",
    "KXEPLTOTAL": "total",
    "KXEPLSPREAD": "margin",
    "KXEPLBTTS": "btts",
}
FUTURES_SERIES = {
    "KXPREMIERLEAGUE": "title",
    "KXEPLTOP4": "top4",
    "KXEPLRELEGATION": "relegation",
}


def taker_fee(price: float) -> float:
    return TAKER_FEE_RATE * price * (1 - price)


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _events(series: str, status: str = "open") -> list[dict]:
    events, cursor = [], None
    while True:
        params = {"series_ticker": series, "status": status, "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = http.get(f"{BASE}/events", params=params).json()
        events += r.get("events", [])
        cursor = r.get("cursor")
        if not cursor or not r.get("events"):
            return events


def _split_title(title: str) -> tuple[str, str]:
    title = title.split(":")[0]
    home, away = re.split(r"\s+vs\.?\s+", title, maxsplit=1)
    return canonical(home.strip()), canonical(away.strip())


def _quote(m: dict) -> dict:
    return {
        "bid": _f(m.get("yes_bid_dollars")),
        "ask": _f(m.get("yes_ask_dollars")),
        "last": _f(m.get("last_price_dollars")),
        "volume": _f(m.get("volume_fp")),
        "open_interest": _f(m.get("open_interest_fp")),
        "ticker": m["ticker"],
        "close_time": m.get("close_time"),
    }


def match_quotes() -> pd.DataFrame:
    """One row per (match, market, selection) with YES bid/ask in probability units.

    Binary markets also get the NO side as the complementary selection
    (e.g. NO on 'over 2.5' is 'under 2.5' at ask = 1 - yes_bid).
    """
    rows = []
    fetched = config.utcnow().isoformat()
    for series, market in MATCH_SERIES.items():
        for ev in _events(series):
            try:
                home, away = _split_title(ev["title"])
            except (KeyError, ValueError):
                continue
            for m in ev.get("markets", []):
                if m.get("status") not in ("active", "open"):
                    continue
                q = _quote(m)
                base = {"venue": "kalshi", "home": home, "away": away, "market": market,
                        "event": ev["event_ticker"], "fetched_utc": fetched}
                sub = m.get("yes_sub_title", "")
                if market == "1x2":
                    if sub.lower() in ("tie", "draw"):
                        sel = "D"
                    else:
                        sel = "H" if canonical(sub) == home else "A"
                    rows.append({**base, "selection": sel, "line": None, **q})
                    continue
                if market == "total":
                    sel_yes, sel_no, line = "over", "under", _f(m.get("floor_strike"))
                elif market == "btts":
                    sel_yes, sel_no, line = "yes", "no", None
                else:  # margin: "<team> wins by more than x goals"
                    team = canonical(re.split(r"\s+wins by", m.get("yes_sub_title", ""))[0])
                    side = "home" if team == home else "away"
                    sel_yes, sel_no, line = side, f"not_{side}", _f(m.get("floor_strike"))
                rows.append({**base, "selection": sel_yes, "line": line, **q})
                no = dict(q)
                no["bid"] = None if q["ask"] is None else round(1 - q["ask"], 4)
                no["ask"] = None if q["bid"] is None else round(1 - q["bid"], 4)
                no["last"] = None if q["last"] is None else round(1 - q["last"], 4)
                rows.append({**base, "selection": sel_no, "line": line, **no})
    return pd.DataFrame(rows)


def futures_quotes() -> pd.DataFrame:
    rows = []
    fetched = config.utcnow().isoformat()
    for series, market in FUTURES_SERIES.items():
        for ev in _events(series):
            for m in ev.get("markets", []):
                if m.get("status") not in ("active", "open"):
                    continue
                try:
                    team = canonical(m.get("yes_sub_title", ""))
                except KeyError:
                    continue
                rows.append({"venue": "kalshi", "market": market, "team": team,
                             "event": ev["event_ticker"], "fetched_utc": fetched, **_quote(m)})
    return pd.DataFrame(rows)
