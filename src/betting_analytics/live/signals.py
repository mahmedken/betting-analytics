"""Turn the rules that survived testing (site/data/lab.json) into live signals.

Signal types:
  maker      Kalshi limit orders against retail flow: bid the draw, offer on
             big-six wins, 24-96 hours before kick-off.
  season     Season-market price vs the market-implied season simulation,
             when the gap exceeds 3 points after costs.
  best_price Longest bookmaker price at least 5% above the fair price.
  arbitrage  Buying home, draw and away across venues costs less than $1.

Each signal carries the historical evidence for its rule. New signals are
appended to data/ledger/signals.csv so the rules are tracked forward.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .. import config
from ..data.teams import code, display
from . import pricing

BIG6 = {"Man United", "Liverpool", "Arsenal", "Chelsea", "Man City", "Tottenham"}
LEDGER = config.LEDGER / "signals.csv"


def _lab() -> dict:
    p = config.SITE_DATA / "lab.json"
    return {f["id"]: f for f in json.loads(p.read_text())["findings"]} if p.exists() else {}


def _maker_evidence(lab: dict, entry_h: int, side: str, group: str) -> dict | None:
    f = lab.get("maker")
    if not f:
        return None
    cells = [c for c in f["evidence"] if c["side"] == side and c["group"] == group and c["improve"] == 0.01]
    if not cells:
        return None
    c = min(cells, key=lambda c: abs(c["entry_h"] - entry_h))
    return {"finding": "maker", "entry_h": c["entry_h"], "clv": c["clv"], "clv_ci90": c["clv_ci90"],
            "n": c["n_filled"], "fill_rate": c["fill_rate"]}


def maker_signals(kq: pd.DataFrame, fixtures: pd.DataFrame, now: pd.Timestamp, lab: dict) -> list[dict]:
    out = []
    if kq is None or kq.empty:
        return out
    fx = fixtures.set_index(["home", "away"])
    for r in kq[kq["market"] == "1x2"].itertuples(index=False):
        if (r.home, r.away) not in fx.index or pd.isna(r.bid) or pd.isna(r.ask):
            continue
        f = fx.loc[(r.home, r.away)]
        hours = (f["kickoff_utc"] - now).total_seconds() / 3600
        if not (24 <= hours <= 96) or r.ask - r.bid < 0.01:
            continue
        team = r.home if r.selection == "H" else r.away if r.selection == "A" else None
        if r.selection == "D":
            px = round(min(r.bid + 0.01, r.ask - 0.01), 2)
            ev = _maker_evidence(lab, hours, "bid", "draw")
            action, what = "bid", "Draw"
            cost = px + 0.0175 * px * (1 - px)
        elif team in BIG6:
            px = round(max(r.ask - 0.01, r.bid + 0.01), 2)
            ev = _maker_evidence(lab, hours, "ask", "big-six")
            action, what = "offer", f"{display(team)} to win"
            cost = (1 - px) + 0.0175 * px * (1 - px)
        else:
            continue
        if ev is None:
            continue
        out.append({"type": "maker", "id": f"maker|{f['match_id']}|{r.selection}", "venue": "Kalshi",
                    "match_id": f["match_id"], "kickoff_utc": f["kickoff_utc"].isoformat(),
                    "home": r.home, "away": r.away, "home_code": code(r.home), "away_code": code(r.away),
                    "action": action, "what": what, "limit": px, "bid": r.bid, "ask": r.ask, "cost": round(cost, 4),
                    "expected": ev["clv"], "hours_to_kickoff": round(hours, 1), "evidence": ev, "ticker": r.ticker})
    return out


def season_signals(teams: list[dict], lab: dict, min_gap: float = 0.03, max_spread: float = 0.06) -> list[dict]:
    out = []
    names = {"title": "win the league", "top4": "finish top four", "relegation": "be relegated"}
    ev = lab.get("futures")
    for t in teams:
        for mk in ("title", "top4", "relegation"):
            p = t[mk]
            for venue, q in (t.get("markets", {}).get(mk) or {}).items():
                bid, ask, fee = q.get("bid"), q.get("ask"), q.get("fee_rate")
                if bid is None or ask is None or ask - bid > max_spread:
                    continue
                cy = pricing.cost_per_contract(ask, venue, fee)
                cn = pricing.cost_per_contract(1 - bid, venue, fee)
                for side, prob, cost in (("yes", p, cy), ("no", 1 - p, cn)):
                    if prob - cost > min_gap and cost > 0.02:
                        out.append({"type": "season", "id": f"season|{t['team']}|{mk}|{venue}|{side}",
                                    "venue": venue.capitalize(), "team": t["team"], "team_code": t["code"],
                                    "market": mk, "action": "buy", "side": side,
                                    "what": f"{display(t['team'])} {'to' if side == 'yes' else 'not to'} {names[mk]}",
                                    "price": ask if side == "yes" else 1 - bid, "cost": round(cost, 4),
                                    "sim": prob, "expected": prob / cost - 1,
                                    "evidence": None if not ev else {"finding": "futures", "verdict": ev["verdict"],
                                                                     "brier_sim": ev["evidence"]["brier_sim"],
                                                                     "brier_market": ev["evidence"]["brier_market"]}})
    # one row per contract: keep the venue with the larger edge
    best = {}
    for s_ in sorted(out, key=lambda s: -s["expected"]):
        best.setdefault((s_["team"], s_["market"], s_["side"]), s_)
    return list(best.values())


def best_price_signals(matches: list[dict], lab: dict, min_edge: float = 0.05) -> list[dict]:
    out = []
    ev = lab.get("best_price")
    for m in matches:
        if not m.get("xg_market"):
            continue          # fair price must be anchored to a bookmaker price
        for q in m["quotes"]:
            if q["venue"] != "best price" or (q.get("edge") or 0) < min_edge:
                continue
            out.append({"type": "best_price", "id": f"best|{m['match_id']}|{q['market']}|{q['selection']}|{q['line']}",
                        "venue": "best bookmaker price", "match_id": m["match_id"], "kickoff_utc": m["kickoff_utc"],
                        "home": m["home"], "away": m["away"], "home_code": m["home_code"], "away_code": m["away_code"],
                        "action": "buy", "market": q["market"], "selection": q["selection"], "line": q["line"],
                        "odds": q["odds"], "fair": q["fair"], "expected": q["edge"],
                        "evidence": None if not ev else {"finding": "best_price", "clv": ev["number"]}})
    return out


def arbitrage_signals(matches: list[dict]) -> list[dict]:
    out = []
    for m in matches:
        best = {}
        for q in m["quotes"]:
            if q["market"] != "1x2" or not q.get("cost") or q["venue"] not in ("kalshi", "polymarket"):
                continue
            if q["selection"] not in best or q["cost"] < best[q["selection"]]["cost"]:
                best[q["selection"]] = q
        if len(best) == 3:
            total = sum(b["cost"] for b in best.values())
            if total < 0.995:
                out.append({"type": "arbitrage", "id": f"arb|{m['match_id']}", "match_id": m["match_id"],
                            "kickoff_utc": m["kickoff_utc"], "home": m["home"], "away": m["away"],
                            "home_code": m["home_code"], "away_code": m["away_code"], "total_cost": total,
                            "expected": 1 / total - 1,
                            "legs": {s: {"venue": b["venue"], "price": b["ask"], "cost": b["cost"]} for s, b in best.items()}})
    return out


def log(signals: list[dict], now: pd.Timestamp) -> None:
    """Append signals not seen before (by id) to the signals ledger."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    seen = set(pd.read_csv(LEDGER)["id"]) if LEDGER.exists() else set()
    new = [s for s in signals if s["id"] not in seen]
    if not new:
        return
    rows = [{"id": s["id"], "type": s["type"], "first_seen_utc": now.isoformat(), "venue": s.get("venue"),
             "match_id": s.get("match_id"), "team": s.get("team"), "what": s.get("what"), "action": s.get("action"),
             "price": s.get("limit", s.get("price", s.get("odds"))), "cost": s.get("cost"),
             "expected": s.get("expected")} for s in new]
    pd.DataFrame(rows).to_csv(LEDGER, mode="a", header=not LEDGER.exists(), index=False)
