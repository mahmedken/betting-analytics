"""Team identity across data sources.

The canonical team name is the football-data.co.uk spelling ("Man United",
"Nott'm Forest"). Every other source (Understat, FPL, Kalshi, Polymarket) is
mapped onto it through `canonical()`.
"""

from __future__ import annotations

import re
import unicodedata

# canonical name -> (display name, 3-letter code, extra aliases)
TEAMS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "Arsenal": ("Arsenal", "ARS", ()),
    "Aston Villa": ("Aston Villa", "AVL", ()),
    "Birmingham": ("Birmingham", "BIR", ("birmingham city",)),
    "Blackburn": ("Blackburn", "BLB", ("blackburn rovers",)),
    "Blackpool": ("Blackpool", "BLP", ()),
    "Bolton": ("Bolton", "BOL", ("bolton wanderers",)),
    "Bournemouth": ("Bournemouth", "BOU", ("afc bournemouth",)),
    "Bradford": ("Bradford", "BRA", ("bradford city",)),
    "Brentford": ("Brentford", "BRE", ()),
    "Brighton": ("Brighton", "BHA", ("brighton and hove albion", "brighton hove albion", "brighton and hove")),
    "Bristol City": ("Bristol City", "BRC", ()),
    "Burnley": ("Burnley", "BUR", ()),
    "Cardiff": ("Cardiff", "CAR", ("cardiff city",)),
    "Charlton": ("Charlton", "CHA", ("charlton athletic",)),
    "Chelsea": ("Chelsea", "CHE", ()),
    "Coventry": ("Coventry", "COV", ("coventry city",)),
    "Crystal Palace": ("Crystal Palace", "CRY", ()),
    "Derby": ("Derby", "DER", ("derby county",)),
    "Everton": ("Everton", "EVE", ()),
    "Fulham": ("Fulham", "FUL", ()),
    "Huddersfield": ("Huddersfield", "HUD", ("huddersfield town",)),
    "Hull": ("Hull", "HUL", ("hull city",)),
    "Ipswich": ("Ipswich", "IPS", ("ipswich town",)),
    "Leeds": ("Leeds", "LEE", ("leeds united", "leeds utd")),
    "Leicester": ("Leicester", "LEI", ("leicester city",)),
    "Liverpool": ("Liverpool", "LIV", ()),
    "Luton": ("Luton", "LUT", ("luton town",)),
    "Man City": ("Man City", "MCI", ("manchester city",)),
    "Man United": ("Man United", "MUN", ("manchester united", "man utd", "manchester utd")),
    "Middlesbrough": ("Middlesbrough", "MID", ()),
    "Millwall": ("Millwall", "MIL", ()),
    "Newcastle": ("Newcastle", "NEW", ("newcastle united", "newcastle utd")),
    "Norwich": ("Norwich", "NOR", ("norwich city",)),
    "Nott'm Forest": ("Nott'm Forest", "NFO", ("nottingham forest", "nottm forest", "nottingham")),
    "Oxford": ("Oxford", "OXF", ("oxford united",)),
    "Plymouth": ("Plymouth", "PLY", ("plymouth argyle",)),
    "Portsmouth": ("Portsmouth", "POR", ()),
    "Preston": ("Preston", "PNE", ("preston north end",)),
    "QPR": ("QPR", "QPR", ("queens park rangers",)),
    "Reading": ("Reading", "REA", ()),
    "Sheffield United": ("Sheffield United", "SHU", ("sheffield utd", "sheff utd")),
    "Sheffield Weds": ("Sheffield Weds", "SHW", ("sheffield wednesday",)),
    "Southampton": ("Southampton", "SOU", ()),
    "Stoke": ("Stoke", "STK", ("stoke city",)),
    "Sunderland": ("Sunderland", "SUN", ()),
    "Swansea": ("Swansea", "SWA", ("swansea city",)),
    "Tottenham": ("Tottenham", "TOT", ("tottenham hotspur", "spurs")),
    "Watford": ("Watford", "WAT", ()),
    "West Brom": ("West Brom", "WBA", ("west bromwich albion", "west bromwich")),
    "West Ham": ("West Ham", "WHU", ("west ham united",)),
    "Wigan": ("Wigan", "WIG", ("wigan athletic",)),
    "Wolves": ("Wolves", "WOL", ("wolverhampton wanderers", "wolverhampton")),
    "Wrexham": ("Wrexham", "WRX", ()),
}

_DROP_TOKENS = {"fc", "afc", "the"}


def _normalise(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t not in _DROP_TOKENS]
    return " ".join(tokens)


_ALIASES: dict[str, str] = {}
for _canon, (_display, _code, _extra) in TEAMS.items():
    for _alias in (_canon, _display, *_extra):
        _ALIASES[_normalise(_alias)] = _canon


def canonical(name: str) -> str:
    """Map any source's team name onto the canonical name.

    Raises KeyError for unknown names so that a silent mismatch never drops a
    match from the data.
    """
    key = _normalise(name)
    if key in _ALIASES:
        return _ALIASES[key]
    raise KeyError(f"unknown team name: {name!r} (normalised {key!r})")


def code(name: str) -> str:
    return TEAMS[canonical(name)][1]


def display(name: str) -> str:
    return TEAMS[canonical(name)][0]
