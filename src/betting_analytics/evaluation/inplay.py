"""Estimate the goal clock and validate in-play probabilities -> site/data/inplay.json.

Goal clock. Goal minutes from martj42/international_results goalscorers.csv
(men's internationals since 2010 whose goal list matches the final score;
extra-time goals dropped). The file records first-half stoppage goals as
minute 45 and second-half stoppage goals as minute 90, so each of those
minutes holds a spike. Regular-time goals in those minutes are taken as the
average of the two minutes before; the excess is stoppage time. Average
stoppage lengths come from the added minute of stoppage goals in ESPN's
event data (a goal at 90'+k falls, on average, halfway through stoppage).

Validation. For AFCON qualifiers with complete goal events in ESPN (the
2024 campaign and the current one), pre-match scoring rates come from the
walk-forward national-team model (fitted only on earlier matches, frozen
settings). At fixed points of each match the in-play probabilities are
scored against the final result: ranked probability score by checkpoint and
calibration pooled over checkpoints. A uniform goal clock is the baseline.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .. import config
from ..data import afcon, espn, http
from ..models import dixon_coles as dc
from ..models import inplay
from ..models import international as intl
from . import metrics

GOALS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/goalscorers.csv"
OUT = config.SITE_DATA / "inplay.json"
MONTHS = ("202409", "202410", "202411", "202603", "202609", "202610", "202611", "202703")
CHECKPOINTS = [("15'", 15.0, False), ("30'", 30.0, False), ("half time", 45.0, True),
               ("60'", 60.0, False), ("75'", 75.0, False), ("85'", 85.0, False)]


def _goalscorers() -> pd.DataFrame:
    path = afcon.RAW / "goalscorers.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(http.get(GOALS_URL).content)
    return pd.read_csv(path, parse_dates=["date"])


def _espn_matches() -> tuple[pd.DataFrame, dict]:
    rows, goals = [], {}
    for mo in MONTHS:
        try:
            evs = espn.scoreboard("caf.nations_qual", mo)
        except Exception:
            continue
        for e in evs:
            if e["status"]["type"]["state"] != "post":
                continue
            g = espn.goal_events(e)
            c = e["competitions"][0]
            sc = {x["homeAway"]: int(x["score"]) for x in c["competitors"]}
            if sum(x["side"] == "home" for x in g) != sc["home"] or sum(x["side"] == "away" for x in g) != sc["away"]:
                continue      # incomplete event list
            goals[e["id"]] = g
            rows.append(e["id"])
    ev = afcon.espn_events(MONTHS)
    return ev[ev["event_id"].isin(rows)].reset_index(drop=True), goals


def estimate_clock(gs: pd.DataFrame, res: pd.DataFrame, espn_goals: dict) -> tuple[inplay.GoalClock, dict]:
    res = res[res["date"] >= "2010-01-01"]
    g = gs[gs["date"] >= "2010-01-01"]
    key = ["date", "home_team", "away_team"]
    n_rec = g.groupby(key).size().rename("n").reset_index()
    m = res.rename(columns={"home": "home_team", "away": "away_team"})[key + ["hg", "ag"]].merge(n_rec, on=key)
    complete = m.loc[m["n"] == m["hg"] + m["ag"], key]
    g = g.merge(complete, on=key)
    g = g[g["minute"] <= 90]
    c = g["minute"].astype(int).value_counts().reindex(range(1, 91), fill_value=0).astype(float)
    reg45, reg90 = (c[43] + c[44]) / 2, (c[88] + c[89]) / 2
    stop1, stop2 = max(c[45] - reg45, 0.0), max(c[90] - reg90, 0.0)
    c[45], c[90] = reg45, reg90
    total = c.sum() + stop1 + stop2
    added = [(x["base"], x["added"]) for gl in espn_goals.values() for x in gl if x["added"] > 0]
    a1 = [a for b, a in added if b == 45]
    a2 = [a for b, a in added if b == 90]
    # a goal shown as 45'+k happened in added minute k; uniform over a stoppage of length L gives mean k = L/2 + 1/2
    len1 = 2 * (np.mean(a1) - 0.5) if len(a1) >= 5 else 3.0
    len2 = 2 * (np.mean(a2) - 0.5) if len(a2) >= 5 else 6.0
    clock = inplay.GoalClock((c / total).values, stop1 / total, stop2 / total, float(len1), float(len2))
    info = {"n_goals": int(len(g)), "n_matches": int(len(complete)), "since": "2010",
            "n_stoppage_goals_espn": [len(a1), len(a2)],
            "share_first_half": round(float(clock.share[:45].sum() + clock.stop1), 4),
            "share_stoppage": round(clock.stop1 + clock.stop2, 4)}
    return clock, info


def _score_at(goals: list[dict], minute: float, halftime: bool) -> tuple[int, int]:
    if halftime:
        inc = [x for x in goals if x["base"] <= 45]
    else:
        inc = [x for x in goals if x["base"] <= minute and not (x["base"] == minute and x["added"] > 0)]
    return sum(x["side"] == "home" for x in inc), sum(x["side"] == "away" for x in inc)


def validate(clock: inplay.GoalClock, ev: pd.DataFrame, goals: dict) -> dict:
    cfg = json.loads((config.DATA / "model" / "afcon.json").read_text())
    res = afcon.load_results()
    H = afcon.match_history(res[res["date"] >= "2008-01-01"], afcon.espn_events(MONTHS))
    uni = inplay.uniform_clock(clock.len1, clock.len2)
    recs = []
    for day, grp in ev.groupby(ev["kickoff_utc"].dt.floor("D")):
        m = intl.fit_as_of(H, day, cfg["xi"], cfg["friendly_weight"], cfg["prior_sd"],
                           teams=list(set(grp["home"]) | set(grp["away"])))
        lam, nu = m.rates(grp["home"].values, grp["away"].values, grp["neutral"].values)
        pre = dc.outcome_probs(m.score_matrix(grp["home"].values, grp["away"].values, grp["neutral"].values))
        for j, r in enumerate(grp.itertuples()):
            result = "H" if r.hg > r.ag else "D" if r.hg == r.ag else "A"
            recs.append({"label": "kick-off", "minute": 0.0, "day": str(day.date()), "result": result,
                         "p": pre[j], "p_uniform": pre[j], "lead": 0})
            for label, mnt, ht in CHECKPOINTS:
                x, y = _score_at(goals[r.event_id], mnt, ht)
                rem = clock.remaining(mnt, 0.0, halftime=ht)
                rem_u = uni.remaining(mnt, 0.0, halftime=ht)
                recs.append({"label": label, "minute": mnt, "day": str(day.date()), "result": result,
                             "p": inplay.probs(lam[j], nu[j], x, y, rem), "p_uniform": inplay.probs(lam[j], nu[j], x, y, rem_u),
                             "lead": int(np.sign(x - y))})
    R = pd.DataFrame(recs)
    out = {"n_matches": int(len(ev)), "campaigns": "AFCON qualifiers Sep–Nov 2024 and Sep 2026 onward (ESPN goal events)",
           "checkpoints": []}
    for label in ["kick-off"] + [c[0] for c in CHECKPOINTS]:
        s = R[R["label"] == label]
        P, Pu = np.vstack(s["p"].values), np.vstack(s["p_uniform"].values)
        out["checkpoints"].append({"label": label, "n": int(len(s)),
                                   "rps": float(metrics.rps(P, s["result"]).mean()),
                                   "rps_uniform": float(metrics.rps(Pu, s["result"]).mean()),
                                   "log_loss": float(metrics.log_loss(P, s["result"]).mean())})
    live = R[R["label"] != "kick-off"]
    P = np.vstack(live["p"].values)
    y = metrics.onehot(live["result"])
    out["calibration"] = metrics.calibration_table(P.ravel(), y.ravel(), np.linspace(0, 1, 11)).to_dict(orient="records")
    out["ece"] = metrics.ece(P.ravel(), y.ravel())
    # Does the leading team win as often as the model says? (score effects would show up here)
    lead = live[live["lead"] != 0]
    p_lead = np.array([p[0] if l > 0 else p[2] for p, l in zip(lead["p"], lead["lead"])])
    won = np.array([(r == "H") if l > 0 else (r == "A") for r, l in zip(lead["result"], lead["lead"])])
    out["leader"] = {"n": int(len(lead)), "mean_pred": float(p_lead.mean()), "freq": float(won.mean())}
    return out


def run() -> dict:
    gs = _goalscorers()
    res = afcon.load_results()
    ev, goals = _espn_matches()
    clock, info = estimate_clock(gs, res, goals)
    out = {"generated_utc": config.utcnow().isoformat(), "clock": {**clock.to_json(), **info},
           "validation": validate(clock, ev, goals)}
    from .report import _clean
    OUT.write_text(json.dumps(_clean(out), separators=(",", ":")))
    v = out["validation"]
    print("inplay:", info, "| rps by checkpoint", [(c["label"], round(c["rps"], 4), round(c["rps_uniform"], 4)) for c in v["checkpoints"]],
          "| ece", round(v["ece"], 4), "| leader", v["leader"])
    return out
