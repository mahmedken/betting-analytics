"""Walk-forward backtest.

Protocol
--------
* Every forecast for a match on day d is made by a model fitted only on matches
  played before day d. No result from day d or later is visible.
* Hyperparameters are chosen on the validation seasons only (2016-17 to
  2018-19) and then frozen. The test seasons (2019-20 onward) are never used
  for any choice.
* The promoted-team prior is estimated from seasons before the validation
  period.
* The model-market pool for test season S is fitted on the out-of-sample
  forecasts of all earlier seasons, so it too uses only past information.
"""

from __future__ import annotations

import itertools
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .. import config
from ..models import devig, elo
from ..models import dixon_coles as dc
from ..models.fitting import DCConfig, History, estimate_promoted_prior, fit_as_of
from ..models.pool import LogPool
from . import metrics

VALIDATION_SEASONS = (2016, 2017, 2018)
TEST_FIRST = 2019
PRIOR_SEASONS = tuple(range(2006, 2016))

DC_GRID = {
    "xi": (0.002, 0.003, 0.004, 0.005, 0.006),
    "w_goals": (0.0, 0.25, 0.5, 0.75, 1.0),
    "prior_sd": (0.5, 1.0, 2.0, 5.0),
}
ELO_GRID = {
    "k": (4.0, 6.0, 8.0, 10.0, 15.0),
    "home_adv": (60.0, 80.0, 100.0, 120.0),
    "carry": (0.9, 1.0),
}


# ---------------------------------------------------------------------------
# Dixon-Coles walk-forward
# ---------------------------------------------------------------------------

def walk_forward_dc(df: pd.DataFrame, cfg: DCConfig, seasons, prior: tuple[float, float],
                    draws: int = 0, seed: int = 0) -> pd.DataFrame:
    test = df[df["season"].isin(seasons) & df["hg"].notna()]
    hist = History.from_df(df)
    rng = np.random.default_rng(seed)
    out = []
    for day, g in test.groupby(test["kickoff_utc"].dt.floor("D")):
        m = fit_as_of(hist, day, cfg, prior, season=int(g["season"].iloc[0]))
        M = m.score_matrix(g["home"].values, g["away"].values)
        p = dc.outcome_probs(M)
        lam, nu = m.rates(g["home"].values, g["away"].values)
        rec = pd.DataFrame({
            "match_id": g["match_id"].values,
            "dc_h": p[:, 0], "dc_d": p[:, 1], "dc_a": p[:, 2],
            "dc_o25": dc.prob_over(M, 2.5), "dc_btts": dc.prob_btts(M),
            "dc_lam": lam, "dc_nu": nu,
        })
        if draws:
            th = m.sample_theta(draws, rng)
            Pd = dc.outcome_probs(m.score_matrix(g["home"].values, g["away"].values, th))  # (S, n, 3)
            for j, s in enumerate("hda"):
                rec[f"dc_{s}_sd"] = Pd[:, :, j].std(axis=0)
                rec[f"dc_{s}_q05"] = np.quantile(Pd[:, :, j], 0.05, axis=0)
                rec[f"dc_{s}_q95"] = np.quantile(Pd[:, :, j], 0.95, axis=0)
        out.append(rec)
    return pd.concat(out, ignore_index=True)


def _score_dc(args) -> dict:
    df, cfg, prior = args
    pred = walk_forward_dc(df, cfg, VALIDATION_SEASONS, prior)
    res = pred.merge(df[["match_id", "date", "result", "hg", "ag"]], on="match_id")
    p = res[["dc_h", "dc_d", "dc_a"]].values
    over = ((res["hg"] + res["ag"]) > 2.5).astype(float).values
    r = metrics.rps(p, res["result"])
    return {**cfg.as_dict(),
            "rps": float(r.mean()),
            "log_loss": float(metrics.log_loss(p, res["result"]).mean()),
            "ou_log_loss": float(metrics.binary_log_loss(res["dc_o25"].values, over).mean()),
            "n": len(res),
            "_rps": r, "_date": res["date"].astype(str).values}


