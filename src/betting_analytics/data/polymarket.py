"""Polymarket prices for EPL matches and season outcomes (Gamma API).

Sports markets charge takers fee = rate * p * (1 - p) per share with
rate = 0.05 (Polymarket fee docs; market field feeSchedule.rate). The rate is
read from each market when present.
"""

from __future__ import annotations

import json
import re

import pandas as pd

from .. import config
from . import http
from .teams import canonical

GAMMA = "https://gamma-api.polymarket.com"
EPL_SERIES_ID = 10188
DEFAULT_FEE_RATE = 0.05

FUTURES_EVENTS = {
    "title": "epl-2027-champion-20260701200428749",
    "top4": "premier-league-top-4-finishers-2026-27",
    "relegation": "premier-league-teams-relegated-2026-27",
}

_MAIN_SLUG = re.compile(r"^epl-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2}$")


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fee_rate(m: dict) -> float:
    sched = m.get("feeSchedule") or {}
    if m.get("feesEnabled") is False:
        return 0.0
    return float(sched.get("rate", DEFAULT_FEE_RATE))


def _split_title(title: str) -> tuple[str, str]:
    title = re.sub(r"\s+-\s+.*$", "", title)
    home, away = re.split(r"\s+vs\.?\s+", title, maxsplit=1)
    return canonical(home), canonical(away)


def _open_events() -> list[dict]:
    events, offset = [], 0
    while True:
        batch = http.get(f"{GAMMA}/events", params={"series_id": EPL_SERIES_ID, "closed": "false",
                                                     "limit": 100, "offset": offset}).json()
        events += batch
        if len(batch) < 100:
            return events
        offset += 100


def _token(m: dict) -> str | None:
    """Order-book token id of the market's Yes outcome (the site polls its live book)."""
    try:
        ids = json.loads(m.get("clobTokenIds") or "[]")
    except (TypeError, ValueError):
        return None
    return str(ids[0]) if ids else None


def _quote(m: dict) -> dict:
    return {"bid": _f(m.get("bestBid")), "ask": _f(m.get("bestAsk")),
            "last": _f(m.get("lastTradePrice")), "volume": _f(m.get("volume")),
            "liquidity": _f(m.get("liquidity")), "ticker": m.get("slug"), "token": _token(m), "invert": False,
            "fee_rate": _fee_rate(m), "close_time": m.get("gameStartTime") or m.get("endDate")}


def _complement(q: dict) -> dict:
    """The No side of a binary market, quoted as the complementary selection (bid/ask from 1 - Yes ask/bid)."""
    no = dict(q, invert=True)
    no["bid"] = None if q["ask"] is None else round(1 - q["ask"], 4)
    no["ask"] = None if q["bid"] is None else round(1 - q["bid"], 4)
    no["last"] = None if q["last"] is None else round(1 - q["last"], 4)
    return no


def match_quotes() -> pd.DataFrame:
    rows = []
    fetched = config.utcnow().isoformat()
    for ev in _open_events():
        slug = ev.get("slug", "")
        is_main = bool(_MAIN_SLUG.match(slug))
        is_more = slug.endswith("-more-markets")
        if not (is_main or is_more):
            continue
        try:
            home, away = _split_title(ev["title"])
        except (KeyError, ValueError):
            continue
        base = {"venue": "polymarket", "home": home, "away": away, "event": slug, "fetched_utc": fetched}
        for m in ev.get("markets", []):
            if m.get("closed") or not m.get("acceptingOrders", True):
                continue
            kind = m.get("sportsMarketType")
            q = _quote(m)
            if kind == "moneyline":
                title = m.get("groupItemTitle", "")
                if title.lower().startswith("draw"):
                    sel = "D"
                else:
                    sel = "H" if canonical(title) == home else "A"
                rows.append({**base, "market": "1x2", "selection": sel, "line": None, **q})
            elif kind == "totals":
                line = _f(m.get("line"))
                rows.append({**base, "market": "total", "selection": "over", "line": line, **q})
                rows.append({**base, "market": "total", "selection": "under", "line": line, **_complement(q)})
            elif kind == "both_teams_to_score":
                rows.append({**base, "market": "btts", "selection": "yes", "line": None, **q})
                rows.append({**base, "market": "btts", "selection": "no", "line": None, **_complement(q)})
            elif kind == "spreads":
                outcomes = json.loads(m.get("outcomes") or "[]")
                line = _f(m.get("line"))
                if not outcomes or line is None or line >= 0:
                    continue
                side = "home" if canonical(outcomes[0]) == home else "away"
                # "Team (-1.5)" pays if that team wins by more than 1.5 goals.
                rows.append({**base, "market": "margin", "selection": side, "line": -line, **q})
                rows.append({**base, "market": "margin", "selection": f"not_{side}", "line": -line,
                             **_complement(q)})
    return pd.DataFrame(rows)


def futures_quotes() -> pd.DataFrame:
    rows = []
    fetched = config.utcnow().isoformat()
    for market, slug in FUTURES_EVENTS.items():
        evs = http.get(f"{GAMMA}/events", params={"slug": slug}).json()
        if not evs:
            continue
        for m in evs[0].get("markets", []):
            if m.get("closed"):
                continue
            name = m.get("groupItemTitle") or ""
            try:
                team = canonical(name)
            except KeyError:
                continue
            rows.append({"venue": "polymarket", "market": market, "team": team, "event": slug,
                         "fetched_utc": fetched, **_quote(m)})
    return pd.DataFrame(rows)
