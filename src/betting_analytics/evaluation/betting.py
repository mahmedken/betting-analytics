"""Betting simulation on out-of-sample forecasts.

For each match, the selection with the largest expected value
    edge = p_model * odds - 1
is bet if the edge exceeds a threshold. Odds are the pre-match prices from the
chosen source (never the closing price, which is not available when the
forecast is made). Closing line value (CLV) is the expected return of the bet
under the de-margined closing price, odds * q_close - 1; it measures whether
the bet beat the final market price and is far less noisy than profit.

Stakes are either flat (1 unit) or fractional Kelly on a fixed 100-unit
bankroll (not compounded), capped at 5 units per bet.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics

BETFAIR_COMMISSION = 0.02
# A "best price" more than this many probability points longer than the market
# average is treated as a stale or erroneous quote and dropped. Added after
# finding that in the 2026-27 football-data.co.uk file, 28% of maximum
# over-2.5 prices sat more than 20% above the average (0% in every earlier
# season), e.g. 2.50 against an average of 1.93.
MAX_PRICE_GAP = 0.05


def _selections(market: str):
    if market == "1x2":
        return ["h", "d", "a"]
    return ["o25", "u25"]


def candidate_bets(pred: pd.DataFrame, odds: pd.DataFrame, model: str, market: str,
                   price: str, close_prefix: str = "mkt_close") -> pd.DataFrame:
    """All (match, selection) pairs with model probability, pre-match odds and closing probability.

    pred: forecasts with columns {model}_h/d/a or {model}_o25, result, hg, ag,
          and de-margined closing probabilities mkt_close_*.
    odds: raw odds table (the match table) with columns {price}_{sel}.
    """
    cols = [f"{price}_{s}" for s in _selections(market)]
    if price.startswith("max"):
        cols += [f"avg_{price.split('_', 1)[1]}_{s}" for s in _selections(market)]
    d = pred.merge(odds[["match_id"] + cols], on="match_id", how="left")
    rows = []
    for s in _selections(market):
        if market == "1x2":
            p = d[f"{model}_{s}"].values
            q = d[f"{close_prefix}_{s}"].values
            won = (d["result"].str.lower() == s).values
        else:
            po = d[f"{model}_o25"].values
            qo = d[f"{close_prefix}_o25"].values
            over = ((d["hg"] + d["ag"]) > 2.5).values
            p = po if s == "o25" else 1 - po
            q = qo if s == "o25" else 1 - qo
            won = over if s == "o25" else ~over
        o = d[f"{price}_{s}"].values.astype(float)
        if price.startswith("max"):
            avg = d[f"avg_{price.split('_', 1)[1]}_{s}"].values.astype(float)
            o = np.where(1 / avg - 1 / o > MAX_PRICE_GAP, np.nan, o)
        if price.startswith("bfe"):
            o = 1 + (o - 1) * (1 - BETFAIR_COMMISSION)
        rows.append(pd.DataFrame({"match_id": d["match_id"].values, "season": d["season"].values,
                                  "date": d["date"].values, "selection": s, "p": p, "odds": o,
                                  "q_close": q, "won": won}))
    c = pd.concat(rows, ignore_index=True)
    c = c[np.isfinite(c["p"]) & np.isfinite(c["odds"])]
    c["edge"] = c["p"] * c["odds"] - 1
    c["clv"] = c["odds"] * c["q_close"] - 1
    return c


def select_bets(cand: pd.DataFrame, threshold: float, staking: str = "flat",
                kelly_fraction: float = 0.25) -> pd.DataFrame:
    """At most one bet per match: the selection with the largest positive edge."""
    best = cand.sort_values("edge", ascending=False).drop_duplicates("match_id")
    bets = best[best["edge"] > threshold].copy()
    if staking == "flat":
        bets["stake"] = 1.0
    else:
        f = (bets["p"] * bets["odds"] - 1) / (bets["odds"] - 1)
        bets["stake"] = np.clip(kelly_fraction * f * 100, 0, 5)
    bets["profit"] = np.where(bets["won"], bets["stake"] * (bets["odds"] - 1), -bets["stake"])
    return bets.sort_values("date")


def summarise(bets: pd.DataFrame, n_boot: int = 2000) -> dict:
    if bets.empty:
        return {"n_bets": 0}
    staked = bets["stake"].sum()
    profit = bets["profit"].sum()
    roi_per_bet = bets["profit"] / bets["stake"].mean()   # so the mean equals profit / staked
    lo, hi = metrics.cluster_bootstrap_mean(roi_per_bet.values, bets["date"].values, n_boot=n_boot)
    clv_lo, clv_hi = (metrics.cluster_bootstrap_mean(bets["clv"].dropna().values,
                                                     bets.loc[bets["clv"].notna(), "date"].values,
                                                     n_boot=n_boot)
                      if bets["clv"].notna().sum() > 10 else (np.nan, np.nan))
    cum = bets["profit"].cumsum().values
    drawdown = float(np.max(np.maximum.accumulate(np.concatenate([[0], cum]))[1:] - cum)) if len(cum) else 0.0
    return {
        "n_bets": int(len(bets)),
        "staked": float(staked),
        "profit": float(profit),
        "roi": float(profit / staked),
        "roi_ci90": [lo, hi],
        "hit_rate": float(bets["won"].mean()),
        "mean_odds": float(bets["odds"].mean()),
        "mean_edge": float(bets["edge"].mean()),
        "clv": float(bets["clv"].mean()) if bets["clv"].notna().any() else None,
        "clv_ci90": [clv_lo, clv_hi],
        "max_drawdown": drawdown,
    }


def edge_buckets(cand: pd.DataFrame, edges=(0.0, 0.02, 0.05, 0.1, 0.2, np.inf)) -> pd.DataFrame:
    """Realised return and CLV of every candidate bet, grouped by model edge.

    Uses every selection with a positive edge (not only the best per match),
    flat stakes. This is the table the dashboard uses to show how bets of a
    given claimed edge have actually performed.
    """
    c = cand[cand["edge"] > edges[0]].copy()
    c["bucket"] = pd.cut(c["edge"], bins=list(edges), right=False)
    rows = []
    for b, g in c.groupby("bucket", observed=True):
        ret = np.where(g["won"], g["odds"] - 1, -1.0)
        lo, hi = metrics.cluster_bootstrap_mean(ret, g["date"].values, n_boot=1000)
        clv = g["clv"].dropna()
        rows.append({"edge_lo": float(b.left), "edge_hi": float(b.right), "n": int(len(g)),
                     "mean_edge": float(g["edge"].mean()), "roi": float(ret.mean()),
                     "roi_ci90": [lo, hi], "clv": float(clv.mean()) if len(clv) else None,
                     "hit_rate": float(g["won"].mean())})
    return pd.DataFrame(rows)
