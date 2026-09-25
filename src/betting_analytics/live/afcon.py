"""AFCON 2027 qualifying: live match forecasts, group simulation, markets -> site/data/afcon.json.

Qualification rule (CAF): the top two in each of the 12 groups qualify; in the
groups of the co-hosts (Kenya, Uganda, Tanzania), who qualify automatically,
only the best-placed other team goes through.

Group ranking: points; then among teams level on points, head-to-head points,
head-to-head goal difference, head-to-head goals, head-to-head away goals;
then overall goal difference, goals scored, away goals; then lots (random).
"""

from __future__ import annotations

import json
import random

import numpy as np
import pandas as pd

from .. import config
from ..data import afcon
from ..models import devig
from ..models import dixon_coles as dc
from ..models import international as intl
from . import pricing

OUT = config.SITE_DATA / "afcon.json"
LEDGER = config.LEDGER / "afcon_predictions.csv"


def _r(x, nd=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(x) else round(x, nd)


def _rank_group(teams: list[str], results: list[tuple], rng: random.Random) -> list[str]:
    """results: (home, away, hg, ag). Returns teams ordered 1st..4th."""
    st = {t: [0, 0, 0, 0] for t in teams}           # pts, gd, gf, away gf
    for h, a, x, y in results:
        st[h][1] += x - y; st[h][2] += x
        st[a][1] += y - x; st[a][2] += y; st[a][3] += y
        if x > y:
            st[h][0] += 3
        elif x < y:
            st[a][0] += 3
        else:
            st[h][0] += 1; st[a][0] += 1
    by_pts: dict[int, list[str]] = {}
    for t in teams:
        by_pts.setdefault(st[t][0], []).append(t)
    order = []
    for pts in sorted(by_pts, reverse=True):
        tied = by_pts[pts]
        if len(tied) == 1:
            order += tied
            continue
        s = set(tied)
        h2h = {t: [0, 0, 0, 0] for t in tied}
        for h, a, x, y in results:
            if h in s and a in s:
                h2h[h][1] += x - y; h2h[h][2] += x
                h2h[a][1] += y - x; h2h[a][2] += y; h2h[a][3] += y
                if x > y:
                    h2h[h][0] += 3
                elif x < y:
                    h2h[a][0] += 3
                else:
                    h2h[h][0] += 1; h2h[a][0] += 1
        order += sorted(tied, key=lambda t: (h2h[t][0], h2h[t][1], h2h[t][2], h2h[t][3],
                                             st[t][1], st[t][2], st[t][3], rng.random()), reverse=True)
    return order


def simulate_groups(model: intl.IntlModel, groups: dict[str, list[str]], played: pd.DataFrame,
                    neutral_home: set[str], n_sims: int = 10000, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    prng = random.Random(seed)
    theta = model.sample_theta(n_sims, rng) if model.cov is not None else np.tile(model.theta, (n_sims, 1))
    out = {}
    for g, teams in groups.items():
        done = played[played["home"].isin(teams) & played["away"].isin(teams)]
        fixed = [(r.home, r.away, int(r.hg), int(r.ag)) for r in done.itertuples()]
        have = {(r.home, r.away) for r in done.itertuples()}
        rem = [(h, a) for h in teams for a in teams if h != a and (h, a) not in have]
        counts = {t: np.zeros(4) for t in teams}
        qual = {t: 0 for t in teams}
        pts_sum = {t: 0.0 for t in teams}
        if rem:
            home = [h for h, _ in rem]
            away = [a for _, a in rem]
            neutral = [h in neutral_home for h in home]
            M = model.score_matrix(home, away, neutral, theta)            # (S, m, G, G)
            G = M.shape[-1]
            flat = M.reshape(n_sims, len(rem), G * G)
            cdf = np.cumsum(flat, axis=-1)
            u = rng.random((n_sims, len(rem), 1))
            k = np.minimum((u > cdf).sum(axis=-1), G * G - 1)
            X, Y = k // G, k % G
        hosts = [t for t in teams if t in afcon.HOSTS]
        for s in range(n_sims):
            res = fixed + ([(h, a, int(X[s, j]), int(Y[s, j])) for j, (h, a) in enumerate(rem)] if rem else [])
            order = _rank_group(teams, res, prng)
            for pos, t in enumerate(order):
                counts[t][pos] += 1
            if hosts:
                qual[hosts[0]] += 1
                qual[next(t for t in order if t not in afcon.HOSTS)] += 1
            else:
                qual[order[0]] += 1
                qual[order[1]] += 1
            for h, a, x, y in res:
                pts_sum[h] += 3 if x > y else 1 if x == y else 0
                pts_sum[a] += 3 if y > x else 1 if x == y else 0
        out[g] = [{"team": t, "p_first": float(counts[t][0] / n_sims), "positions": [float(v / n_sims) for v in counts[t]],
                   "p_qualify": float(qual[t] / n_sims), "exp_points": float(pts_sum[t] / n_sims),
                   "host": t in afcon.HOSTS} for t in teams]
        out[g].sort(key=lambda r: (-r["p_qualify"], -r["p_first"]))
    return out


def _table(teams: list[str], played: pd.DataFrame) -> dict:
    t = {x: {"p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0} for x in teams}
    for r in played.itertuples():
        if r.home in t and r.away in t:
            for me, gf, ga in ((r.home, r.hg, r.ag), (r.away, r.ag, r.hg)):
                t[me]["p"] += 1; t[me]["gf"] += int(gf); t[me]["ga"] += int(ga)
                if gf > ga:
                    t[me]["w"] += 1; t[me]["pts"] += 3
                elif gf == ga:
                    t[me]["d"] += 1; t[me]["pts"] += 1
                else:
                    t[me]["l"] += 1
    return t


def _update_ledger(rows: list[dict], played: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    cols = ["event_id", "kickoff_utc", "home", "away", "first_forecast_utc", "last_forecast_utc",
            "p_h", "p_d", "p_a", "book_h", "book_d", "book_a", "book", "hg", "ag"]
    led = pd.read_csv(LEDGER, dtype={"event_id": str}) if LEDGER.exists() else pd.DataFrame(columns=cols)
    led = led.set_index("event_id") if len(led) else pd.DataFrame(columns=cols).set_index("event_id")
    for r in rows:
        if pd.Timestamp(r["kickoff_utc"]) <= now:
            continue
        rec = {k: r.get(k) for k in cols if k not in ("event_id", "first_forecast_utc", "hg", "ag")}
        rec["last_forecast_utc"] = now.isoformat()
        if r["event_id"] in led.index:
            if pd.Timestamp(led.at[r["event_id"], "kickoff_utc"]) <= now:
                continue
            for k, v in rec.items():
                led.at[r["event_id"], k] = v
        else:
            rec["first_forecast_utc"] = now.isoformat()
            led.loc[r["event_id"]] = pd.Series(rec)
    res = played.set_index("event_id") if "event_id" in played else pd.DataFrame()
    for eid in led.index:
        if eid in res.index and pd.isna(led.at[eid, "hg"]):
            led.at[eid, "hg"], led.at[eid, "ag"] = res.at[eid, "hg"], res.at[eid, "ag"]
    led = led.reset_index().rename(columns={"index": "event_id"})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    led[cols].to_csv(LEDGER, index=False, float_format="%.5f")
    return led[cols]


def run(n_sims: int = 10000) -> dict:
    now = pd.Timestamp(config.utcnow())
    cfg = json.loads((config.DATA / "model" / "afcon.json").read_text())
    results = afcon.load_results(refresh=True)
    camp = afcon.espn_events()
    hist = afcon.match_history(results[results["date"] >= "2016-01-01"], camp)
    stand = afcon.espn_standings()
    groups = {g: list(x["team"]) for g, x in stand.groupby("group")}
    all_teams = sorted(stand["team"])
    model = intl.fit_as_of(hist, now, cfg["xi"], cfg["friendly_weight"], cfg["prior_sd"], with_cov=True, teams=all_teams)

    group_of = {t: g for g, ts in groups.items() for t in ts}
    gs = camp[camp["home"].isin(group_of) & camp["away"].isin(group_of)
              & (camp["home"].map(group_of) == camp["away"].map(group_of))]
    played = gs[gs["state"] == "post"]
    # A team whose latest home qualifier was at a neutral venue is assumed to keep playing "home" games away.
    last_home = gs.sort_values("kickoff_utc").groupby("home").tail(1)
    neutral_home = set(last_home.loc[last_home["neutral"], "home"])

    seed = int(now.timestamp()) // 3600
    sim = simulate_groups(model, groups, played, neutral_home, n_sims=n_sims, seed=seed)
    theta_s = model.sample_theta(1000, np.random.default_rng(seed + 1))
    # International matches per team in the model's eight-year window; few matches = uncertain rating.
    recent = hist[hist["kickoff_utc"] >= now - pd.Timedelta(days=8 * 365)]
    n_matches = pd.concat([recent["home"], recent["away"]]).value_counts()
    try:
        pm = afcon.polymarket_groups()
        pm = pm[pm["team"].isin(all_teams)]
    except Exception:
        pm = pd.DataFrame(columns=["team", "bid", "ask", "fee_rate", "volume"])
    meta = {}
    for r in camp.itertuples():
        meta.setdefault(r.home, {"code": r.home_code, "color": r.home_color, "logo": r.home_logo})
        meta.setdefault(r.away, {"code": r.away_code, "color": r.away_color, "logo": r.away_logo})
    for r in stand.itertuples():
        meta.setdefault(r.team, {"code": r.code, "color": "#6b7280", "logo": r.logo})

    groups_out, gaps = [], []
    for g, rows in sim.items():
        table = _table(groups[g], played)
        for r in rows:
            r.update(meta.get(r["team"], {}))
            r["table"] = table[r["team"]]
            r["n_matches"] = int(n_matches.get(r["team"], 0))
            q = pm[pm["team"] == r["team"]]
            if len(q):
                q = q.iloc[0]
                r["market"] = {"venue": "Polymarket", "bid": _r(q["bid"]), "ask": _r(q["ask"]), "volume": _r(q["volume"], 0)}
                if np.isfinite(q["ask"]) and np.isfinite(q["bid"]) and q["ask"] - q["bid"] <= 0.08:
                    fee = q["fee_rate"] if pd.notna(q["fee_rate"]) else 0.0
                    cy = pricing.cost_per_contract(q["ask"], "polymarket", fee)
                    cn = pricing.cost_per_contract(1 - q["bid"], "polymarket", fee)
                    for side, p, cost, px in (("yes", r["p_first"], cy, q["ask"]), ("no", 1 - r["p_first"], cn, 1 - q["bid"])):
                        if p - cost > 0.04 and cost > 0.02:
                            gaps.append({"group": g, "team": r["team"], "code": r.get("code"), "logo": r.get("logo"),
                                         "side": side, "price": _r(px), "cost": _r(cost), "model": _r(p),
                                         "what": f"{r['team']} {'to win' if side == 'yes' else 'not to win'} Group {g}",
                                         "expected": _r(p / cost - 1)})
        groups_out.append({"group": g, "hosts": [t for t in groups[g] if t in afcon.HOSTS], "teams": rows})
    gaps.sort(key=lambda x: -x["expected"])
    best_gap = {}
    for x in gaps:
        best_gap.setdefault((x["team"], x["side"]), x)

    # matches: this window's fixtures and results. Started matches show the forecast frozen before kick-off.
    frozen = pd.read_csv(LEDGER, dtype={"event_id": str}).set_index("event_id") if LEDGER.exists() else pd.DataFrame()
    matches, ledger_rows = [], []
    for r in gs.itertuples():
        m = {"event_id": r.event_id, "kickoff_utc": r.kickoff_utc.isoformat(), "group": group_of[r.home],
             "home": r.home, "away": r.away, "neutral": bool(r.neutral), "state": r.state, "status": r.status,
             "venue": r.venue, "hg": _r(r.hg, 0), "ag": _r(r.ag, 0), "live_hg": _r(r.live_hg, 0), "live_ag": _r(r.live_ag, 0),
             "clock": r.clock, "home_meta": meta.get(r.home), "away_meta": meta.get(r.away),
             "n_matches": [int(n_matches.get(r.home, 0)), int(n_matches.get(r.away, 0))]}
        if r.state != "pre" and r.event_id in frozen.index:
            f = frozen.loc[r.event_id]
            m.update({"p_h": _r(f["p_h"]), "p_d": _r(f["p_d"]), "p_a": _r(f["p_a"]), "frozen_utc": f["last_forecast_utc"]})
            if pd.notna(f["book_h"]):
                m["book"] = {"provider": f["book"], "p": [_r(f["book_h"]), _r(f["book_d"]), _r(f["book_a"])]}
        if r.state == "pre":
            M = model.score_matrix([r.home], [r.away], [r.neutral])[0]
            p = dc.outcome_probs(M)
            lam, nu = model.rates([r.home], [r.away], [r.neutral])
            ps = dc.outcome_probs(model.score_matrix([r.home], [r.away], [r.neutral], theta_s))[:, 0]   # (1000, 3)
            m["range90"] = [[_r(q) for q in np.quantile(ps[:, k], [0.05, 0.95])] for k in range(3)]
            m.update({"p_h": _r(p[0]), "p_d": _r(p[1]), "p_a": _r(p[2]), "p_o25": _r(dc.prob_over(M, 2.5)),
                      "xg": [_r(lam[0], 2), _r(nu[0], 2)], "score_matrix": [[_r(v, 4) for v in row[:6]] for row in M[:6]]})
            try:
                o = afcon.espn_odds(r.event_id)
            except Exception:
                o = {}
            if o.get("odds_h"):
                pb = devig.shin(np.array([[o["odds_h"], o["odds_d"], o["odds_a"]]]))[0]
                m["book"] = {"provider": o["provider"], "odds": [_r(o["odds_h"], 3), _r(o["odds_d"], 3), _r(o["odds_a"], 3)],
                             "p": [_r(x) for x in pb], "ou_line": o.get("ou_line"),
                             "odds_over": _r(o.get("odds_over"), 3), "odds_under": _r(o.get("odds_under"), 3)}
                m["edges"] = {s: _r(pm_ * od - 1) for s, pm_, od in (("H", p[0], o["odds_h"]), ("D", p[1], o["odds_d"]), ("A", p[2], o["odds_a"]))}
            ledger_rows.append({"event_id": r.event_id, "kickoff_utc": r.kickoff_utc.isoformat(), "home": r.home, "away": r.away,
                                "p_h": p[0], "p_d": p[1], "p_a": p[2],
                                "book_h": m.get("book", {}).get("p", [None] * 3)[0], "book_d": m.get("book", {}).get("p", [None] * 3)[1],
                                "book_a": m.get("book", {}).get("p", [None] * 3)[2], "book": m.get("book", {}).get("provider")})
        matches.append(m)
    led = _update_ledger(ledger_rows, played, now)
    settled = led.dropna(subset=["hg"])
    record = {"n_settled": int(len(settled))}
    if len(settled):
        from ..evaluation import metrics
        res = np.where(settled["hg"] > settled["ag"], "H", np.where(settled["hg"] == settled["ag"], "D", "A"))
        record["rps_model"] = float(metrics.rps(settled[["p_h", "p_d", "p_a"]].values.astype(float), res).mean())
        b = settled.dropna(subset=["book_h"])
        if len(b):
            rb = np.where(b["hg"] > b["ag"], "H", np.where(b["hg"] == b["ag"], "D", "A"))
            record["rps_book"] = float(metrics.rps(b[["book_h", "book_d", "book_a"]].values.astype(float), rb).mean())
            record["rps_model_same"] = float(metrics.rps(b[["p_h", "p_d", "p_a"]].values.astype(float), rb).mean())
            record["n_book"] = int(len(b))

    n = len(model.teams)
    net = dict(zip(model.teams, model.theta[2:2 + n] - model.theta[2 + n:]))
    out = {"generated_utc": now.isoformat(), "n_sims": n_sims, "config": cfg,
           "rule": "Top two in each group qualify; in Kenya's, Uganda's and Tanzania's groups the host qualifies automatically and only the best other team goes through.",
           "groups": groups_out, "matches": matches, "market_gaps": list(best_gap.values()), "record": record,
           "ratings": sorted([{"team": t, "net": _r(net[t], 3), **(meta.get(t) or {})} for t in all_teams], key=lambda x: -x["net"])}
    OUT.write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"afcon: {len(matches)} matches, {len(gaps)} market gaps, {record}")
    return out
