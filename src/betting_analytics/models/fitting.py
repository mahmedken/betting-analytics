"""Fit the Dixon-Coles model as of a point in time, using only earlier matches."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import dixon_coles as dc


@dataclass(frozen=True)
class DCConfig:
    xi: float = 0.002           # time-decay rate per day (weight = exp(-xi * age))
    w_goals: float = 0.5        # target = w * goals + (1 - w) * xG
    prior_sd: float = 0.3       # sd of the Gaussian prior on each att/dfn
    window_days: int = 5 * 365  # matches older than this are dropped

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class History:
    """Played matches as sorted numpy arrays, for fast slicing by time."""
    kickoff: np.ndarray        # int64 nanoseconds since epoch (UTC), ascending
    home: np.ndarray
    away: np.ndarray
    hg: np.ndarray
    ag: np.ndarray
    hxg: np.ndarray
    axg: np.ndarray
    season_teams: dict[int, set[str]]

    @classmethod
    def from_df(cls, df: pd.DataFrame) -> "History":
        teams = {int(s): set(g["home"]) | set(g["away"]) for s, g in df.groupby("season")}
        p = df[df["hg"].notna()].sort_values("kickoff_utc")
        return cls(
            kickoff=p["kickoff_utc"].values.astype("datetime64[ns]").astype(np.int64),
            home=p["home"].values, away=p["away"].values,
            hg=p["hg"].values.astype(float), ag=p["ag"].values.astype(float),
            hxg=p["hxg"].values.astype(float), axg=p["axg"].values.astype(float),
            season_teams=teams,
        )

    def promoted(self, season: int, teams_now: set[str] | None = None) -> set[str]:
        teams_now = teams_now if teams_now is not None else self.season_teams.get(season, set())
        prev = self.season_teams.get(season - 1, set())
        return teams_now - prev if prev else set()


def season_teams(df: pd.DataFrame, season: int) -> set[str]:
    s = df[df["season"] == season]
    return set(s["home"]) | set(s["away"])


def estimate_promoted_prior(df: pd.DataFrame, seasons) -> tuple[float, float, float]:
    """Distribution of promoted teams' single-season att/dfn relative to the league mean.

    Each season is fitted on its own with a nearly flat prior. Returns the mean
    att, the mean dfn and the pooled standard deviation across promoted teams,
    which is used as the prior sd for a promoted team in its first season.
    Call this only with seasons that precede the evaluation period.
    """
    hist = History.from_df(df)
    atts, dfns = [], []
    for s in seasons:
        tr = df[(df["season"] == s) & df["hg"].notna()]
        prom = hist.promoted(s)
        if tr.empty or not prom:
            continue
        m = dc.fit(tr["home"], tr["away"], tr["hg"], tr["ag"], np.ones(len(tr)),
                   tr["hg"], tr["ag"], prior_sd=10.0)
        att = m.att() - m.att().mean()
        dfn = m.dfn() - m.dfn().mean()
        for t in prom:
            i = m.index[t]
            atts.append(att[i])
            dfns.append(dfn[i])
    sd = float(np.sqrt((np.var(atts, ddof=1) + np.var(dfns, ddof=1)) / 2))
    return float(np.mean(atts)), float(np.mean(dfns)), sd


def fit_as_of(hist: History, cutoff: pd.Timestamp, cfg: DCConfig,
              promoted_prior: tuple[float, ...], season: int,
              teams_now: set[str] | None = None) -> dc.DixonColes:
    """Fit on played matches with kick-off strictly before `cutoff`.

    promoted_prior = (att mean, dfn mean[, sd]). Teams promoted this season get
    that prior; with the sd given, it replaces cfg.prior_sd for those teams.
    """
    c = pd.Timestamp(cutoff).value
    lo = np.searchsorted(hist.kickoff, c - int(cfg.window_days * 86400e9), side="left")
    hi = np.searchsorted(hist.kickoff, c, side="left")
    sl = slice(lo, hi)
    age = (c - hist.kickoff[sl]) / 86400e9
    w = np.exp(-cfg.xi * age)
    hg, ag, hxg, axg = hist.hg[sl], hist.ag[sl], hist.hxg[sl], hist.axg[sl]
    has_xg = np.isfinite(hxg) & np.isfinite(axg)
    wg = cfg.w_goals
    y_h = np.where(has_xg, wg * hg + (1 - wg) * np.nan_to_num(hxg), hg)
    y_a = np.where(has_xg, wg * ag + (1 - wg) * np.nan_to_num(axg), ag)
    teams_now = teams_now if teams_now is not None else hist.season_teams.get(season, set())
    promoted = hist.promoted(season, teams_now)
    prior = {t: (promoted_prior[0], promoted_prior[1]) for t in promoted}
    sd_team = {t: promoted_prior[2] for t in promoted} if len(promoted_prior) > 2 else None
    return dc.fit(hist.home[sl], hist.away[sl], y_h, y_a, w, hg, ag,
                  prior_mean=prior, prior_sd=cfg.prior_sd, prior_sd_team=sd_team, teams=sorted(teams_now))
