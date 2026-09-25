"""Live refresh: data -> model -> prices -> opportunities -> season simulation -> JSON.

Run by the scheduled GitHub Action. Every number written to site/data comes
from a source fetched in this run or from the committed backtest output.
"""

from __future__ import annotations

import json
import traceback

import numpy as np
import pandas as pd

from .. import config
from ..cli import load_model_config
from ..data import dataset, espn, football_data, fpl, kalshi, polymarket, understat
from ..data.teams import canonical, code, display
from ..evaluation.betting import MAX_PRICE_GAP
from ..models import devig, implied
from ..models import dixon_coles as dc
from ..models.fitting import DCConfig, History, fit_as_of
from ..models.pool import LogPool
from ..evaluation import backtest
from ..evaluation.report import _clean as _clean_json
from ..models import market_ratings
from . import ledger as ledger_mod
from . import signals as signals_mod
from . import pricing, season_sim

HORIZON_DAYS = 21
MODEL_VERSION = "dc-xg-v1"
SNAPSHOT_DIR = config.DATA / "snapshots"
SNAPSHOT_VENUES = ("kalshi", "polymarket", "betfair")
SNAPSHOT_KEY = ["match_id", "venue", "market", "selection", "line"]
TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)
MARGIN_LINES = (1.5, 2.5, 3.5)   # 0.5 is the same as the match result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _r(x, nd=4):
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return x
    return None if not np.isfinite(xf) else round(xf, nd)


def _flag(x) -> bool:
    return bool(x) if isinstance(x, (bool, np.bool_)) else False


def _str(x) -> str | None:
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else str(x)


def _write(name: str, obj) -> None:
    config.SITE_DATA.mkdir(parents=True, exist_ok=True)
    (config.SITE_DATA / name).write_text(json.dumps(obj, separators=(",", ":"), default=str))


def _supplement_with_fpl(df: pd.DataFrame, fx: pd.DataFrame, season: int) -> pd.DataFrame:
    """Add matches FPL marks finished that football-data.co.uk has not published yet.

    Goals come from FPL; xG from Understat when it already has the match.
    """
    fin = fx[fx["finished"] & fx["hg"].notna()].copy()
    fin["match_id"] = dataset.match_id(fin)
    have = set(zip(df.loc[df["season"] == season, "home"], df.loc[df["season"] == season, "away"]))
    new = fin[[(h, a) not in have for h, a in zip(fin["home"], fin["away"])]]
    if new.empty:
        return df
    us = understat.load_season(season).set_index(["home", "away"])
    rows = []
    for r in new.itertuples(index=False):
        xg = us.loc[(r.home, r.away)] if (r.home, r.away) in us.index else None
        rows.append({"match_id": r.match_id, "season": season, "date": r.kickoff_utc.tz_convert(None).normalize(),
                     "kickoff_utc": r.kickoff_utc, "home": r.home, "away": r.away,
                     "hg": float(r.hg), "ag": float(r.ag),
                     "hxg": None if xg is None else xg["us_hxg"], "axg": None if xg is None else xg["us_axg"],
                     "result": "H" if r.hg > r.ag else "D" if r.hg == r.ag else "A",
                     "xg_source": "" if xg is None else "understat"})
    print(f"added {len(rows)} FPL results not yet in football-data.co.uk")
    return pd.concat([df, pd.DataFrame(rows)], ignore_index=True).sort_values("kickoff_utc").reset_index(drop=True)