def one_se_choice(rows: list[dict]) -> pd.DataFrame:
    """One-standard-error rule (Breiman et al. 1984; Hastie, Tibshirani & Friedman 2009, 7.10).

    Among configurations whose validation RPS is within one standard error of
    the best (paired, clustered by match date), pick the most regularised one:
    the smallest prior sd, then the lowest RPS.
    """
    best = min(rows, key=lambda r: r["rps"])
    out = []
    for r in rows:
        dm = metrics.diebold_mariano(r["_rps"], best["_rps"], r["_date"])
        out.append({k: v for k, v in r.items() if not k.startswith("_")}
                   | {"diff_vs_best": dm["mean_diff"], "se_vs_best": dm["se"] if r is not best else 0.0})
    t = pd.DataFrame(out)
    t["within_1se"] = t["diff_vs_best"] <= t["se_vs_best"]
    t = t.sort_values(["within_1se", "prior_sd", "rps"], ascending=[False, True, True]).reset_index(drop=True)
    t["chosen"] = False
    t.loc[0, "chosen"] = True
    return t


def _single_thread():
    # One BLAS thread per worker process; the grid runs one process per core.
    threadpool_limits(1)


def tune_dc(df: pd.DataFrame, prior: tuple[float, float], workers: int = 4) -> pd.DataFrame:
    cfgs = [DCConfig(xi=xi, w_goals=wg, prior_sd=sd)
            for xi, wg, sd in itertools.product(DC_GRID["xi"], DC_GRID["w_goals"], DC_GRID["prior_sd"])]
    with ProcessPoolExecutor(max_workers=workers, initializer=_single_thread) as ex:
        rows = list(ex.map(_score_dc, [(df, c, prior) for c in cfgs]))
    return one_se_choice(rows)


# ---------------------------------------------------------------------------
# Elo walk-forward
# ---------------------------------------------------------------------------

def elo_forecasts(df: pd.DataFrame, cfg: elo.EloConfig, seasons, fit_window: int = 5) -> pd.DataFrame:
    """Ratings are sequential (always pre-match); the ordered logit for season S
    is fitted on the `fit_window` seasons before S."""
    r = elo.run(df, cfg)
    d = df[["match_id", "season", "result"]].join(r)
    out = []
    for s in seasons:
        train = d[(d["season"] < s) & (d["season"] >= s - fit_window) & (d["result"] != "")]
        model = elo.OrderedLogit.fit(train["elo_diff"], train["result"])
        test = d[(d["season"] == s) & (d["result"] != "")]
        p = model.predict(test["elo_diff"].values)
        out.append(pd.DataFrame({"match_id": test["match_id"].values, "elo_h": p[:, 0],
                                 "elo_d": p[:, 1], "elo_a": p[:, 2],
                                 "elo_diff": test["elo_diff"].values}))
    return pd.concat(out, ignore_index=True)


