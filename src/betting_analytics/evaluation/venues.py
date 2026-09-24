"""How good are Kalshi and Polymarket prices for EPL 1X2, and can they be beaten?

Uses the settled-contract price history (data.venue_history) joined to the
walk-forward backtest forecasts. Prices are read 24 hours and 1 hour before
kick-off.

Cost of a contract:
  Kalshi      yes_ask + 0.07 * p * (1 - p) (exact executable price and taker fee)
  Polymarket  price + 0.5 cents + fee_rate * p * (1 - p). The price history has
              no bid/ask, so half a cent of spread is assumed; this is stated
              wherever the numbers are shown.

Timing: the fair forecast uses bookmaker odds that football-data.co.uk
collects before the match (Friday for weekend games, Tuesday for midweek). At
1 hour before kick-off those odds are always older than the venue price, so
the comparison is conservative for the fair forecast. At 24 hours, midweek
matches can have bookmaker odds collected after the venue price; the 1-hour
results are therefore the primary ones.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .. import config
from ..data import dataset, venue_history
from ..models.pool import LogPool
from . import metrics

OUT_JSON = config.SITE_DATA / "venues.json"
POLY_HALF_SPREAD = 0.005
KALSHI_FEE = 0.07


def _wide_kalshi(k: pd.DataFrame, off: int) -> pd.DataFrame:
    k = k.copy()
    k["mid"] = (k[f"bid_{off}h"] + k[f"ask_{off}h"]) / 2
    k["ask"] = k[f"ask_{off}h"]
    k["bid"] = k[f"bid_{off}h"]
    w = k.pivot_table(index="match_id", columns="selection", values=["mid", "ask", "bid", "volume"], aggfunc="first")
    w.columns = [f"{a}_{b.lower()}" for a, b in w.columns]
    return w.reset_index()


def _wide_poly(p: pd.DataFrame, off: int) -> pd.DataFrame:
    p = p.copy()
    p["mid"] = p[f"price_{off}h"]
    p["fee_rate"] = p["fee_rate"].fillna(0.0)
    w = p.pivot_table(index="match_id", columns="selection", values=["mid", "volume", "fee_rate"], aggfunc="first")
    w.columns = [f"{a}_{b.lower()}" for a, b in w.columns]
    return w.reset_index()


def _cost(venue: str, price: np.ndarray, fee_rate: np.ndarray | None = None) -> np.ndarray:
    if venue == "kalshi":
        return price + KALSHI_FEE * price * (1 - price)
    fr = np.zeros_like(price) if fee_rate is None else np.nan_to_num(fee_rate)
    q = price + POLY_HALF_SPREAD
    return q + fr * q * (1 - q)


def evaluate_venue(venue: str, wide: pd.DataFrame, pred: pd.DataFrame, off: int) -> dict:
    d = wide.merge(pred, on="match_id", how="inner")
    mids = d[["mid_h", "mid_d", "mid_a"]].values
    ok = np.isfinite(mids).all(axis=1) & (mids.sum(axis=1) > 0.5)
    d = d[ok].copy()
    mids = mids[ok]
    p_venue = mids / mids.sum(axis=1, keepdims=True)
    res = d["result"].values
    days = d["date"].astype(str).values
    out = {"venue": venue, "offset_hours": off, "n_matches": int(len(d))}
    if len(d) < 30:
        return out

    if venue == "kalshi":
        asks = d[["ask_h", "ask_d", "ask_a"]].values
        out["mean_overround_at_ask"] = float(np.nanmean(asks.sum(axis=1) - 1))
        out["mean_spread"] = float(np.nanmean(d[["ask_h", "ask_d", "ask_a"]].values - d[["bid_h", "bid_d", "bid_a"]].values))
    else:
        out["mean_overround_mid"] = float(np.mean(mids.sum(axis=1) - 1))

    forecasts = {
        venue: p_venue,
        "bookmaker_pre": d[["mkt_pre_h", "mkt_pre_d", "mkt_pre_a"]].values,
        "bookmaker_close": d[["mkt_close_h", "mkt_close_d", "mkt_close_a"]].values,
        "fair": d[["fair_h", "fair_d", "fair_a"]].values,
        "model": d[["dc_h", "dc_d", "dc_a"]].values,
    }
    rps = {}
    acc = {}
    for name, p in forecasts.items():
        good = np.isfinite(p).all(axis=1)
        if good.sum() < len(p) * 0.9:
            continue
        r = metrics.rps(np.where(good[:, None], p, p_venue), res)
        rps[name] = r
        lo, hi = metrics.cluster_bootstrap_mean(r, days, n_boot=1000)
        acc[name] = {"rps": float(r.mean()), "rps_ci90": [lo, hi],
                     "log_loss": float(metrics.log_loss(np.where(good[:, None], p, p_venue), res).mean())}
    out["accuracy"] = acc
    out["dm_venue_vs"] = {k: metrics.diebold_mariano(rps[venue], v, days) for k, v in rps.items() if k != venue}

    y = metrics.onehot(res)
    enc = {}
    for other in ("bookmaker_close", "fair"):
        po = forecasts[other]
        good = np.isfinite(po).all(axis=1)
        pool = LogPool.fit(po[good], p_venue[good], y[good])
        enc[f"{other}_with_{venue}"] = {"weight_other": pool.a, "se_other": pool.se_a,
                                         "weight_venue": pool.b, "se_venue": pool.se_b, "n": pool.n}
    out["encompassing"] = enc

    # Calibration and the favourite-longshot pattern, on individual contracts.
    flat_p = mids.ravel()
    flat_y = y.ravel()
    t = metrics.calibration_table(flat_p, flat_y, [0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0])
    out["calibration"] = t.to_dict(orient="records")
    if venue == "kalshi":
        price_buy = d[["ask_h", "ask_d", "ask_a"]].values.ravel()
        cost = _cost("kalshi", price_buy)
    else:
        fee = d[["fee_rate_h", "fee_rate_d", "fee_rate_a"]].values.ravel() if "fee_rate_h" in d else None
        cost = _cost("polymarket", flat_p, fee)
    ret = np.where(flat_y == 1, 1 / cost - 1, -1.0)
    bucket_rows = []
    edges = [0, 0.1, 0.2, 0.35, 0.5, 0.65, 1.0]
    days3 = np.repeat(days, 3)
    for lo_, hi_ in zip(edges[:-1], edges[1:]):
        m = (cost >= lo_) & (cost < hi_) & np.isfinite(ret)
        if m.sum() < 20:
            continue
        clo, chi = metrics.cluster_bootstrap_mean(ret[m], days3[m], n_boot=1000)
        bucket_rows.append({"cost_lo": lo_, "cost_hi": hi_, "n": int(m.sum()), "mean_cost": float(cost[m].mean()),
                            "win_rate": float(flat_y[m].mean()), "roi": float(ret[m].mean()), "roi_ci90": [clo, chi]})
    out["buy_every_contract_by_price"] = bucket_rows

    # Every contract of one outcome type, with closing line value against the
    # de-margined bookmaker close (a far less noisy measure than profit).
    q_close_all = forecasts["bookmaker_close"].ravel()
    clv_all = q_close_all / cost - 1
    gap_all = flat_p - q_close_all
    sel_all = np.tile(np.array(["H", "D", "A"]), len(d))
    sel_rows = []
    for sel in ("H", "D", "A"):
        m = (sel_all == sel) & np.isfinite(ret) & np.isfinite(clv_all)
        clo, chi = metrics.cluster_bootstrap_mean(ret[m], days3[m], n_boot=1000)
        vlo, vhi = metrics.cluster_bootstrap_mean(clv_all[m], days3[m], n_boot=1000)
        sel_rows.append({"selection": sel, "n": int(m.sum()), "mean_price_minus_close": float(gap_all[m].mean()),
                         "roi": float(ret[m].mean()), "roi_ci90": [clo, chi],
                         "clv": float(clv_all[m].mean()), "clv_ci90": [vlo, vhi]})
    out["buy_every_contract_by_outcome"] = sel_rows

    # Strategy: buy when a forecast says the contract is cheap.
    strategies = []
    q_close = forecasts["bookmaker_close"].ravel()
    for fname in ("fair", "model", "bookmaker_close"):
        pf = forecasts[fname].ravel()
        edge = pf / cost - 1
        for thr in (0.0, 0.02, 0.05, 0.10):
            m = np.isfinite(edge) & (edge > thr) & np.isfinite(ret)
            if m.sum() < 10:
                strategies.append({"forecast": fname, "threshold": thr, "n_bets": int(m.sum())})
                continue
            r = ret[m]
            clo, chi = metrics.cluster_bootstrap_mean(r, days3[m], n_boot=1000)
            clv = q_close[m] / cost[m] - 1
            clv_ok = np.isfinite(clv)
            vlo, vhi = (metrics.cluster_bootstrap_mean(clv[clv_ok], days3[m][clv_ok], n_boot=1000)
                        if clv_ok.sum() > 10 else (np.nan, np.nan))
            strategies.append({"forecast": fname, "threshold": thr, "n_bets": int(m.sum()),
                               "roi": float(r.mean()), "roi_ci90": [clo, chi],
                               "clv": float(np.nanmean(clv)), "clv_ci90": [vlo, vhi],
                               "mean_edge": float(edge[m].mean()), "win_rate": float(flat_y[m].mean())})
    out["strategies"] = strategies
    # "bookmaker_close" is not known before kick-off; it is included as an
    # upper bound: what a perfectly informed sharp price would have made.
    return out


def run(fetch: bool = True) -> dict:
    df = dataset.load()
    if fetch:
        k = venue_history.fetch_kalshi(df)
        p = venue_history.fetch_polymarket(df)
    else:
        k = pd.read_csv(venue_history.CACHE / "kalshi_epl_1x2.csv")
        p = pd.read_csv(venue_history.CACHE / "polymarket_epl_1x2.csv")
    pred = pd.read_csv(config.DATA / "model" / "backtest_predictions.csv")
    report = {"generated_utc": config.utcnow().isoformat(),
              "assumptions": {"kalshi_cost": "yes_ask + 0.07*p*(1-p)",
                              "polymarket_cost": f"price + {POLY_HALF_SPREAD} + fee_rate*p*(1-p) (no historical bid/ask)"},
              "results": []}
    for off in venue_history.OFFSETS_H:
        report["results"].append(evaluate_venue("kalshi", _wide_kalshi(k, off), pred, off))
        report["results"].append(evaluate_venue("polymarket", _wide_poly(p, off), pred, off))
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    from .report import _clean
    OUT_JSON.write_text(json.dumps(_clean(report), separators=(",", ":")))
    for r in report["results"]:
        if "accuracy" not in r:
            continue
        a = r["accuracy"]
        print(f"{r['venue']:10s} T-{r['offset_hours']}h n={r['n_matches']} " +
              " ".join(f"{k}={v['rps']:.4f}" for k, v in a.items()))
        print("   dm venue vs close p=%.3f" % r["dm_venue_vs"].get("bookmaker_close", {}).get("p_value", np.nan),
              "| enc:", {k: (round(v["weight_other"], 2), round(v["se_other"], 2)) for k, v in r["encompassing"].items()})
        for s in r["strategies"]:
            if s.get("roi") is not None and s["threshold"] in (0.02, 0.05):
                print(f"   {s['forecast']:16s} thr {s['threshold']:.2f} n {s['n_bets']:4d} roi {s['roi']:+.3f} "
                      f"[{s['roi_ci90'][0]:+.3f},{s['roi_ci90'][1]:+.3f}] clv {s['clv']:+.4f}")
        for b in r["buy_every_contract_by_price"]:
            print(f"   cost {b['cost_lo']:.2f}-{b['cost_hi']:.2f} n {b['n']:4d} win {b['win_rate']:.3f} roi {b['roi']:+.3f}")
    return report
