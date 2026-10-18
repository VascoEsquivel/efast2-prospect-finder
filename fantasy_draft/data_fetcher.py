"""Every data source -> one normalized player DataFrame (see schema.SCHEMA_COLUMNS).

No Streamlit imports: app.py wraps these functions with st.cache_data. Each network fetch
saves its raw response to data/raw/ and a normalized snapshot to data/cache/, and falls back
to that snapshot when the network or the API fails.
"""

from __future__ import annotations

import difflib
import io
import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

from schema import EXTRA_STATS, SCHEMA_COLUMNS, STAT_COLS, expand_positions, normalize_team

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
# Runtime files (raw responses, snapshots, uploads, draft state). Tests point this elsewhere.
RUNTIME_DIR = Path(os.environ.get("FANTASY_DATA_DIR") or DATA_DIR)
RAW_DIR = RUNTIME_DIR / "raw"
CACHE_DIR = RUNTIME_DIR / "cache"
UPLOAD_DIR = RAW_DIR / "uploads"
BUNDLED_LAST_SEASON = DATA_DIR / "bundled" / "last_season_2025_26.parquet"
BUNDLED_PROJECTIONS = DATA_DIR / "bundled" / "projections_2026_27.parquet"
OVERRIDES_PATH = DATA_DIR / "name_overrides.json"
USER_AGENT = "Mozilla/5.0 (fantasy-draft-assistant; personal use)"


class SourceError(RuntimeError):
    """A source could not be loaded and has no cached snapshot to fall back to."""


@dataclass
class SourceMeta:
    source: str                 # human label shown in the UI
    kind: str                   # projections | last_season_actuals | synthetic | adp | metadata
    fetched_at: str = ""
    origin: str = ""            # URL or file name
    n_players: int = 0
    from_cache: bool = False
    warnings: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def apply_overrides(df: pd.DataFrame, overrides: dict[str, str]) -> pd.DataFrame:
    """Recompute name_key with the current alias table (cheap; run after every load)."""
    out = df.copy()
    out["name_key"] = [name_key(n, overrides) for n in out["name"]]
    return out


# ── Schema ────────────────────────────────────────────────────────────────────

