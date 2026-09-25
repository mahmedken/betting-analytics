"""In-play result probabilities from the current score and the match clock.

Goals still to come are Poisson with the pre-match scoring rate times the
share of a match's goals that are scored after the current moment:

    home goals to come ~ Poisson(lam * R(t)),   away ~ Poisson(nu * R(t))

R(t) comes from the empirical distribution of goal times in international
football (GoalClock), which is not uniform: more goals come late in each half
and in stoppage time. The match clock is regular minutes 1-45, first-half
stoppage, minutes 46-90, second-half stoppage. The site computes the same
function in the browser (site/assets/quant.js).

Not modelled: red cards and the tendency of trailing teams to attack (Dixon &
Robinson 1998). The validation in evaluation/inplay.py measures how much this
costs in calibration.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson


@dataclass
class GoalClock:
    share: np.ndarray    # share of goals in regular minute k = 1..90 (index k-1), stoppage excluded
    stop1: float         # share of goals in first-half stoppage time
    stop2: float         # share of goals in second-half stoppage time
    len1: float          # average first-half stoppage (minutes)
    len2: float          # average second-half stoppage (minutes)

    def _after(self, elapsed: float) -> float:
        """Share of regular-time goals after `elapsed` minutes (goals uniform within each minute)."""
        e = min(max(elapsed, 0.0), 90.0)
        k = int(np.floor(e))
        part = self.share[k] * (1 - (e - k)) if k < 90 else 0.0
        return float(self.share[k + 1:].sum() + part)

    def remaining(self, minute: float, added: float = 0.0, halftime: bool = False) -> float:
        """Share of the match's goals still to come.

        minute  elapsed regular minutes (ESPN shows "17'" during the 17th minute: pass 16.5)
        added   minutes into stoppage time when minute is 45 or 90
        """
        second = float(self.share[45:].sum()) + self.stop2
        if halftime:
            return second
        if minute >= 90:
            if added <= 0:
                return self.stop2
            return self.stop2 * max(0.0, 1 - added / self.len2)
        if minute >= 45 and added > 0:
            return self.stop1 * max(0.0, 1 - added / self.len1) + second
        if minute < 45:
            return self._after(minute) - float(self.share[45:].sum()) + self.stop1 + second
        return self._after(minute) + self.stop2

    def to_json(self) -> dict:
        return {"share": [round(float(x), 6) for x in self.share], "stop1": round(self.stop1, 6),
                "stop2": round(self.stop2, 6), "len1": round(self.len1, 2), "len2": round(self.len2, 2)}


def uniform_clock(len1: float = 3.0, len2: float = 6.0) -> GoalClock:
    """Goals equally likely in every minute of play, stoppage included (a baseline)."""
    total = 90 + len1 + len2
    return GoalClock(np.full(90, 1 / total), len1 / total, len2 / total, len1, len2)


def probs(lam: float, nu: float, hg: int, ag: int, rem: float, gmax: int = 15) -> np.ndarray:
    """P(home win, draw, away win) at full time given the current score and the share of goals to come."""
    k = np.arange(gmax)
    ph = poisson.pmf(k, lam * rem) if rem > 0 else (k == 0).astype(float)
    pa = poisson.pmf(k, nu * rem) if rem > 0 else (k == 0).astype(float)
    M = np.outer(ph, pa)
    d = (hg + k)[:, None] - (ag + k)[None, :]
    out = np.array([M[d > 0].sum(), M[d == 0].sum(), M[d < 0].sum()])
    return out / out.sum()
