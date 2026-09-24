"""Goal-difference Elo ratings with an ordered-logit map to 1X2 probabilities.

Baseline in the style of Hvattum & Arntzen (2010): ratings are updated after
every match with the World Football Elo goal-difference multiplier, and the
pre-match rating difference is turned into home/draw/away probabilities by an
ordered logistic regression fitted on earlier matches only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize
from scipy.special import expit


@dataclass
class EloConfig:
    k: float = 20.0
    home_adv: float = 60.0
    carry: float = 0.8          # share of (rating - 1500) kept between seasons
    init: float = 1500.0


def _gd_mult(gd: int) -> float:
    gd = abs(gd)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11 + gd) / 8


def run(df: pd.DataFrame, cfg: EloConfig) -> pd.DataFrame:
    """Pre-match ratings for every row of `df` (sorted by kick-off).

    Unplayed matches get the latest ratings. A team entering the league takes
    the mean end-of-season rating of the teams relegated the season before.
    """
    df = df.sort_values("kickoff_utc")
    ratings: dict[str, float] = {}
    season_teams: dict[int, set[str]] = df.groupby("season").apply(
        lambda g: set(g["home"]) | set(g["away"]), include_groups=False).to_dict()
    pre_h, pre_a = [], []
    current = None
    for row in df.itertuples(index=False):
        if row.season != current:
            prev_teams = season_teams.get(current, set()) if current is not None else set()
            teams_now = season_teams[row.season]
            relegated = prev_teams - teams_now
            promoted_rating = (np.mean([ratings[t] for t in relegated if t in ratings])
                               if relegated else cfg.init - 100)
            for t in list(ratings):
                ratings[t] = cfg.init + cfg.carry * (ratings[t] - cfg.init)
            for t in teams_now:
                if t not in prev_teams:
                    ratings[t] = promoted_rating if current is not None else cfg.init
            current = row.season
        rh, ra = ratings[row.home], ratings[row.away]
        pre_h.append(rh)
        pre_a.append(ra)
        if np.isnan(row.hg):
            continue
        exp_h = 1.0 / (1.0 + 10 ** (-(rh + cfg.home_adv - ra) / 400))
        score = 1.0 if row.hg > row.ag else 0.5 if row.hg == row.ag else 0.0
        delta = cfg.k * _gd_mult(int(row.hg - row.ag)) * (score - exp_h)
        ratings[row.home] = rh + delta
        ratings[row.away] = ra - delta
    out = pd.DataFrame({"elo_home": pre_h, "elo_away": pre_a}, index=df.index)
    out["elo_diff"] = (out["elo_home"] + cfg.home_adv - out["elo_away"]) / 400
    return out.reindex(df.index)


@dataclass
class OrderedLogit:
    """P(A) = s(c0 - b x), P(A or D) = s(c1 - b x), P(H) = 1 - s(c1 - b x)."""
    c0: float
    c1: float
    b: float

    def predict(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        pa = expit(self.c0 - self.b * x)
        pad = expit(self.c1 - self.b * x)
        return np.stack([1 - pad, pad - pa, pa], axis=-1)

    @classmethod
    def fit(cls, x, result, weights=None) -> "OrderedLogit":
        x = np.asarray(x, dtype=float)
        y = np.asarray(result)
        w = np.ones(len(x)) if weights is None else np.asarray(weights, dtype=float)
        col = np.select([y == "H", y == "D", y == "A"], [0, 1, 2])

        def nll(params):
            c0, gap, b = params
            p = cls(c0, c0 + np.exp(gap), b).predict(x)
            return -np.sum(w * np.log(np.clip(p[np.arange(len(x)), col], 1e-12, None)))

        res = optimize.minimize(nll, x0=[-1.0, 0.0, 3.0], method="Nelder-Mead",
                                options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000})
        c0, gap, b = res.x
        return cls(float(c0), float(c0 + np.exp(gap)), float(b))