def _book_quotes(fd_fix: pd.DataFrame) -> pd.DataFrame:
    """Bookmaker prices from football-data.co.uk fixtures as quote rows (cost = 1/odds)."""
    rows = []
    books = {"b365": "bet365", "bfe": "betfair", "max": "best price", "avg": "average"}
    for r in fd_fix.itertuples(index=False):
        for book, venue in books.items():
            for sel, col in (("H", "h"), ("D", "d"), ("A", "a")):
                o = getattr(r, f"{book}_pre_{col}")
                if pd.notna(o):
                    rows.append({"venue": venue, "home": r.home, "away": r.away, "market": "1x2",
                                 "selection": sel, "line": None, "odds": float(o)})
            for sel, col in (("over", "o25"), ("under", "u25")):
                o = getattr(r, f"{book}_pre_{col}")
                if pd.notna(o):
                    rows.append({"venue": venue, "home": r.home, "away": r.away, "market": "total",
                                 "selection": sel, "line": 2.5, "odds": float(o)})
    q = pd.DataFrame(rows)
    if q.empty:
        return q
    # Drop "best price" quotes far longer than the market average: stale or
    # erroneous (same rule as the backtest, evaluation.betting.MAX_PRICE_GAP).
    key = ["home", "away", "market", "selection"]
    avg = q[q["venue"] == "average"].set_index(key)["odds"]
    is_max = q["venue"] == "best price"
    avg_for = q.loc[is_max, key].apply(tuple, axis=1).map(avg)
    bad = (1 / avg_for - 1 / q.loc[is_max, "odds"]) > MAX_PRICE_GAP
    q = q.drop(index=bad[bad].index)
    eff = np.where(q["venue"] == "betfair", 1 + (q["odds"] - 1) * 0.98, q["odds"])
    q["cost"] = 1.0 / eff
    q["ask"] = 1.0 / q["odds"]
    return q


def _sharp_book(fd_fix: pd.DataFrame) -> pd.DataFrame:
    """De-margined 1X2 and over-2.5 probabilities: Betfair Exchange if present, else market average."""
    out = []
    for r in fd_fix.itertuples(index=False):
        rec = {"home": r.home, "away": r.away}
        for book in ("bfe", "avg"):
            o = np.array([[getattr(r, f"{book}_pre_{s}") for s in "hda"]], dtype=float)
            if np.isfinite(o).all():
                p = devig.shin(o)[0]
                rec.update({"book_h": p[0], "book_d": p[1], "book_a": p[2], "book_source": book})
                ou = np.array([[getattr(r, f"{book}_pre_o25"), getattr(r, f"{book}_pre_u25")]], dtype=float)
                rec["book_o25"] = devig.shin(ou)[0][0] if np.isfinite(ou).all() else np.nan
                break
        out.append(rec)
    return pd.DataFrame(out)