def finalize_schema(df: pd.DataFrame, source: str, overrides: dict[str, str] | None = None) -> pd.DataFrame:
    """Coerce any source frame into SCHEMA_COLUMNS with consistent types."""
    out = pd.DataFrame(index=range(len(df)))
    src = df.reset_index(drop=True)
    for c in SCHEMA_COLUMNS:
        out[c] = src[c] if c in src else None
    out = out[out["name"].notna()]  # before astype(str): pandas 2 would turn NaN into the name "nan"
    out["name"] = out["name"].astype(str).str.strip()
    out = out[out["name"].str.len() > 0].reset_index(drop=True)
    out["name_key"] = [name_key(n, overrides) for n in out["name"]]
    out["team"] = [normalize_team(t) for t in out["team"]]
    out["positions"] = [expand_positions(_as_list(p)) for p in out["positions"]]
    out["positions_source"] = out["positions_source"].fillna("").astype(str)
    out["injury_status"] = out["injury_status"].fillna("").astype(str).replace({"None": "", "nan": ""})
    out["proj_notes"] = out["proj_notes"].fillna("").astype(str).replace({"None": "", "nan": ""})
    for c in STAT_COLS + EXTRA_STATS + ["adp_espn", "adp_sleeper", "adp_market"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    for c in ["espn_id", "sleeper_id"]:
        out[c] = pd.Series([_id_str(v) for v in out[c]], dtype=object)
    out["source"] = source
    out["player_id"] = [_id_str(pid) or _make_player_id(e, s, k) for pid, e, s, k in
                        zip(out["player_id"], out["espn_id"], out["sleeper_id"], out["name_key"])]
    return _dedupe_ids(out)


def _as_list(v):
    """Parquet round-trips lists as numpy arrays; CSVs give strings. Normalize to list/str."""
    if isinstance(v, np.ndarray):
        return v.tolist()
    return v


def _id_str(v) -> str | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return None if s.lower() in {"", "none", "nan", "null"} else s


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


# ── Disk cache: raw responses + normalized snapshots ──────────────────────────

def save_raw(name: str, payload, keep: int = 3) -> Path:
    """Write a raw response to data/raw/<name>_<timestamp>.<ext>, keeping the newest `keep`."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ext = "json" if isinstance(payload, (dict, list)) else "txt"
    path = RAW_DIR / f"{name}_{datetime.now():%Y%m%d-%H%M%S}.{ext}"
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload))
    elif isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(str(payload))
    for old in sorted(RAW_DIR.glob(f"{name}_*.{ext}"))[:-keep]:
        old.unlink(missing_ok=True)
    return path


def latest_raw(name: str) -> Path | None:
    files = sorted(RAW_DIR.glob(f"{name}_*.json"))
    return files[-1] if files else None


def save_snapshot(key: str, df: pd.DataFrame, meta: SourceMeta) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f"{key}.parquet.tmp"
    df.to_parquet(tmp, index=False)
    tmp.replace(CACHE_DIR / f"{key}.parquet")
    (CACHE_DIR / f"{key}.meta.json").write_text(json.dumps(asdict(meta), indent=1))


def load_snapshot(key: str) -> tuple[pd.DataFrame, SourceMeta] | None:
    path = CACHE_DIR / f"{key}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if len(df) == 0 or "name" not in df.columns:
        return None  # unusable (e.g. written by an older version after an empty API answer)
    if "positions" in df:
        df["positions"] = [list(_as_list(p)) if p is not None else [] for p in df["positions"]]
    for col in ["espn_id", "sleeper_id"]:
        if col in df:  # pandas 3 reads missing ids back as NaN; keep them as None like fresh data
            df[col] = pd.Series([_id_str(v) for v in df[col]], index=df.index, dtype=object)
    try:
        meta = SourceMeta(**json.loads((CACHE_DIR / f"{key}.meta.json").read_text()))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        meta = SourceMeta(source=key, kind="unknown")
    return df, meta


def snapshot_age_hours(key: str) -> float | None:
    path = CACHE_DIR / f"{key}.parquet"
    return (time.time() - path.stat().st_mtime) / 3600 if path.exists() else None


def fetch_with_fallback(key: str, fetch: Callable[[], tuple[pd.DataFrame, SourceMeta]]) -> tuple[pd.DataFrame, SourceMeta]:
    """Run `fetch`; on success snapshot it, on any failure return the last snapshot (flagged)."""
    try:
        df, meta = fetch()
        if df is None or len(df) == 0 or "name" not in df.columns:
            # An API that answers 200 with nothing usable must not replace a good snapshot.
            raise SourceError("the source answered but returned no players")
        save_snapshot(key, df, meta)
        return df, meta
    except Exception as exc:  # noqa: BLE001 - any API breakage should fall back, not crash the draft
        snap = load_snapshot(key)
        if snap is None:
            raise SourceError(f"{key}: {type(exc).__name__}: {exc} (and no cached snapshot yet)") from exc
        df, meta = snap
        meta.from_cache = True
        meta.warnings = [f"live fetch failed ({type(exc).__name__}: {str(exc)[:160]}); "
                         f"using cached snapshot from {meta.fetched_at}"] + list(meta.warnings)
        return df, meta


def _get(url: str, timeout: float, headers: dict | None = None, params: dict | None = None) -> requests.Response:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    resp = requests.get(url, headers=h, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp


# ── Last season: bundled snapshot (real 2025-26 actuals) ──────────────────────

LAST_SEASON_LABEL = "LAST-SEASON ACTUALS 2025-26 (not projections)"


def load_last_season(path: Path = BUNDLED_LAST_SEASON) -> tuple[pd.DataFrame, SourceMeta]:
    raw = pd.read_parquet(path)
    try:
        info = json.loads(path.with_suffix(".meta.json").read_text())
    except FileNotFoundError:
        info = {}
    df = raw.rename(columns={"pos_group": "positions"}).copy()
    df["positions_source"] = "approx (ESPN G/F/C)"
    df["injury_status"] = np.where(df.get("on_2026_27_roster", True), "", "no 26-27 team (Aug)")
    out = finalize_schema(df, LAST_SEASON_LABEL)
    meta = SourceMeta(source=LAST_SEASON_LABEL, kind="last_season_actuals",
                      fetched_at=info.get("built_at", ""), origin="ESPN box scores via sportsdataverse "
                      f"({', '.join(info.get('box_score_dates', []))})", n_players=len(out),
                      warnings=["Positions are approximate (ESPN lists only G/F/C). Load Sleeper or ESPN for real eligibility."])
    return out, meta


PROJECTIONS_LABEL = "2026-27 PROJECTIONS (bundled model)"


def load_projections(path: Path = BUNDLED_PROJECTIONS) -> tuple[pd.DataFrame, SourceMeta]:
    """Bundled 2026-27 projections from build_projections.py (3 seasons of box scores + 2026-27 rosters)."""
    raw = pd.read_parquet(path)
    try:
        info = json.loads(path.with_suffix(".meta.json").read_text())
    except FileNotFoundError:
        info = {}
    df = raw.rename(columns={"pos_group": "positions"}).copy()
    df["positions_source"] = "approx (ESPN G/F/C)"
    df["injury_status"] = np.where(df.get("on_2026_27_roster", True), "", "unsigned")
    out = finalize_schema(df, PROJECTIONS_LABEL)
    n_blank = int(out["MIN"].isna().sum())
    seasons = ", ".join(info.get("box_score_seasons", []))
    meta = SourceMeta(source=PROJECTIONS_LABEL, kind="projections", fetched_at=info.get("built_at", ""),
                      origin=f"ESPN box scores {seasons} + 2026-27 rosters via sportsdataverse", n_players=len(out),
                      warnings=["Positions are approximate (ESPN lists only G/F/C). Load Sleeper or ESPN for real eligibility.",
                                f"{n_blank} rookies / players with no NBA games are listed without stats "
                                "(not projected, so they rank last)."])
    return out, meta


def fetch_nba_api_last_season(season: str = "2025-26", timeout: int = 30) -> tuple[pd.DataFrame, SourceMeta]:
    """Real per-game regular-season stats from stats.nba.com via the nba_api package."""
    from nba_api.stats.endpoints import leaguedashplayerstats  # optional dependency

    endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season, per_mode_detailed="PerGame", season_type_all_star="Regular Season", timeout=timeout)
    save_raw("nba_api_leaguedashplayerstats", endpoint.get_dict())
    df = parse_nba_api_frame(endpoint.get_data_frames()[0])
    label = f"LAST-SEASON ACTUALS {season} via nba_api (not projections)"
    return finalize_schema(df, label), SourceMeta(source=label, kind="last_season_actuals", fetched_at=_now(),
                                                  origin="stats.nba.com LeagueDashPlayerStats", n_players=len(df))


def parse_nba_api_frame(raw: pd.DataFrame) -> pd.DataFrame:
    cols = {"PLAYER_NAME": "name", "TEAM_ABBREVIATION": "team", "GP": "GP", "MIN": "MIN", "PTS": "PTS",
            "REB": "REB", "AST": "AST", "FG3M": "3PM", "STL": "STL", "BLK": "BLK", "TOV": "TO",
            "FGM": "FGM", "FGA": "FGA", "FTM": "FTM", "FTA": "FTA"}
    df = raw[[c for c in cols if c in raw]].rename(columns=cols)
    df["player_id"] = "nba:" + raw["PLAYER_ID"].astype(str)
    df["positions_source"] = ""
    return df


# ── ESPN fantasy (public league defaults) ─────────────────────────────────────

ESPN_HOSTS = ["https://lm-api-reads.fantasy.espn.com", "https://fantasy.espn.com"]
ESPN_PATH = "/apis/v3/games/fba/seasons/{season}/segments/0/leaguedefaults/3"
# From espn-api (cwendt94/espn-api) basketball/constant.py; self-checked against box scores in the Data tab.
ESPN_STAT_IDS = {"0": "PTS", "1": "BLK", "2": "STL", "3": "AST", "6": "REB", "11": "TO", "13": "FGM",
                 "14": "FGA", "15": "FTM", "16": "FTA", "17": "3PM", "40": "MIN", "42": "GP"}
ESPN_STAT_NAMES = {**ESPN_STAT_IDS, "4": "OREB", "5": "DREB", "9": "PF", "18": "3PA", "19": "FG%",
                   "20": "FT%", "21": "3P%", "28": "MPG", "41": "GS"}
ESPN_POSITIONS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"}
ESPN_TEAMS = {0: "FA", 1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GSW",
              10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK",
              19: "ORL", 20: "PHI", 21: "PHX", 22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA", 27: "WAS",
              28: "TOR", 29: "MEM", 30: "CHA"}


def espn_filter(season: int = 2027, limit: int = 1000) -> dict:
    return {"players": {
        "limit": limit,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
        "filterStatsForTopScoringPeriodIds": {
            "value": 2, "additionalValue": [f"00{season}", f"10{season}", f"00{season - 1}"]},
    }}


def fetch_espn(season: int = 2027, limit: int = 1000, timeout: float = 15) -> tuple[pd.DataFrame, SourceMeta]:
    errors = []
    for host in ESPN_HOSTS:
        url = host + ESPN_PATH.format(season=season)
        try:
            resp = _get(url, timeout, headers={"x-fantasy-filter": json.dumps(espn_filter(season, limit))},
                        params={"view": "kona_player_info"})
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{host}: {exc}")
            continue
        save_raw("espn_kona_player_info", data)
        df, warnings = parse_espn(data, season)
        if len(df) == 0:
            errors.append(f"{host}: answered, but with no {season} projections (not published yet, or the format changed)")
            continue
        if len(df) < 50:
            warnings.append(f"only {len(df)} players have a {season} projection; ESPN may not have published them yet")
        label = f"ESPN projections {season - 1}-{str(season)[2:]}"
        return df, SourceMeta(source=label, kind="projections", fetched_at=_now(), origin=url,
                              n_players=len(df), warnings=warnings)
    raise SourceError("ESPN: " + " | ".join(errors))


def espn_stat_entry(entries: list[dict], season: int, kind: str = "projection") -> dict | None:
    """Pick the full-season projection ('10'+season) or actuals ('00'+season) entry."""
    want_id = ("10" if kind == "projection" else "00") + str(season)
    for e in entries or []:
        if str(e.get("id")) == want_id:
            return e
    source_id = 1 if kind == "projection" else 0
    for e in entries or []:
        if e.get("seasonId") == season and e.get("statSourceId") == source_id and e.get("statSplitTypeId") == 0:
            return e
    return None


def espn_per_game(entry: dict, stat_map: dict[str, str] = ESPN_STAT_IDS) -> dict[str, float]:
    """Per-game values for one stat entry: averageStats when present, else totals / GP."""
    totals = {str(k): v for k, v in (entry.get("stats") or {}).items()}
    avgs = {str(k): v for k, v in (entry.get("averageStats") or {}).items()}
    gp = totals.get("42") or 0
    out: dict[str, float] = {}
    for sid, col in stat_map.items():
        if col == "GP":
            out["GP"] = float(gp) if gp else np.nan
        elif sid in avgs:
            out[col] = float(avgs[sid])
        elif sid in totals and gp:
            out[col] = float(totals[sid]) / float(gp)
    if "MIN" not in out and "28" in avgs:
        out["MIN"] = float(avgs["28"])
    return out


def parse_espn(data, season: int = 2027, stat_map: dict[str, str] = ESPN_STAT_IDS) -> tuple[pd.DataFrame, list[str]]:
    entries = data.get("players", []) if isinstance(data, dict) else list(data or [])
    rows, no_proj = [], 0
    for entry in entries:
        pl = entry.get("player", entry)
        proj = espn_stat_entry(pl.get("stats", []), season, "projection")
        if proj is None:
            no_proj += 1
            continue
        slots = [ESPN_POSITIONS[s] for s in pl.get("eligibleSlots", []) if s in ESPN_POSITIONS]
        if not slots and isinstance(pl.get("defaultPositionId"), int):
            slots = [ESPN_POSITIONS.get(pl["defaultPositionId"] - 1, "")]
        adp = (pl.get("ownership") or {}).get("averageDraftPosition")
        injury = pl.get("injuryStatus") or ""
        rows.append({
            "name": pl.get("fullName", ""), "espn_id": pl.get("id", entry.get("id")),
            "team": ESPN_TEAMS.get(pl.get("proTeamId"), "FA"), "positions": slots,
            "positions_source": "espn", "injury_status": "" if injury in ("ACTIVE", "NORMAL") else injury,
            "adp_espn": adp if adp and adp > 0 else np.nan, **espn_per_game(proj, stat_map),
        })
    warnings = [f"{no_proj} ESPN players had no {season} projection entry and were skipped"] if no_proj else []
    df = pd.DataFrame(rows)
    return (finalize_schema(df, f"ESPN projections {season}") if len(df) else df), warnings


def verify_espn_stat_map(data, reference: pd.DataFrame, season: int = 2026, min_gp: int = 40,
                         tolerance: float = 0.05) -> pd.DataFrame:
    """Check ESPN stat ids against known per-game actuals for the same ESPN player ids.

    Compares ESPN's own `00<season>` actuals with `reference` (the bundled box-score averages).
    Returns one row per mapped stat id with the median relative error and the best-matching id.
    """
    entries = data.get("players", []) if isinstance(data, dict) else list(data or [])
    ref = reference.dropna(subset=["espn_id"]).set_index("espn_id")
    ref = ref[ref["GP"] >= min_gp]
    espn_rows = {}
    for entry in entries:
        pl = entry.get("player", entry)
        pid = _id_str(pl.get("id", entry.get("id")))
        actual = espn_stat_entry(pl.get("stats", []), season, "actual")
        if pid in ref.index and actual:
            espn_rows[pid] = espn_per_game(actual, {k: k for k in ESPN_STAT_NAMES if k != "42"})
    espn = pd.DataFrame.from_dict(espn_rows, orient="index")
    out = []
    for sid, col in ESPN_STAT_IDS.items():
        if col == "GP":
            continue
        row = {"stat_id": sid, "mapped_to": col, "players_compared": len(espn)}
        if len(espn) == 0 or sid not in espn:
            out.append({**row, "median_rel_error": np.nan, "status": "no data", "best_match_id": ""})
            continue
        target = ref.loc[espn.index, col].astype(float)
        errs = {}
        for cand in espn.columns:
            vals = espn[cand].astype(float)
            ok = target.abs() > 0.05
            if ok.sum() >= 5:
                errs[cand] = float(((vals[ok] - target[ok]).abs() / target[ok].abs()).median())
        err = errs.get(sid, np.nan)
        best = min(errs, key=errs.get) if errs else ""
        status = "✅ verified" if err <= tolerance else "❌ mismatch"
        out.append({**row, "median_rel_error": err, "status": status,
                    "best_match_id": best if best != sid else ""})
    return pd.DataFrame(out)


# ── Sleeper ───────────────────────────────────────────────────────────────────

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nba"
SLEEPER_DRAFT_URL = "https://api.sleeper.app/v1/draft/{draft_id}"


def fetch_sleeper_players(max_age_hours: float = 24, force: bool = False, timeout: float = 30) -> tuple[pd.DataFrame, SourceMeta]:
    """Sleeper's NBA player dump. Hit the network at most once per `max_age_hours`."""
    age = snapshot_age_hours("sleeper_players")
    if not force and age is not None and age < max_age_hours:
        df, meta = load_snapshot("sleeper_players")
        return df, meta

    def fetch():
        data = _get(SLEEPER_PLAYERS_URL, timeout).json()
        save_raw("sleeper_players_nba", data, keep=1)
        df = parse_sleeper_players(data)
        return df, SourceMeta(source="Sleeper player metadata", kind="metadata", fetched_at=_now(),
                              origin=SLEEPER_PLAYERS_URL, n_players=len(df))
    return fetch_with_fallback("sleeper_players", fetch)


def parse_sleeper_players(data: dict) -> pd.DataFrame:
    rows = []
    for sid, p in (data or {}).items():
        if not isinstance(p, dict) or not (p.get("active") or p.get("team")):
            continue
        name = p.get("full_name") or " ".join(x for x in [p.get("first_name"), p.get("last_name")] if x)
        rows.append({
            "name": name, "sleeper_id": p.get("player_id") or sid, "espn_id": p.get("espn_id"),
            "team": p.get("team"), "positions": p.get("fantasy_positions") or p.get("position") or [],
            "positions_source": "sleeper", "injury_status": p.get("injury_status") or "",
        })
    df = pd.DataFrame(rows, columns=["name", "sleeper_id", "espn_id", "team", "positions", "positions_source",
                                     "injury_status"])
    return finalize_schema(df, "Sleeper player metadata")


def fetch_sleeper_draft(draft_id: str, timeout: float = 8) -> dict:
    return _get(SLEEPER_DRAFT_URL.format(draft_id=draft_id), timeout).json()


def fetch_sleeper_draft_picks(draft_id: str, timeout: float = 4) -> list[dict]:
    data = _get(SLEEPER_DRAFT_URL.format(draft_id=draft_id) + "/picks", timeout).json()
    return data if isinstance(data, list) else []


# ── Market rank: FantasyPros ECR snapshot (optional, labeled) ─────────────────

ECR_URL = "https://raw.githubusercontent.com/combosm/fantasy/main/data/inputs/external_rankings.csv"


def fetch_market_rank_ecr(timeout: float = 15) -> tuple[pd.DataFrame, SourceMeta]:
    """FantasyPros ESPN-points expert consensus rank, as captured in the combosm/fantasy repo."""
    def fetch():
        text = _get(ECR_URL, timeout, headers={"Accept": "text/csv"}).text
        save_raw("fantasypros_ecr", text, keep=1)
        raw = pd.read_csv(io.StringIO(text))
        df = pd.DataFrame({"name": raw["player_name"], "adp": raw["rank"]})
        published = ", ".join(sorted(raw["published_at"].astype(str).unique()))
        label = f"FantasyPros ECR, ESPN points ({published}) - expert rank, not ADP"
        return finalize_adp(df), SourceMeta(source=label, kind="adp", fetched_at=_now(), origin=ECR_URL,
                                            n_players=len(df))
    return fetch_with_fallback("market_rank_ecr", fetch)


# ── CSV import ────────────────────────────────────────────────────────────────

CSV_ALIASES = {
    "name": ["name", "player", "player name", "player_name", "full name", "playername", "players"],
    "team": ["team", "tm", "nba team", "pro team", "team_abbreviation", "teams"],
    "positions": ["positions", "position", "pos", "eligible positions", "elig", "eligibility"],
    "injury_status": ["injury", "injury status", "inj", "status", "health"],
    "GP": ["gp", "g", "games", "games played", "proj gp", "gms"],
    "MIN": ["min", "mpg", "minutes", "mins", "min/g", "m/g", "minutes per game"],
    "PTS": ["pts", "points", "ppg", "pts/g"],
    "REB": ["reb", "rebounds", "trb", "rpg", "treb", "reb/g"],
    "AST": ["ast", "assists", "apg", "ast/g"],
    "3PM": ["3pm", "3ptm", "fg3m", "3p", "threes", "3pt", "3-pt made", "tpm", "3s", "3pt made", "3pg", "3pm/g"],
    "STL": ["stl", "steals", "spg", "stl/g"],
    "BLK": ["blk", "blocks", "bpg", "blk/g"],
    "TO": ["to", "tov", "turnovers", "topg", "to/g", "tov/g"],
    "FGM": ["fgm", "fg made", "fgm/g", "fg"],  # Basketball-Reference style: FG = makes
    "FGA": ["fga", "fg att", "fg attempts", "fga/g"],
    "FTM": ["ftm", "ft made", "ftm/g", "ft"],
    "FTA": ["fta", "ft att", "ft attempts", "fta/g"],
    "FG%": ["fg%", "fg pct", "fg_pct", "fgpct", "field goal %"],
    "FT%": ["ft%", "ft pct", "ft_pct", "ftpct", "free throw %"],
    "adp": ["adp", "avg pick", "average draft position", "avg. pick", "espn adp", "yahoo adp", "sleeper adp",
            "avg", "adp overall", "overall adp"],
    "espn_id": ["espn_id", "espn id", "espnid"],
    "sleeper_id": ["sleeper_id", "sleeper id", "sleeperid"],
}
PROJECTION_TARGETS = ["name", "team", "positions", "injury_status", "GP", "MIN", "PTS", "REB", "AST", "3PM",
                      "STL", "BLK", "TO", "FGM", "FGA", "FTM", "FTA", "FG%", "FT%", "adp", "espn_id", "sleeper_id"]
ADP_TARGETS = ["name", "adp", "team", "espn_id", "sleeper_id"]


def _norm_header(h) -> str:
    return re.sub(r"\s+", " ", str(h).replace("﻿", "").strip().lower())


def auto_map_columns(columns, targets=PROJECTION_TARGETS) -> dict[str, str | None]:
    """Guess which CSV column feeds each target field (exact alias match, first wins)."""
    normed = {_norm_header(c): c for c in columns}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for t in targets:
        mapping[t] = None
        for alias in CSV_ALIASES.get(t, [t.lower()]):
            col = normed.get(alias)
            if col is not None and col not in used:
                mapping[t] = col
                used.add(col)
                break
    # Second pass for the two columns every file needs: accept headers like "ADP (Sleeper)" or "Player Name".
    for t, needles in {"name": ("player", "name"), "adp": ("adp", "avg pick", "average pick")}.items():
        if t in mapping and mapping[t] is None:
            col = next((c for n, c in normed.items() if c not in used and any(x in n for x in needles)), None)
            if col is not None:
                mapping[t] = col
                used.add(col)
    return mapping


def read_csv_any(data: bytes | str) -> pd.DataFrame:
    """Read a CSV/TSV with an unknown delimiter and optional BOM."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    return pd.read_csv(io.StringIO(text), sep=None, engine="python")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.replace("%", "").str.strip(), errors="coerce")


def _pct(s: pd.Series) -> pd.Series:
    v = _num(s)
    return np.where(v > 1.5, v / 100, v)  # '47.5' or '47.5%' -> 0.475


def load_projection_csv(raw: pd.DataFrame, mapping: dict[str, str | None], season_totals: bool = False,
                        label: str = "Projection CSV") -> tuple[pd.DataFrame, SourceMeta]:
    """Map an arbitrary projections CSV into the schema. FG%/FT% only ever become makes."""
    warnings: list[str] = []
    if not mapping.get("name"):
        raise ValueError("map a column to 'name' first")
    get = lambda t: raw[mapping[t]] if mapping.get(t) else None  # noqa: E731
    df = pd.DataFrame({"name": get("name")})
    for t in ["team", "positions", "injury_status", "espn_id", "sleeper_id"]:
        if get(t) is not None:
            df[t] = get(t)
    for t in ["GP", "MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "FGM", "FGA", "FTM", "FTA"]:
        df[t] = _num(get(t)) if get(t) is not None else np.nan
    for pct, (m, a) in {"FG%": ("FGM", "FGA"), "FT%": ("FTM", "FTA")}.items():
        p = get(pct)
        if p is not None and df[m].isna().all() and df[a].notna().any():
            df[m] = _pct(p) * df[a]
            warnings.append(f"{m} derived as {pct} x {a}")
        elif p is not None and df[a].isna().all() and df[m].notna().any():
            with np.errstate(divide="ignore", invalid="ignore"):
                df[a] = np.where(_pct(p) > 0, df[m] / _pct(p), np.nan)
            warnings.append(f"{a} derived as {m} / {pct}")
        if df[m].isna().all() or df[a].isna().all():
            warnings.append(f"{pct} needs {m} and {a} (or {pct} plus one of them); it will score 0 for everyone")
        too_many = int((df[m] > df[a] + 1e-9).sum())
        if too_many:
            warnings.append(f"{too_many} rows have more makes than attempts for {pct[:-1]}: check the column mapping")
    if season_totals:
        gp = df["GP"]
        if gp.isna().all():
            raise ValueError("season-total CSVs need a GP column to convert to per-game")
        for t in ["MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "FGM", "FGA", "FTM", "FTA"]:
            df[t] = df[t] / gp.where(gp > 0)
    if df["MIN"].isna().all():
        warnings.append("no minutes column: the replacement pool will be seeded in file order")
    if get("adp") is not None:
        df["adp_market"] = _num(get("adp"))
    df["positions_source"] = "csv" if get("positions") is not None else ""
    out = finalize_schema(df, label)
    return out, SourceMeta(source=label, kind="projections", fetched_at=_now(), origin=label,
                           n_players=len(out), warnings=warnings)


def finalize_adp(df: pd.DataFrame, overrides: dict[str, str] | None = None) -> pd.DataFrame:
    df = df[df["name"].notna()]
    out = pd.DataFrame({"name": df["name"].astype(str).str.strip(), "adp": _num(df["adp"])})
    for c in ["team", "espn_id", "sleeper_id"]:
        out[c] = df[c] if c in df else None
    out["team"] = [normalize_team(t) if t is not None else "FA" for t in out["team"]]
    out["espn_id"] = pd.Series([_id_str(v) for v in out["espn_id"]], dtype=object)
    out["sleeper_id"] = pd.Series([_id_str(v) for v in out["sleeper_id"]], dtype=object)
    out["name_key"] = [name_key(n, overrides) for n in out["name"]]
    return out[out["adp"].notna() & (out["name"].str.len() > 0)].reset_index(drop=True)


def load_adp_csv(raw: pd.DataFrame, mapping: dict[str, str | None], label: str = "ADP CSV") -> tuple[pd.DataFrame, SourceMeta]:
    if not mapping.get("name") or not mapping.get("adp"):
        raise ValueError("map columns to 'name' and 'adp' first")
    df = pd.DataFrame({t: raw[c] for t, c in mapping.items() if c})
    out = finalize_adp(df)
    return out, SourceMeta(source=label, kind="adp", fetched_at=_now(), origin=label, n_players=len(out))


def save_upload(kind: str, data: bytes) -> Path:
    """Keep a copy of an uploaded CSV so the app can reload it after a browser refresh."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{kind}.csv"
    path.write_bytes(data)
    return path


# ── Matching sources together ─────────────────────────────────────────────────

@dataclass
class MatchReport:
    source: str
    matched: int
    total: int
    unmatched_other: pd.DataFrame  # rows of the other source with no match in the base dataset
    unmatched_base: pd.DataFrame   # base players the other source doesn't know

    @property
    def n_unmatched(self) -> int:
        return len(self.unmatched_other)


def match_players(base: pd.DataFrame, other: pd.DataFrame) -> pd.Series:
    """For each row of `other`, the matching base index (or NaN). IDs first, then name_key + team."""
    result = pd.Series(np.nan, index=other.index, dtype=float)
    for col in ["espn_id", "sleeper_id"]:
        if col in base and col in other:
            lookup = {k: i for i, v in base[col].items() if (k := _id_str(v))}
            hits = other[col].map(lambda v: lookup.get(_id_str(v)) if _id_str(v) else None)
            result = result.where(result.notna(), pd.to_numeric(hits, errors="coerce"))
    by_key: dict[str, list[int]] = {}
    for i, k in base["name_key"].items():
        by_key.setdefault(k, []).append(i)
    taken = set(result.dropna().astype(int))
    # Pass 1: same name AND same team, so namesakes pair up regardless of row order.
    if "team" in other and "team" in base:
        for j in other.index[result.isna()]:
            same = [i for i in by_key.get(other.at[j, "name_key"], [])
                    if i not in taken and base.at[i, "team"] == other.at[j, "team"] and other.at[j, "team"] != "FA"]
            if len(same) == 1:
                result.at[j] = same[0]
                taken.add(same[0])
    # Pass 2: a unique remaining name.
    for j in other.index[result.isna()]:
        cands = [i for i in by_key.get(other.at[j, "name_key"], []) if i not in taken]
        if len(cands) == 1:
            result.at[j] = cands[0]
            taken.add(cands[0])
    return result


def suggest_names(keys: list[str], base: pd.DataFrame, n: int = 3) -> list[str]:
    pool = list(base["name_key"])
    names = dict(zip(base["name_key"], base["name"]))
    return [", ".join(names[k] for k in difflib.get_close_matches(key, pool, n=n, cutoff=0.75)) for key in keys]


POSITION_PRIORITY = {"csv": 5, "synthetic": 5, "sleeper": 4, "espn": 3}


def _pos_rank(src: str) -> int:
    return POSITION_PRIORITY.get(src, 1 if str(src).startswith("approx") else 0)


def enrich(base: pd.DataFrame, other: pd.DataFrame, source: str, adp_target: str | None = None,
           live_metadata: bool = False) -> tuple[pd.DataFrame, MatchReport]:
    """Fill the base dataset from another source matched by id / name.

    Always: missing espn_id/sleeper_id, and positions when the other source is more reliable.
    live_metadata (Sleeper, ESPN): also take its team and injury status, which are newer.
    adp_target: copy the other source's ADP ('adp' or that same column) into this column.
    """
    out = base.copy()
    m = match_players(out, other)
    adp_col = "adp" if "adp" in other else adp_target
    for j, i in m.dropna().astype(int).items():
        row = other.loc[j]
        for col in ["espn_id", "sleeper_id"]:
            if col in other and _id_str(row[col]) and not _id_str(out.at[i, col]):
                out.at[i, col] = _id_str(row[col])
        if "positions" in other and len(row["positions"]) and \
                _pos_rank(row["positions_source"]) > _pos_rank(out.at[i, "positions_source"]):
            out.at[i, "positions"] = list(row["positions"])
            out.at[i, "positions_source"] = row["positions_source"]
        if live_metadata:
            if row.get("injury_status") is not None:
                out.at[i, "injury_status"] = row["injury_status"]
            if row.get("team") and row["team"] != "FA":
                out.at[i, "team"] = row["team"]
        if adp_target and adp_col in other and pd.notna(row[adp_col]):
            out.at[i, adp_target] = row[adp_col]
    miss = other[m.isna()]
    cols = [c for c in ["name", "team", "adp", adp_target] if c and c in miss]
    unmatched_other = miss[list(dict.fromkeys(cols))].copy()
    unmatched_other["suggestions"] = suggest_names(list(miss["name_key"]), out)
    matched_base = set(m.dropna().astype(int))
    unmatched_base = out.loc[[i for i in out.index if i not in matched_base], ["player_id", "name", "team"]]
    report = MatchReport(source=source, matched=int(m.notna().sum()), total=len(other),
                         unmatched_other=unmatched_other.reset_index(drop=True),
                         unmatched_base=unmatched_base.reset_index(drop=True))
    return out, report


def player_lookup(df: pd.DataFrame) -> tuple[dict[str, dict], dict[str, dict]]:
    """(by player_id, by name_key) dicts of player records, for resolving saved picks."""
    recs = df[["player_id", "name", "name_key", "team", "positions", "sleeper_id", "espn_id"]].to_dict("records")
    by_id = {r["player_id"]: r for r in recs}
    by_key: dict[str, dict] = {}
    for r in recs:
        by_key.setdefault(r["name_key"], r)
    return by_id, by_key


# ── Synthetic (tests / offline demo only) ─────────────────────────────────────

SYNTHETIC_LABEL = "Synthetic test data (fake players)"


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
    df = pd.DataFrame({
        "name": [f"Test Player {i:03d}" for i in range(1, n + 1)],
        "team": "FA",
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
    return finalize_schema(df, SYNTHETIC_LABEL)


def load_synthetic() -> tuple[pd.DataFrame, SourceMeta]:
    df = synthetic_players()
    return df, SourceMeta(source=SYNTHETIC_LABEL, kind="synthetic", fetched_at=_now(), origin="generated",
                          n_players=len(df), warnings=["Fake players for testing. Not real stats."])