def tune_elo(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    res = df.set_index("match_id")["result"]
    for k, ha, carry in itertools.product(ELO_GRID["k"], ELO_GRID["home_adv"], ELO_GRID["carry"]):
        cfg = elo.EloConfig(k=k, home_adv=ha, carry=carry)
        pred = elo_forecasts(df, cfg, VALIDATION_SEASONS)
        p = pred[["elo_h", "elo_d", "elo_a"]].values
        rows.append({"k": k, "home_adv": ha, "carry": carry,
                     "rps": float(metrics.rps(p, res.loc[pred["match_id"]].values).mean())})
    return pd.DataFrame(rows).sort_values("rps").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Market benchmark
# ---------------------------------------------------------------------------

def market_probs(df: pd.DataFrame, method: str = "shin") -> pd.DataFrame:
    """De-margined 1X2 and over-2.5 probabilities from the sharpest available book.

    Order of preference: Pinnacle, Betfair Exchange, market average. The book
    used is recorded per row.
    """
    out = pd.DataFrame({"match_id": df["match_id"].values})
    for when in ("pre", "close"):
        probs = np.full((len(df), 3), np.nan)
        src = np.full(len(df), "", dtype=object)
        for book in ("avg", "bfe", "ps"):      # later books overwrite earlier ones
            cols = [f"{book}_{when}_{s}" for s in "hda"]
            p = devig.devig(df[cols].values, method)
            ok = np.isfinite(p).all(axis=1)
            probs[ok] = p[ok]
            src[ok] = book
        out[f"mkt_{when}_h"], out[f"mkt_{when}_d"], out[f"mkt_{when}_a"] = probs.T
        out[f"mkt_{when}_src"] = src

        p_o = np.full(len(df), np.nan)
        src_o = np.full(len(df), "", dtype=object)
        for book in ("avg", "bfe", "ps"):
            cols = [f"{book}_{when}_o25", f"{book}_{when}_u25"]
            p = devig.devig(df[cols].values, method)
            ok = np.isfinite(p).all(axis=1)
            p_o[ok] = p[ok, 0]
            src_o[ok] = book
        out[f"mkt_{when}_o25"] = p_o
        out[f"mkt_{when}_o25_src"] = src_o
    return out


def compare_devig(df: pd.DataFrame, seasons) -> pd.DataFrame:
    """RPS of each margin-removal method on Pinnacle closing odds."""
    d = df[df["season"].isin(seasons) & df["ps_close_h"].notna() & (df["result"] != "")]
    rows = []
    for method in devig.METHODS:
        for when in ("pre", "close"):
            p = devig.devig(d[[f"ps_{when}_{s}" for s in "hda"]].values, method)
            rows.append({"method": method, "odds": f"pinnacle {when}", "n": len(d),
                         "rps": float(metrics.rps(p, d["result"]).mean()),
                         "log_loss": float(metrics.log_loss(p, d["result"]).mean())})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Pool of model and market
# ---------------------------------------------------------------------------

def pooled_forecasts(pred: pd.DataFrame, seasons, first_train_season: int) -> tuple[pd.DataFrame, list[dict]]:
    """For each season S, fit the pool on all forecasts from first_train_season..S-1."""
    rows, fits = [], []
    y = metrics.onehot(pred["result"])
    for s in seasons:
        tr = (pred["season"] < s) & (pred["season"] >= first_train_season)
        te = pred["season"] == s
        pool = LogPool.fit(pred.loc[tr, ["dc_h", "dc_d", "dc_a"]].values,
                           pred.loc[tr, ["mkt_pre_h", "mkt_pre_d", "mkt_pre_a"]].values, y[tr.values])
        p = pool.predict(pred.loc[te, ["dc_h", "dc_d", "dc_a"]].values,
                         pred.loc[te, ["mkt_pre_h", "mkt_pre_d", "mkt_pre_a"]].values)
        over = ((pred["hg"] + pred["ag"]) > 2.5).astype(float).values
        ok_o = tr & pred["mkt_pre_o25"].notna()
        pool_o = LogPool.fit(pred.loc[ok_o, "dc_o25"].values, pred.loc[ok_o, "mkt_pre_o25"].values,
                             over[ok_o.values])
        p_o = pool_o.predict(pred.loc[te, "dc_o25"].values, pred.loc[te, "mkt_pre_o25"].fillna(0.5).values)
        rows.append(pd.DataFrame({"match_id": pred.loc[te, "match_id"].values,
                                  "pool_h": p[:, 0], "pool_d": p[:, 1], "pool_a": p[:, 2],
                                  "pool_o25": np.where(pred.loc[te, "mkt_pre_o25"].notna(), p_o, np.nan)}))
        fits.append({"season": s, "a_1x2": pool.a, "b_1x2": pool.b, "se_a_1x2": pool.se_a,
                     "a_ou": pool_o.a, "b_ou": pool_o.b, "se_a_ou": pool_o.se_a, "n_train": pool.n})
    return pd.concat(rows, ignore_index=True), fits


def encompassing_test(pred: pd.DataFrame, seasons) -> dict:
    """Fit the pool on all test-season forecasts and report the model weight."""
    d = pred[pred["season"].isin(seasons)]
    y = metrics.onehot(d["result"])
    pool = LogPool.fit(d[["dc_h", "dc_d", "dc_a"]].values, d[["mkt_close_h", "mkt_close_d", "mkt_close_a"]].values, y)
    pool_pre = LogPool.fit(d[["dc_h", "dc_d", "dc_a"]].values, d[["mkt_pre_h", "mkt_pre_d", "mkt_pre_a"]].values, y)
    over = ((d["hg"] + d["ag"]) > 2.5).astype(float).values
    ok = d["mkt_close_o25"].notna().values
    pool_o = LogPool.fit(d.loc[ok, "dc_o25"].values, d.loc[ok, "mkt_close_o25"].values, over[ok])
    return {
        "vs_closing_1x2": {"a": pool.a, "se_a": pool.se_a, "b": pool.b, "se_b": pool.se_b, "n": pool.n},
        "vs_pre_1x2": {"a": pool_pre.a, "se_a": pool_pre.se_a, "b": pool_pre.b, "se_b": pool_pre.se_b, "n": pool_pre.n},
        "vs_closing_ou25": {"a": pool_o.a, "se_a": pool_o.se_a, "b": pool_o.b, "se_b": pool_o.se_b, "n": pool_o.n},
    }
