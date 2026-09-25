"""Walk-forward backtest of the national-team model on AFCON qualifiers.

Every AFCON qualifying match from 2018 is forecast by a model fitted only on
international matches played before that day. Settings (time decay, weight of
friendlies, prior sd) are chosen on 2018-2021 qualifiers and frozen for
2022 onward. Baseline: World Football Elo with an ordered-logit map to
home/draw/away fitted on earlier qualifiers. Market benchmark: the sportsbook
closing odds ESPN kept for the October and November 2024 qualifiers.
"""

from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from .. import config
from ..data import afcon
from ..models import devig
from ..models import dixon_coles as dc
from ..models import international as intl
from . import metrics

OUT = config.SITE_DATA / "afcon_backtest.json"
VALID_END = pd.Timestamp("2022-01-01", tz="UTC")
GRID = {"xi": (0.00025, 0.0005, 0.001), "friendly_weight": (0.6, 1.0), "prior_sd": (0.3, 0.5, 0.75)}


def _history():
    res = afcon.load_results()
    camp = afcon.espn_events()
    return afcon.match_history(res[res["date"] >= "2008-01-01"], camp)


def _result(h, a):
    return np.where(h > a, "H", np.where(h == a, "D", "A"))


def walk_forward(H: pd.DataFrame, targets: pd.DataFrame, xi, fw, sd) -> pd.DataFrame:
    out = []
    for day, g in targets.groupby(targets["kickoff_utc"].dt.floor("D")):
        m = intl.fit_as_of(H, day, xi, fw, sd, teams=list(set(g["home"]) | set(g["away"])))
        M = m.score_matrix(g["home"].values, g["away"].values, g["neutral"].values)
        p = dc.outcome_probs(M)
        out.append(pd.DataFrame({"idx": g.index, "p_h": p[:, 0], "p_d": p[:, 1], "p_a": p[:, 2],
                                 "p_o25": dc.prob_over(M, 2.5)}))
    return pd.concat(out).set_index("idx").reindex(targets.index)


