"""Logarithmic opinion pool of the model and the market.

    p_pool(k) ∝ p_model(k) ** a * p_market(k) ** b

The weights are fitted by maximum likelihood on earlier out-of-sample
forecasts. Beyond producing a combined forecast, the fit is an encompassing
test (in the spirit of Fair & Shiller 1990): if the market price already
contains everything the model knows, the best weight on the model is zero. A
model weight that is reliably above zero is evidence that the model carries
information the market has not priced.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import optimize


def _combine(log_pm: np.ndarray, log_pk: np.ndarray, a: float, b: float) -> np.ndarray:
    z = a * log_pm + b * log_pk
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _as_multiclass(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    return np.stack([p, 1 - p], axis=1) if p.ndim == 1 else p


@dataclass
class LogPool:
    a: float
    b: float
    se_a: float
    se_b: float
    n: int

    def predict(self, p_model: np.ndarray, p_market: np.ndarray) -> np.ndarray:
        binary = np.asarray(p_model).ndim == 1
        pm, pk = _as_multiclass(p_model), _as_multiclass(p_market)
        out = _combine(np.log(np.clip(pm, 1e-12, None)), np.log(np.clip(pk, 1e-12, None)), self.a, self.b)
        return out[:, 0] if binary else out

    @classmethod
    def fit(cls, p_model: np.ndarray, p_market: np.ndarray, y: np.ndarray) -> "LogPool":
        """y: (n, k) one-hot outcomes, or (n,) 0/1 for a binary market."""
        pm, pk = _as_multiclass(p_model), _as_multiclass(p_market)
        y = np.asarray(y, dtype=float)
        if y.ndim == 1:
            y = np.stack([y, 1 - y], axis=1)
        ok = np.isfinite(pm).all(axis=1) & np.isfinite(pk).all(axis=1)
        lpm = np.log(np.clip(pm[ok], 1e-12, None))
        lpk = np.log(np.clip(pk[ok], 1e-12, None))
        y = y[ok]

        def nll(ab):
            p = _combine(lpm, lpk, ab[0], ab[1])
            return -np.sum(y * np.log(np.clip(p, 1e-15, None)))

        res = optimize.minimize(nll, x0=[0.2, 0.8], method="BFGS")
        a, b = res.x
        # Standard errors from a finite-difference Hessian of the NLL.
        h = 1e-4
        H = np.zeros((2, 2))
        for i in range(2):
            for j in range(2):
                e_i, e_j = np.eye(2)[i] * h, np.eye(2)[j] * h
                H[i, j] = (nll(res.x + e_i + e_j) - nll(res.x + e_i - e_j)
                           - nll(res.x - e_i + e_j) + nll(res.x - e_i - e_j)) / (4 * h * h)
        try:
            cov = np.linalg.inv(H)
            se_a, se_b = np.sqrt(np.clip(np.diag(cov), 0, None))
        except np.linalg.LinAlgError:
            se_a = se_b = float("nan")
        return cls(float(a), float(b), float(se_a), float(se_b), int(ok.sum()))
