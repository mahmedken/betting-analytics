"""Run the full walk-forward backtest and write site/data/backtest.json.

Models compared on the held-out test seasons:
  elo        goal-difference Elo + ordered logit (baseline)
  dc         Dixon-Coles on a goals/xG blend (the model)
  mkt_pre    de-margined pre-match price of the sharpest available book
  mkt_close  de-margined closing price (the efficient-market benchmark)
  pool       log-linear pool of dc and mkt_pre, weights fitted on earlier seasons
  fair       Dixon-Coles at rates blended between dc and the market-implied
             rates, blend weight fitted on earlier seasons; this is the
             forecast the live dashboard uses for every market.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .. import config
from ..cli import load_model_config, save_model_config
from ..data import dataset
from ..models import dixon_coles as dc
from ..models import elo, implied
from ..models.fitting import DCConfig
from ..models.pool import LogPool
from . import backtest as bt
from . import betting, metrics

OUT_PRED = config.DATA / "model" / "backtest_predictions.csv"
OUT_JSON = config.SITE_DATA / "backtest.json"

MODELS_1X2 = ("elo", "dc", "pool", "fair", "mkt_pre", "mkt_close")


def _wf_season(args):
    df, cfg, season, prior, draws = args
    return bt.walk_forward_dc(df, cfg, [season], prior, draws=draws, seed=season)


def build_predictions(df: pd.DataFrame, mcfg: dict, workers: int = 4) -> pd.DataFrame:
    cfg = DCConfig(**mcfg["dixon_coles"])
    prior = (mcfg["promoted_prior"]["att"], mcfg["promoted_prior"]["dfn"], mcfg["promoted_prior"]["sd"])
    seasons = list(range(bt.VALIDATION_SEASONS[0], int(df.loc[df["hg"].notna(), "season"].max()) + 1))
    with ProcessPoolExecutor(max_workers=workers, initializer=bt._single_thread) as ex:
        parts = list(ex.map(_wf_season, [(df, cfg, s, prior, 300) for s in seasons]))
    pred = pd.concat(parts, ignore_index=True)

    ecfg = elo.EloConfig(**mcfg["elo"])
    pred = pred.merge(bt.elo_forecasts(df, ecfg, seasons), on="match_id", how="left")
    pred = pred.merge(bt.market_probs(df), on="match_id", how="left")
    meta = df[["match_id", "season", "date", "kickoff_utc", "home", "away", "hg", "ag", "result", "hxg", "axg"]]
    pred = meta.merge(pred, on="match_id", how="inner")

    # Market-implied rates from the pre-match price (the price known at forecast time).
    rho = 0.0
    lam_m, nu_m = implied.implied_rates(pred[["mkt_pre_h", "mkt_pre_d", "mkt_pre_a"]].values,
                                        pred["mkt_pre_o25"].values, rho)
    pred["mkt_lam"], pred["mkt_nu"] = lam_m, nu_m
    return pred.sort_values("kickoff_utc").reset_index(drop=True)


def add_combined(pred: pd.DataFrame, first_train: int) -> tuple[pd.DataFrame, list[dict]]:
    """Walk-forward pool and fair forecasts: season S uses fits on seasons [first_train, S-1]."""
    seasons = sorted(s for s in pred["season"].unique() if s > first_train)
    pool_df, pool_fits = bt.pooled_forecasts(pred, seasons, first_train)
    pred = pred.merge(pool_df, on="match_id", how="left")
    fits = []
    for c in ("fair_h", "fair_d", "fair_a", "fair_o25", "fair_btts"):
        pred[c] = np.nan
    for s in seasons:
        tr = (pred["season"] < s) & (pred["season"] >= first_train)
        te = (pred["season"] == s).values
        alpha = implied.fit_alpha(pred.loc[tr, "dc_lam"].values, pred.loc[tr, "dc_nu"].values,
                                  pred.loc[tr, "mkt_lam"].values, pred.loc[tr, "mkt_nu"].values,
                                  0.0, pred.loc[tr, "hg"].values, pred.loc[tr, "ag"].values)
        lam, nu = implied.blend_rates(pred.loc[te, "dc_lam"].values, pred.loc[te, "dc_nu"].values,
                                      pred.loc[te, "mkt_lam"].values, pred.loc[te, "mkt_nu"].values, alpha)
        M = dc.score_matrix(lam, nu, 0.0)
        p = dc.outcome_probs(M)
        pred.loc[te, "fair_h"], pred.loc[te, "fair_d"], pred.loc[te, "fair_a"] = p[:, 0], p[:, 1], p[:, 2]
        pred.loc[te, "fair_o25"] = dc.prob_over(M, 2.5)
        pred.loc[te, "fair_btts"] = dc.prob_btts(M)
        f = next(x for x in pool_fits if x["season"] == s)
        fits.append({**f, "alpha": alpha})
    return pred, fits


def _metric_block(p: np.ndarray, result: np.ndarray, clusters: np.ndarray) -> dict:
    r = metrics.rps(p, result)
    lo, hi = metrics.cluster_bootstrap_mean(r, clusters, n_boot=1000)
    return {**metrics.summary_1x2(p, result), "rps_ci90": [lo, hi]}


def evaluate(pred: pd.DataFrame, test_seasons: list[int]) -> dict:
    d = pred[pred["season"].isin(test_seasons)].dropna(
        subset=[f"{m}_{s}" for m in MODELS_1X2 for s in "hda"]).copy()
    days = d["date"].astype(str).values
    res = d["result"].values
    out = {"n": int(len(d)), "seasons": [config.season_label(s) for s in test_seasons], "models": {}}
    rps_by_model = {}
    for m in MODELS_1X2:
        p = d[[f"{m}_h", f"{m}_d", f"{m}_a"]].values
        out["models"][m] = _metric_block(p, res, days)
        rps_by_model[m] = metrics.rps(p, res)
    out["dm_vs_close"] = {m: metrics.diebold_mariano(rps_by_model[m], rps_by_model["mkt_close"], days)
                          for m in MODELS_1X2 if m != "mkt_close"}
    out["dm_vs_pre"] = {m: metrics.diebold_mariano(rps_by_model[m], rps_by_model["mkt_pre"], days)
                        for m in MODELS_1X2 if m != "mkt_pre"}

    # Per-season RPS
    per = []
    for s in test_seasons:
        g = d[d["season"] == s]
        if g.empty:
            continue
        row = {"season": config.season_label(s), "n": int(len(g))}
        for m in MODELS_1X2:
            row[m] = float(metrics.rps(g[[f"{m}_h", f"{m}_d", f"{m}_a"]].values, g["result"].values).mean())
        per.append(row)
    out["per_season"] = per

    # Over/under 2.5
    o = pred[pred["season"].isin(test_seasons)].dropna(subset=["dc_o25", "fair_o25", "mkt_pre_o25", "mkt_close_o25"])
    y = ((o["hg"] + o["ag"]) > 2.5).astype(float).values
    ou = {}
    for m in ("dc", "fair", "pool", "mkt_pre", "mkt_close"):
        col = f"{m}_o25"
        if col not in o or o[col].isna().any():
            continue
        ou[m] = {"n": int(len(o)), "log_loss": float(metrics.binary_log_loss(o[col].values, y).mean()),
                 "brier": float(metrics.binary_brier(o[col].values, y).mean())}
    out["over_under_25"] = ou
    return out


def calibration(pred: pd.DataFrame, test_seasons: list[int]) -> dict:
    d = pred[pred["season"].isin(test_seasons)].dropna(subset=["dc_h", "fair_h", "mkt_close_h"])
    y = metrics.onehot(d["result"])
    out = {}
    for m in ("dc", "fair", "mkt_close"):
        p = d[[f"{m}_h", f"{m}_d", f"{m}_a"]].values
        t = metrics.calibration_table(p.ravel(), y.ravel(), np.linspace(0, 0.9, 10).tolist() + [1.0])
        out[m] = {"ece": metrics.ece(p.ravel(), y.ravel()), "bins": t.to_dict(orient="records")}
    o = d.dropna(subset=["dc_o25", "mkt_close_o25"])
    yo = ((o["hg"] + o["ag"]) > 2.5).astype(float).values
    for m in ("dc", "fair", "mkt_close"):
        t = metrics.calibration_table(o[f"{m}_o25"].values, yo, np.linspace(0.2, 0.8, 7))
        out[f"{m}_o25"] = {"ece": metrics.ece(o[f"{m}_o25"].values, yo), "bins": t.to_dict(orient="records")}
    return out


def uncertainty_check(pred: pd.DataFrame, test_seasons: list[int]) -> list[dict]:
    """Do wider parameter intervals go with larger model-market disagreement?

    Matches are split into quintiles of the model's posterior sd for P(home
    win); for each, the mean absolute difference between the model and the
    closing market is reported.
    """
    d = pred[pred["season"].isin(test_seasons)].dropna(subset=["dc_h_sd", "mkt_close_h"]).copy()
    d["q"] = pd.qcut(d["dc_h_sd"], 5, labels=False)
    rows = []
    for q, g in d.groupby("q"):
        rows.append({"quintile": int(q) + 1, "n": int(len(g)), "mean_sd": float(g["dc_h_sd"].mean()),
                     "mean_abs_gap_close": float((g["dc_h"] - g["mkt_close_h"]).abs().mean()),
                     "rps_dc": float(metrics.rps(g[["dc_h", "dc_d", "dc_a"]].values, g["result"].values).mean()),
                     "rps_close": float(metrics.rps(g[["mkt_close_h", "mkt_close_d", "mkt_close_a"]].values,
                                                    g["result"].values).mean())})
    return rows


STRATEGIES = [
    # (label, model column prefix, market, price column prefix)
    ("model vs pinnacle, 1x2", "dc", "1x2", "ps_pre"),
    ("fair vs pinnacle, 1x2", "fair", "1x2", "ps_pre"),
    ("model vs best price, 1x2", "dc", "1x2", "max_pre"),
    ("fair vs best price, 1x2", "fair", "1x2", "max_pre"),
    ("model vs bet365, 1x2", "dc", "1x2", "b365_pre"),
    ("model vs pinnacle, o/u 2.5", "dc", "ou", "ps_pre"),
    ("fair vs pinnacle, o/u 2.5", "fair", "ou", "ps_pre"),
    ("model vs best price, o/u 2.5", "dc", "ou", "max_pre"),
    ("fair vs best price, o/u 2.5", "fair", "ou", "max_pre"),
]


CURVES = ("model vs pinnacle, 1x2", "fair vs best price, 1x2", "model vs best price, o/u 2.5",
          "fair vs best price, o/u 2.5")


def betting_results(pred: pd.DataFrame, df: pd.DataFrame, test_seasons: list[int]) -> dict:
    d = pred[pred["season"].isin(test_seasons)]
    out = {"strategies": [], "edge_buckets": {}, "curves": {}}
    for label, model, market, price in STRATEGIES:
        cand = betting.candidate_bets(d, df, model, market, price)
        for thr in (0.0, 0.02, 0.05, 0.10):
            for staking in ("flat", "kelly"):
                bets = betting.select_bets(cand, thr, staking)
                s = betting.summarise(bets, n_boot=1000)
                out["strategies"].append({"label": label, "model": model, "market": market, "price": price,
                                          "threshold": thr, "staking": staking, **s})
                if thr == 0.02 and staking == "flat" and len(bets) and label in CURVES:
                    wk = pd.to_datetime(bets["date"]).dt.to_period("W").dt.start_time.dt.strftime("%Y-%m-%d")
                    weekly = bets.groupby(wk)[["profit", "stake", "clv"]].sum().cumsum()
                    out["curves"][label] = {"date": weekly.index.tolist(),
                                            "profit": weekly["profit"].round(2).tolist(),
                                            "staked": weekly["stake"].round(1).tolist(),
                                            "clv": weekly["clv"].round(2).tolist()}
        if price == "max_pre":
            out["edge_buckets"][label] = betting.edge_buckets(cand).to_dict(orient="records")
    return out


def compact_matches(pred: pd.DataFrame, test_seasons: list[int]) -> dict:
    d = pred[pred["season"].isin(test_seasons)]
    cols = ["match_id", "date", "home", "away", "hg", "ag", "hxg", "axg",
            "dc_h", "dc_d", "dc_a", "fair_h", "fair_d", "fair_a", "mkt_close_h", "mkt_close_d", "mkt_close_a",
            "dc_o25", "mkt_close_o25", "dc_h_q05", "dc_h_q95"]
    rows = []
    for r in d[cols].itertuples(index=False):
        rows.append([r.match_id, str(r.date)[:10], r.home, r.away,
                     None if pd.isna(r.hg) else int(r.hg), None if pd.isna(r.ag) else int(r.ag),
                     *[None if pd.isna(v) else round(float(v), 4) for v in r[6:]]])
    return {"columns": cols, "rows": rows}


def run(workers: int = 4) -> dict:
    df = dataset.load()
    mcfg = load_model_config()
    pred = build_predictions(df, mcfg, workers)
    first_train = bt.VALIDATION_SEASONS[0]
    pred, fits = add_combined(pred, first_train)
    OUT_PRED.parent.mkdir(parents=True, exist_ok=True)
    pred.to_csv(OUT_PRED, index=False, float_format="%.5f")

    last_complete = int(df.groupby("season")["hg"].count().loc[lambda s: s >= 380].index.max())
    test_seasons = [s for s in sorted(pred["season"].unique()) if s >= bt.TEST_FIRST]
    complete_test = [s for s in test_seasons if s <= last_complete]

    # Weights for live use: fitted on every completed season since validation start.
    tr = pred[pred["season"].between(first_train, last_complete)]
    y = metrics.onehot(tr["result"])
    pool_all = LogPool.fit(tr[["dc_h", "dc_d", "dc_a"]].values, tr[["mkt_pre_h", "mkt_pre_d", "mkt_pre_a"]].values, y)
    alpha_all = implied.fit_alpha(tr["dc_lam"].values, tr["dc_nu"].values, tr["mkt_lam"].values,
                                  tr["mkt_nu"].values, 0.0, tr["hg"].values, tr["ag"].values)
    mcfg["live"] = {"alpha": alpha_all, "pool_a": pool_all.a, "pool_b": pool_all.b,
                    "fitted_on": [config.season_label(first_train), config.season_label(last_complete)]}
    save_model_config(mcfg)

    report = {
        "generated_utc": config.utcnow().isoformat(),
        "config": mcfg,
        "protocol": {
            "prior_seasons": [config.season_label(s) for s in bt.PRIOR_SEASONS],
            "validation_seasons": [config.season_label(s) for s in bt.VALIDATION_SEASONS],
            "test_seasons": [config.season_label(s) for s in test_seasons],
        },
        "devig_comparison": bt.compare_devig(df, complete_test).to_dict(orient="records"),
        "test": evaluate(pred, test_seasons),
        "test_complete_seasons": evaluate(pred, complete_test),
        "calibration": calibration(pred, test_seasons),
        "uncertainty": uncertainty_check(pred, test_seasons),
        "encompassing": bt.encompassing_test(pred, complete_test),
        "combination_fits": fits,
        "betting": betting_results(pred, df, test_seasons),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(_clean(report), separators=(",", ":")))
    _print_summary(report)
    return report


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 6)
    if isinstance(x, np.integer):
        return int(x)
    return x


def _print_summary(r: dict) -> None:
    t = r["test"]
    print(f"test seasons {t['seasons'][0]}..{t['seasons'][-1]}, n={t['n']}")
    for m, v in t["models"].items():
        dm = t["dm_vs_close"].get(m, {})
        print(f"  {m:10s} rps {v['rps']:.5f} [{v['rps_ci90'][0]:.4f},{v['rps_ci90'][1]:.4f}] "
              f"logloss {v['log_loss']:.4f} acc {v['accuracy']:.3f} "
              f"dm_vs_close p={dm.get('p_value', float('nan')):.3f}")
    print("  o/u 2.5:", {m: round(v["log_loss"], 5) for m, v in t["over_under_25"].items()})
    print("  encompassing:", json.dumps(_clean(r["encompassing"])))
    for s in r["betting"]["strategies"]:
        if s["threshold"] in (0.02, 0.05) and s["staking"] == "flat" and s.get("n_bets"):
            print(f"  {s['label']:30s} thr {s['threshold']:.2f} n {s['n_bets']:5d} roi {s['roi']:+.4f} "
                  f"ci [{s['roi_ci90'][0]:+.3f},{s['roi_ci90'][1]:+.3f}] clv {s['clv'] if s['clv'] is None else round(s['clv'], 4)}")
