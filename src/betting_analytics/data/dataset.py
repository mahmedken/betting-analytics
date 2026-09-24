"""Build the canonical match table: results + xG + bookmaker odds."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from . import football_data, understat


def match_id(df: pd.DataFrame) -> pd.Series:
    from .teams import code
    return (df["kickoff_utc"].dt.strftime("%Y%m%d") + "-"
            + df["home"].map(code) + "-" + df["away"].map(code))


def build(refresh: bool = True, save: bool = True) -> pd.DataFrame:
    fd = football_data.load_history(refresh_current=refresh)
    us = understat.load_history()
    df = fd.merge(us, on=["season", "home", "away"], how="left", validate="one_to_one")

    played = df["hg"].notna()
    has_us = df["us_hg"].notna()
    mismatch = played & has_us & ((df["hg"] != df["us_hg"]) | (df["ag"] != df["us_ag"]))
    if mismatch.any():
        bad = df.loc[mismatch, ["season", "home", "away", "hg", "ag", "us_hg", "us_ag"]]
        raise ValueError(f"score mismatch between football-data and Understat:\n{bad}")

    # xG source: Understat. football-data.co.uk xG (added in 2026-27) is kept in
    # separate columns and only used where Understat is missing, so that the
    # xG definition stays the same across seasons wherever possible.
    df["hxg"] = df["us_hxg"].fillna(df["fd_hxg"])
    df["axg"] = df["us_axg"].fillna(df["fd_axg"])
    df["xg_source"] = np.where(df["us_hxg"].notna(), "understat",
                               np.where(df["fd_hxg"].notna(), "football-data", ""))
    df = df.drop(columns=["us_hg", "us_ag", "us_hxg", "us_axg"])

    df["result"] = np.select([df["hg"] > df["ag"], df["hg"] == df["ag"], df["hg"] < df["ag"]],
                             ["H", "D", "A"], default="")
    df.loc[~played, "result"] = ""
    df.insert(0, "match_id", match_id(df))
    if df["match_id"].duplicated().any():
        raise ValueError("duplicate match ids")

    if save:
        config.PROCESSED.mkdir(parents=True, exist_ok=True)
        df.to_csv(config.PROCESSED / "matches.csv", index=False)
    return df


def load() -> pd.DataFrame:
    df = pd.read_csv(config.PROCESSED / "matches.csv", parse_dates=["date"])
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"], utc=True)
    df["result"] = df["result"].fillna("")
    df["xg_source"] = df["xg_source"].fillna("")
    return df
