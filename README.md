# betting-analytics

A dashboard for Premier League and AFCON qualifying betting and prediction
markets, built on backtested scoreline models. It prices every match market (result, total
goals, both teams to score, winning margin, correct score) and season market
(title, top four, relegation). It compares those prices with Kalshi,
Polymarket and bookmaker quotes and publishes an honest record of how well the
model has done.

Everything is computed from public data by the code in this repository and
refreshed hourly by GitHub Actions. The site is static (`site/`) and served by
GitHub Pages.

## What the lab found

Every idea is tested against the sharp closing price (de-margined Pinnacle or
Betfair Exchange odds at kick-off), with all fees, a first-half / second-half
split, and intervals that resample match days. Full results are on the site's
**Lab** tab.

| finding | verdict | number |
|---|---|---|
| Kalshi limit orders against retail flow: bid the draw, offer against big-six wins, 2-3 days out | **edge** | +2.6% vs the close per filled order (90% CI +1.7 to +3.5), both halves positive; the same orders the other way lose 2-3% |
| Longest bookmaker price vs a sharp-anchored fair price | **edge, hard to use** | +3.6% vs the close (736 bets); needs many bookmaker accounts |
| Season markets vs a season simulated from match-market team strengths | suggestive | 6% lower Brier score than Kalshi's season prices last season, not yet significant |
| Big clubs overpriced / draws underpriced on Kalshi and Polymarket a day out | real, below taker fees | +1.1¢ on big-six wins, -0.7¢ on draws |
| 64 public-stats signals (xG luck, form, finishing, rest) vs the close | no edge | 0 significant, 0 replicated |
| Our xG model vs the closing price | no edge | RPS 0.2009 vs 0.1970 |
| Kalshi / Polymarket vs the close, 1 h before kick-off | no edge | equally accurate |
| Polymarket side markets vs its own match odds | unproven | benchmark shares model error; realised returns do not confirm |

The live site turns the rules that survived into signals (Today tab): Kalshi
limit orders to post 24-96 hours before kick-off, season-market gaps, best
bookmaker prices and cross-venue arbitrage. Every signal is logged to
`data/ledger/signals.csv` so the rules are tracked forward.

## AFCON 2027 qualifying

The AFCON tab forecasts every qualifier, simulates the 12 groups and compares
the result with DraftKings match odds and Polymarket group-winner prices.

- **Rule.** Top two in each group qualify. In the groups of the co-hosts
  (Kenya, Uganda, Tanzania), the host qualifies automatically and only the
  best other team goes through. Ties on points: head-to-head points, goal
  difference, goals and away goals, then overall goal difference and goals.
- **Model.** The same Dixon–Coles scoreline model, fitted to every men's
  international of the last eight years (martj42/international_results plus
  ESPN for results the file does not have yet). Sparse Newton fit over about
  280 teams, home advantage only at home venues, time decay, Gaussian prior.
- **Backtest.** Walk-forward on every AFCON qualifier since 2018: settings
  chosen on 2018–21, tested on 2022–26 (316 matches).

| test | model | benchmark | verdict |
|---|---|---|---|
| vs World Football Elo (ordered logit) | RPS 0.1773 | 0.1840 | better, p = 0.09 |
| vs ESPN BET closing odds, Oct–Nov 2024 (94 matches) | RPS 0.2015 | 0.1999 | level, p = 0.82 |
| betting 5%+ model value at ESPN BET (56 bets) | ROI +17% | 90% CI −19% to +46% | not proven |

Tested and rejected: a prior mean that depends on how often a team plays
(worse on validation), and a separate home advantage for African teams
(better on validation, no gain on the test set).

- **Groups.** 10,000 simulations of the remaining fixtures. Each draws the
  team ratings from their estimation uncertainty.
- **Markets.** Polymarket group-winner gaps are listed when the model exceeds
  the all-in cost by 4 points or more with a spread of 8¢ or less. The group
  simulation has not been validated against those prices, so the gaps are
  labelled unproven.
- **Ledger.** `data/ledger/afcon_predictions.csv`, frozen at kick-off.

