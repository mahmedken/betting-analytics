"""Historical pre-match prices on Kalshi and Polymarket for settled EPL matches.

For every settled 1X2 contract, the price is read at fixed offsets before
kick-off (24 hours and 1 hour). Kick-off times come from the match table, not
from the venue, because both venues keep trading in-play.

Kalshi: hourly candles with yes_bid and yes_ask (the executable prices).
        Markets settled before Kalshi's historical cutoff are served from
        /historical endpoints.
Polymarket: hourly price series from the CLOB prices-history endpoint. This is
        a single price per hour (no bid/ask), so execution cost is estimated;
        see evaluation.venues.

Results are cached in data/raw/venues/ and only new matches are fetched.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .. import config
from . import http
from .teams import canonical

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
OFFSETS_H = (24, 1)
CACHE = config.RAW / "venues"


def _ts(s: str) -> int:
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def _match_lookup(df: pd.DataFrame) -> dict:
    """(home, away, date) -> kickoff timestamp, match_id."""
    out = {}
    for r in df.itertuples(index=False):
        out[(r.home, r.away, r.kickoff_utc.strftime("%Y-%m-%d"))] = (int(r.kickoff_utc.timestamp()), r.match_id)
    return out


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------

def _kalshi_markets() -> list[dict]:
    ms = []
    for path in ("/historical/markets", "/markets"):
        cursor = None
        while True:
            params = {"series_ticker": "KXEPLGAME", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            j = http.get(KALSHI + path, params=params).json()
            ms += [m for m in j.get("markets", []) if m.get("status") in ("finalized", "settled")]
            cursor = j.get("cursor")
            if not cursor or not j.get("markets"):
                break
    return ms


# Team names start with a capital letter, which skips "the result of" in the
# draw contract's wording ("If Tie is the result of the X vs Y professional ...").
_RULE = re.compile(r"the ([A-Z][^,]*?) vs ([A-Z].*?) professional EPL soccer game originally scheduled for (\w+ \d+, \d{4})")


def _kalshi_parse(m: dict) -> tuple[str, str, str, str] | None:
    g = _RULE.search(m.get("rules_primary", ""))
    if not g:
        return None
    try:
        home, away = canonical(g.group(1)), canonical(g.group(2))
    except KeyError:
        return None
    date = dt.datetime.strptime(g.group(3), "%b %d, %Y").strftime("%Y-%m-%d")
    sub = m.get("yes_sub_title", "")
    if sub.lower() in ("tie", "draw"):
        sel = "D"
    else:
        try:
            sel = "H" if canonical(sub) == home else "A"
        except KeyError:
            return None
    return home, away, date, sel


def _kalshi_candles(ticker: str, settled_ts: int, kickoff: int, historical: bool) -> list[dict]:
    path = f"/historical/markets/{ticker}/candlesticks" if historical else f"/series/KXEPLGAME/markets/{ticker}/candlesticks"
    params = {"start_ts": kickoff - 30 * 3600, "end_ts": kickoff, "period_interval": 60}
    try:
        return http.get(KALSHI + path, params=params).json().get("candlesticks", [])
    except Exception:
        return []


def fetch_kalshi(df: pd.DataFrame) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "kalshi_epl_1x2.csv"
    cached = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["ticker"])
    done = set(cached["ticker"])
    cutoff = _ts(http.get(KALSHI + "/historical/cutoff").json()["market_settled_ts"])
    look = _match_lookup(df)
    todo = []
    for m in _kalshi_markets():
        if m["ticker"] in done:
            continue
        parsed = _kalshi_parse(m)
        if parsed is None:
            continue
        home, away, date, sel = parsed
        key = (home, away, date)
        if key not in look:
            continue
        kickoff, mid = look[key]
        settled = _ts(m.get("settlement_ts") or m["close_time"])
        todo.append((m, mid, home, away, sel, kickoff, settled < cutoff))

    def work(item):
        m, mid, home, away, sel, kickoff, hist = item
        candles = _kalshi_candles(m["ticker"], 0, kickoff, hist)
        row = {"ticker": m["ticker"], "match_id": mid, "home": home, "away": away, "selection": sel,
               "kickoff_ts": kickoff, "result": m.get("result"), "volume": float(m.get("volume_fp") or 0)}
        for off in OFFSETS_H:
            t = kickoff - off * 3600
            c = [x for x in candles if x["end_period_ts"] <= t]
            last = c[-1] if c else None
            row[f"bid_{off}h"] = float(last["yes_bid"]["close"]) if last and last.get("yes_bid", {}).get("close") else np.nan
            row[f"ask_{off}h"] = float(last["yes_ask"]["close"]) if last and last.get("yes_ask", {}).get("close") else np.nan
        return row

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(work, todo))
    new = pd.DataFrame(rows)
    out = pd.concat([cached, new], ignore_index=True) if len(cached) else new
    out.to_csv(path, index=False)
    print(f"kalshi: {len(new)} new contracts, {len(out)} total")
    return out


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------

_MAIN = re.compile(r"^epl-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2}$")
# Events before late 2025 have no sportsMarketType; identify 1X2 markets by wording.
_MONEYLINE_Q = re.compile(r"^Will .+ (win on \d{4}-\d{2}-\d{2}|end in a draw)\?$")


def _is_moneyline(m: dict) -> bool:
    t = m.get("sportsMarketType")
    return t == "moneyline" or (t is None and bool(_MONEYLINE_Q.match(m.get("question", ""))))


def _poly_events() -> list[dict]:
    events, off = [], 0
    while True:
        b = http.get(f"{GAMMA}/events", params={"series_id": 10188, "closed": "true", "limit": 100, "offset": off}).json()
        events += [e for e in b if _MAIN.match(e.get("slug", ""))]
        if len(b) < 100:
            return events
        off += 100


def fetch_polymarket(df: pd.DataFrame) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "polymarket_epl_1x2.csv"
    cached = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["token"])
    done = set(cached["token"].astype(str))
    look = _match_lookup(df)
    todo = []
    for e in _poly_events():
        try:
            title = re.sub(r"\s+-\s+.*$", "", e["title"])
            h_name, a_name = re.split(r"\s+vs\.?\s+", title, maxsplit=1)
            home, away = canonical(h_name), canonical(a_name)
        except (KeyError, ValueError):
            continue
        for m in e.get("markets", []):
            if not _is_moneyline(m):
                continue
            start = m.get("gameStartTime")
            if not start:
                continue
            date = start[:10]
            key = (home, away, date)
            if key not in look:
                continue
            kickoff, mid = look[key]
            gt = m.get("groupItemTitle", "")
            sel = "D" if gt.lower().startswith("draw") else ("H" if canonical(gt) == home else "A")
            token = json.loads(m["clobTokenIds"])[0]
            if token in done:
                continue
            won = json.loads(m.get("outcomePrices") or "[]")
            todo.append((token, mid, home, away, sel, kickoff, won[0] if won else None,
                         float(m.get("volume") or 0), (m.get("feeSchedule") or {}).get("rate")))

    def work(item):
        token, mid, home, away, sel, kickoff, won, vol, fee = item
        try:
            hist = http.get(f"{CLOB}/prices-history", params={"market": token, "startTs": kickoff - 30 * 3600,
                                                              "endTs": kickoff, "fidelity": 60}).json().get("history", [])
        except Exception:
            hist = []
        row = {"token": token, "match_id": mid, "home": home, "away": away, "selection": sel,
               "kickoff_ts": kickoff, "result": "yes" if won == "1" else "no" if won == "0" else None,
               "volume": vol, "fee_rate": fee}
        for off in OFFSETS_H:
            t = kickoff - off * 3600
            pts = [x for x in hist if x["t"] <= t]
            row[f"price_{off}h"] = float(pts[-1]["p"]) if pts else np.nan
        return row

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(work, todo))
    new = pd.DataFrame(rows)
    out = pd.concat([cached, new], ignore_index=True) if len(cached) else new
    out.to_csv(path, index=False)
    print(f"polymarket: {len(new)} new contracts, {len(out)} total")
    return out
