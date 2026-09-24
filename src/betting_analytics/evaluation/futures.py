"""Are season markets (title, top four, relegation) consistent with match markets?

For past seasons, every 14 days: fit market-implied team ratings from the
closing prices of matches played so far, simulate the rest of the season
(with rating drift), and compare the simulated probabilities and Kalshi's
season-market prices against how the season actually ended.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data import http
from ..data.venue_history import CACHE, KALSHI
from ..data.teams import canonical
from ..live import season_sim
from ..models import market_ratings as mr
from . import metrics

EVENTS = {("title", 2025): "KXPREMIERLEAGUE-26", ("top4", 2025): "KXEPLTOP4-26",
          ("relegation", 2025): "KXEPLRELEGATION-26", ("title", 2024): "KXPREMIERLEAGUE-25"}


def fetch_candles() -> pd.DataFrame:
    path = CACHE / "kalshi_futures_daily.csv"
    if path.exists():
        return pd.read_csv(path)
    rows = []
    for (market, season), ev in EVENTS.items():
        series = ev.rsplit("-", 1)[0]
        ms = http.get(KALSHI + "/historical/markets", params={"event_ticker": ev, "limit": 100}).json().get("markets", [])
        for m in ms:
            try:
                team = canonical(m["yes_sub_title"])
            except KeyError:
                continue
            c = http.get(KALSHI + f"/historical/markets/{m['ticker']}/candlesticks",
                         params={"start_ts": 1719800000, "end_ts": 1781000000, "period_interval": 1440}).json().get("candlesticks", [])
            for x in c:
                b, a = x.get("yes_bid", {}).get("close"), x.get("yes_ask", {}).get("close")
                rows.append({"market": market, "season": season, "team": team, "t": x["end_period_ts"],
                             "bid": float(b) if b else np.nan, "ask": float(a) if a else np.nan,
                             "result": m.get("result")})
    d = pd.DataFrame(rows)
    d.to_csv(path, index=False)
    return d


def final_positions(df: pd.DataFrame, season: int) -> dict:
    s = df[(df["season"] == season) & df["hg"].notna()]
    t = {}
    for r in s.itertuples():
        for team, gf, ga in ((r.home, r.hg, r.ag), (r.away, r.ag, r.hg)):
            p, g, f = t.get(team, (0, 0, 0))
            t[team] = (p + (3 if gf > ga else 1 if gf == ga else 0), g + gf - ga, f + gf)
    order = sorted(t, key=lambda k: t[k], reverse=True)
    return {team: i + 1 for i, team in enumerate(order)}


def run(df: pd.DataFrame, rates: pd.DataFrame, drift: float, n_sims: int = 3000) -> dict:
    fut = fetch_candles()
    fut["date"] = pd.to_datetime(fut["t"], unit="s", utc=True).dt.normalize()
    rows = []
    for season in sorted({s for (_, s) in EVENTS}):
        sm = df[df["season"] == season]
        teams = sorted(set(sm["home"]))
        pos = final_positions(df, season)
        outcome = {"title": {t: pos[t] == 1 for t in teams}, "top4": {t: pos[t] <= 4 for t in teams},
                   "relegation": {t: pos[t] >= 18 for t in teams}}
        start = sm["kickoff_utc"].min() + pd.Timedelta(days=45)
        end = sm["kickoff_utc"].max() - pd.Timedelta(days=21)
        for date in pd.date_range(start.normalize(), end, freq="14D"):
            played = sm[sm["kickoff_utc"] < date][["home", "away", "hg", "ag"]]
            remaining = sm[sm["kickoff_utc"] >= date][["home", "away", "kickoff_utc"]]
            m = mr.fit(rates, date, teams).model
            sim = season_sim.simulate(m, played, remaining, teams, n_sims=n_sims, seed=int(date.timestamp()) % 100000,
                                      draw_params=False, drift_sd_per_week=drift, now=date)
            simp = {r["team"]: r for r in sim["teams"]}
            quotes = fut[(fut["season"] == season) & (fut["date"] == date.normalize())]
            for market in ("title", "top4", "relegation"):
                if (market, season) not in EVENTS:
                    continue
                for t in teams:
                    q = quotes[(quotes["market"] == market) & (quotes["team"] == t)]
                    bid = float(q["bid"].iloc[0]) if len(q) else np.nan
                    ask = float(q["ask"].iloc[0]) if len(q) else np.nan
                    rows.append({"season": season, "date": date, "market": market, "team": t,
                                 "sim": simp[t][market], "bid": bid, "ask": ask,
                                 "outcome": float(outcome[market][t])})
    return pd.DataFrame(rows)


def score(r: pd.DataFrame, max_spread: float = 0.06) -> dict:
    q = r[(r["ask"] - r["bid"] <= max_spread) & (r["bid"] >= 0) & r["ask"].notna()].copy()
    q["mid"] = (q["bid"] + q["ask"]) / 2
    out = {"n": int(len(q)), "by_market": {}}
    for mkt, g in q.groupby("market"):
        out["by_market"][mkt] = {"n": int(len(g)),
                                 "brier_sim": float(((g["sim"] - g["outcome"]) ** 2).mean()),
                                 "brier_market": float(((g["mid"] - g["outcome"]) ** 2).mean())}
    out["brier_sim"] = float(((q["sim"] - q["outcome"]) ** 2).mean())
    out["brier_market"] = float(((q["mid"] - q["outcome"]) ** 2).mean())
    dm = metrics.diebold_mariano((q["sim"] - q["outcome"]) ** 2, (q["mid"] - q["outcome"]) ** 2, q["date"].astype(str))
    out["dm"] = dm
    # trade when the simulation disagrees with the executable price by > 3 points (taker, with fee)
    fee = lambda p: 0.07 * p * (1 - p)
    trades = []
    for x in q.itertuples():
        if x.sim - (x.ask + fee(x.ask)) > 0.03:
            c = x.ask + fee(x.ask)
            trades.append((x.date, x.market, x.team, "yes", c, (1 / c - 1) if x.outcome else -1.0))
        no_cost = 1 - x.bid + fee(1 - x.bid)
        if (1 - x.sim) - no_cost > 0.03:
            trades.append((x.date, x.market, x.team, "no", no_cost, (1 / no_cost - 1) if not x.outcome else -1.0))
    t = pd.DataFrame(trades, columns=["date", "market", "team", "side", "cost", "ret"])
    if len(t):
        lo, hi = metrics.cluster_bootstrap_mean(t["ret"].values, t["team"].values, 1000)
        out["trades"] = {"n": int(len(t)), "roi": float(t["ret"].mean()), "roi_ci90_by_team": [lo, hi],
                         "by_side": t.groupby("side")["ret"].agg(["count", "mean"]).round(4).to_dict()}
    return out
