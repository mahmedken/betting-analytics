"""Dixon-Coles scoreline model with time decay, priors and parameter uncertainty.

Model (Maher 1982; Dixon & Coles 1997):

    home goals X ~ Poisson(lam),  log lam = mu + home + att[h] + dfn[a]
    away goals Y ~ Poisson(nu),   log nu  = mu + att[a] + dfn[h]
    P(X=x, Y=y) = tau(x, y) * Pois(x; lam) * Pois(y; nu)

`att` is attacking strength (higher scores more), `dfn` is defensive weakness
(higher concedes more). tau is the Dixon-Coles correction that moves
probability between the 0-0, 1-0, 0-1 and 1-1 scores by a single parameter rho.

Estimation, in two stages:

1. Rates. Maximise a time-weighted Poisson log-likelihood plus a Gaussian prior
   on each team's att/dfn (weight exp(-xi * age_in_days), Dixon & Coles 1997).
   The target can be goals, expected goals (xG), or a blend
   y = w * goals + (1 - w) * xG; for non-integer targets this is the Poisson
   quasi-likelihood, which has the same score equations. The maximum is found by
   Newton's method. The prior keeps promoted teams and early-season estimates
   stable and makes the parameters identifiable without a hard constraint.
2. rho. Maximise the weighted Dixon-Coles likelihood of the actual scores over
   rho with the rates held fixed.

Parameter uncertainty is the Laplace approximation: a Gaussian centred at the
maximum with covariance equal to the inverse of the negative Hessian of the
log-posterior, scaled by the quasi-Poisson dispersion phi (McCullagh & Nelder
1989). phi is the weighted Pearson statistic divided by its degrees of
freedom; it is below 1 when the target is mostly xG, which varies less from
match to match than goals do, and about 1 for goals. Drawing parameters from it gives a distribution for every
derived probability, which is what the dashboard's intervals show. The
intervals describe uncertainty about the model's parameters only, not the
randomness of the match itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import optimize
from scipy.special import gammaln

MAX_GOALS = 10


@dataclass
class DixonColes:
    teams: list[str]
    theta: np.ndarray          # [mu, home, att(n), dfn(n)]
    cov: np.ndarray            # Laplace covariance of theta
    rho: float
    n_matches: int
    info: dict = field(default_factory=dict)

    @property
    def index(self) -> dict[str, int]:
        return {t: i for i, t in enumerate(self.teams)}

    @property
    def n(self) -> int:
        return len(self.teams)

    def att(self, theta: np.ndarray | None = None) -> np.ndarray:
        th = self.theta if theta is None else theta
        return th[..., 2:2 + self.n]

    def dfn(self, theta: np.ndarray | None = None) -> np.ndarray:
        th = self.theta if theta is None else theta
        return th[..., 2 + self.n:]

    def _idx(self, teams) -> np.ndarray:
        ix = self.index
        return np.array([ix[t] for t in teams], dtype=int)

    def rates(self, home, away, theta: np.ndarray | None = None):
        """Expected goals (lam, nu). theta may be (p,) or (S, p) for draws."""
        th = self.theta if theta is None else theta
        h, a = self._idx(home), self._idx(away)
        n = self.n
        mu, adv = th[..., 0:1], th[..., 1:2]
        att, dfn = th[..., 2:2 + n], th[..., 2 + n:]
        lam = np.exp(mu + adv + att[..., h] + dfn[..., a])
        nu = np.exp(mu + att[..., a] + dfn[..., h])
        return lam, nu

    def score_matrix(self, home, away, theta: np.ndarray | None = None) -> np.ndarray:
        lam, nu = self.rates(home, away, theta)
        return score_matrix(lam, nu, self.rho)

    def sample_theta(self, n_draws: int, rng: np.random.Generator) -> np.ndarray:
        return rng.multivariate_normal(self.theta, self.cov, size=n_draws, method="cholesky")


def score_matrix(lam, nu, rho: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """P(home=x, away=y) for x, y in 0..max_goals; shape (..., G, G).

    Rates are clipped to [0.02, 12] so that extreme parameter draws cannot
    overflow; no real match has expected goals outside that range.
    """
    lam = np.clip(np.asarray(lam, dtype=float), 0.02, 12.0)[..., None]
    nu = np.clip(np.asarray(nu, dtype=float), 0.02, 12.0)[..., None]
    g = np.arange(max_goals + 1)
    log_px = g * np.log(lam) - lam - gammaln(g + 1)
    log_py = g * np.log(nu) - nu - gammaln(g + 1)
    m = np.exp(log_px[..., :, None] + log_py[..., None, :])
    lam0, nu0 = lam[..., 0], nu[..., 0]
    m[..., 0, 0] *= 1 - lam0 * nu0 * rho
    m[..., 0, 1] *= 1 + lam0 * rho
    m[..., 1, 0] *= 1 + nu0 * rho
    m[..., 1, 1] *= 1 - rho
    m = np.clip(m, 0, None)
    return m / m.sum(axis=(-2, -1), keepdims=True)


def _design(h_idx, a_idx, n_teams):
    """Rows: 2 per match (home goals, then away goals)."""
    n_m = len(h_idx)
    p = 2 + 2 * n_teams
    X = np.zeros((2 * n_m, p))
    r = np.arange(n_m)
    X[2 * r, 0] = 1
    X[2 * r, 1] = 1
    X[2 * r, 2 + h_idx] = 1
    X[2 * r, 2 + n_teams + a_idx] = 1
    X[2 * r + 1, 0] = 1
    X[2 * r + 1, 2 + a_idx] = 1
    X[2 * r + 1, 2 + n_teams + h_idx] = 1
    return X


def _log_tau(x, y, lam, nu, rho):
    t = np.ones_like(lam)
    t = np.where((x == 0) & (y == 0), 1 - lam * nu * rho, t)
    t = np.where((x == 0) & (y == 1), 1 + lam * rho, t)
    t = np.where((x == 1) & (y == 0), 1 + nu * rho, t)
    t = np.where((x == 1) & (y == 1), 1 - rho, t)
    return np.log(np.clip(t, 1e-12, None))


def fit(home, away, y_home, y_away, weights, goals_home, goals_away, *,
        prior_mean: dict[str, tuple[float, float]] | None = None,
        prior_sd: float = 0.3, prior_sd_team: dict[str, float] | None = None,
        teams: list[str] | None = None,
        max_iter: int = 50, tol: float = 1e-8) -> DixonColes:
    home = np.asarray(home)
    away = np.asarray(away)
    teams = sorted(set(home) | set(away) | set(teams or []))
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    h_idx = np.array([ix[t] for t in home], dtype=int)
    a_idx = np.array([ix[t] for t in away], dtype=int)
    w = np.repeat(np.asarray(weights, dtype=float), 2)
    y = np.empty(2 * len(home))
    y[0::2] = y_home
    y[1::2] = y_away
    X = _design(h_idx, a_idx, n)
    p = X.shape[1]

    m0 = np.zeros(p)
    prec = np.zeros(p)
    prec[:2] = 1e-4                              # effectively flat on mu, home
    prec[2:] = 1.0 / prior_sd ** 2
    if prior_mean:
        for t, (ma, md) in prior_mean.items():
            if t in ix:
                m0[2 + ix[t]] = ma
                m0[2 + n + ix[t]] = md
    if prior_sd_team:
        for t, sd in prior_sd_team.items():
            if t in ix:
                prec[2 + ix[t]] = prec[2 + n + ix[t]] = 1.0 / sd ** 2

    def log_post(th):
        eta = X @ th
        return float(np.sum(w * (y * eta - np.exp(eta))) - 0.5 * np.sum(prec * (th - m0) ** 2))

    theta = m0.copy()
    mean_goals = np.average(y, weights=w)
    theta[0] = np.log(max(mean_goals, 0.1))
    theta[1] = 0.2
    lp = log_post(theta)
    for _ in range(max_iter):
        eta = X @ theta
        lam = np.exp(eta)
        grad = X.T @ (w * (y - lam)) - prec * (theta - m0)
        neg_hess = (X * (w * lam)[:, None]).T @ X + np.diag(prec)
        step = np.linalg.solve(neg_hess, grad)
        t = 1.0
        while True:
            cand = theta + t * step
            lp_new = log_post(cand)
            if lp_new >= lp - 1e-10 or t < 1e-4:
                break
            t *= 0.5
        theta, improvement, lp = cand, lp_new - lp, lp_new
        if abs(improvement) < tol:
            break

    eta = X @ theta
    lam = np.exp(eta)
    # Quasi-Poisson dispersion from weighted Pearson residuals.
    n_eff = w.sum() ** 2 / np.sum(w ** 2)
    phi = float(np.sum(w * (y - lam) ** 2 / lam) / w.sum() * n_eff / max(n_eff - p, 1.0))
    neg_hess = (X * (w * lam)[:, None]).T @ X + np.diag(prec)
    cov = phi * np.linalg.inv(neg_hess)
    cov = 0.5 * (cov + cov.T)

    # Stage 2: rho on actual scores.
    gh = np.asarray(goals_home, dtype=float)
    ga = np.asarray(goals_away, dtype=float)
    lam_h, nu_a = lam[0::2], lam[1::2]
    wm = np.asarray(weights, dtype=float)

    def neg_ll_rho(rho):
        return -np.sum(wm * _log_tau(gh, ga, lam_h, nu_a, rho))

    # tau must stay positive for every match: 1 - lam*nu*rho > 0 and 1 + lam*rho > 0 ...
    hi = min(0.99, float(np.min(1.0 / (lam_h * nu_a))) - 1e-6)
    lo = max(-0.99, float(np.max(-1.0 / np.maximum(lam_h, nu_a))) + 1e-6)
    rho = optimize.minimize_scalar(neg_ll_rho, bounds=(lo, hi), method="bounded").x

    return DixonColes(teams=teams, theta=theta, cov=cov, rho=float(rho), n_matches=len(home),
                      info={"log_posterior": lp, "dispersion": phi})


# ---------------------------------------------------------------------------
# Market probabilities derived from a score matrix
# ---------------------------------------------------------------------------

def outcome_probs(m: np.ndarray) -> np.ndarray:
    """(..., G, G) -> (..., 3) probabilities of home win, draw, away win."""
    home = np.tril(m, -1).sum(axis=(-2, -1))
    draw = np.trace(m, axis1=-2, axis2=-1)
    away = np.triu(m, 1).sum(axis=(-2, -1))
    return np.stack([home, draw, away], axis=-1)


def total_goals_dist(m: np.ndarray) -> np.ndarray:
    G = m.shape[-1]
    tot = np.add.outer(np.arange(G), np.arange(G))
    return np.stack([(m * (tot == k)).sum(axis=(-2, -1)) for k in range(2 * G - 1)], axis=-1)


def prob_over(m: np.ndarray, line: float) -> np.ndarray:
    G = m.shape[-1]
    tot = np.add.outer(np.arange(G), np.arange(G))
    return (m * (tot > line)).sum(axis=(-2, -1))


def prob_btts(m: np.ndarray) -> np.ndarray:
    return m[..., 1:, 1:].sum(axis=(-2, -1))


def prob_margin(m: np.ndarray, side: str, more_than: float) -> np.ndarray:
    """P(side wins by more than `more_than` goals); side in {'home', 'away'}."""
    G = m.shape[-1]
    diff = np.subtract.outer(np.arange(G), np.arange(G))   # home - away
    if side == "away":
        diff = -diff
    return (m * (diff > more_than)).sum(axis=(-2, -1))


def prob_team_over(m: np.ndarray, side: str, line: float) -> np.ndarray:
    g = np.arange(m.shape[-1])
    if side == "home":
        return m.sum(axis=-1)[..., g > line].sum(axis=-1)
    return m.sum(axis=-2)[..., g > line].sum(axis=-1)
