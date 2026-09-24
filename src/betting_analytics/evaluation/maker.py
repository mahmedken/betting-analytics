"""Limit-order (maker) strategies on Kalshi EPL 1X2 contracts.

Taker trades on Kalshi pay 0.07*p*(1-p) per contract and cross the spread;
the retail biases found in the price data (popular clubs overpriced, draws
underpriced a day or more before kick-off) are smaller than that cost. A
resting limit order pays the maker fee instead (0.0175*p*(1-p) on this
series) and earns the spread, so the same biases may be worth harvesting by
providing liquidity.

Fill model (conservative): an order posted at time T-E at price B is filled
only if a trade prints strictly through B (trade low < B for a bid, trade
high > A for an ask) in a later hourly candle before kick-off. Touching the
price is not enough, and orders are never filled at a better price than
posted. Filled orders are held to settlement.

Benchmark: the de-margined sharp closing price for the same outcome (no
model). CLV = q_close / cost - 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from ..data.venue_history import CACHE
from . import metrics

MAKER_FEE = 0.0175
BIG6 = {"Man United", "Liverpool", "Arsenal", "Chelsea", "Man City", "Tottenham"}


def load(pred: pd.DataFrame) -> pd.DataFrame:
    c = pd.read_csv(CACHE / "kalshi_1x2_candles.csv.gz")
    p = pred.set_index("match_id")
    c = c[c["match_id"].isin(p.index) & c["result"].isin(["yes", "no"])]
    q = np.select([c["selection"] == "H", c["selection"] == "D", c["selection"] == "A"],
                  [p.loc[c["match_id"], "mkt_close_h"].values, p.loc[c["match_id"], "mkt_close_d"].values,
                   p.loc[c["match_id"], "mkt_close_a"].values])
    c = c.assign(q_close=q, date=p.loc[c["match_id"], "date"].values,
                 home=p.loc[c["match_id"], "home"].values, away=p.loc[c["match_id"], "away"].values)
    team = np.where(c["selection"] == "H", c["home"], np.where(c["selection"] == "A", c["away"], ""))
    c["group"] = np.where(c["selection"] == "D", "draw", np.where(pd.Series(team).isin(BIG6).values, "big-six", "other"))
    return c.dropna(subset=["q_close"])


def simulate(c: pd.DataFrame, entry_h: int, side: str, improve: float = 0.0) -> pd.DataFrame:
    """side 'bid': buy YES at bid+improve. side 'ask': sell YES (buy NO) at ask-improve."""
    out = []
    for tk, g in c.groupby("ticker", sort=False):
        g = g.sort_values("t")
        k = g["kickoff_ts"].iloc[0]
        at = g[g["t"] <= k - entry_h * 3600]
        if at.empty:
            continue
        snap = at.iloc[-1]
        bid, ask = snap["bid"], snap["ask"]
        if not (np.isfinite(bid) and np.isfinite(ask)) or bid <= 0 or ask >= 1 or ask - bid < 0.005:
            continue
        later = g[(g["t"] > snap["t"]) & (g["vol"] > 0)]
        won_yes = g["result"].iloc[0] == "yes"
        if side == "bid":
            px = min(bid + improve, ask - 0.01)
            filled = bool((later["lo"] < px).any())
            cost = px + MAKER_FEE * px * (1 - px)
            fair = snap["q_close"]
            won = won_yes
        else:
            px = max(ask - improve, bid + 0.01)
            filled = bool((later["hi"] > px).any())
            cost = (1 - px) + MAKER_FEE * px * (1 - px)
            fair = 1 - snap["q_close"]
            won = not won_yes
        out.append({"ticker": tk, "match_id": snap["match_id"], "date": snap["date"], "group": snap["group"],
                    "spread": ask - bid, "price": px, "filled": filled, "cost": cost, "fair": fair,
                    "clv": fair / cost - 1, "ret": (1 / cost - 1) if won else -1.0})
    return pd.DataFrame(out)


def summarise(r: pd.DataFrame) -> dict:
    f = r[r["filled"]]
    if len(f) < 20:
        return {"n_posted": int(len(r)), "n_filled": int(len(f))}
    cut = f["date"].sort_values().iloc[len(f) // 2]
    lo, hi = metrics.cluster_bootstrap_mean(f["clv"].values, f["date"].astype(str).values, 1000)
    rlo, rhi = metrics.cluster_bootstrap_mean(f["ret"].values, f["date"].astype(str).values, 1000)
    return {"n_posted": int(len(r)), "n_filled": int(len(f)), "fill_rate": float(len(f) / len(r)),
            "clv": float(f["clv"].mean()), "clv_ci90": [lo, hi], "roi": float(f["ret"].mean()), "roi_ci90": [rlo, rhi],
            "clv_discovery": float(f[f["date"] < cut]["clv"].mean()),
            "clv_confirmation": float(f[f["date"] >= cut]["clv"].mean()),
            "mean_price": float(f["price"].mean())}


def run(pred: pd.DataFrame | None = None) -> pd.DataFrame:
    pred = pred if pred is not None else pd.read_csv(config.DATA / "model" / "backtest_predictions.csv")
    c = load(pred)
    rows = []
    for entry in (72, 48, 24, 6):
        for side in ("bid", "ask"):
            for imp in (0.0, 0.01):
                r = simulate(c, entry, side, imp)
                for grp in ("draw", "big-six", "other"):
                    s = summarise(r[r["group"] == grp])
                    rows.append({"entry_h": entry, "side": side, "improve": imp, "group": grp, **s})
    return pd.DataFrame(rows)
