import random

import numpy as np
import pandas as pd

from betting_analytics.live import afcon as live
from betting_analytics.models import international as intl


def test_head_to_head_beats_goal_difference():
    # A and B finish level on 8 points; B has the better overall goal difference
    # (+6 against +1), but A took 4 of 6 head-to-head points, so CAF rules put A above B.
    res = [("A", "B", 1, 0), ("B", "A", 0, 0), ("A", "C", 0, 1), ("A", "D", 2, 0),
           ("B", "C", 5, 0), ("B", "D", 5, 0), ("C", "D", 0, 0), ("D", "C", 0, 0),
           ("C", "A", 0, 0), ("D", "A", 2, 1), ("C", "B", 3, 0), ("D", "B", 0, 0)]
    order = live._rank_group(["A", "B", "C", "D"], res, random.Random(0))
    pts = {t: 0 for t in "ABCD"}
    for h, a, x, y in res:
        pts[h] += 3 if x > y else 1 if x == y else 0
        pts[a] += 3 if y > x else 1 if x == y else 0
    assert pts["A"] == pts["B"] == 8
    assert order == ["C", "A", "B", "D"]


def _model(teams):
    n = len(teams)
    theta = np.r_[np.log(1.2), 0.25, np.linspace(0.3, -0.3, n), np.zeros(n)]
    return intl.IntlModel(sorted(teams), theta, None, 0.0)


def test_group_simulation_qualifies_two_and_respects_host_rule():
    played = pd.DataFrame(columns=["home", "away", "hg", "ag"])
    normal = ["Algeria", "Burundi", "Togo", "Zambia"]
    hosted = ["Guinea", "Kenya", "Eritrea", "South Africa"]
    sim = live.simulate_groups(_model(normal + hosted), {"I": normal, "D": hosted}, played, set(), n_sims=400, seed=1)
    for g in ("I", "D"):
        rows = sim[g]
        assert abs(sum(r["p_qualify"] for r in rows) - 2) < 1e-9
        assert abs(sum(r["p_first"] for r in rows) - 1) < 1e-9
        for r in rows:
            assert abs(sum(r["positions"]) - 1) < 1e-9
    kenya = next(r for r in sim["D"] if r["team"] == "Kenya")
    assert kenya["host"] and kenya["p_qualify"] == 1.0


def test_sparse_fit_recovers_strength_order():
    rng = np.random.default_rng(0)
    teams = ["A", "B", "C", "D", "E"]
    true_att = dict(zip(teams, [0.5, 0.25, 0.0, -0.25, -0.5]))
    rows = []
    for _ in range(40):
        for h in teams:
            for a in teams:
                if h != a:
                    rows.append((h, a, rng.poisson(np.exp(0.2 + 0.2 + true_att[h] - true_att[a])),
                                 rng.poisson(np.exp(0.2 + true_att[a] - true_att[h]))))
    d = pd.DataFrame(rows, columns=["home", "away", "hg", "ag"])
    m = intl.fit(d.home, d.away, d.hg, d.ag, np.zeros(len(d), bool), np.ones(len(d)), prior_sd=1.0)
    n = len(m.teams)
    net = dict(zip(m.teams, m.theta[2:2 + n] - m.theta[2 + n:]))
    assert [t for t, _ in sorted(net.items(), key=lambda x: -x[1])] == teams
    assert abs(m.theta[1] - 0.2) < 0.08


def test_goal_clock_remaining_share():
    from betting_analytics.models import inplay
    share = np.full(90, 0.9 / 90)
    clock = inplay.GoalClock(share, 0.04, 0.06, 3.0, 6.0)
    assert abs(clock.remaining(0.0) - 1.0) < 1e-9
    assert abs(clock.remaining(45.0, halftime=True) - (0.45 + 0.06)) < 1e-9
    assert abs(clock.remaining(45.0, 1.5) - (0.02 + 0.45 + 0.06)) < 1e-9     # halfway through first-half stoppage
    assert abs(clock.remaining(90.0, 3.0) - 0.03) < 1e-9
    assert clock.remaining(90.0, 10.0) == 0.0
    xs = [clock.remaining(m) for m in np.arange(0, 45, 0.5)] + [clock.remaining(45, a) for a in (0.5, 1, 2)] + \
         [clock.remaining(45, halftime=True)] + [clock.remaining(m) for m in np.arange(45.5, 90, 0.5)] + [clock.remaining(90, a) for a in (0.5, 3, 5)]
    assert all(a >= b - 1e-12 for a, b in zip(xs, xs[1:]))


def test_inplay_probs():
    from betting_analytics.models import inplay
    assert np.allclose(inplay.probs(1.5, 1.0, 2, 1, 0.0), [1, 0, 0])
    p = inplay.probs(1.4, 1.1, 0, 0, 1.0)
    assert abs(p.sum() - 1) < 1e-12 and p[0] > p[2]
    # a one-goal lead late on is worth more than the same lead early
    assert inplay.probs(1.4, 1.1, 1, 0, 0.1)[0] > inplay.probs(1.4, 1.1, 1, 0, 0.8)[0]
