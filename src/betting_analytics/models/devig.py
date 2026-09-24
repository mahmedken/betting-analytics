"""Convert bookmaker odds into probabilities by removing the bookmaker margin.

Implied probabilities 1/odds sum to more than 1; the excess is the margin
(overround). Three standard ways to remove it:

multiplicative  p_i = pi_i / sum(pi). Assumes the margin is spread in
                proportion to the probability.
power           p_i = pi_i ** k with k chosen so the p_i sum to 1. Puts more of
                the margin on long shots (Clarke, Kovalchik & Ingram 2017).
shin            Shin (1993) model in which the bookmaker protects against a
                fraction z of insider money; also loads more margin on long
                shots. Recommended by Strumbelj (2014) for football 1X2.

All functions take an (n, k) array of decimal odds and return an (n, k) array
of probabilities; rows with any missing odds return NaN.
"""

from __future__ import annotations

import numpy as np


def _implied(odds: np.ndarray) -> np.ndarray:
    odds = np.asarray(odds, dtype=float)
    if odds.ndim == 1:
        odds = odds[None, :]
    return 1.0 / odds


def overround(odds: np.ndarray) -> np.ndarray:
    return _implied(odds).sum(axis=1) - 1.0


def multiplicative(odds: np.ndarray) -> np.ndarray:
    pi = _implied(odds)
    return pi / pi.sum(axis=1, keepdims=True)


def _bisect(f, lo: np.ndarray, hi: np.ndarray, iters: int = 80) -> np.ndarray:
    """Vectorised bisection for f(x) = 0 with f(lo) and f(hi) of opposite sign."""
    f_lo = f(lo)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        same = np.sign(f_mid) == np.sign(f_lo)
        lo = np.where(same, mid, lo)
        f_lo = np.where(same, f_mid, f_lo)
        hi = np.where(same, hi, mid)
    return 0.5 * (lo + hi)


def power(odds: np.ndarray) -> np.ndarray:
    pi = _implied(odds)
    ok = np.isfinite(pi).all(axis=1)
    out = np.full_like(pi, np.nan)
    if not ok.any():
        return out
    p = pi[ok]
    # sum(p**k) is decreasing in k; k=1 gives the booksum (>1 with a margin).
    k = _bisect(lambda k: (p ** k[:, None]).sum(axis=1) - 1.0,
                np.full(len(p), 0.5), np.full(len(p), 3.0))
    out[ok] = p ** k[:, None]
    return out / out.sum(axis=1, keepdims=True)


def _shin_probs(pi: np.ndarray, z: np.ndarray) -> np.ndarray:
    booksum = pi.sum(axis=1, keepdims=True)
    z = z[:, None]
    return (np.sqrt(z ** 2 + 4 * (1 - z) * pi ** 2 / booksum) - z) / (2 * (1 - z))


def shin(odds: np.ndarray, return_z: bool = False):
    pi = _implied(odds)
    ok = np.isfinite(pi).all(axis=1)
    out = np.full_like(pi, np.nan)
    zs = np.full(len(pi), np.nan)
    if ok.any():
        p = pi[ok]
        # sum of Shin probabilities is decreasing in z; z=0 gives the booksum.
        z = _bisect(lambda z: _shin_probs(p, z).sum(axis=1) - 1.0,
                    np.zeros(len(p)), np.full(len(p), 0.4))
        probs = _shin_probs(p, z)
        out[ok] = probs / probs.sum(axis=1, keepdims=True)
        zs[ok] = z
    return (out, zs) if return_z else out


METHODS = {"multiplicative": multiplicative, "power": power, "shin": shin}


def devig(odds: np.ndarray, method: str = "shin") -> np.ndarray:
    return METHODS[method](odds)