def run() -> dict:
    H = _history()
    Q = H[(H["tournament"] == afcon.QUAL_TOURNAMENT) & (H["kickoff_utc"] >= pd.Timestamp("2018-01-01", tz="UTC"))].copy()
    Q["result"] = _result(Q["hg"].values, Q["ag"].values)
    val = Q[Q["kickoff_utc"] < VALID_END]
    test = Q[Q["kickoff_utc"] >= VALID_END]

    # Tune on validation qualifiers
    rows = []
    for xi, fw, sd in itertools.product(GRID["xi"], GRID["friendly_weight"], GRID["prior_sd"]):
        p = walk_forward(H, val, xi, fw, sd)
        rows.append({"xi": xi, "friendly_weight": fw, "prior_sd": sd,
                     "rps": float(metrics.rps(p[["p_h", "p_d", "p_a"]].values, val["result"]).mean())})
    tune = pd.DataFrame(rows).sort_values("rps").reset_index(drop=True)
    best = tune.iloc[0].to_dict()

    # Test
    pt = walk_forward(H, test, best["xi"], best["friendly_weight"], best["prior_sd"])
    test = test.join(pt)

    # Elo baseline: sequential ratings; ordered logit fitted on qualifiers before the test period
    E = intl.elo_run(H)
    H2 = H.join(E[["elo_diff"]])
    Q2 = H2.loc[Q.index]
    fit_on = Q2[Q2["kickoff_utc"] < VALID_END]
    olog = intl.ordered_logit_fit(fit_on["elo_diff"], _result(fit_on["hg"].values, fit_on["ag"].values))
    pe = olog(H2.loc[test.index, "elo_diff"].values)
    test["elo_h"], test["elo_d"], test["elo_a"] = pe[:, 0], pe[:, 1], pe[:, 2]

    days = test["kickoff_utc"].dt.strftime("%Y-%m-%d").values
    res = {"n_test": int(len(test)), "n_validation": int(len(val)), "chosen": best,
           "tuning": tune.to_dict(orient="records")}
    rm = metrics.rps(test[["p_h", "p_d", "p_a"]].values, test["result"])
    re_ = metrics.rps(test[["elo_h", "elo_d", "elo_a"]].values, test["result"])
    res["model"] = {"rps": float(rm.mean()), "log_loss": float(metrics.log_loss(test[["p_h", "p_d", "p_a"]].values, test["result"]).mean()),
                    "accuracy": float((np.argmax(test[["p_h", "p_d", "p_a"]].values, 1) == np.select([test.result == "H", test.result == "D"], [0, 1], 2)).mean())}
    res["elo"] = {"rps": float(re_.mean()), "log_loss": float(metrics.log_loss(test[["elo_h", "elo_d", "elo_a"]].values, test["result"]).mean())}
    res["model_vs_elo"] = metrics.diebold_mariano(rm, re_, days)

    # Bookmaker comparison on the matches ESPN kept odds for
    ev = afcon.espn_events(afcon.BACKTEST_MONTHS)
    ev = ev[ev["state"] == "post"]
    odds = []
    for r in ev.itertuples():
        o = afcon.espn_odds(r.event_id)
        if o.get("odds_h"):
            odds.append({"date": r.kickoff_utc.strftime("%Y-%m-%d"), "home": r.home, "away": r.away, **o})
    O = pd.DataFrame(odds)
    book = {}
    if len(O):
        t = test.assign(date=test["kickoff_utc"].dt.strftime("%Y-%m-%d")).merge(O, on=["date", "home", "away"], how="inner")
        pb = devig.shin(t[["odds_h", "odds_d", "odds_a"]].values)
        ok = np.isfinite(pb).all(axis=1)
        t, pb = t[ok], pb[ok]
        rb = metrics.rps(pb, t["result"])
        rmm = metrics.rps(t[["p_h", "p_d", "p_a"]].values, t["result"])
        ree = metrics.rps(t[["elo_h", "elo_d", "elo_a"]].values, t["result"])
        dd = t["date"].values
        margin = float(np.mean(1 / t["odds_h"] + 1 / t["odds_d"] + 1 / t["odds_a"] - 1))
        # value bets: model probability vs bookmaker price
        bets = []
        for i, r in enumerate(t.itertuples()):
            for sel, p, o in (("H", r.p_h, r.odds_h), ("D", r.p_d, r.odds_d), ("A", r.p_a, r.odds_a)):
                e = p * o - 1
                if e > 0.05:
                    bets.append({"date": r.date, "ret": (o - 1) if r.result == sel else -1.0, "edge": e})
        B = pd.DataFrame(bets)
        book = {"provider": O["provider"].mode().iloc[0], "n": int(len(t)), "margin": margin,
                "rps_book": float(rb.mean()), "rps_model": float(rmm.mean()), "rps_elo": float(ree.mean()),
                "model_vs_book": metrics.diebold_mariano(rmm, rb, dd)}
        if len(B):
            lo, hi = metrics.cluster_bootstrap_mean(B["ret"].values, B["date"].values, 1000)
            book["value_bets"] = {"n": int(len(B)), "roi": float(B["ret"].mean()), "roi_ci90": [lo, hi]}
    res["bookmaker"] = book

    # Calibration of the model on the test set
    y = metrics.onehot(test["result"])
    cal = metrics.calibration_table(test[["p_h", "p_d", "p_a"]].values.ravel(), y.ravel(), np.linspace(0, 1, 11))
    res["calibration"] = cal.to_dict(orient="records")
    res["generated_utc"] = config.utcnow().isoformat()
    from .report import _clean
    OUT.write_text(json.dumps(_clean(res), separators=(",", ":")))
    cfg_path = config.DATA / "model" / "afcon.json"
    cfg_path.write_text(json.dumps({k: float(v) for k, v in best.items() if k != "rps"}, indent=2) + "\n")
    return res
