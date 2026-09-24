"""Forward prediction record.

Every refresh writes the current forecast for each upcoming match. A row can be
updated until kick-off and is frozen after it: rows whose kick-off has passed
are never modified except to add the final score. Because the file is committed
to git on every refresh, the commit history timestamps each forecast, so
anyone can verify that a forecast existed before the match started.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from ..evaluation import metrics

PATH = config.LEDGER / "predictions.csv"

FORECAST_COLS = [
    "model_h", "model_d", "model_a", "model_o25",
    "fair_h", "fair_d", "fair_a", "fair_o25",
    "book_h", "book_d", "book_a", "book_o25", "book_source",
    "kalshi_h", "kalshi_d", "kalshi_a",
    "poly_h", "poly_d", "poly_a",
]
COLUMNS = (["match_id", "season", "gameweek", "kickoff_utc", "home", "away",
            "first_forecast_utc", "last_forecast_utc", "model_version"]
           + FORECAST_COLS + ["hg", "ag", "result"])


TEXT_COLS = ["match_id", "kickoff_utc", "home", "away", "first_forecast_utc", "last_forecast_utc",
             "model_version", "book_source", "result"]


def load() -> pd.DataFrame:
    if not PATH.exists():
        return pd.DataFrame(columns=COLUMNS).astype({c: "object" for c in TEXT_COLS})
    df = pd.read_csv(PATH, dtype={c: "object" for c in TEXT_COLS})
    for c in COLUMNS:
        if c not in df:
            df[c] = np.nan
    return df[COLUMNS]


def update(ledger: pd.DataFrame, forecasts: pd.DataFrame, results: pd.DataFrame,
           now: pd.Timestamp, model_version: str) -> pd.DataFrame:
    """forecasts: one row per upcoming match with match_id, season, gameweek,
    kickoff_utc, home, away and FORECAST_COLS. results: match_id, hg, ag."""
    led = ledger.set_index("match_id") if len(ledger) else pd.DataFrame(columns=COLUMNS).set_index("match_id")
    now_s = now.isoformat()
    for r in forecasts.itertuples(index=False):
        kick = pd.Timestamp(r.kickoff_utc)
        if kick <= now:
            continue
        row = {c: getattr(r, c) for c in FORECAST_COLS}
        row.update({"season": r.season, "gameweek": r.gameweek, "kickoff_utc": kick.isoformat(),
                    "home": r.home, "away": r.away, "last_forecast_utc": now_s,
                    "model_version": model_version})
        if r.match_id in led.index:
            if pd.Timestamp(led.at[r.match_id, "kickoff_utc"]) <= now:
                continue   # frozen
            for k, v in row.items():
                led.at[r.match_id, k] = v
        else:
            row["first_forecast_utc"] = now_s
            led.loc[r.match_id] = pd.Series(row)
    res = results.set_index("match_id")
    for mid in led.index:
        if mid in res.index and pd.isna(led.at[mid, "result"]):
            hg, ag = res.at[mid, "hg"], res.at[mid, "ag"]
            if pd.notna(hg):
                led.at[mid, "hg"] = int(hg)
                led.at[mid, "ag"] = int(ag)
                led.at[mid, "result"] = "H" if hg > ag else "D" if hg == ag else "A"
    out = led.reset_index().rename(columns={"index": "match_id"})
    return out[COLUMNS].sort_values("kickoff_utc").reset_index(drop=True)


def save(ledger: pd.DataFrame) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(PATH, index=False, float_format="%.5f")


def score(ledger: pd.DataFrame) -> dict:
    """RPS of each forecast source on settled matches where that source exists,
    plus a head-to-head on matches where every compared source exists."""
    done = ledger[ledger["result"].isin(["H", "D", "A"])]
    sources = {"model": "model", "fair": "fair", "bookmaker": "book", "kalshi": "kalshi", "polymarket": "poly"}
    out = {"n_settled": int(len(done)), "sources": {}}
    for name, pre in sources.items():
        cols = [f"{pre}_{s}" for s in "hda"]
        d = done.dropna(subset=cols)
        if d.empty:
            continue
        p = d[cols].values.astype(float)
        p = p / p.sum(axis=1, keepdims=True)
        out["sources"][name] = {"n": int(len(d)), "rps": float(metrics.rps(p, d["result"]).mean()),
                                "log_loss": float(metrics.log_loss(p, d["result"]).mean())}
    return out
