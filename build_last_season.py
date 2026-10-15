"""Rebuild data/bundled/last_season_2025_26.parquet from real ESPN box scores.

Source: sportsdataverse/sportsdataverse-data GitHub releases (ESPN data scraped by the
hoopR project). Two files are used:
  - espn_nba_player_boxscores/player_box_2026.parquet  (every 2025-26 game, one row per player)
  - espn_nba_rosters/rosters_2027.parquet              (2026-27 rosters, for current teams)

Usage:
    python build_last_season.py                  # download from GitHub, then build
    python build_last_season.py --from-dir DIR   # build from files you already downloaded
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from schema import NBA_TEAMS, normalize_team

BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
FILES = {
    "box": "espn_nba_player_boxscores/player_box_2026.parquet",
    "rosters": "espn_nba_rosters/rosters_2027.parquet",
}
HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "raw" / "sportsdataverse"
OUT = HERE / "data" / "bundled" / "last_season_2025_26.parquet"
REGULAR_SEASON = 2


def download(dest_dir: Path) -> dict[str, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, rel in FILES.items():
        url = f"{BASE}/{rel}"
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        path = dest_dir / Path(rel).name
        path.write_bytes(resp.content)
        paths[key] = path
        print(f"downloaded {url} ({len(resp.content):,} bytes)")
    return paths


def aggregate(box: pd.DataFrame, rosters: pd.DataFrame) -> pd.DataFrame:
    """Per-game regular-season averages for every player who logged minutes."""
    teams = {normalize_team(t) for t in box["team_abbreviation"].unique()} & set(NBA_TEAMS)
    played = box[
        (box["season_type"] == REGULAR_SEASON)
        & (box["did_not_play"] != True)  # noqa: E712 - column can hold NaN
        & (box["minutes"].fillna(0) > 0)
        & box["athlete_id"].notna()
        # Drops All-Star exhibition rosters (STARS, STRIPES, WORLD).
        & box["team_abbreviation"].map(normalize_team).isin(teams)
    ].copy()
    played["athlete_id"] = played["athlete_id"].astype("int64")
    played = played.sort_values("game_date")

    g = played.groupby("athlete_id")
    out = pd.DataFrame({
        "name": g["athlete_display_name"].last(),
        "team_2025_26": g["team_abbreviation"].last().map(normalize_team),
        "pos_group": g["athlete_position_abbreviation"].last(),
        "GP": g["game_id"].nunique(),
        "MIN": g["minutes"].mean(),
        "PTS": g["points"].mean(),
        "REB": g["rebounds"].mean(),
        "AST": g["assists"].mean(),
        "3PM": g["three_point_field_goals_made"].mean(),
        "STL": g["steals"].mean(),
        "BLK": g["blocks"].mean(),
        "TO": g["turnovers"].mean(),
        "FGM": g["field_goals_made"].mean(),
        "FGA": g["field_goals_attempted"].mean(),
        "FTM": g["free_throws_made"].mean(),
        "FTA": g["free_throws_attempted"].mean(),
    }).reset_index().rename(columns={"athlete_id": "espn_id"})

    r = rosters.copy()
    r["athlete_id"] = r["athlete_id"].astype("int64")
    r = r.drop_duplicates("athlete_id", keep="last").set_index("athlete_id")
    out["team"] = out["espn_id"].map(r["team_abbreviation"]).map(
        lambda t: normalize_team(t) if isinstance(t, str) else "FA")
    # Prefer the newer roster position group when present.
    roster_pos = out["espn_id"].map(r["position_abbreviation"])
    out["pos_group"] = roster_pos.where(roster_pos.notna(), out["pos_group"])
    out["on_2026_27_roster"] = out["espn_id"].isin(r.index)
    return out.sort_values("MIN", ascending=False).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir", type=Path, help="directory holding player_box_2026.parquet and rosters_2027.parquet")
    args = ap.parse_args()
    if args.from_dir:
        paths = {k: args.from_dir / Path(v).name for k, v in FILES.items()}
    else:
        paths = download(RAW_DIR)

    box = pd.read_parquet(paths["box"])
    rosters = pd.read_parquet(paths["rosters"])
    df = aggregate(box, rosters)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    meta = {
        "source": "Last-season actuals 2025-26 (ESPN box scores via sportsdataverse)",
        "kind": "last_season_actuals",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "box_score_dates": [str(box["game_date"].min()), str(box["game_date"].max())],
        "players": int(len(df)),
        "on_2026_27_roster": int(df["on_2026_27_roster"].sum()),
        "urls": {k: f"{BASE}/{v}" for k, v in FILES.items()},
    }
    OUT.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {OUT} ({len(df)} players)")


if __name__ == "__main__":
    main()
