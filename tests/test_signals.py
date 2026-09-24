import numpy as np
import pandas as pd

from betting_analytics.evaluation import maker
from betting_analytics.live import season_sim, signals
from betting_analytics.models import dixon_coles as dc
from betting_analytics.models import implied


def _candles(lo_after, result="yes"):
    k = 1_000_000
    rows = [{"ticker": "T", "match_id": "m", "selection": "D", "kickoff_ts": k, "result": result, "t": k - 80 * 3600,
             "bid": 0.24, "ask": 0.27, "lo": 0.25, "hi": 0.26, "vol": 10, "q_close": 0.27, "date": "2026-01-01",
             "group": "draw", "home": "A", "away": "B"},
            {"ticker": "T", "match_id": "m", "selection": "D", "kickoff_ts": k, "result": result, "t": k - 10 * 3600,
             "bid": 0.23, "ask": 0.26, "lo": lo_after, "hi": 0.26, "vol": 5, "q_close": 0.27, "date": "2026-01-01",
             "group": "draw", "home": "A", "away": "B"}]
    return pd.DataFrame(rows)


def test_maker_fill_requires_trade_through():
    # bid posted at 0.25 (bid 0.24 + 1c); a later trade at exactly 0.25 does not fill it
    r = maker.simulate(_candles(0.25), 72, "bid", 0.01)
    assert not r["filled"].iloc[0]
    r = maker.simulate(_candles(0.24), 72, "bid", 0.01)
    assert r["filled"].iloc[0]
    row = r.iloc[0]
    assert abs(row["cost"] - (0.25 + 0.0175 * 0.25 * 0.75)) < 1e-9
    assert abs(row["clv"] - (0.27 / row["cost"] - 1)) < 1e-9


def test_implied_rates_rho_reproduce_market_exactly():
    p = np.array([[0.46, 0.27, 0.27]])
    lam, nu, rho = implied.implied_rates_rho(p, np.array([0.5]))
    M = dc.score_matrix(lam, nu, rho[0])
    np.testing.assert_allclose(dc.outcome_probs(M)[0], p[0], atol=1e-6)
    np.testing.assert_allclose(dc.prob_over(M, 2.5)[0], 0.5, atol=1e-6)


def test_season_sim_with_drift_is_a_distribution():
    teams = ["A", "B", "C", "D"]
    m = dc.DixonColes(teams=teams, theta=np.array([0.2, 0.2, 0.3, 0.1, -0.1, -0.3, -0.2, 0, 0.1, 0.1]),
                      cov=np.eye(10) * 1e-4, rho=-0.05, n_matches=100)
    now = pd.Timestamp("2026-01-01", tz="UTC")
    fx = [(h, a) for h in teams for a in teams if h != a]
    remaining = pd.DataFrame({"home": [f[0] for f in fx], "away": [f[1] for f in fx],
                              "kickoff_utc": [now + pd.Timedelta(days=7 * i) for i in range(len(fx))]})
    played = pd.DataFrame(columns=["home", "away", "hg", "ag"])
    out = season_sim.simulate(m, played, remaining, teams, n_sims=600, batch=200, draw_params=False,
                              drift_sd_per_week=0.05, now=now)
    assert abs(sum(t["title"] for t in out["teams"]) - 1) < 1e-9
    assert out["teams"][0]["team"] == "A"


def test_season_signals_keep_one_venue_per_contract():
    t = [{"team": "Chelsea", "code": "CHE", "title": 0.02, "top4": 0.3, "relegation": 0.01,
          "markets": {"top4": {"kalshi": {"bid": 0.48, "ask": 0.50, "fee_rate": None},
                               "polymarket": {"bid": 0.50, "ask": 0.52, "fee_rate": 0.03}}}}]
    s = signals.season_signals(t, {})
    assert len(s) == 1 and s[0]["side"] == "no" and s[0]["venue"] == "Polymarket"
