"""Forecast scoring rules, calibration and significance tests.

Ranked probability score (RPS) is the primary metric for 1X2 because the
outcomes are ordered (home > draw > away) and RPS rewards putting probability
near the actual outcome (Epstein 1969; Constantinou & Fenton 2012). Lower is
better. Log loss and Brier score are reported alongside it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

OUTCOMES = ("H", "D", "A")


def onehot(result) -> np.ndarray:
    r = np.asarray(result)
    return np.stack([(r == o).astype(float) for o in OUTCOMES], axis=-1)


def rps(p: np.ndarray, result) -> np.ndarray:
    """Per-match ranked probability score for 3 ordered outcomes."""
    y = onehot(result)
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(y, axis=1)[:, :-1]
    return ((cp - cy) ** 2).sum(axis=1) / (p.shape[1] - 1)


def log_loss(p: np.ndarray, result) -> np.ndarray:
    y = onehot(result)
    return -np.log(np.clip((p * y).sum(axis=1), 1e-15, None))


def brier(p: np.ndarray, result) -> np.ndarray:
    y = onehot(result)
    return ((p - y) ** 2).sum(axis=1)


def binary_log_loss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-15, 1 - 1e-15)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def binary_brier(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (p - y) ** 2


def summary_1x2(p: np.ndarray, result) -> dict:
    return {
        "n": int(len(result)),
        "rps": float(rps(p, result).mean()),
        "log_loss": float(log_loss(p, result).mean()),
        "brier": float(brier(p, result).mean()),
        "accuracy": float((np.argmax(p, axis=1) == np.argmax(onehot(result), axis=1)).mean()),
    }


def calibration_table(p: np.ndarray, y: np.ndarray, bins=None) -> pd.DataFrame:
    """Reliability table: mean forecast vs observed frequency per probability bin.

    The interval is a Wilson 90% interval for the observed frequency.
    """
    bins = np.linspace(0, 1, 11) if bins is None else np.asarray(bins)
    p = np.asarray(p, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    idx = np.clip(np.digitize(p, bins) - 1, 0, len(bins) - 2)
    rows = []
    z = stats.norm.ppf(0.95)
    for b in range(len(bins) - 1):
        m = idx == b
        n = int(m.sum())
        if n == 0:
            continue
        obs = float(y[m].mean())
        denom = 1 + z ** 2 / n
        centre = (obs + z ** 2 / (2 * n)) / denom
        half = z * np.sqrt(obs * (1 - obs) / n + z ** 2 / (4 * n ** 2)) / denom
        rows.append({"bin_lo": float(bins[b]), "bin_hi": float(bins[b + 1]), "n": n,
                     "mean_forecast": float(p[m].mean()), "observed": obs,
                     "obs_lo": float(centre - half), "obs_hi": float(centre + half)})
    return pd.DataFrame(rows)


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """Expected calibration error: sample-weighted mean |forecast - observed|."""
    t = calibration_table(p, y, np.linspace(0, 1, n_bins + 1))
    return float((t["n"] * (t["mean_forecast"] - t["observed"]).abs()).sum() / t["n"].sum())


def diebold_mariano(loss_a: np.ndarray, loss_b: np.ndarray, clusters=None) -> dict:
    """Test of equal expected loss (Diebold & Mariano 1995).

    d = loss_a - loss_b; negative mean means A is better. Standard errors are
    clustered by `clusters` (e.g. match date) because matches forecast from the
    same fitted parameters are not independent.
    """
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    n = len(d)
    mean = d.mean()
    if clusters is None:
        se = d.std(ddof=1) / np.sqrt(n)
    else:
        g = pd.Series(d - mean).groupby(np.asarray(clusters)).sum().values
        k = len(g)
        se = np.sqrt((g ** 2).sum() * k / (k - 1)) / n
    t = mean / se
    return {"mean_diff": float(mean), "se": float(se), "t": float(t),
            "p_value": float(2 * stats.norm.sf(abs(t))), "n": int(n)}


def cluster_bootstrap_mean(values: np.ndarray, clusters, n_boot: int = 2000,
                           seed: int = 0, ci: float = 0.9) -> tuple[float, float]:
    """Percentile interval for a mean, resampling whole clusters."""
    rng = np.random.default_rng(seed)
    s = pd.DataFrame({"v": values, "c": clusters}).groupby("c")["v"].agg(["sum", "count"])
    sums, counts = s["sum"].values, s["count"].values
    k = len(sums)
    idx = rng.integers(0, k, size=(n_boot, k))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.quantile(means, [(1 - ci) / 2, 1 - (1 - ci) / 2])
    return float(lo), float(hi)
