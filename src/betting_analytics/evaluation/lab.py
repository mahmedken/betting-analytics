"""The research findings shown on the site's Lab tab (site/data/lab.json).

Each finding is a tested hypothesis about where EPL markets are mispriced,
with a verdict, a headline number, a small chart and the evidence tables.
Everything is recomputed from cached price data by `ba lab`.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .. import config
from ..data import dataset
from ..data.venue_history import CACHE
from ..models import market_ratings as mr
from . import backtest as bt
from . import futures, maker, metrics
from .report import _clean

OUT = config.SITE_DATA / "lab.json"
BIG6 = maker.BIG6


def _ci(x):
    return [round(float(x[0]), 4), round(float(x[1]), 4)]


# ---------------------------------------------------------------------------
def finding_maker(pred) -> dict:
    r = maker.run(pred)
    cells = []
    for (entry, side, imp, grp), g in r.groupby(["entry_h", "side", "improve", "group"]):
        s = g.iloc[0].to_dict()
        if s.get("clv") is None or pd.isna(s.get("clv")):
            continue
        cells.append({k: s[k] for k in ("entry_h", "side", "improve", "group", "n_posted", "n_filled", "fill_rate",
                                         "mean_price", "clv", "clv_ci90", "roi", "roi_ci90", "clv_discovery",
                                         "clv_confirmation")})

    def cell(entry, side, grp, imp=0.0):
        return next(c for c in cells if c["entry_h"] == entry and c["side"] == side and c["group"] == grp and c["improve"] == imp)

    chart = [
        {"label": "bid for the draw", "with_bias": True, **{k: cell(72, "bid", "draw")[k] for k in ("clv", "clv_ci90", "n_filled")}},
        {"label": "offer against a big-six win", "with_bias": True, **{k: cell(72, "ask", "big-six")[k] for k in ("clv", "clv_ci90", "n_filled")}},
        {"label": "offer against the draw", "with_bias": False, **{k: cell(72, "ask", "draw")[k] for k in ("clv", "clv_ci90", "n_filled")}},
        {"label": "bid for a big-six win", "with_bias": False, **{k: cell(72, "bid", "big-six")[k] for k in ("clv", "clv_ci90", "n_filled")}},
        {"label": "bid for another club's win", "with_bias": False, **{k: cell(72, "bid", "other")[k] for k in ("clv", "clv_ci90", "n_filled")}},
    ]
    d72 = cell(72, "bid", "draw")
    return {
        "id": "maker", "verdict": "edge", "venue": "Kalshi",
        "title": "Rest orders against retail flow on Kalshi",
        "number": d72["clv"], "number_kind": "pct", "number_label": "beat the sharp closing price, per filled order",
        "summary": ("Retail money on Kalshi overpays for big clubs and underpays for draws a few days before kick-off. "
                    "Taking prices does not pay after fees, but resting limit orders on the other side does: "
                    "bids for the draw and offers against big-six wins, placed 2 to 3 days out, beat the sharp "
                    "closing price. The same orders placed with the crowd lose, so this is not a generic market-making gain."),
        "how": "Place a limit bid for the draw, or a limit offer on a big-six win, 48 to 72 hours before kick-off, at or just inside the best price. Hold to settlement.",
        "method": ("1,275 settled Kalshi EPL match contracts, hourly order-book candles. An order counts as filled only if a later "
                   "trade prints strictly through its price before kick-off. Maker fee 0.0175·p·(1−p). Benchmark: de-margined "
                   "Pinnacle / Betfair Exchange closing price. First half of the data used for discovery, second half to confirm."),
        "chart": {"type": "bars_ci", "unit": "pct", "rows": chart},
        "evidence": cells,
    }


# ---------------------------------------------------------------------------
def finding_retail_bias(pred) -> dict:
    p = pred.set_index("match_id")
    out, per_team = {}, []
    for venue, fn, px in (("Kalshi", "kalshi_epl_1x2.csv", "mid"), ("Polymarket", "polymarket_epl_1x2.csv", "price")):
        v = pd.read_csv(CACHE / fn)
        v = v[v["match_id"].isin(p.index)]
        q = np.select([v["selection"] == "H", v["selection"] == "D", v["selection"] == "A"],
                      [p.loc[v["match_id"], "mkt_close_h"].values, p.loc[v["match_id"], "mkt_close_d"].values,
                       p.loc[v["match_id"], "mkt_close_a"].values])
        mid = (v["bid_24h"] + v["ask_24h"]) / 2 if px == "mid" else v["price_24h"]
        team = np.where(v["selection"] == "H", v["home"], np.where(v["selection"] == "A", v["away"], "Draw"))
        d = pd.DataFrame({"gap": (mid - q).values, "team": team, "date": p.loc[v["match_id"], "date"].values}).dropna()
        d["group"] = np.where(d["team"] == "Draw", "draw", np.where(d["team"].isin(BIG6), "big-six", "other"))
        grp = {}
        for g, x in d.groupby("group"):
            grp[g] = {"n": int(len(x)), "gap_cents": float(100 * x["gap"].mean()),
                      "ci90_cents": _ci(100 * np.array(metrics.cluster_bootstrap_mean(x["gap"].values, x["date"].astype(str).values, 1000)))}
        out[venue] = grp
        if venue == "Kalshi":
            t = d.groupby("team")["gap"].agg(["mean", "count"])
            t = t[t["count"] >= 20].sort_values("mean")
            per_team = [{"team": i, "gap_cents": float(100 * r["mean"]), "n": int(r["count"])} for i, r in t.iterrows()]
    return {
        "id": "retail", "verdict": "real, below fees",
        "title": "Big clubs cost more on prediction markets",
        "number": out["Kalshi"]["big-six"]["gap_cents"] / 100, "number_kind": "cents",
        "number_label": "Kalshi premium on a big-six win, 24 h before kick-off",
        "summary": ("A day before kick-off, Kalshi and Polymarket price wins for Arsenal, Chelsea, Liverpool, Man City, Man United "
                    "and Tottenham above the sharp bookmaker price, and draws below it. The gap closes by kick-off. It is smaller "
                    "than the taker fee, which is why the profitable way to use it is resting orders (above)."),
        "method": "Venue mid price 24 hours before kick-off minus the de-margined sharp closing price; 90% intervals resample match days.",
        "chart": {"type": "team_bars", "unit": "cents", "rows": per_team},
        "evidence": out,
    }


# ---------------------------------------------------------------------------
def finding_futures(df) -> dict:
    mk = bt.market_probs(df)
    rates = mr.implied_match_rates(df[df["season"] >= 2018], mk)
    drift = mr.estimate_drift(rates, [2019, 2021, 2022, 2023, 2024])
    r = futures.run(df, rates, drift, n_sims=2000)
    s = futures.score(r)
    rows = [{"label": m, "sim": v["brier_sim"], "market": v["brier_market"], "n": v["n"]} for m, v in s["by_market"].items()]
    return {
        "id": "futures", "verdict": "suggestive",
        "title": "Season markets drift away from match markets",
        "number": 1 - s["brier_sim"] / s["brier_market"], "number_kind": "pct",
        "number_label": "lower Brier score than Kalshi's season prices",
        "summary": ("Team strengths implied by every match's closing price, run through a season simulation, predicted last "
                    "season's title and relegation outcomes better than Kalshi's own season markets did. The difference is not "
                    "yet statistically significant: only about two seasons of season-market history exist."),
        "method": (f"Every 14 days through 2024-25 (title) and 2025-26 (title, top four, relegation): market-implied ratings "
                   f"from closing prices of matches played so far; 2,000 simulated seasons with rating drift of "
                   f"{drift:.3f} per week (estimated on 2019-24). Compared with Kalshi mid prices where the spread was 6 cents or less."),
        "chart": {"type": "pair_bars", "unit": "brier", "rows": rows},
        "evidence": s, "drift_sd_per_week": drift,
    }


# ---------------------------------------------------------------------------
def finding_best_price() -> dict:
    b = json.loads((config.SITE_DATA / "backtest.json").read_text())
    rows = [s for s in b["betting"]["strategies"] if s["label"] == "fair vs best price, 1x2" and s["staking"] == "flat"]
    main = next(s for s in rows if s["threshold"] == 0.05)
    return {
        "id": "best_price", "verdict": "edge, hard to use",
        "title": "The best bookmaker price is often too long",
        "number": main["clv"], "number_kind": "pct", "number_label": "beat the closing price, bets with 5%+ claimed edge",
        "summary": ("Across roughly 30 bookmakers, the longest price on offer frequently beats the fair price. Taking it beat the "
                    "sharp close by 3 to 4 per cent on average. The catch is practical: it needs accounts at many bookmakers, and "
                    "bookmakers restrict customers who do this."),
        "method": "2019-20 to 2026-27, fair price = sharp pre-match price blended with the model; best price from football-data.co.uk; quotes more than 5 points longer than the market average dropped as errors.",
        "chart": {"type": "bars_ci", "unit": "pct",
                  "rows": [{"label": f"claimed edge {int(s['threshold'] * 100)}%+", "with_bias": True, "clv": s["clv"],
                            "clv_ci90": s["clv_ci90"], "n_filled": s["n_bets"]} for s in rows]},
        "evidence": rows,
    }


# ---------------------------------------------------------------------------
def finding_public_stats(df, pred) -> dict:
    """Do recent-form / xG features predict outcomes beyond the closing price?"""
    d = df[df["hg"].notna()].sort_values("kickoff_utc").merge(bt.market_probs(df), on="match_id")
    rows = []
    for r in d.itertuples():
        rows.append((r.match_id, r.kickoff_utc, r.home, 1, r.hg, r.ag, r.hxg, r.axg, r.mkt_close_h, r.mkt_close_a))
        rows.append((r.match_id, r.kickoff_utc, r.away, 0, r.ag, r.hg, r.axg, r.hxg, r.mkt_close_a, r.mkt_close_h))
    L = pd.DataFrame(rows, columns=["match_id", "t", "team", "home", "gf", "ga", "xgf", "xga", "pw", "pl"]).sort_values("t")
    L["pts"] = np.where(L["gf"] > L["ga"], 3, np.where(L["gf"] == L["ga"], 1, 0))
    L["xpts"] = 3 * L["pw"] + (1 - L["pw"] - L["pl"])
    g = L.groupby("team")
    series = {"xG luck": (L["gf"] - L["ga"]) - (L["xgf"] - L["xga"]), "results vs market": L["pts"] - L["xpts"],
              "xG difference": L["xgf"] - L["xga"], "finishing": L["gf"] - L["xgf"], "goalkeeping": L["ga"] - L["xga"]}
    feats = []
    for name, s in series.items():
        for n in (3, 6, 10):
            L[f"{name}|{n}"] = s.groupby(L["team"]).transform(lambda x: x.shift(1).rolling(n, min_periods=n).mean())
            feats.append(f"{name}|{n}")
    L["rest days|1"] = g["t"].diff().dt.total_seconds() / 86400
    feats.append("rest days|1")
    H = L[L["home"] == 1].set_index("match_id")[feats]
    A = L[L["home"] == 0].set_index("match_id")[feats]
    D = d.set_index("match_id")
    D = D[D["season"] >= 2014]
    lg = lambda p: np.log(p / (1 - p))
    targets = {"home win": ("H", "mkt_close_h", lambda f: H[f] - A[f]), "away win": ("A", "mkt_close_a", lambda f: A[f] - H[f]),
               "draw": ("D", "mkt_close_d", lambda f: (H[f] - A[f]).abs()), "over 2.5": ("O", "mkt_close_o25", lambda f: H[f] + A[f])}
    res = []
    for f in feats:
        for tname, (code, off, fx) in targets.items():
            x = fx(f).reindex(D.index)
            y = ((D["hg"] + D["ag"]) > 2.5).astype(float) if code == "O" else (D["result"] == code).astype(float)
            for period, m in (("discovery", D["season"] <= 2020), ("confirmation", D["season"] >= 2021)):
                z = pd.DataFrame({"y": y, "off": lg(D[off].clip(0.01, 0.99)), "x": x})[m].dropna()
                if len(z) < 300:
                    continue
                xs = (z["x"] - z["x"].mean()) / z["x"].std()
                fit = sm.GLM(z["y"], sm.add_constant(xs), family=sm.families.Binomial(), offset=z["off"]).fit()
                res.append({"feature": f, "target": tname, "period": period, "beta": float(fit.params.iloc[1]),
                            "p": float(fit.pvalues.iloc[1])})
    R = pd.DataFrame(res).pivot_table(index=["feature", "target"], columns="period", values=["beta", "p"])
    R.columns = [f"{a}_{b}" for a, b in R.columns]
    R = R.dropna()
    n_tests = len(R)
    disc = R[R["p_discovery"] < 0.05]
    repl = disc[(disc["p_confirmation"] < 0.05) & (np.sign(disc["beta_discovery"]) == np.sign(disc["beta_confirmation"]))]
    b = json.loads((config.SITE_DATA / "backtest.json").read_text())["test"]["models"]
    return {
        "id": "public_stats", "verdict": "no edge",
        "title": "Public stats are already in the closing price",
        "number": float(len(repl)), "number_kind": "count", "number_of": n_tests,
        "number_label": f"of {n_tests} form and xG signals held up out of sample",
        "summary": (f"We tested {n_tests} pre-match signals (xG over- and under-performance, form against expectations, finishing, "
                    f"goalkeeping, rest) for information beyond the sharp closing price. {len(disc)} looked significant on 2014-20 data; "
                    f"{len(repl)} survived on 2021-26. Our xG model alone scores {b['dc']['rps']:.4f} RPS against the closing "
                    f"price's {b['mkt_close']['rps']:.4f}. Whatever public statistics say, the sharp market already knows."),
        "method": "Logistic regression of each outcome on the feature with the logit of the closing probability as an offset; discovery 2014-15 to 2020-21, confirmation 2021-22 onward.",
        "chart": {"type": "rps", "rows": [{"label": "Elo", "rps": b["elo"]["rps"]}, {"label": "our xG model", "rps": b["dc"]["rps"]},
                                           {"label": "pre-match price", "rps": b["mkt_pre"]["rps"]},
                                           {"label": "closing price", "rps": b["mkt_close"]["rps"]}]},
        "evidence": {"n_tests": n_tests, "significant_in_discovery": int(len(disc)), "replicated": int(len(repl)),
                     "discovery_hits": disc.reset_index().round(4).to_dict(orient="records")},
    }


# ---------------------------------------------------------------------------
def finding_pm_efficiency() -> dict:
    v = json.loads((config.SITE_DATA / "venues.json").read_text())
    rows = []
    for r in v["results"]:
        if r["offset_hours"] == 1 and "accuracy" in r:
            rows.append({"label": r["venue"].capitalize(), "venue_rps": r["accuracy"][r["venue"]]["rps"],
                         "close_rps": r["accuracy"]["bookmaker_close"]["rps"], "n": r["n_matches"],
                         "p": r["dm_venue_vs"]["bookmaker_close"]["p_value"]})
    return {
        "id": "pm_efficiency", "verdict": "no edge",
        "title": "At kick-off, Kalshi and Polymarket are as sharp as Pinnacle",
        "number": rows[0]["venue_rps"] - rows[0]["close_rps"], "number_kind": "rps_diff",
        "number_label": "RPS difference, Kalshi vs sharp close, 1 h before kick-off",
        "summary": ("An hour before kick-off, prediction-market match prices are as accurate as the sharpest bookmaker. "
                    "Buying any outcome at the ask loses about the size of the spread plus fees. The inefficiency is earlier, "
                    "and it belongs to whoever provides liquidity."),
        "method": "Ranked probability score of normalised mid prices vs the de-margined closing price on the same matches.",
        "chart": {"type": "rps_pairs", "rows": rows}, "evidence": v["results"],
    }


def finding_side_markets() -> dict:
    return {
        "id": "side_markets", "verdict": "unproven",
        "title": "Side markets out of line with the match odds",
        "number": None, "number_kind": "none", "number_label": "",
        "summary": ("Polymarket's totals, spreads and both-teams-to-score contracts often disagree with the scoreline implied by "
                    "Polymarket's own match odds. Measured against a model-implied closing price they look cheap, but that "
                    "benchmark shares the model's errors, and realised returns over one season do not confirm it. Kept under "
                    "observation; not shown as a signal."),
        "method": "Side contracts priced from the venue's own 1X2 and over/under 2.5 mid prices with a recalibrated Dixon-Coles distribution.",
        "chart": None, "evidence": None,
    }


def run() -> dict:
    df = dataset.load()
    pred = pd.read_csv(config.DATA / "model" / "backtest_predictions.csv")
    findings = [finding_maker(pred), finding_retail_bias(pred), finding_futures(df), finding_best_price(),
                finding_public_stats(df, pred), finding_pm_efficiency(), finding_side_markets()]
    out = {"generated_utc": config.utcnow().isoformat(), "findings": findings}
    OUT.write_text(json.dumps(_clean(out), separators=(",", ":"), default=str))
    for f in findings:
        n = f["number"]
        print(f"{f['verdict']:18s} {f['title']}: {n}")
    return out
