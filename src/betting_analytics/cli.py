"""Command-line entry point.

    ba data       rebuild data/processed/matches.csv from the sources
    ba tune       choose hyperparameters on the validation seasons
    ba backtest   walk-forward test on the held-out seasons -> site/data/backtest.json
    ba venues     Kalshi / Polymarket price history vs outcomes -> site/data/venues.json
    ba lab        test mispricing hypotheses -> site/data/lab.json (--fetch extends price caches)
    ba refresh    live forecasts, prices, season simulation, ledger -> site/data/*.json
"""

from __future__ import annotations

import argparse
import json
import time

from . import config

MODEL_CONFIG = config.DATA / "model" / "config.json"


def load_model_config() -> dict:
    return json.loads(MODEL_CONFIG.read_text())


def save_model_config(cfg: dict) -> None:
    MODEL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    MODEL_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


def cmd_data(args) -> None:
    from .data import dataset
    df = dataset.build(refresh=True)
    print(f"matches: {len(df)}; played: {int(df['hg'].notna().sum())}")


def cmd_tune(args) -> None:
    import pandas as pd
    from .data import dataset
    from .evaluation import backtest as bt
    from .models.fitting import estimate_promoted_prior

    df = dataset.load()
    t0 = time.time()
    prior = estimate_promoted_prior(df, bt.PRIOR_SEASONS)
    dc_res = bt.tune_dc(df, prior, workers=args.workers)
    elo_res = bt.tune_elo(df)
    out_dir = config.DATA / "model"
    out_dir.mkdir(parents=True, exist_ok=True)
    dc_res.to_csv(out_dir / "tuning_dixon_coles.csv", index=False)
    elo_res.to_csv(out_dir / "tuning_elo.csv", index=False)
    best = dc_res[dc_res["chosen"]].iloc[0]
    best_elo = elo_res.iloc[0]
    cfg = load_model_config() if MODEL_CONFIG.exists() else {}
    cfg.update({
        "dixon_coles": {"xi": float(best["xi"]), "w_goals": float(best["w_goals"]),
                        "prior_sd": float(best["prior_sd"]), "window_days": int(best["window_days"])},
        "elo": {"k": float(best_elo["k"]), "home_adv": float(best_elo["home_adv"]),
                "carry": float(best_elo["carry"])},
        "promoted_prior": {"att": prior[0], "dfn": prior[1], "sd": prior[2],
                           "estimated_on_seasons": [config.season_label(s) for s in bt.PRIOR_SEASONS]},
        "validation_seasons": [config.season_label(s) for s in bt.VALIDATION_SEASONS],
        "selection_rule": "one-standard-error rule on validation RPS, most regularised (smallest prior sd)",
        "validation_rps": {"chosen": float(best["rps"]), "best": float(dc_res["rps"].min())},
    })
    save_model_config(cfg)
    pd.set_option("display.width", 200)
    print(dc_res.sort_values("rps").head(8).to_string())
    print("chosen:", dc_res[dc_res["chosen"]].to_string())
    print(elo_res.head(5).to_string())
    print(f"tuned in {time.time() - t0:.0f}s -> {MODEL_CONFIG}")


def cmd_backtest(args) -> None:
    from .evaluation import report
    report.run(workers=args.workers)


def cmd_venues(args) -> None:
    from .evaluation import venues
    venues.run(fetch=not args.no_fetch)


def cmd_lab(args) -> None:
    from .evaluation import lab
    if args.fetch:
        from .data import dataset, venue_paths
        df = dataset.load()
        venue_paths.fetch_kalshi_paths(df)
        venue_paths.fetch_polymarket_paths(df)
        venue_paths.fetch_kalshi_candles(df)
    lab.run()


def cmd_refresh(args) -> None:
    from .live import pipeline
    pipeline.run(n_draws=args.draws, n_sims=args.sims, skip_venues=args.skip_venues)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ba")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("data").set_defaults(func=cmd_data)
    t = sub.add_parser("tune")
    t.add_argument("--workers", type=int, default=4)
    t.set_defaults(func=cmd_tune)
    b = sub.add_parser("backtest")
    b.add_argument("--workers", type=int, default=4)
    b.set_defaults(func=cmd_backtest)
    v = sub.add_parser("venues")
    v.add_argument("--no-fetch", action="store_true")
    v.set_defaults(func=cmd_venues)
    lb = sub.add_parser("lab")
    lb.add_argument("--fetch", action="store_true", help="extend the venue price caches first")
    lb.set_defaults(func=cmd_lab)
    r = sub.add_parser("refresh")
    r.add_argument("--draws", type=int, default=1000)
    r.add_argument("--sims", type=int, default=10000)
    r.add_argument("--skip-venues", action="store_true")
    r.set_defaults(func=cmd_refresh)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
