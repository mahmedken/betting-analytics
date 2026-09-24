"""Price any supported market selection from a scoreline distribution, and
compute the value of a quote against that price.

Selections (market, selection, line):
    1x2     H | D | A
    total   over | under              line e.g. 2.5
    btts    yes | no
    margin  home | away | not_home | not_away   line e.g. 1.5  ("wins by more than line")
"""

from __future__ import annotations

import numpy as np

from ..data import kalshi
from ..models import dixon_coles as dc


def selection_prob(M: np.ndarray, market: str, selection: str, line: float | None) -> np.ndarray:
    if market == "1x2":
        return dc.outcome_probs(M)[..., "HDA".index(selection)]
    if market == "total":
        p = dc.prob_over(M, line)
        return p if selection == "over" else 1 - p
    if market == "btts":
        p = dc.prob_btts(M)
        return p if selection == "yes" else 1 - p
    if market == "margin":
        side = selection.replace("not_", "")
        p = dc.prob_margin(M, side, line)
        return 1 - p if selection.startswith("not_") else p
    raise ValueError(f"unknown market {market}")


def cost_per_contract(ask: float, venue: str, fee_rate: float | None = None) -> float:
    """All-in cost of one $1-payout contract bought at `ask` (taker)."""
    if venue == "kalshi":
        return ask + kalshi.taker_fee(ask)
    if venue == "polymarket":
        rate = 0.05 if fee_rate is None or not np.isfinite(fee_rate) else fee_rate
        return ask + rate * ask * (1 - ask)
    return ask


def edge(p: float | np.ndarray, cost: float) -> float | np.ndarray:
    """Expected profit per $1 staked: p / cost - 1."""
    return np.asarray(p) / cost - 1


def kelly_fraction(p: float, cost: float) -> float:
    """Full-Kelly fraction of bankroll for a contract costing `cost` paying 1."""
    b = (1 - cost) / cost          # net odds
    f = (p * (b + 1) - 1) / b
    return float(max(f, 0.0))