## Method in brief

- **Data.** football-data.co.uk: results and odds since 2005-06, including
  Pinnacle 2012–2026 and Betfair Exchange from 2024. Understat xG since
  2014-15. The FPL API supplies fixtures. The Kalshi and Polymarket public
  APIs supply live prices and settled-contract price history.
- **Model.** Dixon–Coles bivariate Poisson with time decay, fitted to a blend
  of goals and xG. Gaussian priors on team strength. Newly promoted teams get a
  prior centred on the historical first-season strength of promoted teams.
  Parameter uncertainty comes from the Laplace approximation scaled by the
  quasi-Poisson dispersion.
- **Fair price.** Expected goals implied by the bookmaker's 1X2 and
  over/under 2.5 prices, blended with the model's on the log scale. The blend
  weight is fitted on earlier seasons. Every market is priced from the
  resulting scoreline distribution, so all prices on a match are consistent
  with each other.
- **Costs.** Kalshi taker fee 0.07·p·(1−p). Polymarket rate·p·(1−p), with the
  rate read from each market. Betfair 2% commission.
- **Evaluation.** Walk-forward forecasts, one-standard-error hyperparameter
  selection on validation seasons, RPS/log loss/Brier. Diebold–Mariano tests
  and bootstrap intervals clustered by match day. Encompassing tests via a
  logarithmic pool. Closing line value alongside profit.
- **Ledger.** Every hourly refresh commits `data/ledger/predictions.csv`. A
  row freezes at kick-off, so the git history proves when each forecast was
  made.

## Repository layout

```
src/betting_analytics/
  data/         football_data, understat, fpl, kalshi, polymarket, venue_history, teams, dataset, afcon
  models/       dixon_coles, fitting, elo, devig, implied, pool, international
  evaluation/   metrics, backtest, betting, report, venues, lab, afcon_backtest
  live/         pipeline, pricing, season_sim, ledger, signals, afcon
  cli.py        `ba` command
site/           static dashboard (index.html, assets/, data/*.json written by the pipeline)
data/           raw source cache, processed match table, model config, ledger, price snapshots
tests/
.github/workflows/  ci, refresh (hourly), backtest (weekly), pages
```

## Running locally

```
uv sync --extra dev
uv run pytest
uv run ba data        # rebuild the match table from the sources
uv run ba tune        # choose settings on the validation seasons (about 2 min)
uv run ba backtest    # walk-forward test -> site/data/backtest.json (about 6 min)
uv run ba venues      # Kalshi/Polymarket history study -> site/data/venues.json
uv run ba lab --fetch # extend venue price caches, test hypotheses -> site/data/lab.json
uv run ba refresh     # live forecasts and prices -> site/data/*.json
uv run ba afcon-backtest  # AFCON walk-forward test, sets the AFCON model settings (about 2 min)
uv run ba afcon       # AFCON forecasts, group simulation, markets -> site/data/afcon.json
python -m http.server -d site 8000
```

## Hosting

The workflows deploy `site/` to GitHub Pages. One-time setup in the
repository settings:

1. Make the repository public. Free GitHub Pages requires a public repository.
2. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. Actions → *refresh* → *Run workflow* for the first deployment. After that it
   runs every hour.

## Roadmap

1. Settle the signals ledger automatically (fill, closing price, result) so
   the Kalshi limit-order edge is confirmed or rejected on live data.
2. Record Polymarket order books hourly; Polymarket makers pay no fee and
   earn rebates, so the same retail-flow edge may be larger there, but its
   price history has no bid/ask to test it.
3. A live sharp price feed (Betfair Exchange or an odds API) between
   football-data.co.uk updates.
4. Team news (injuries, line-ups): the largest information gap between the
   model and the market.
5. More leagues; lower divisions are usually priced less efficiently.
6. AFCON: backtest the group simulation on the 2021, 2023 and 2025 campaigns
   (calibration of qualify and group-winner probabilities), and record
   Polymarket group prices hourly so the gaps can be tested.

Forecasts are probabilities, not advice.
