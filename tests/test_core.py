import numpy as np
import pandas as pd
import pytest

from betting_analytics.data.teams import canonical
from betting_analytics.evaluation import metrics
from betting_analytics.live import ledger, pricing
from betting_analytics.models import devig, implied
from betting_analytics.models import dixon_coles as dc
from betting_analytics.models.fitting import DCConfig, History, fit_as_of
from betting_analytics.models.pool import LogPool


def test_devig_methods_sum_to_one_and_remove_margin():
    odds = np.array([[1.90, 3.60, 4.20], [1.25, 6.50, 11.0]])
    assert (devig.overround(odds) > 0).all()
    for method in devig.METHODS:
        p = devig.devig(odds, method)
        np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-9)
        assert (p > 0).all()
    # Shin and power move margin onto the long shot: its probability falls below the proportional one.
    mult = devig.multiplicative(odds)
    assert devig.shin(odds)[1, 2] < mult[1, 2]
    assert devig.power(odds)[1, 2] < mult[1, 2]


def test_devig_fair_odds_unchanged():
    p = np.array([[0.5, 0.3, 0.2]])
    for method in devig.METHODS:
        np.testing.assert_allclose(devig.devig(1 / p, method), p, atol=1e-6)


def test_rps_known_values():
    assert metrics.rps(np.array([[1.0, 0.0, 0.0]]), ["H"])[0] == 0
    np.testing.assert_allclose(metrics.rps(np.array([[1 / 3, 1 / 3, 1 / 3]]), ["H"])[0], 5 / 18)
    # A draw forecast is closer to a home win than an away forecast is.
    assert metrics.rps(np.array([[0, 1.0, 0]]), ["H"])[0] < metrics.rps(np.array([[0, 0, 1.0]]), ["H"])[0]


def test_score_matrix_is_a_distribution():
    M = dc.score_matrix(np.array([1.6, 0.7]), np.array([1.1, 2.3]), -0.08)
    np.testing.assert_allclose(M.sum(axis=(1, 2)), 1.0)
    p = dc.outcome_probs(M)
    np.testing.assert_allclose(p.sum(axis=1), 1.0)
    assert p[0, 0] > p[0, 2] and p[1, 2] > p[1, 0]
    over = dc.prob_over(M, 2.5)
    under = pricing.selection_prob(M, "total", "under", 2.5)
    np.testing.assert_allclose(over + under, 1.0)


def test_dixon_coles_recovers_simulated_parameters():
    rng = np.random.default_rng(3)
    teams = [f"T{i}" for i in range(12)]
    att = rng.normal(0, 0.3, 12)
    att -= att.mean()
    dfn = rng.normal(0, 0.3, 12)
    dfn -= dfn.mean()
    mu, home = 0.15, 0.25
    rows = []
    for _ in range(12):   # 12 double round-robins
        for i in range(12):
            for j in range(12):
                if i == j:
                    continue
                lam = np.exp(mu + home + att[i] + dfn[j])
                nu = np.exp(mu + att[j] + dfn[i])
                rows.append((teams[i], teams[j], rng.poisson(lam), rng.poisson(nu)))
    h, a, x, y = map(np.array, zip(*rows))
    m = dc.fit(h, a, x, y, np.ones(len(h)), x, y, prior_sd=10.0)
    assert abs(m.theta[1] - home) < 0.05
    order = [m.index[t] for t in teams]          # model sorts teams by name
    fitted = (m.att() - m.att().mean())[order]
    assert np.corrcoef(fitted, att)[0, 1] > 0.95
    assert abs(m.rho) < 0.1
    # Laplace sd of the identified contrasts (att_i - mean att) should match the
    # empirical scale of estimation error. Raw att_i are not identified on their
    # own: adding a constant to every att and subtracting it from mu changes nothing.
    C = np.eye(12) - 1 / 12
    cov_centred = C @ m.cov[2:14, 2:14] @ C.T
    sd_att = np.sqrt(np.diag(cov_centred))[order]
    assert 0.5 < np.std(fitted - att) / sd_att.mean() < 2.0


