"""Search for systematic mispricing on Kalshi and Polymarket EPL contracts.

Every settled contract (1X2, totals, winning margin, both teams to score) is
priced against the sharp closing price: the de-margined Pinnacle or Betfair
Exchange 1X2 and over/under 2.5 closing odds pin down the expected goals of
each side, and the Dixon-Coles scoreline distribution at those rates prices
every other contract. That "sharp fair" value is the benchmark.

Three families of rule are tested, each on both the YES and the NO side:

structural  always buy a given kind of contract at a given time before
            kick-off (e.g. the draw 7 days out). Needs no forecast.
coherence   price every contract from the venue's own 1X2 and over/under 2.5
            mid prices at that time, and buy contracts that trade below that
            internally implied value. Needs no outside price feed.
cross-venue buy on the venue whose price is lower when Kalshi and Polymarket
            disagree by more than a threshold.

Measures per rule: closing line value against the sharp fair price (CLV, the
primary measure), realised ROI, number of contracts. Intervals are cluster
bootstrap over match days. Rules are found on the first half of the data
(by kick-off date) and checked on the second half.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from ..data.venue_history import CACHE
from ..live import pricing
from ..models import dixon_coles as dc
from ..models import implied
from . import metrics

BIG6 = {"Man United", "Liverpool", "Arsenal", "Chelsea", "Man City", "Tottenham"}
KALSHI_FEE = 0.07
POLY_HALF_SPREAD = 0.005
MAX_SPREAD = 0.04   # Kalshi: ignore quotes with a bid-ask spread wider than 4 cents


def _sharp_fair(pred: pd.DataFrame) -> pd.DataFrame:
    """Closing-price implied rates per match."""
    p = pred.dropna(subset=["mkt_close_h", "mkt_close_d", "mkt_close_a", "mkt_close_o25"]).copy()
    lam, nu, rho = implied.implied_rates_rho(p[["mkt_close_h", "mkt_close_d", "mkt_close_a"]].values,
                                             p["mkt_close_o25"].values)
    p["lam_close"], p["nu_close"], p["rho_close"] = lam, nu, rho
    return p[["match_id", "date", "season", "home", "away", "hg", "ag", "result", "lam_close", "nu_close",
              "rho_close", "mkt_close_h", "mkt_close_d", "mkt_close_a"]]


def _prob(lam, nu, market, sel, line, rho=0.0):
    M = dc.score_matrix(lam, nu, rho)
    return float(pricing.selection_prob(M, market, sel, None if pd.isna(line) else float(line)))


def _complement_sel(market, sel):
    return {"1x2": None, "total": {"over": "under"}, "btts": {"yes": "no"},
            "margin": {"home": "not_home", "away": "not_away"}}[market].get(sel) if market != "1x2" else f"not_{sel}"


CAL_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)   # before any Kalshi/Polymarket data


def side_calibration(pred: pd.DataFrame) -> dict:
    """Logistic recalibration of closing-price-implied side-market probabilities.

    The Dixon-Coles distribution implied by the closing 1X2 and over/under 2.5
    prices is well calibrated for draws, both teams to score and low totals,
    but overstates large home wins and totals above 4 goals (independent
    Poisson tails are too fat). For each contract type,
    logit(p) = a + b * logit(p_model) is fitted on 2019-20 to 2024-25, seasons
    that end before any prediction-market data begins.
    """
    import statsmodels.api as sm
    p = pred[pred["season"].isin(CAL_SEASONS)].dropna(subset=["mkt_close_h", "mkt_close_o25"])
    lam, nu, rho = implied.implied_rates_rho(p[["mkt_close_h", "mkt_close_d", "mkt_close_a"]].values,
                                             p["mkt_close_o25"].values)
    ok = np.isfinite(lam)
    p, lam, nu, rho = p[ok], lam[ok], nu[ok], rho[ok]
    M = np.stack([dc.score_matrix(a, b, r) for a, b, r in zip(lam, nu, rho)])
    hg, ag = p["hg"].values, p["ag"].values
    out = {}
    specs = [("total", "over", l) for l in (0.5, 1.5, 3.5, 4.5, 5.5)] + [("btts", "yes", None)]
    specs += [("margin", s_, l) for s_ in ("home", "away") for l in (1.5, 2.5, 3.5)]
    for m, s_, l in specs:
        pr = np.clip(pricing.selection_prob(M, m, s_, l), 1e-4, 1 - 1e-4)
        if m == "total":
            y = hg + ag > l
        elif m == "btts":
            y = (hg > 0) & (ag > 0)
        else:
            y = (hg - ag > l) if s_ == "home" else (ag - hg > l)
        x = sm.add_constant(np.log(pr / (1 - pr)))
        fit = sm.GLM(y.astype(float), x, family=sm.families.Binomial()).fit()
        out[(m, s_, l)] = (float(fit.params[0]), float(fit.params[1]))
    return out


def _recal(p: float, ab) -> float:
    if ab is None:
        return p
    p = min(max(p, 1e-4), 1 - 1e-4)
    z = ab[0] + ab[1] * np.log(p / (1 - p))
    return float(1 / (1 + np.exp(-z)))


def load_contracts(pred: pd.DataFrame, poly_half_spread: float = POLY_HALF_SPREAD) -> pd.DataFrame:
    """Long table: one row per (contract side, offset) with cost, sharp fair and outcome."""
    fair = _sharp_fair(pred).set_index("match_id")
    cal = side_calibration(pred)
    frames = []
    for venue in ("kalshi", "polymarket"):
        path = CACHE / f"paths_{venue}.csv"
        if not path.exists():
            continue
        d = pd.read_csv(path)
        d = d[d["match_id"].isin(fair.index) & d["result"].isin(["yes", "no"])].copy()
        if venue == "kalshi":
            d["mid"] = (d["bid"] + d["ask"]) / 2
            yes_cost = d["ask"] + KALSHI_FEE * d["ask"] * (1 - d["ask"])
            no_px = 1 - d["bid"]
            no_cost = no_px + KALSHI_FEE * no_px * (1 - no_px)
            d = d[(d["bid"] > 0) & (d["ask"] < 1) & (d["ask"] > d["bid"]) & (d["ask"] - d["bid"] <= MAX_SPREAD)]
        else:
            d["mid"] = d["price"]
            fr = d["fee_rate"].fillna(0.0)
            q = d["price"] + poly_half_spread
            yes_cost = q + fr * q * (1 - q)
            qn = 1 - d["price"] + poly_half_spread
            no_cost = qn + fr * qn * (1 - qn)
            d = d[(d["price"] > 0.005) & (d["price"] < 0.995)]
            yes_cost, no_cost = yes_cost.loc[d.index], no_cost.loc[d.index]
        if venue == "kalshi":
            yes_cost, no_cost = yes_cost.loc[d.index], no_cost.loc[d.index]
        d["venue"] = venue
        f = fair.loc[d["match_id"]]
        d["date"] = f["date"].values
        d["season"] = f["season"].values
        d["home"] = f["home"].values
        d["away"] = f["away"].values
        direct = {"H": f["mkt_close_h"].values, "D": f["mkt_close_d"].values, "A": f["mkt_close_a"].values}
        fv = []
        for i, (l, n, r, m, s_, ln) in enumerate(zip(f["lam_close"].values, f["nu_close"].values, f["rho_close"].values,
                                                     d["market"], d["selection"], d["line"])):
            if m == "1x2":
                fv.append(direct[s_][i])       # de-margined closing price itself
            else:
                key = (m, s_, None if pd.isna(ln) else float(ln))
                fv.append(_recal(_prob(l, n, m, s_, ln, r), cal.get(key)))
        d["fair"] = fv
        yes = d.assign(side="yes", cost=yes_cost.values, won=(d["result"] == "yes").astype(float))
        no = d.assign(side="no", cost=no_cost.values, won=(d["result"] == "no").astype(float),
                      fair=1 - d["fair"].values, mid=1 - d["mid"].values)
        frames += [yes, no]
    c = pd.concat(frames, ignore_index=True)
    c = c[np.isfinite(c["cost"]) & (c["cost"] > 0.01) & (c["cost"] < 0.99)]
    c["clv"] = c["fair"] / c["cost"] - 1
    c["ret"] = np.where(c["won"] == 1, 1 / c["cost"] - 1, -1.0)
    c["gap"] = c["mid"] - c["fair"]
    team = np.where(c["selection"].isin(["H", "home", "not_home"]), c["home"],
                    np.where(c["selection"].isin(["A", "away", "not_away"]), c["away"], ""))
    c["big6"] = pd.Series(team).isin(BIG6).values
    c["kind"] = _kind(c)
    return c


def _kind(c: pd.DataFrame) -> pd.Series:
    """Human-readable contract kind, including the side bought."""
    out = []
    for m, s, ln, side, big in zip(c["market"], c["selection"], c["line"], c["side"], c["big6"]):
        if m == "1x2":
            base = "draw" if s == "D" else ("big-six win" if big else "other-club win")
        elif m == "total":
            base = f"over {ln:g}"
        elif m == "btts":
            base = "both teams score"
        else:
            base = f"{'big-six' if big else 'other club'} wins by {int(ln) + 1}+"
        out.append(base if side == "yes" else f"NOT {base}")
    return pd.Series(out, index=c.index)


def _summ(g: pd.DataFrame) -> dict:
    lo, hi = metrics.cluster_bootstrap_mean(g["clv"].values, g["date"].astype(str).values, 500)
    rlo, rhi = metrics.cluster_bootstrap_mean(g["ret"].values, g["date"].astype(str).values, 500)
    return {"n": int(len(g)), "clv": float(g["clv"].mean()), "clv_ci90": [lo, hi],
            "roi": float(g["ret"].mean()), "roi_ci90": [rlo, rhi], "gap_cents": float(100 * g["gap"].mean()),
            "mean_cost": float(g["cost"].mean())}


def split_halves(c: pd.DataFrame) -> pd.Series:
    cut = c["date"].sort_values().iloc[len(c) // 2]
    return np.where(c["date"] < cut, "discovery", "confirmation")


def structural(c: pd.DataFrame, min_n: int = 60) -> pd.DataFrame:
    c = c.assign(half=split_halves(c))
    rows = []
    for (venue, kind, off), g in c.groupby(["venue", "kind", "offset_h"]):
        if len(g) < min_n:
            continue
        r = {"venue": venue, "rule": f"buy {kind}", "offset_h": int(off), **_summ(g)}
        for h in ("discovery", "confirmation"):
            gh = g[g["half"] == h]
            r[f"clv_{h}"] = float(gh["clv"].mean()) if len(gh) >= 20 else None
            r[f"n_{h}"] = int(len(gh))
        rows.append(r)
    return pd.DataFrame(rows).sort_values("clv", ascending=False).reset_index(drop=True)


def coherence(c: pd.DataFrame, thresholds=(0.0, 0.03, 0.06), cal: dict | None = None) -> pd.DataFrame:
    """Price side contracts from the venue's own 1X2 + over 2.5 mids at the same moment.

    The internal price uses the same outcome recalibration as the benchmark
    (side_calibration), so the known Poisson bias on large margins does not
    masquerade as mispricing.
    """
    base = c[(c["side"] == "yes")]
    one = base[base["market"] == "1x2"].pivot_table(index=["venue", "match_id", "offset_h"], columns="selection",
                                                     values="mid", aggfunc="first")
    ou = base[(base["market"] == "total") & (base["line"] == 2.5)].set_index(["venue", "match_id", "offset_h"])["mid"]
    ou = ou[~ou.index.duplicated()]
    k = one.join(ou.rename("o25"), how="inner").dropna()
    p = k[["H", "D", "A"]].values
    p = p / p.sum(axis=1, keepdims=True)
    lam, nu, rho = implied.implied_rates_rho(p, k["o25"].values)
    rates = pd.DataFrame({"lam_v": lam, "nu_v": nu, "rho_v": rho}, index=k.index)
    s = c[~((c["market"] == "1x2") | ((c["market"] == "total") & (c["line"] == 2.5)))]
    s = s.join(rates, on=["venue", "match_id", "offset_h"], how="inner")
    cal = cal or {}

    def internal(l, n, r, m, sel, ln, side):
        p = _recal(_prob(l, n, m, sel, ln, r), cal.get((m, sel, None if pd.isna(ln) else float(ln))))
        return p if side == "yes" else 1 - p

    s["fair_internal"] = [internal(*a) for a in zip(s["lam_v"], s["nu_v"], s["rho_v"], s["market"],
                                                    s["selection"], s["line"], s["side"])]
    s["edge_internal"] = s["fair_internal"] / s["cost"] - 1
    s = s.assign(half=split_halves(s))
    rows = []
    for (venue, off), g in s.groupby(["venue", "offset_h"]):
        for thr in thresholds:
            b = g[g["edge_internal"] > thr]
            if len(b) < 30:
                continue
            r = {"venue": venue, "rule": f"side contract priced below venue's own 1X2 + total by >{thr:.0%}",
                 "threshold": thr, "offset_h": int(off), **_summ(b)}
            for h in ("discovery", "confirmation"):
                bh = b[b["half"] == h]
                r[f"clv_{h}"] = float(bh["clv"].mean()) if len(bh) >= 15 else None
                r[f"n_{h}"] = int(len(bh))
            rows.append(r)
    return pd.DataFrame(rows).sort_values("clv", ascending=False).reset_index(drop=True), s


def cross_venue(c: pd.DataFrame, thresholds=(0.01, 0.02, 0.04)) -> pd.DataFrame:
    y = c[c["side"] == "yes"]
    key = ["match_id", "market", "selection", "line", "offset_h"]
    y = y.assign(line=y["line"].fillna(-1))
    k = y[y["venue"] == "kalshi"].drop_duplicates(key).set_index(key)
    pm = y[y["venue"] == "polymarket"].drop_duplicates(key).set_index(key)
    j = k[["mid", "date"]].join(pm[["mid"]], rsuffix="_p", how="inner").dropna()
    rows = []
    both = c.assign(line=c["line"].fillna(-1)).set_index(key + ["venue", "side"])
    for thr in thresholds:
        cheap_k = j[j["mid_p"] - j["mid"] > thr].index
        cheap_p = j[j["mid"] - j["mid_p"] > thr].index
        picks = [(i, "kalshi") for i in cheap_k] + [(i, "polymarket") for i in cheap_p]
        sel = [both.loc[i + (v, "yes")] for i, v in picks if (i + (v, "yes")) in both.index]
        if len(sel) < 30:
            continue
        b = pd.DataFrame(sel)
        b = b[~b.index.duplicated()]
        rows.append({"rule": f"buy the cheaper venue when Kalshi and Polymarket differ by >{thr * 100:.0f}c",
                     "threshold": thr, **_summ(b)})
    return pd.DataFrame(rows)


def run(poly_half_spread: float = POLY_HALF_SPREAD) -> dict:
    pred = pd.read_csv(config.DATA / "model" / "backtest_predictions.csv")
    c = load_contracts(pred, poly_half_spread)
    st = structural(c)
    co, _ = coherence(c, cal=side_calibration(pred))
    cv = cross_venue(c)
    return {"contracts": c, "structural": st, "coherence": co, "cross_venue": cv}
