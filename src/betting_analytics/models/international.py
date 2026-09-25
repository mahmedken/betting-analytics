"""National-team models: a sparse Dixon-Coles fit and a World Football Elo baseline.

Differences from the club model:
  * about 230 teams, so the Newton step accumulates the gradient and Hessian
    from the four non-zero entries of each row instead of a dense design matrix;
  * home advantage applies only when the match is not at a neutral venue;
  * each match carries a weight: time decay times a competition weight
    (friendlies are less informative than competitive matches);
  * a Gaussian prior on attack/defence shrinks teams with few matches.

Tested and rejected (walk-forward on AFCON qualifiers):
  * a prior mean that depends on log(1 + matches played), since teams that
    rarely play tend to be weak: validation RPS worse at every prior sd
    (best 0.1724 against 0.1708);
  * a separate home advantage for African home teams: validation RPS improved
    (0.1692 against 0.1708, extra log-rate +0.05) but on the 2022-26 test set
    the extra term came out at -0.02 and RPS moved by 0.0001 (p = 0.44).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize
from scipy.special import expit

from . import dixon_coles as dc


@dataclass
class IntlModel:
    teams: list[str]
    theta: np.ndarray       # [mu, home, att(n), dfn(n)]
    cov: np.ndarray | None
    rho: float

    @property
    def index(self):
        return {t: i for i, t in enumerate(self.teams)}

    def rates(self, home, away, neutral, theta=None):
        th = self.theta if theta is None else theta
        ix = self.index
        n = len(self.teams)
        h = np.array([ix[t] for t in home])
        a = np.array([ix[t] for t in away])
        hf = 1.0 - np.asarray(neutral, dtype=float)
        mu, adv = th[..., 0:1], th[..., 1:2]
        att, dfn = th[..., 2:2 + n], th[..., 2 + n:]
        lam = np.exp(mu + adv * hf + att[..., h] + dfn[..., a])
        nu = np.exp(mu + att[..., a] + dfn[..., h])
        return lam, nu

    def score_matrix(self, home, away, neutral, theta=None):
        lam, nu = self.rates(home, away, neutral, theta)
        return dc.score_matrix(lam, nu, self.rho)

    def sample_theta(self, n, rng):
        return rng.multivariate_normal(self.theta, self.cov, size=n, method="cholesky")


def fit(home, away, hg, ag, neutral, weights, prior_sd: float = 1.0, teams=None,
        with_cov: bool = False, max_iter: int = 40) -> IntlModel:
    home, away = np.asarray(home), np.asarray(away)
    teams = sorted(set(home) | set(away) | set(teams or []))
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    p = 2 + 2 * n
    hi = np.array([ix[t] for t in home])
    ai = np.array([ix[t] for t in away])
    hf = 1.0 - np.asarray(neutral, dtype=float)
    w = np.asarray(weights, dtype=float)
    hg, ag = np.asarray(hg, dtype=float), np.asarray(ag, dtype=float)
    # Rows: home-goal rows then away-goal rows. Column indices of the non-zeros.
    cols_h = np.stack([np.zeros_like(hi), np.ones_like(hi), 2 + hi, 2 + n + ai], axis=1)
    vals_h = np.stack([np.ones_like(hf), hf, np.ones_like(hf), np.ones_like(hf)], axis=1)
    cols_a = np.stack([np.zeros_like(ai), np.ones_like(ai), 2 + ai, 2 + n + hi], axis=1)
    vals_a = np.stack([np.ones_like(hf), np.zeros_like(hf), np.ones_like(hf), np.ones_like(hf)], axis=1)
    cols = np.concatenate([cols_h, cols_a])
    vals = np.concatenate([vals_h, vals_a])
    y = np.concatenate([hg, ag])
    ww = np.concatenate([w, w])
    prec = np.full(p, 1.0 / prior_sd ** 2)
    prec[:2] = 1e-4

    def eta_of(th):
        return (th[cols] * vals).sum(axis=1)

    def logpost(th):
        e = eta_of(th)
        return float(np.sum(ww * (y * e - np.exp(e))) - 0.5 * np.sum(prec * th ** 2))

    def grad_hess(th):
        e = eta_of(th)
        lam = np.exp(e)
        r = ww * (y - lam)
        g = np.bincount(cols.ravel(), weights=(vals * r[:, None]).ravel(), minlength=p) - prec * th
        wl = ww * lam
        H = np.zeros((p, p))
        for i in range(4):
            for j in range(4):
                np.add.at(H, (cols[:, i], cols[:, j]), vals[:, i] * vals[:, j] * wl)
        H[np.diag_indices(p)] += prec
        return g, H

    th = np.zeros(p)
    th[0] = np.log(max(np.average(y, weights=ww), 0.1))
    th[1] = 0.25
    lp = logpost(th)
    for _ in range(max_iter):
        g, H = grad_hess(th)
        step = np.linalg.solve(H, g)
        t = 1.0
        while True:
            cand = th + t * step
            lp_new = logpost(cand)
            if lp_new >= lp - 1e-10 or t < 1e-4:
                break
            t *= 0.5
        th, done = cand, abs(lp_new - lp) < 1e-7
        lp = lp_new
        if done:
            break
    cov = None
    if with_cov:
        _, H = grad_hess(th)
        e = eta_of(th)
        lam_all = np.exp(e)
        n_eff = ww.sum() ** 2 / np.sum(ww ** 2)
        phi = float(np.sum(ww * (y - lam_all) ** 2 / lam_all) / ww.sum() * n_eff / max(n_eff - p, 1.0))
        cov = max(phi, 1.0) * np.linalg.inv(H)
        cov = 0.5 * (cov + cov.T)
    # rho on actual scores, rates fixed
    lam_h = np.exp(eta_of(th)[: len(hg)])
    nu_a = np.exp(eta_of(th)[len(hg):])

    def nll(r):
        return -np.sum(w * dc._log_tau(hg, ag, lam_h, nu_a, r))

    hi_b = min(0.3, float(np.min(1 / (lam_h * nu_a))) - 1e-6)
    lo_b = max(-0.3, float(np.max(-1 / np.maximum(lam_h, nu_a))) + 1e-6)
    rho = optimize.minimize_scalar(nll, bounds=(lo_b, hi_b), method="bounded").x
    return IntlModel(teams, th, cov, float(rho))


# ---------------------------------------------------------------------------
# match weights
# ---------------------------------------------------------------------------

def competition_weight(tournament: pd.Series, friendly_weight: float) -> np.ndarray:
    return np.where(tournament.str.contains("Friendly", case=False, na=False), friendly_weight, 1.0)


def fit_as_of(hist: pd.DataFrame, cutoff: pd.Timestamp, xi: float, friendly_weight: float,
              prior_sd: float, window_days: int = 8 * 365, with_cov: bool = False, teams=None) -> IntlModel:
    h = hist[(hist["kickoff_utc"] < cutoff) & (hist["kickoff_utc"] >= cutoff - pd.Timedelta(days=window_days))]
    age = (cutoff - h["kickoff_utc"]).dt.total_seconds().values / 86400
    w = np.exp(-xi * age) * competition_weight(h["tournament"], friendly_weight)
    return fit(h["home"].values, h["away"].values, h["hg"].values, h["ag"].values, h["neutral"].values, w,
               prior_sd=prior_sd, with_cov=with_cov, teams=teams)


# ---------------------------------------------------------------------------
# World Football Elo (eloratings.net rules) as a baseline
# ---------------------------------------------------------------------------

def _k(tournament: str) -> float:
    t = tournament.lower()
    if t == "fifa world cup":
        return 60
    if "qualification" in t:
        return 40
    if t in ("african cup of nations", "uefa euro", "copa américa", "afc asian cup", "gold cup", "confederations cup"):
        return 50
    if "friendly" in t:
        return 20
    return 30


def elo_run(hist: pd.DataFrame, home_adv: float = 100.0) -> pd.DataFrame:
    """Pre-match Elo difference for every match (home + advantage - away)."""
    r: dict[str, float] = {}
    diff = np.empty(len(hist))
    for i, m in enumerate(hist.itertuples(index=False)):
        rh, ra = r.get(m.home, 1500.0), r.get(m.away, 1500.0)
        d = rh + (0 if m.neutral else home_adv) - ra
        diff[i] = d
        exp_h = 1 / (1 + 10 ** (-d / 400))
        s = 1.0 if m.hg > m.ag else 0.5 if m.hg == m.ag else 0.0
        gd = abs(m.hg - m.ag)
        mult = 1 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        delta = _k(m.tournament) * mult * (s - exp_h)
        r[m.home] = rh + delta
        r[m.away] = ra - delta
    out = hist[["kickoff_utc", "home", "away"]].copy()
    out["elo_diff"] = diff / 400
    out.attrs["ratings"] = r
    return out


def ordered_logit_fit(x, result):
    x = np.asarray(x, float)
    col = np.select([np.asarray(result) == "H", np.asarray(result) == "D", np.asarray(result) == "A"], [0, 1, 2])

    def probs(c0, c1, b, x):
        pa = expit(c0 - b * x)
        pad = expit(c1 - b * x)
        return np.stack([1 - pad, pad - pa, pa], axis=-1)

    def nll(v):
        p = probs(v[0], v[0] + np.exp(v[1]), v[2], x)
        return -np.sum(np.log(np.clip(p[np.arange(len(x)), col], 1e-12, None)))

    v = optimize.minimize(nll, [-1.0, 0.0, 3.0], method="Nelder-Mead", options={"maxiter": 4000}).x
    return lambda xx: probs(v[0], v[0] + np.exp(v[1]), v[2], np.asarray(xx, float))
