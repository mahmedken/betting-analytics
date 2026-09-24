"""Team strength as priced by the match market.

Each played match's de-margined closing 1X2 and over/under 2.5 prices imply
expected goals for both sides (models.implied). Fitting the Dixon-Coles rate
structure log rate = mu + home + att + dfn to those implied rates (instead of
to goals) recovers the attack and defence ratings the market is using. They
move as soon as prices move, so they carry information the results do not
yet show: injuries, transfers, managers.

Strengths also drift over a season. The drift (a random walk in each team's
net rating) is estimated from how much market-implied ratings change between
dates in past seasons; the season simulation adds it so that season-long
probabilities are not overconfident.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import dixon_coles as dc
from . import implied


def implied_match_rates(df: pd.DataFrame, mk: pd.DataFrame) -> pd.DataFrame:
    """Closing-price implied (lam, nu) for every played match that has closing prices."""
    d = df[["match_id", "season", "kickoff_utc", "home", "away", "hg", "ag"]].merge(mk, on="match_id")
    d = d[d["hg"].notna()].dropna(subset=["mkt_close_h", "mkt_close_d", "mkt_close_a"])
    po = d["mkt_close_o25"].fillna(d["mkt_pre_o25"]).fillna(0.52).values
    lam, nu, _ = implied.implied_rates_rho(d[["mkt_close_h", "mkt_close_d", "mkt_close_a"]].values, po)
    d["lam_mkt"], d["nu_mkt"] = lam, nu
    return d.dropna(subset=["lam_mkt"])


@dataclass
class MarketRatings:
    model: dc.DixonColes
    drift_sd_per_week: float


def fit(rates: pd.DataFrame, cutoff: pd.Timestamp, teams: list[str], xi: float = 0.01,
        window_days: int = 400, prior_sd: float = 1.0, drift_sd_per_week: float = 0.0) -> MarketRatings:
    r = rates[(rates["kickoff_utc"] < cutoff) & (rates["kickoff_utc"] >= cutoff - pd.Timedelta(days=window_days))]
    age = (cutoff - r["kickoff_utc"]).dt.total_seconds().values / 86400
    w = np.exp(-xi * age)
    m = dc.fit(r["home"].values, r["away"].values, r["lam_mkt"].values, r["nu_mkt"].values, w,
               r["hg"].values, r["ag"].values, prior_sd=prior_sd, teams=teams)
    return MarketRatings(m, drift_sd_per_week)


def net_ratings(m: dc.DixonColes) -> pd.Series:
    return pd.Series(m.att() - m.dfn(), index=m.teams)


def estimate_drift(rates: pd.DataFrame, seasons, step_days: int = 28) -> float:
    """sd per week of the change in a team's net rating, from past seasons.

    Ratings are refitted every `step_days`; the variance of the change
    between consecutive fits, minus nothing (fits use overlapping windows,
    so this is a conservative, slightly low estimate), divided by weeks.
    """
    diffs = []
    for s in seasons:
        rs = rates[rates["season"] == s]
        if rs.empty:
            continue
        start, end = rs["kickoff_utc"].min() + pd.Timedelta(days=60), rs["kickoff_utc"].max()
        dates = pd.date_range(start, end, freq=f"{step_days}D")
        prev = None
        teams = sorted(set(rs["home"]) | set(rs["away"]))
        for t in dates:
            cur = net_ratings(fit(rates, t, teams, xi=0.03, window_days=120).model)
            if prev is not None:
                d = (cur - prev).dropna()
                diffs += list(d - d.mean())
            prev = cur
    return float(np.std(diffs) / np.sqrt(step_days / 7))