def _append_snapshots(snap: pd.DataFrame, path) -> None:
    """Append price snapshots, keeping only rows whose bid or ask changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    snap["line"] = snap["line"].fillna(-1.0)
    if path.exists():
        old = pd.read_csv(path)
        old["line"] = old["line"].fillna(-1.0)
        last = old.groupby(SNAPSHOT_KEY, dropna=False)[["bid", "ask"]].last()
        prev = snap.join(last, on=SNAPSHOT_KEY, rsuffix="_prev")
        same = (prev["bid"].fillna(-1).eq(prev["bid_prev"].fillna(-1))
                & prev["ask"].fillna(-1).eq(prev["ask_prev"].fillna(-1)))
        snap = snap[~same.values]
    if len(snap):
        snap.to_csv(path, mode="a", header=not path.exists(), index=False)


def _team_ratings(model: dc.DixonColes, teams: list[str]) -> list[dict]:
    """Expected goals for/against per match vs an average team at a neutral ground."""
    th = model.theta
    n = model.n
    att, dfn = model.att(), model.dfn()
    base = th[0] + th[1] / 2
    rng = np.random.default_rng(1)
    draws = model.sample_theta(2000, rng)
    out = []
    for t in teams:
        i = model.index[t]
        gf = np.exp(base + att[i] + dfn.mean())
        ga = np.exp(base + att.mean() + dfn[i])
        d_base = draws[:, 0] + draws[:, 1] / 2
        d_att, d_dfn = draws[:, 2:2 + n], draws[:, 2 + n:]
        gf_d = np.exp(d_base + d_att[:, i] + d_dfn.mean(axis=1))
        ga_d = np.exp(d_base + d_att.mean(axis=1) + d_dfn[:, i])
        net = gf_d - ga_d
        out.append({"team": t, "name": display(t), "code": code(t),
                    "xgf": _r(gf, 3), "xga": _r(ga, 3), "net": _r(gf - ga, 3),
                    "xgf_ci": [_r(np.quantile(gf_d, 0.05), 3), _r(np.quantile(gf_d, 0.95), 3)],
                    "xga_ci": [_r(np.quantile(ga_d, 0.05), 3), _r(np.quantile(ga_d, 0.95), 3)],
                    "net_ci": [_r(np.quantile(net, 0.05), 3), _r(np.quantile(net, 0.95), 3)]})
    return sorted(out, key=lambda r: -r["net"])


def _form(df: pd.DataFrame, team: str, n: int = 6) -> list[dict]:
    g = df[((df["home"] == team) | (df["away"] == team)) & df["hg"].notna()].sort_values("kickoff_utc").tail(n)
    out = []
    for r in g.itertuples(index=False):
        home = r.home == team
        gf, ga = (r.hg, r.ag) if home else (r.ag, r.hg)
        xgf, xga = (r.hxg, r.axg) if home else (r.axg, r.hxg)
        out.append({"date": str(r.kickoff_utc)[:10], "opp": code(r.away if home else r.home),
                    "venue": "H" if home else "A", "gf": int(gf), "ga": int(ga),
                    "xgf": _r(xgf, 2), "xga": _r(xga, 2),
                    "res": "W" if gf > ga else "D" if gf == ga else "L"})
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run(n_draws: int = 1000, n_sims: int = 10000, skip_venues: bool = False) -> None:
    now = pd.Timestamp(config.utcnow())
    season = config.current_season(now.date())
    mcfg = load_model_config()
    cfg = DCConfig(**mcfg["dixon_coles"])
    prior = (mcfg["promoted_prior"]["att"], mcfg["promoted_prior"]["dfn"], mcfg["promoted_prior"]["sd"])
    alpha = mcfg["live"]["alpha"]
    pool = LogPool(mcfg["live"]["pool_a"], mcfg["live"]["pool_b"], np.nan, np.nan, 0)
    status: dict[str, dict] = {}

    df = dataset.build(refresh=True)
    status["football-data.co.uk results"] = {"ok": True, "last_match": str(df.loc[df["hg"].notna(), "kickoff_utc"].max())}
    fx = fpl.load_fixtures()
    status["fantasy.premierleague.com fixtures"] = {"ok": True, "n": int(len(fx))}
    df = _supplement_with_fpl(df, fx, season)
    teams_now = sorted(set(fx["home"]) | set(fx["away"]))

    hist = History.from_df(df)
    model = fit_as_of(hist, now, cfg, prior, season=season, teams_now=set(teams_now))
    rng = np.random.default_rng(int(now.timestamp()) // 3600)
    theta_draws = model.sample_theta(n_draws, rng)

    upcoming = fx[(~fx["started"]) & fx["kickoff_utc"].notna()
                  & (fx["kickoff_utc"] > now) & (fx["kickoff_utc"] <= now + pd.Timedelta(days=HORIZON_DAYS))].copy()
    if upcoming.empty:   # e.g. a long break: show the next gameweek however far away
        nxt = fx[(~fx["started"]) & fx["kickoff_utc"].notna() & (fx["kickoff_utc"] > now)]
        if not nxt.empty:
            upcoming = nxt[nxt["gameweek"] == nxt["gameweek"].min()].copy()
    upcoming = upcoming.sort_values("kickoff_utc").reset_index(drop=True)
    upcoming["match_id"] = dataset.match_id(upcoming)

    # Bookmaker prices (football-data.co.uk publishes these Tuesday and Friday).
    try:
        fd_fix = football_data.load_fixtures()
        status["football-data.co.uk odds"] = {"ok": True, "n": int(len(fd_fix))}
    except Exception as exc:
        fd_fix = pd.DataFrame()
        status["football-data.co.uk odds"] = {"ok": False, "error": str(exc)[:200]}
    sharp = _sharp_book(fd_fix) if len(fd_fix) else pd.DataFrame(columns=["home", "away"])
    quotes = [_book_quotes(fd_fix)] if len(fd_fix) else []
    kalshi_q = pd.DataFrame()

    if not skip_venues:
        for name, fn in (("kalshi", kalshi.match_quotes), ("polymarket", polymarket.match_quotes)):
            try:
                q = fn()
                status[f"{name} match markets"] = {"ok": True, "n": int(len(q))}
                if len(q):
                    fee = q["fee_rate"] if "fee_rate" in q else pd.Series(np.nan, index=q.index)
                    q["cost"] = [pricing.cost_per_contract(a, name, f) if pd.notna(a) else np.nan
                                 for a, f in zip(q["ask"], fee)]
                    quotes.append(q)
                    if name == "kalshi":
                        kalshi_q = q
            except Exception as exc:
                status[f"{name} match markets"] = {"ok": False, "error": str(exc)[:200]}
                traceback.print_exc()
    quotes_df = pd.concat(quotes, ignore_index=True) if quotes else pd.DataFrame()

    # ESPN event ids let the site poll live scores for these fixtures.
    try:
        espn_ids = espn.event_ids("eng.1", upcoming, canonical)
        status["ESPN scoreboard"] = {"ok": True, "n": len(espn_ids)}
    except Exception as exc:
        espn_ids = {}
        status["ESPN scoreboard"] = {"ok": False, "error": str(exc)[:200]}

    # ------------------------------------------------------------------
    # Per-match pricing
    # ------------------------------------------------------------------
    matches, opps, ledger_rows, snapshot_rows = [], [], [], []
    for r in upcoming.itertuples(index=False):
        h, a = r.home, r.away
        lam, nu = model.rates([h], [a])
        lam_d, nu_d = model.rates([h], [a], theta_draws)
        M_model = dc.score_matrix(lam, nu, model.rho)[0]
        M_model_d = dc.score_matrix(lam_d[:, 0], nu_d[:, 0], model.rho)

        sb = sharp[(sharp["home"] == h) & (sharp["away"] == a)] if len(sharp) else sharp
        book = sb.iloc[0].to_dict() if len(sb) else {}
        if book.get("book_h") is not None and np.isfinite(book.get("book_h", np.nan)):
            lam_m, nu_m = implied.implied_rates(np.array([[book["book_h"], book["book_d"], book["book_a"]]]),
                                                np.array([book.get("book_o25", np.nan)]), 0.0)
            lam_f, nu_f = implied.blend_rates(lam, nu, lam_m, nu_m, alpha)
            lam_fd, nu_fd = implied.blend_rates(lam_d[:, 0], nu_d[:, 0], np.repeat(lam_m, n_draws),
                                                np.repeat(nu_m, n_draws), alpha)
            M_fair = dc.score_matrix(lam_f, nu_f, 0.0)[0]
            M_fair_d = dc.score_matrix(lam_fd, nu_fd, 0.0)
            fair_basis = f"model + {'Betfair Exchange' if book['book_source'] == 'bfe' else 'bookmaker average'} (alpha={alpha:.2f})"
            mkt_rates = (float(lam_m[0]), float(nu_m[0]))
            rates_fair = [float(lam_f[0]), float(nu_f[0]), 0.0]
        else:
            M_fair, M_fair_d, fair_basis, mkt_rates = M_model, M_model_d, "model only (no bookmaker price yet)", None
            rates_fair = [float(lam[0]), float(nu[0]), float(model.rho)]

        def probs(market, sel, line):
            pm = float(pricing.selection_prob(M_model, market, sel, line))
            pf = float(pricing.selection_prob(M_fair, market, sel, line))
            dfair = pricing.selection_prob(M_fair_d, market, sel, line)
            dmod = pricing.selection_prob(M_model_d, market, sel, line)
            return pm, pf, dmod, dfair

        # Table of every market the model prices.
        market_rows = []
        specs = [("1x2", s, None) for s in "HDA"]
        specs += [("total", s, l) for l in TOTAL_LINES for s in ("over", "under")]
        specs += [("btts", s, None) for s in ("yes", "no")]
        specs += [("margin", s, l) for l in MARGIN_LINES for s in ("home", "away")]
        for market, sel, line in specs:
            pm, pf, dmod, dfair = probs(market, sel, line)
            market_rows.append({"market": market, "selection": sel, "line": line,
                                "model": _r(pm), "model_ci": [_r(np.quantile(dmod, 0.05)), _r(np.quantile(dmod, 0.95))],
                                "fair": _r(pf), "fair_ci": [_r(np.quantile(dfair, 0.05)), _r(np.quantile(dfair, 0.95))]})

        # Quotes for this match.
        mq = quotes_df[(quotes_df["home"] == h) & (quotes_df["away"] == a)] if len(quotes_df) else quotes_df
        match_quotes = []
        for q in mq.itertuples(index=False):
            line = None if pd.isna(q.line) else float(q.line)
            try:
                pm, pf, dmod, dfair = probs(q.market, q.selection, line)
            except (ValueError, IndexError):
                continue
            cost = getattr(q, "cost", np.nan)
            rec = {"venue": q.venue, "market": q.market, "selection": q.selection, "line": line,
                   "bid": _r(getattr(q, "bid", None)), "ask": _r(q.ask), "cost": _r(cost),
                   "odds": _r(getattr(q, "odds", None), 3), "volume": _r(getattr(q, "volume", None), 0),
                   "ticker": getattr(q, "ticker", None), "token": _str(getattr(q, "token", None)),
                   "invert": _flag(getattr(q, "invert", False)), "fee_rate": _r(getattr(q, "fee_rate", None)),
                   "model": _r(pm), "fair": _r(pf)}
            if pd.notna(cost) and 0 < cost < 1:
                rec["edge"] = _r(pf / cost - 1)
                rec["edge_model"] = _r(pm / cost - 1)
                rec["p_edge_pos"] = _r(float((dfair > cost).mean()), 3)
                rec["kelly"] = _r(pricing.kelly_fraction(pf, cost))
                opps.append({"match_id": r.match_id, "kickoff_utc": r.kickoff_utc.isoformat(),
                             "gameweek": int(r.gameweek), "home": h, "away": a,
                             "home_code": code(h), "away_code": code(a), **rec})
            match_quotes.append(rec)
            if q.venue in SNAPSHOT_VENUES and (q.market == "1x2" or (q.market == "total" and line == 2.5)):
                snapshot_rows.append({"fetched_utc": now.isoformat(), "match_id": r.match_id, "venue": q.venue,
                                      "market": q.market, "selection": q.selection, "line": line,
                                      "bid": rec["bid"], "ask": rec["ask"], "model": rec["model"], "fair": rec["fair"]})

        p_model = dc.outcome_probs(M_model)
        p_fair = dc.outcome_probs(M_fair)

        def mid(venue, sel):
            x = mq[(mq["venue"] == venue) & (mq["market"] == "1x2") & (mq["selection"] == sel)] if len(mq) else mq
            if len(x) and pd.notna(x.iloc[0]["bid"]) and pd.notna(x.iloc[0]["ask"]):
                return (float(x.iloc[0]["bid"]) + float(x.iloc[0]["ask"])) / 2
            return np.nan

        ledger_rows.append({
            "match_id": r.match_id, "season": season, "gameweek": int(r.gameweek), "kickoff_utc": r.kickoff_utc,
            "home": h, "away": a,
            "model_h": p_model[0], "model_d": p_model[1], "model_a": p_model[2],
            "model_o25": float(dc.prob_over(M_model, 2.5)),
            "fair_h": p_fair[0], "fair_d": p_fair[1], "fair_a": p_fair[2],
            "fair_o25": float(dc.prob_over(M_fair, 2.5)),
            "book_h": book.get("book_h", np.nan), "book_d": book.get("book_d", np.nan),
            "book_a": book.get("book_a", np.nan), "book_o25": book.get("book_o25", np.nan),
            "book_source": book.get("book_source", ""),
            "kalshi_h": mid("kalshi", "H"), "kalshi_d": mid("kalshi", "D"), "kalshi_a": mid("kalshi", "A"),
            "poly_h": mid("polymarket", "H"), "poly_d": mid("polymarket", "D"), "poly_a": mid("polymarket", "A"),
        })

        G = 7
        sm = M_fair[:G, :G]
        matches.append({
            "match_id": r.match_id, "gameweek": int(r.gameweek), "kickoff_utc": r.kickoff_utc.isoformat(),
            "home": h, "away": a, "home_name": display(h), "away_name": display(a),
            "home_code": code(h), "away_code": code(a), "espn_id": espn_ids.get((h, a)),
            "rates_fair": [_r(x, 4) for x in rates_fair],
            "xg_model": [_r(lam[0], 3), _r(nu[0], 3)],
            "xg_model_ci": [[_r(np.quantile(lam_d[:, 0], 0.05), 3), _r(np.quantile(lam_d[:, 0], 0.95), 3)],
                            [_r(np.quantile(nu_d[:, 0], 0.05), 3), _r(np.quantile(nu_d[:, 0], 0.95), 3)]],
            "xg_market": None if mkt_rates is None else [_r(mkt_rates[0], 3), _r(mkt_rates[1], 3)],
            "fair_basis": fair_basis,
            "book": {k: _r(v) if k != "book_source" else v for k, v in book.items() if k.startswith("book")},
            "score_matrix": [[_r(v, 5) for v in row] for row in sm],
            "score_matrix_rest": _r(1 - sm.sum(), 5),
            "markets": market_rows,
            "quotes": match_quotes,
            "form": {"home": _form(df, h), "away": _form(df, a)},
        })

    # ------------------------------------------------------------------
    # Season simulation and futures
    # ------------------------------------------------------------------
    # Team strengths implied by the closing prices of recent matches (the
    # market's view), with rating drift; see models.market_ratings and the
    # "futures" finding in the lab.
    played = df[(df["season"] == season) & df["hg"].notna()][["home", "away", "hg", "ag"]]
    remaining = fx[~fx["finished"]][["home", "away", "kickoff_utc"]]
    lab = signals_mod._lab()
    drift = (lab.get("futures") or {}).get("drift_sd_per_week", 0.045)
    rates = market_ratings.implied_match_rates(df[df["season"] >= season - 1], backtest.market_probs(df[df["season"] >= season - 1]))
    mkt_model = market_ratings.fit(rates, now, teams_now).model
    sim = season_sim.simulate(mkt_model, played, remaining, teams_now, n_sims=n_sims,
                              seed=int(now.timestamp()) // 3600, draw_params=False,
                              drift_sd_per_week=drift, now=now)
    futures = []
    if not skip_venues:
        for name, fn in (("kalshi", kalshi.futures_quotes), ("polymarket", polymarket.futures_quotes)):
            try:
                fq = fn()
                status[f"{name} season markets"] = {"ok": True, "n": int(len(fq))}
                futures.append(fq)
            except Exception as exc:
                status[f"{name} season markets"] = {"ok": False, "error": str(exc)[:200]}
    fut_df = pd.concat(futures, ignore_index=True) if futures else pd.DataFrame()
    ratings = _team_ratings(mkt_model, teams_now)
    rating_by_team = {x["team"]: x for x in ratings}
    for t in sim["teams"]:
        t.update({"name": display(t["team"]), "code": code(t["team"]), "rating": rating_by_team[t["team"]]})
        t["markets"] = {}
        for mkt in ("title", "top4", "relegation"):
            t["markets"][mkt] = {}
            if len(fut_df):
                x = fut_df[(fut_df["team"] == t["team"]) & (fut_df["market"] == mkt)]
                for q in x.itertuples(index=False):
                    fee_rate = getattr(q, "fee_rate", np.nan)
                    cost_yes = pricing.cost_per_contract(q.ask, q.venue, fee_rate) if pd.notna(q.ask) else np.nan
                    no_ask = 1 - q.bid if pd.notna(q.bid) else np.nan
                    cost_no = pricing.cost_per_contract(no_ask, q.venue, fee_rate) if pd.notna(no_ask) else np.nan
                    p = t[mkt]
                    t["markets"][mkt][q.venue] = {
                        "bid": _r(q.bid), "ask": _r(q.ask), "last": _r(q.last), "volume": _r(q.volume, 0),
                        "fee_rate": _r(fee_rate) if pd.notna(fee_rate) else None,
                        "ticker": getattr(q, "ticker", None), "token": _str(getattr(q, "token", None)),
                        "edge_yes": _r(p / cost_yes - 1) if pd.notna(cost_yes) and cost_yes > 0 else None,
                        "edge_no": _r((1 - p) / cost_no - 1) if pd.notna(cost_no) and cost_no > 0 else None}

    # ------------------------------------------------------------------
    # Ledger and snapshots
    # ------------------------------------------------------------------
    led = ledger_mod.load()
    results = df[df["season"] == season][["match_id", "hg", "ag"]]
    led = ledger_mod.update(led, pd.DataFrame(ledger_rows), results, now, MODEL_VERSION)
    ledger_mod.save(led)
    if snapshot_rows:
        _append_snapshots(pd.DataFrame(snapshot_rows), SNAPSHOT_DIR / f"{config.season_label(season)}.csv")

    # ------------------------------------------------------------------
    # JSON for the site
    # ------------------------------------------------------------------
    opps_sorted = sorted(opps, key=lambda o: -(o.get("edge") or -9))
    sig = {"maker": signals_mod.maker_signals(kalshi_q, upcoming, now, lab),
           "season": signals_mod.season_signals(sim["teams"], lab),
           "best_price": signals_mod.best_price_signals(matches, lab),
           "arbitrage": signals_mod.arbitrage_signals(matches)}
    signals_mod.log([x for v in sig.values() for x in v], now)
    by_match = {}
    for s_ in sig["maker"] + sig["best_price"] + sig["arbitrage"]:
        by_match.setdefault(s_["match_id"], []).append(s_["type"])
    for m in matches:
        m["signals"] = by_match.get(m["match_id"], [])
    played_all = df[df["hg"].notna()]
    meta = {
        "generated_utc": now.isoformat(),
        "season": config.season_label(season),
        "model_version": MODEL_VERSION,
        "model": {"config": mcfg["dixon_coles"], "alpha": alpha, "pool": [pool.a, pool.b],
                  "home_adv": _r(model.theta[1], 4), "rho": _r(model.rho, 4),
                  "fitted_on_matches": model.n_matches,
                  "last_result_utc": str(played_all["kickoff_utc"].max())},
        "sources": status,
        "counts": {"upcoming": len(matches), "quotes": int(sum(len(m["quotes"]) for m in matches)),
                   "opportunities_pos": int(sum(1 for o in opps if (o.get("edge") or 0) > 0)),
                   "signals": {k: len(v) for k, v in sig.items()},
                   "kalshi_markets_open": int(len(kalshi_q))},
    }
    _write("meta.json", meta)
    _write("board.json", {"generated_utc": now.isoformat(), "opportunities": opps_sorted})
    _write("signals.json", {"generated_utc": now.isoformat(), **_clean_json(sig)})
    _write("matches.json", {"generated_utc": now.isoformat(), "matches": matches})
    _write("season.json", {"generated_utc": now.isoformat(), "season": config.season_label(season),
                           "n_sims": sim["n_sims"], "played": int(len(played)), "drift_sd_per_week": drift,
                           "remaining": int(len(remaining)), "teams": sim["teams"]})
    settled = led[led["result"].isin(["H", "D", "A"])]
    _write("ledger.json", {"generated_utc": now.isoformat(), "score": ledger_mod.score(led),
                           "rows": json.loads(led.to_json(orient="records"))[-400:],
                           "n_settled": int(len(settled))})
    print(f"refresh done: {len(matches)} matches, {len(opps)} priced quotes, "
          f"{meta['counts']['opportunities_pos']} with positive edge")
