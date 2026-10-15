"""Shared constants for the normalized player table. No Streamlit, no I/O."""

from __future__ import annotations

# Per-game counting stats, plus makes/attempts that percentage categories derive from.
STAT_COLS = ["GP", "MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "FGM", "FGA", "FTM", "FTA"]

SCHEMA_COLUMNS = [
    "player_id", "name", "name_key", "team", "positions", "positions_source", "injury_status",
    *STAT_COLS,
    "adp_espn", "adp_sleeper", "espn_id", "sleeper_id", "source",
    "adp_market",  # optional extra: any other market rank (e.g. an expert-consensus CSV)
]
ADP_COLUMNS = {"ESPN ADP": "adp_espn", "Sleeper ADP": "adp_sleeper", "Market rank": "adp_market"}

FANTASY_POSITIONS = ["PG", "SG", "SF", "PF", "C"]

# Coarse ESPN/NBA position groups -> fantasy positions. Only used when no source gives real eligibility.
COARSE_POSITION_MAP = {
    "G": ["PG", "SG"],
    "F": ["SF", "PF"],
    "C": ["C"],
    "G-F": ["SG", "SF"],
    "F-G": ["SG", "SF"],
    "F-C": ["PF", "C"],
    "C-F": ["PF", "C"],
    "PG": ["PG"],
    "SG": ["SG"],
    "SF": ["SF"],
    "PF": ["PF"],
}

NBA_TEAMS = [
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
]

TEAM_ALIASES = {
    "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "NOR": "NOP", "UTAH": "UTA", "UTH": "UTA",
    "WSH": "WAS", "PHO": "PHX", "PHL": "PHI", "BRK": "BKN", "BKLYN": "BKN", "CHO": "CHA", "CHH": "CHA",
    "NJN": "BKN", "SEA": "OKC",
}


def normalize_team(abbr) -> str:
    """Map any source's team abbreviation to one canonical 3-letter code. Unknown/empty -> 'FA'."""
    if abbr is None:
        return "FA"
    s = str(abbr).strip().upper()
    if not s or s in {"NAN", "NONE", "FA", "FREE AGENT"}:
        return "FA"
    s = TEAM_ALIASES.get(s, s)
    return s if s in NBA_TEAMS else "FA"


def expand_positions(raw) -> list[str]:
    """Turn 'PG/SG', 'G', ['PF','C'], 'F-C' etc. into an ordered list of fantasy positions.

    Exact positions win: ['SG', 'G'] -> ['SG'] (a generic 'G' slot tag must not add PG).
    Coarse groups are only expanded when no exact position is given.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p for chunk in raw.replace(",", "/").replace(";", "/").split("/") for p in [chunk.strip().upper()] if p]
    else:
        try:
            parts = [str(p).strip().upper() for p in raw if str(p).strip()]
        except TypeError:
            return []
    exact = [p for p in parts if p in FANTASY_POSITIONS]
    if exact:
        return [p for p in FANTASY_POSITIONS if p in exact]
    out: list[str] = []
    for p in parts:
        for fp in COARSE_POSITION_MAP.get(p, [p] if p in FANTASY_POSITIONS else []):
            if fp not in out:
                out.append(fp)
    return [p for p in FANTASY_POSITIONS if p in out]
