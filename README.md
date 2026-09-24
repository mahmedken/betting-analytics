# betting-analytics

A dashboard for Premier League betting and prediction markets, built on a
backtested scoreline model. It prices every match market (result, total
goals, both teams to score, winning margin, correct score) and season market
(title, top four, relegation). It compares those prices with Kalshi,
Polymarket and bookmaker quotes and publishes an honest record of how well the
model has done.

Everything is computed from public data by the code in this repository and
refreshed hourly by GitHub Actions. The site is static (`site/`) and served by
GitHub Pages.

## What the backtest found

All numbers are from the walk-forward backtest on held-out seasons 2019-20 to
2026-27 (2,710 matches). Settings were chosen on 2016-17 to 2018-19 and then
frozen. The site's *record* page has the full tables, calibration plots and
significance tests.

| forecast, home/draw/away | RPS (lower is better) |
|---|---|
| Elo baseline | 0.2073 |
| Dixon–Coles model (fitted to 50% goals, 50% xG) | 0.2009 |
| fair price (model blended with pre-match bookmaker price) | 0.1983 |
| bookmaker, pre-match (de-margined) | 0.1983 |
| bookmaker, closing (de-margined) | 0.1970 |

- **The model alone does not beat the market.** Its weight in an encompassing
  test against the closing price is −0.05 ± 0.14. It carries no detectable
  information the market has not priced. The live fair price therefore puts
  only 3% weight (log scale) on the model when a bookmaker price exists.
- **Kalshi and Polymarket are as accurate as the sharp bookmaker close** for
  EPL 1X2 one hour before kick-off (343 Kalshi and 430 Polymarket matches
  since August 2025; RPS
  differences far inside the noise).
- **No systematic mispricing on either venue.** Buying every contract has
  negative closing line value (CLV) on every outcome, roughly the size of the
  trading costs.
- **Bets chosen by the model against Pinnacle have negative CLV (about −4%).**
  The model's disagreements with a sharp book are mostly model error. Their
  small positive profit is luck.
- **Price dispersion between bookmakers is the one positive signal.** When the
  best available bookmaker price beats the fair price by 5% or more, CLV is
  +3.6% (90% interval +2.7% to +4.5%, 736 bets). This is the effect described
  by Kaunitz, Zhong & Kreiner (2017). In practice bookmakers limit accounts
  that take such prices.

The main lesson for the next phase: the fair price is only as fresh as its
bookmaker odds, which football-data.co.uk publishes twice a week. A live sharp
price feed is needed before the board can find prediction-market edges that
survive to the close.

## Pages

- **board**: every venue price for the next fixtures against the fair
  probability. Each row shows the edge after fees, its 90% interval from
  parameter uncertainty, and the probability that the edge is positive. Also
  lists fixtures and the largest gaps between the season simulation and the
  season markets.
- **match**: fair and model probabilities for every market, correct-score
  grid, total-goals distribution, venue quotes, team strength and form.
- **season**: 10,000 simulated seasons with parameter uncertainty. Shows title,
  top four and relegation probabilities next to Kalshi/Polymarket prices, the
  finishing-position distribution, and team strength with intervals.
- **record**: backtest accuracy, calibration, encompassing tests, betting
  simulations with CLV, the prediction-market study, and the live forward
  record.
- **method**: how every number is made, with references.

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
  data/         football_data, understat, fpl, kalshi, polymarket, venue_history, teams, dataset
  models/       dixon_coles, fitting, elo, devig, implied, pool
  evaluation/   metrics, backtest, betting, report, venues
  live/         pipeline, pricing, season_sim, ledger
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
uv run ba refresh     # live forecasts and prices -> site/data/*.json
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

1. A live sharp price feed (for example Betfair Exchange or an odds API) so the
   fair price moves with the market between football-data.co.uk updates.
2. Team news: injuries, suspensions and expected line-ups, the largest
   information gap between the model and the market.
3. Backtest the season simulation against past seasons' final tables and
   season-market prices.
4. More leagues. The pipeline is league-agnostic, and lower divisions are
   usually priced less efficiently.
5. Alerts when a venue price moves away from the fair price by more than its
   historical noise.

Forecasts are probabilities, not advice.
