"""Results, match statistics and bookmaker odds from football-data.co.uk.

Odds columns are renamed to `{book}_{when}_{selection}`:
  book       ps (Pinnacle), b365 (Bet365), avg (market average), max (market
             maximum), bfe (Betfair Exchange)
  when       pre   = odds collected before the match (Tuesday/Friday afternoon,
                     per football-data.co.uk notes); this is the price a bettor
                     could have taken when a prediction is made.
             close = closing odds collected just before kick-off.
  selection  h, d, a (1X2) and o25, u25 (over/under 2.5 goals).

Closing odds exist from 2019-20; Pinnacle odds from 2012-13 until Pinnacle
stopped publishing prices partway through 2025-26; Betfair Exchange odds from
2024-25.
"""

from __future__ import annotations

import io
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .. import config
from . import http
from .teams import canonical

BASE = "https://www.football-data.co.uk"
LONDON = ZoneInfo("Europe/London")

# (standard name, candidate source columns in order of preference)
_ODDS_MAP: list[tuple[str, tuple[str, ...]]] = []
for _sel, _suffix in (("h", "H"), ("d", "D"), ("a", "A")):
    _ODDS_MAP += [
        (f"ps_pre_{_sel}", (f"PS{_suffix}",)),
        (f"ps_close_{_sel}", (f"PSC{_suffix}",)),
        (f"b365_pre_{_sel}", (f"B365{_suffix}",)),
        (f"b365_close_{_sel}", (f"B365C{_suffix}",)),
        (f"avg_pre_{_sel}", (f"Avg{_suffix}", f"BbAv{_suffix}")),
        (f"avg_close_{_sel}", (f"AvgC{_suffix}",)),
        (f"max_pre_{_sel}", (f"Max{_suffix}", f"BbMx{_suffix}")),
        (f"max_close_{_sel}", (f"MaxC{_suffix}",)),
        (f"bfe_pre_{_sel}", (f"BFE{_suffix}",)),
        (f"bfe_close_{_sel}", (f"BFEC{_suffix}",)),
    ]
for _sel, _sym in (("o25", ">2.5"), ("u25", "<2.5")):
    _ODDS_MAP += [
        (f"ps_pre_{_sel}", (f"P{_sym}",)),
        (f"ps_close_{_sel}", (f"PC{_sym}",)),
        (f"b365_pre_{_sel}", (f"B365{_sym}",)),
        (f"b365_close_{_sel}", (f"B365C{_sym}",)),
        (f"avg_pre_{_sel}", (f"Avg{_sym}", f"BbAv{_sym}")),
        (f"avg_close_{_sel}", (f"AvgC{_sym}",)),
        (f"max_pre_{_sel}", (f"Max{_sym}", f"BbMx{_sym}")),
        (f"max_close_{_sel}", (f"MaxC{_sym}",)),
        (f"bfe_pre_{_sel}", (f"BFE{_sym}",)),
        (f"bfe_close_{_sel}", (f"BFEC{_sym}",)),
    ]

ODDS_COLUMNS = [name for name, _ in _ODDS_MAP]
BOOKS = ("ps", "b365", "avg", "max", "bfe")

_STAT_MAP = {
    "FTHG": "hg", "FTAG": "ag", "HTHG": "hthg", "HTAG": "htag",
    "HS": "h_shots", "AS": "a_shots", "HST": "h_sot", "AST": "a_sot",
    "HC": "h_corners", "AC": "a_corners", "HxG": "fd_hxg", "AxG": "fd_axg",
}


def _raw_path(div: str, start_year: int):
    return config.RAW / "football-data" / f"{div}_{config.fd_code(start_year)}.csv"


def download_season(start_year: int, div: str = "E0", force: bool = False) -> bytes:
    path = _raw_path(div, start_year)
    if path.exists() and not force:
        return path.read_bytes()
    url = f"{BASE}/mmz4281/{config.fd_code(start_year)}/{div}.csv"
    content = http.get(url).content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _kickoff_utc(dates: pd.Series, times: pd.Series | None) -> pd.Series:
    """Combine UK-local date and time into a UTC timestamp.

    Seasons before 2019-20 have no kick-off time; 15:00 UK time is used, which
    only matters for ordering matches within a day.
    """
    if times is None:
        times = pd.Series("15:00", index=dates.index)
    times = times.fillna("15:00").astype(str).str.slice(0, 5)
    local = pd.to_datetime(dates.dt.strftime("%Y-%m-%d") + " " + times, format="%Y-%m-%d %H:%M")
    return local.dt.tz_localize(LONDON, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")


def standardise(df: pd.DataFrame, start_year: int | None) -> pd.DataFrame:
    """Rename a football-data.co.uk frame into the standard schema."""
    df = df.dropna(subset=["HomeTeam", "AwayTeam"]).copy()
    out = pd.DataFrame(index=df.index)
    out["date"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed")
    out["kickoff_utc"] = _kickoff_utc(out["date"], df["Time"] if "Time" in df else None)
    out["home"] = df["HomeTeam"].map(canonical)
    out["away"] = df["AwayTeam"].map(canonical)
    if start_year is not None:
        out["season"] = start_year
    for src, dst in _STAT_MAP.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce") if src in df else np.nan
    for dst, candidates in _ODDS_MAP:
        col = next((c for c in candidates if c in df.columns), None)
        vals = pd.to_numeric(df[col], errors="coerce") if col else np.nan
        out[dst] = vals
    # Odds <= 1 are data errors.
    for dst in ODDS_COLUMNS:
        out.loc[out[dst] <= 1.0, dst] = np.nan
    if "Div" in df:
        out.insert(0, "div", df["Div"].values)
    return out.reset_index(drop=True)


def load_season(start_year: int, div: str = "E0", refresh: bool = False) -> pd.DataFrame:
    content = download_season(start_year, div, force=refresh)
    df = pd.read_csv(io.StringIO(_decode(content)))
    return standardise(df, start_year)


def load_history(first: int = config.FIRST_SEASON, last: int | None = None,
                 refresh_current: bool = True) -> pd.DataFrame:
    """All EPL matches from `first` to the current season, played or not."""
    last = last if last is not None else config.current_season()
    frames = []
    for year in range(first, last + 1):
        refresh = refresh_current and year == config.current_season()
        try:
            frames.append(load_season(year, refresh=refresh))
        except Exception as exc:  # the new season's file may not exist yet in July
            if year == config.current_season():
                print(f"football-data: no file for {config.season_label(year)} yet ({exc})")
                continue
            raise
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values(["kickoff_utc", "home"]).reset_index(drop=True)


def load_fixtures() -> pd.DataFrame:
    """Upcoming EPL fixtures with pre-match odds (published Tuesday and Friday)."""
    content = http.get(f"{BASE}/fixtures.csv").content
    path = config.LIVE / "football-data-fixtures.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    df = pd.read_csv(io.StringIO(_decode(content)))
    df = df[df["Div"] == "E0"]
    if df.empty:
        return standardise(pd.DataFrame(columns=["Div", "Date", "Time", "HomeTeam", "AwayTeam"]), None)
    return standardise(df, None)
