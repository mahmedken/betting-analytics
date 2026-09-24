"""Monte Carlo simulation of the rest of the season.

Each simulation draws one parameter vector from the model's Laplace posterior
and then samples every remaining match from the Dixon-Coles scoreline
distribution at those parameters. Drawing parameters per simulation (rather
than using the point estimate) carries the model's uncertainty about team
strength into the season outcome probabilities, which otherwise come out
overconfident.

Tie-breaks follow Premier League rules as far as goals allow: points, then goal
difference, then goals scored; remaining ties are broken at random.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..models import dixon_coles as dc
from ..models.dixon_coles import DixonColes


def simulate(model: DixonColes, played: pd.DataFrame, remaining: pd.DataFrame, teams: list[str],
             n_sims: int = 10000, seed: int = 0, batch: int = 500) -> dict:
    rng = np.random.default_rng(seed)
    t_ix = {t: i for i, t in enumerate(teams)}
    n_t = len(teams)

    pts0 = np.zeros(n_t)
    gf0 = np.zeros(n_t)
    ga0 = np.zeros(n_t)
    for r in played.itertuples(index=False):
        h, a = t_ix[r.home], t_ix[r.away]
        gf0[h] += r.hg
        ga0[h] += r.ag
        gf0[a] += r.ag
        ga0[a] += r.hg
        if r.hg > r.ag:
            pts0[h] += 3
        elif r.hg < r.ag:
            pts0[a] += 3
        else:
            pts0[h] += 1
            pts0[a] += 1

    home = remaining["home"].values
    away = remaining["away"].values
    hi = np.array([t_ix[t] for t in home], dtype=int)
    ai = np.array([t_ix[t] for t in away], dtype=int)
    n_m = len(remaining)
    G = dc.MAX_GOALS + 1

    positions = np.zeros((n_t, n_t))      # team x finishing position counts
    points_sum = np.zeros(n_t)
    points_sq = np.zeros(n_t)
    done = 0
    while done < n_sims:
        b = min(batch, n_sims - done)
        pts = np.tile(pts0, (b, 1))
        gf = np.tile(gf0, (b, 1))
        ga = np.tile(ga0, (b, 1))
        if n_m:
            th = model.sample_theta(b, rng)
            M = model.score_matrix(home, away, th).reshape(b, n_m, G * G)   # (b, n_m, G*G)
            cdf = np.cumsum(M, axis=-1)
            u = rng.random((b, n_m, 1))
            k = np.minimum((u > cdf).sum(axis=-1), G * G - 1)
            x, y = k // G, k % G
            for j in range(n_m):
                h, a = hi[j], ai[j]
                gf[:, h] += x[:, j]
                ga[:, h] += y[:, j]
                gf[:, a] += y[:, j]
                ga[:, a] += x[:, j]
                pts[:, h] += np.where(x[:, j] > y[:, j], 3, np.where(x[:, j] == y[:, j], 1, 0))
                pts[:, a] += np.where(y[:, j] > x[:, j], 3, np.where(x[:, j] == y[:, j], 1, 0))
        key = pts * 1e6 + (gf - ga) * 1e3 + gf + rng.random((b, n_t))
        order = np.argsort(-key, axis=1)                  # order[s, pos] = team
        rank = np.empty_like(order)
        rank[np.arange(b)[:, None], order] = np.arange(n_t)[None, :]
        for pos in range(n_t):
            positions[:, pos] += (rank == pos).sum(axis=0)
        points_sum += pts.sum(axis=0)
        points_sq += (pts ** 2).sum(axis=0)
        done += b

    probs = positions / n_sims
    mean_pts = points_sum / n_sims
    sd_pts = np.sqrt(np.maximum(points_sq / n_sims - mean_pts ** 2, 0))
    out = []
    for t in teams:
        i = t_ix[t]
        out.append({
            "team": t,
            "points_now": float(pts0[i]),
            "played": int(((played["home"] == t) | (played["away"] == t)).sum()),
            "gd_now": float(gf0[i] - ga0[i]),
            "exp_points": float(mean_pts[i]),
            "sd_points": float(sd_pts[i]),
            "title": float(probs[i, 0]),
            "top4": float(probs[i, :4].sum()),
            "top6": float(probs[i, :6].sum()),
            "relegation": float(probs[i, -3:].sum()),
            "last": float(probs[i, -1]),
            "positions": [float(v) for v in probs[i]],
        })
    return {"n_sims": n_sims, "teams": sorted(out, key=lambda r: -r["exp_points"])}
