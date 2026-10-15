"""Every data source -> one normalized player DataFrame (see schema.SCHEMA_COLUMNS).

No Streamlit imports: app.py wraps these functions with st.cache_data. Each network fetch
saves its raw response to data/raw/ and a normalized snapshot to data/cache/, and falls back
to that snapshot when the network or the API fails.
"""

from __future__ import annotations

import difflib
import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from schema import FANTASY_POSITIONS, SCHEMA_COLUMNS, STAT_COLS, expand_positions, normalize_team

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
BUNDLED_LAST_SEASON = DATA_DIR / "bundled" / "last_season_2025_26.parquet"
OVERRIDES_PATH = DATA_DIR / "name_overrides.json"
USER_AGENT = "fantasy-draft-assistant/1.0 (personal use)"


@dataclass
class SourceMeta:
    source: str                 # human label shown in the UI
    kind: str                   # projections | last_season_actuals | synthetic | adp | metadata
    fetched_at: str = ""
    origin: str = ""            # URL or file name
    n_players: int = 0
    from_cache: bool = False
    warnings: list[str] = field(default_factory=list)


# ── Names ─────────────────────────────────────────────────────────────────────

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name) -> str:
    """'Nikola Jokić' -> 'nikola jokic'; 'Jaren Jackson Jr.' -> 'jaren jackson'; "De'Aaron Fox" -> 'deaaron fox'."""
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[.'`’]", "", s)          # dots/apostrophes vanish: "D.J." -> "dj"
    s = re.sub(r"[^a-z0-9]+", " ", s)     # hyphens, commas etc. -> spaces
    parts = [p for p in s.split() if p not in _SUFFIXES]
    return " ".join(parts)


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, str]:
    """Alias -> canonical, both already normalized. Keys starting with '_' are comments."""
    try:
        raw = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {normalize_name(k): normalize_name(v) for k, v in raw.items()
            if not str(k).startswith("_") and isinstance(v, str)}


def save_override(alias: str, canonical: str, path: Path = OVERRIDES_PATH) -> None:
    try:
        raw = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}
    raw[alias] = canonical
    Path(path).write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")


def name_key(name, overrides: dict[str, str] | None = None) -> str:
    k = normalize_name(name)
    return (overrides or {}).get(k, k)


# ── Schema ────────────────────────────────────────────────────────────────────

def finalize_schema(df: pd.DataFrame, source: str, overrides: dict[str, str] | None = None) -> pd.DataFrame:
    """Coerce any source frame into SCHEMA_COLUMNS with consistent types."""
    out = pd.DataFrame(index=range(len(df)))
    src = df.reset_index(drop=True)
    for c in SCHEMA_COLUMNS:
        out[c] = src[c] if c in src else None
    out["name"] = out["name"].astype(str).str.strip()
    out["name_key"] = [name_key(n, overrides) for n in out["name"]]
    out["team"] = [normalize_team(t) for t in out["team"]]
    out["positions"] = [expand_positions(p) for p in out["positions"]]
    out["positions_source"] = out["positions_source"].fillna("").astype(str)
    out["injury_status"] = out["injury_status"].fillna("").astype(str)
    for c in STAT_COLS + ["adp_espn", "adp_sleeper"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    for c in ["espn_id", "sleeper_id"]:
        out[c] = [_id_str(v) for v in out[c]]
    out["source"] = source
    out["player_id"] = [_id_str(pid) or _make_player_id(e, s, k) for pid, e, s, k in
                        zip(out["player_id"], out["espn_id"], out["sleeper_id"], out["name_key"])]
    out = _dedupe_ids(out)
    return out


def _id_str(v) -> str | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s or None


def _make_player_id(espn_id, sleeper_id, key) -> str:
    if espn_id:
        return f"espn:{espn_id}"
    if sleeper_id:
        return f"sl:{sleeper_id}"
    return f"nm:{key}"


def _dedupe_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Two rows can't share a player_id (e.g. two 'nm:' ids for namesakes): suffix the extras."""
    dup = df["player_id"].duplicated(keep=False)
    if dup.any():
        counts: dict[str, int] = {}
        ids = []
        for pid, is_dup in zip(df["player_id"], dup):
            if is_dup:
                counts[pid] = counts.get(pid, 0) + 1
                ids.append(pid if counts[pid] == 1 else f"{pid}#{counts[pid]}")
            else:
                ids.append(pid)
        df["player_id"] = ids
    return df


# ── Synthetic (tests / offline demo only) ─────────────────────────────────────

def synthetic_players(n: int = 300, seed: int = 17) -> pd.DataFrame:
    """Obviously fake players ('Test Player 017') with plausible stat shapes. Never real people."""
    rng = np.random.default_rng(seed)
    minutes = np.clip(rng.normal(24, 7, n), 8, 38)
    usage = rng.lognormal(0, 0.25, n)
    pos_choices = [["PG"], ["PG", "SG"], ["SG"], ["SG", "SF"], ["SF"], ["SF", "PF"], ["PF"], ["PF", "C"], ["C"]]
    pos_idx = rng.integers(0, len(pos_choices), n)
    big = np.isin(pos_idx, [6, 7, 8]).astype(float)
    guard = np.isin(pos_idx, [0, 1, 2]).astype(float)
    m = minutes / 36
    fga = m * rng.normal(12, 3, n).clip(3) * usage
    fg_pct = np.clip(rng.normal(0.46 + 0.06 * big, 0.04, n), 0.35, 0.68)
    fta = m * rng.normal(3.5, 1.5, n).clip(0.3) * usage
    ft_pct = np.clip(rng.normal(0.78 - 0.08 * big, 0.07, n), 0.45, 0.94)
    tpm = m * rng.normal(1.8 - 1.2 * big, 0.8, n).clip(0)
    fgm, ftm = fga * fg_pct, fta * ft_pct
    gp = rng.integers(35, 83, n).astype(float)
    teams = ["T%02d" % (i % 30 + 1) for i in range(n)]
    df = pd.DataFrame({
        "name": [f"Test Player {i:03d}" for i in range(1, n + 1)],
        "team": teams,
        "positions": [pos_choices[i] for i in pos_idx],
        "positions_source": "synthetic",
        "injury_status": np.where(rng.random(n) < 0.05, "OUT", ""),
        "GP": gp,
        "MIN": minutes,
        "PTS": 2 * fgm + tpm + ftm,
        "REB": m * rng.normal(4 + 5 * big, 1.5, n).clip(0.5),
        "AST": m * rng.normal(2.5 + 3.5 * guard, 1.2, n).clip(0.2),
        "3PM": tpm,
        "STL": m * rng.normal(1.0, 0.3, n).clip(0.1),
        "BLK": m * rng.normal(0.4 + 1.0 * big, 0.3, n).clip(0),
        "TO": m * rng.normal(1.6, 0.5, n).clip(0.2) * usage,
        "FGM": fgm, "FGA": fga, "FTM": ftm, "FTA": fta,
    })
    df["player_id"] = [f"test:{i:03d}" for i in range(1, n + 1)]
    value_proxy = df["PTS"] + 1.2 * df["REB"] + 1.5 * df["AST"] + 3 * (df["STL"] + df["BLK"])
    noisy = value_proxy.rank(ascending=False) + rng.normal(0, 8, n)
    df["adp_espn"] = pd.Series(noisy).rank().to_numpy()
    df.loc[df["adp_espn"] > 200, "adp_espn"] = np.nan
    return finalize_schema(df, "Synthetic test data")
