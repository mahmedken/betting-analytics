"""Price paths of settled Kalshi and Polymarket EPL contracts.

For every settled contract in the match series (result, total goals, winning
margin, both teams to score), read the price at fixed offsets before kick-off.
Output is long format, one row per (contract, offset), cached in
data/raw/venues/paths_{venue}.csv and extended incrementally.

Kalshi rows have the executable bid and ask. Polymarket rows have the CLOB
history price only (no bid/ask history).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import http
from .teams import canonical
from .venue_history import CACHE, CLOB, GAMMA, KALSHI, _is_moneyline, _match_lookup, _ts

OFFSETS_H = (336, 168, 72, 48, 24, 6, 1)
SERIES = {"KXEPLGAME": "1x2", "KXEPLTOTAL": "total", "KXEPLSPREAD": "margin", "KXEPLBTTS": "btts"}
_RULE = re.compile(r"the ([A-Z][^,]*?) vs ([A-Z].*?) (?:professional )?EPL (?:soccer )?(?:game|match) originally scheduled for (\w+ \d+, \d{4})")


class _Rate:
    """Simple limiter: at most `per_s` calls per second across threads."""
    def __init__(self, per_s: float):
        self.gap, self.lock, self.next = 1.0 / per_s, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


def _kalshi_markets(series: str) -> list[dict]:
    ms = []
    for path in ("/historical/markets", "/markets"):
        cursor = None
        while True:
            params = {"series_ticker": series, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            j = http.get(KALSHI + path, params=params).json()
            ms += [dict(m, _hist=path.startswith("/historical")) for m in j.get("markets", [])
                   if m.get("status") in ("finalized", "settled")]
            cursor = j.get("cursor")
            if not cursor or not j.get("markets"):
                break
    return ms


def _kalshi_selection(m: dict, market: str, home: str, away: str):
    sub = m.get("yes_sub_title", "")
    if market == "1x2":
        if sub.lower() in ("tie", "draw"):
            return "D", None
        return ("H" if canonical(sub) == home else "A"), None
    if market == "total":
        return "over", float(m["floor_strike"])
    if market == "btts":
        return "yes", None
    team = canonical(re.split(r"\s+wins by", sub)[0])
    return ("home" if team == home else "away"), float(m["floor_strike"])


def fetch_kalshi_paths(df: pd.DataFrame, series=tuple(SERIES)) -> pd.DataFrame:
    path = CACHE / "paths_kalshi.csv"
    cached = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["ticker"])
    done = set(cached["ticker"])
    look = _match_lookup(df)
    todo = []
    for s in series:
        market = SERIES[s]
        for m in _kalshi_markets(s):
            if m["ticker"] in done:
                continue
            g = _RULE.search(m.get("rules_primary", ""))
            if not g:
                continue
            try:
                home, away = canonical(g.group(1)), canonical(g.group(2))
                date = dt.datetime.strptime(g.group(3), "%b %d, %Y").strftime("%Y-%m-%d")
                sel, line = _kalshi_selection(m, market, home, away)
            except (KeyError, ValueError, TypeError):
                continue
            if (home, away, date) not in look:
                continue
            kickoff, mid = look[(home, away, date)]
            todo.append((s, m, market, sel, line, home, away, kickoff, mid))
    rate = _Rate(8)

    def work(item):
        s, m, market, sel, line, home, away, kickoff, mid = item
        p = (f"/historical/markets/{m['ticker']}/candlesticks" if m["_hist"]
             else f"/series/{s}/markets/{m['ticker']}/candlesticks")
        rate.wait()
        try:
            c = http.get(KALSHI + p, params={"start_ts": kickoff - (max(OFFSETS_H) + 2) * 3600,
                                             "end_ts": kickoff, "period_interval": 60}).json().get("candlesticks", [])
        except Exception:
            c = []
        out = []
        for off in OFFSETS_H:
            t = kickoff - off * 3600
            prior = [x for x in c if x["end_period_ts"] <= t]
            last = prior[-1] if prior else None
            bid = last.get("yes_bid", {}).get("close") if last else None
            ask = last.get("yes_ask", {}).get("close") if last else None
            out.append({"ticker": m["ticker"], "match_id": mid, "market": market, "selection": sel, "line": line,
                        "offset_h": off, "bid": float(bid) if bid else np.nan, "ask": float(ask) if ask else np.nan,
                        "result": m.get("result"), "volume": float(m.get("volume_fp") or 0)})
        return out

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for rs in ex.map(work, todo) for r in rs]
    new = pd.DataFrame(rows)
    out = pd.concat([cached, new], ignore_index=True) if len(cached) else new
    out.to_csv(path, index=False)
    print(f"kalshi paths: {len(todo)} new contracts; {out['ticker'].nunique()} total")
    return out


_MAIN = re.compile(r"^epl-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2}(-more-markets)?$")


def fetch_polymarket_paths(df: pd.DataFrame) -> pd.DataFrame:
    path = CACHE / "paths_polymarket.csv"
    cached = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["token"])
    done = set(cached["token"].astype(str))
    look = _match_lookup(df)
    events, off = [], 0
    while True:
        b = http.get(f"{GAMMA}/events", params={"series_id": 10188, "closed": "true", "limit": 100, "offset": off}).json()
        events += [e for e in b if _MAIN.match(e.get("slug", ""))]
        if len(b) < 100:
            break
        off += 100
    todo = []
    for e in events:
        try:
            title = re.sub(r"\s+-\s+.*$", "", e["title"])
            h, a = re.split(r"\s+vs\.?\s+", title, maxsplit=1)
            home, away = canonical(h), canonical(a)
        except (KeyError, ValueError):
            continue
        for m in e.get("markets", []):
            start = m.get("gameStartTime")
            if not start or (home, away, start[:10]) not in look:
                continue
            kickoff, mid = look[(home, away, start[:10])]
            kind = m.get("sportsMarketType")
            line = m.get("line")
            try:
                if _is_moneyline(m):
                    gt = m.get("groupItemTitle", "")
                    market, sel, ln = "1x2", ("D" if gt.lower().startswith("draw") else "H" if canonical(gt) == home else "A"), None
                elif kind == "totals" and line in (1.5, 2.5, 3.5):
                    market, sel, ln = "total", "over", float(line)
                elif kind == "both_teams_to_score":
                    market, sel, ln = "btts", "yes", None
                elif kind == "spreads" and line is not None and float(line) in (-1.5, -2.5):
                    outcomes = json.loads(m.get("outcomes") or "[]")
                    market, sel, ln = "margin", ("home" if canonical(outcomes[0]) == home else "away"), -float(line)
                else:
                    continue
            except (KeyError, ValueError, IndexError):
                continue
            token = json.loads(m["clobTokenIds"])[0]
            if token in done:
                continue
            won = json.loads(m.get("outcomePrices") or "[]")
            result = "yes" if won and won[0] == "1" else "no" if won and won[0] == "0" else None
            fee = (m.get("feeSchedule") or {}).get("rate")
            todo.append((token, mid, market, sel, ln, kickoff, result, float(m.get("volume") or 0), fee))
    rate = _Rate(10)

    def work(item):
        token, mid, market, sel, ln, kickoff, result, vol, fee = item
        rate.wait()
        try:
            hist = http.get(f"{CLOB}/prices-history", params={"market": token, "startTs": kickoff - (max(OFFSETS_H) + 2) * 3600,
                                                              "endTs": kickoff, "fidelity": 60}).json().get("history", [])
        except Exception:
            hist = []
        out = []
        for o in OFFSETS_H:
            t = kickoff - o * 3600
            pts = [x for x in hist if x["t"] <= t]
            out.append({"token": token, "match_id": mid, "market": market, "selection": sel, "line": ln,
                        "offset_h": o, "price": float(pts[-1]["p"]) if pts else np.nan, "result": result,
                        "volume": vol, "fee_rate": fee})
        return out

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for rs in ex.map(work, todo) for r in rs]
    new = pd.DataFrame(rows)
    out = pd.concat([cached, new], ignore_index=True) if len(cached) else new
    out.to_csv(path, index=False)
    print(f"polymarket paths: {len(todo)} new contracts; {out['token'].nunique()} total")
    return out


def fetch_kalshi_candles(df: pd.DataFrame, hours: int = 96) -> pd.DataFrame:
    """Hourly candles for the last `hours` before kick-off of every settled Kalshi 1X2 contract
    (used by evaluation.maker). Incremental: tickers already cached are skipped."""
    path = CACHE / "kalshi_1x2_candles.csv.gz"
    cached = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["ticker"])
    done = set(cached["ticker"])
    look = _match_lookup(df)
    todo = []
    for m in _kalshi_markets("KXEPLGAME"):
        if m["ticker"] in done:
            continue
        g = _RULE.search(m.get("rules_primary", ""))
        if not g:
            continue
        try:
            home, away = canonical(g.group(1)), canonical(g.group(2))
            date = dt.datetime.strptime(g.group(3), "%b %d, %Y").strftime("%Y-%m-%d")
            sel, _ = _kalshi_selection(m, "1x2", home, away)
        except (KeyError, ValueError, TypeError):
            continue
        if (home, away, date) in look:
            todo.append((m, sel, *look[(home, away, date)]))
    rate = _Rate(8)

    def f(d, k):
        return float(d[k]) if d and d.get(k) not in (None, "") else None

    def work(it):
        m, sel, kickoff, mid = it
        p = (f"/historical/markets/{m['ticker']}/candlesticks" if m["_hist"]
             else f"/series/KXEPLGAME/markets/{m['ticker']}/candlesticks")
        rate.wait()
        try:
            c = http.get(KALSHI + p, params={"start_ts": kickoff - hours * 3600, "end_ts": kickoff,
                                             "period_interval": 60}).json().get("candlesticks", [])
        except Exception:
            c = []
        return [{"ticker": m["ticker"], "match_id": mid, "selection": sel, "kickoff_ts": kickoff,
                 "result": m.get("result"), "t": x["end_period_ts"], "bid": f(x.get("yes_bid"), "close"),
                 "ask": f(x.get("yes_ask"), "close"), "lo": f(x.get("price"), "low"), "hi": f(x.get("price"), "high"),
                 "vol": float(x.get("volume") or 0)} for x in c]

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for rs in ex.map(work, todo) for r in rs]
    out = pd.concat([cached, pd.DataFrame(rows)], ignore_index=True) if rows else cached
    out.to_csv(path, index=False)
    print(f"kalshi candles: {len(todo)} new contracts; {out['ticker'].nunique()} total")
    return out
