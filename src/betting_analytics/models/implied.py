"""Scoreline distributions implied by market prices, and rate-level blending.

A bookmaker's 1X2 and over/under 2.5 prices pin down (to a good approximation)
the expected goals of each side. Solving for the Dixon-Coles rates (lam, nu)
that reproduce the de-margined prices gives a market-implied scoreline
distribution, from which every other market (other totals lines, both teams
to score, winning margins) can be priced consistently with the market.

Blending the model with the market at the level of rates,
    log lam_fair = alpha * log lam_model + (1 - alpha) * log lam_market,
yields one coherent "fair" scoreline distribution. alpha is fitted on earlier
seasons in the backtest.
"""

from __future__ import annotations

import numpy as np
from scipy import optimize

from . import dixon_coles as dc


def implied_rates(p1x2: np.ndarray, p_over25: np.ndarray | None, rho: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Rates (lam, nu) whose DC probabilities best match the market (least squares on probabilities)."""
    p1x2 = np.atleast_2d(np.asarray(p1x2, dtype=float))
    n = len(p1x2)
    po = np.full(n, np.nan) if p_over25 is None else np.atleast_1d(np.asarray(p_over25, dtype=float))
    lam = np.full(n, np.nan)
    nu = np.full(n, np.nan)
    for i in range(n):
        if not np.isfinite(p1x2[i]).all():
            continue
        target = p1x2[i]
        use_ou = np.isfinite(po[i])

        def resid(x):
            M = dc.score_matrix(np.exp(x[0]), np.exp(x[1]), rho)
            r = dc.outcome_probs(M) - target
            if use_ou:
                r = np.append(r, dc.prob_over(M, 2.5) - po[i])
            return r

        # Starting point from a rough 1X2 heuristic.
        x0 = np.log([1.4 + 1.2 * (target[0] - target[2]), 1.2 - 0.9 * (target[0] - target[2])])
        x0 = np.clip(x0, np.log(0.2), np.log(4.0))
        sol = optimize.least_squares(resid, x0, bounds=(np.log(0.05), np.log(8.0)), xtol=1e-10, ftol=1e-12)
        lam[i], nu[i] = np.exp(sol.x)
    return lam, nu


def blend_rates(lam_model, nu_model, lam_mkt, nu_mkt, alpha: float):
    lam_model, nu_model = np.asarray(lam_model, float), np.asarray(nu_model, float)
    lam_mkt, nu_mkt = np.asarray(lam_mkt, float), np.asarray(nu_mkt, float)
    have = np.isfinite(lam_mkt) & np.isfinite(nu_mkt)
    lam = np.where(have, np.exp(alpha * np.log(lam_model) + (1 - alpha) * np.log(np.where(have, lam_mkt, 1))), lam_model)
    nu = np.where(have, np.exp(alpha * np.log(nu_model) + (1 - alpha) * np.log(np.where(have, nu_mkt, 1))), nu_model)
    return lam, nu


def fit_alpha(lam_model, nu_model, lam_mkt, nu_mkt, rho, hg, ag) -> float:
    """alpha maximising the likelihood of the 1X2 result and the over/under 2.5 outcome."""
    hg, ag = np.asarray(hg), np.asarray(ag)
    res_idx = np.select([hg > ag, hg == ag, hg < ag], [0, 1, 2])
    over = (hg + ag) > 2.5
    ok = np.isfinite(lam_mkt) & np.isfinite(nu_mkt)

    def nll(a):
        lam, nu = blend_rates(lam_model[ok], nu_model[ok], lam_mkt[ok], nu_mkt[ok], a)
        M = dc.score_matrix(lam, nu, rho)
        p = dc.outcome_probs(M)[np.arange(ok.sum()), res_idx[ok]]
        po = dc.prob_over(M, 2.5)
        po = np.where(over[ok], po, 1 - po)
        return -np.sum(np.log(np.clip(p, 1e-12, None)) + np.log(np.clip(po, 1e-12, None)))

    return float(optimize.minimize_scalar(nll, bounds=(0.0, 1.0), method="bounded").x)