def test_fit_as_of_uses_only_earlier_matches():
    k = pd.date_range("2024-08-01", periods=40, freq="D", tz="UTC")
    teams = ["A", "B", "C", "D"]
    df = pd.DataFrame({
        "kickoff_utc": k, "season": 2024,
        "home": [teams[i % 4] for i in range(40)], "away": [teams[(i + 1) % 4] for i in range(40)],
        "hg": np.arange(40) % 3, "ag": np.arange(40) % 2, "hxg": np.nan, "axg": np.nan,
    })
    hist = History.from_df(df)
    cutoff = pd.Timestamp("2024-08-21", tz="UTC")
    m = fit_as_of(hist, cutoff, DCConfig(), (0.0, 0.0), season=2024)
    assert m.n_matches == int((df["kickoff_utc"] < cutoff).sum()) == 20


def test_team_aliases():
    assert canonical("Manchester United FC") == "Man United"
    assert canonical("Man Utd") == "Man United"
    assert canonical("Brighton & Hove Albion FC") == "Brighton"
    assert canonical("AFC Bournemouth") == "Bournemouth"
    assert canonical("Nottingham Forest") == "Nott'm Forest"
    assert canonical("Spurs") == "Tottenham"
    with pytest.raises(KeyError):
        canonical("Real Madrid")


def test_implied_rates_reproduce_market():
    M = dc.score_matrix(1.7, 1.0, 0.0)
    p = dc.outcome_probs(M)
    po = dc.prob_over(M, 2.5)
    lam, nu = implied.implied_rates(p[None, :], np.array([po]))
    np.testing.assert_allclose([lam[0], nu[0]], [1.7, 1.0], atol=1e-3)


def test_pool_weights_detect_informative_forecast():
    rng = np.random.default_rng(0)
    n = 4000
    truth = rng.dirichlet([4, 3, 3], size=n)
    y = np.array([rng.choice(3, p=t) for t in truth])
    onehot = np.eye(3)[y]
    noisy = 0.5 * truth + 0.5 * rng.dirichlet([4, 3, 3], size=n)
    uninformative = np.tile([0.45, 0.27, 0.28], (n, 1))
    pool = LogPool.fit(noisy, uninformative, onehot)
    assert pool.a > 3 * pool.se_a


def test_ledger_rows_freeze_at_kickoff():
    now = pd.Timestamp("2026-10-01 12:00", tz="UTC")
    base = {c: 0.3 for c in ledger.FORECAST_COLS}
    base["book_source"] = ""
    f1 = pd.DataFrame([{**base, "match_id": "m1", "season": 2026, "gameweek": 7,
                        "kickoff_utc": pd.Timestamp("2026-10-01 14:00", tz="UTC"), "home": "Arsenal", "away": "Leeds"}])
    led = ledger.update(ledger.load().iloc[0:0], f1, pd.DataFrame(columns=["match_id", "hg", "ag"]), now, "v")
    assert led.loc[0, "model_h"] == 0.3
    f2 = f1.copy()
    f2["model_h"] = 0.9
    later = pd.Timestamp("2026-10-01 15:00", tz="UTC")   # after kick-off
    led2 = ledger.update(led, f2, pd.DataFrame({"match_id": ["m1"], "hg": [2], "ag": [1]}), later, "v")
    assert led2.loc[0, "model_h"] == 0.3
    assert led2.loc[0, "result"] == "H"


def test_prediction_market_costs_include_fees():
    assert pricing.cost_per_contract(0.5, "kalshi") == pytest.approx(0.5 + 0.07 * 0.25)
    assert pricing.cost_per_contract(0.5, "polymarket", 0.05) == pytest.approx(0.5 + 0.05 * 0.25)
    assert pricing.edge(0.6, 0.5) == pytest.approx(0.2)
