"""Rebuild data/bundled/projections_2026_27.parquet: 2026-27 projections from real box scores.

Model: projections.py (multi-season baseline + 2026-27 rosters: minutes, ball-share/usage budget,
team style). Source data: sportsdataverse/sportsdataverse-data GitHub releases (ESPN data):
  - player_box / team_box for 2023-24, 2024-25 and 2025-26 (every regular-season game)
  - rosters_2027 (2026-27 rosters: teams after the off-season moves, birth dates)
  - draft_2026 (rookies: listed so they can be drafted, but never given invented stats)

Usage:
    python build_projections.py                  # download from GitHub, then build
    python build_projections.py --from-dir DIR   # build from files you already downloaded
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import projections as pj
from data_fetcher import normalize_name
from schema import normalize_team

BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
SEASONS = (2026, 2025, 2024)    # season end years, most recent first
FILES = {f"player_box_{y}": f"espn_nba_player_boxscores/player_box_{y}.parquet" for y in SEASONS}
FILES |= {f"team_box_{y}": f"espn_nba_team_boxscores/team_box_{y}.parquet" for y in SEASONS}
FILES |= {"rosters": "espn_nba_rosters/rosters_2027.parquet", "draft": "espn_nba_draft/draft_2026.parquet"}
HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "raw" / "sportsdataverse"
OUT = HERE / "data" / "bundled" / "projections_2026_27.parquet"
SEASON_MIDPOINT = pd.Timestamp("2027-02-01")  # ages are taken mid-season
STAT_OUT = ["GP", "MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "FGM", "FGA", "FTM", "FTA", "OREB"]
DIAGNOSTICS = ["USG", "usg_factor", "team_usage_load", "ast_factor", "reb_factor", "three_factor", "pace_new",
               "top_teammates", "last_team", "moved", "age"]


def fetch(from_dir: Path | None) -> dict[str, Path]:
    d = from_dir or RAW_DIR
    d.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, rel in FILES.items():
        path = d / Path(rel).name
        if not path.exists():
            resp = requests.get(f"{BASE}/{rel}", timeout=300)
            resp.raise_for_status()
            path.write_bytes(resp.content)
            print(f"downloaded {BASE}/{rel} ({len(resp.content):,} bytes)")
        paths[key] = path
    return paths


def build(data: dict[str, pd.DataFrame], params: pj.ProjectionParams = pj.ProjectionParams()) -> pd.DataFrame:
    """Projection rows for players with NBA history, plus stat-less rows for everyone else on a roster."""
    logs = {y: pj.game_logs(data[f"player_box_{y}"], data[f"team_box_{y}"]) for y in SEASONS}
    lines = [pj.season_lines(logs[y]) for y in SEASONS]
    ros = data["rosters"].dropna(subset=["athlete_id"]).copy()
    ros["athlete_id"] = ros["athlete_id"].astype("int64")
    ros["team"] = ros["team_abbreviation"].map(normalize_team)
    ros = ros.drop_duplicates("athlete_id", keep="last")
    ages = pj.ages_on([ros], SEASON_MIDPOINT)

    draft = data["draft"].copy()
    # Draft files use different athlete ids than rosters; match drafted players by name.
    team_by_name = {normalize_name(n): t for n, t in zip(ros["full_name"], ros["team"])}
    draft["team"] = draft["athlete_display_name"].map(lambda n: team_by_name.get(normalize_name(n)))
    draft["pick"] = pd.to_numeric(draft["overall_pick"], errors="coerce")
    rookies = draft.dropna(subset=["team"])[["team", "pick"]]

    base = pj.baseline(lines, ages, params)
    proj = pj.project(base, ros[["athlete_id", "team"]], pj.team_styles(logs[SEASONS[0]]), rookies, params)
    out = proj[["name", "team", "pos_group", *STAT_OUT, *DIAGNOSTICS, "proj_notes"]].copy()
    # Points-league extras: double-doubles, triple-doubles and big-game bonuses per game.
    out = out.join(pj.event_rates([logs[y] for y in SEASONS], proj))
    out["projected"] = True
    # Roster position groups are newer than box-score ones.
    pos = ros.set_index("athlete_id")["position_abbreviation"]
    out["pos_group"] = pos.reindex(out.index).fillna(out["pos_group"])

    # Everyone else on a 2026-27 roster: rookies and players with no NBA games. Listed (so a pick can be
    # entered) with no stats; they rank at the bottom unless you load a source that projects them.
    pick_by_name = {normalize_name(n): p for n, p in zip(draft["athlete_display_name"], draft["pick"])}
    rest = ros[~ros["athlete_id"].isin(out.index)].set_index("athlete_id")
    picks = rest["full_name"].map(lambda n: pick_by_name.get(normalize_name(n)))
    extra = pd.DataFrame({
        "name": rest["full_name"], "team": rest["team"], "pos_group": rest["position_abbreviation"],
        "age": ages.reindex(rest.index), "projected": False,
        "proj_notes": [f"2026 draft pick #{p:.0f}: no NBA stats yet, not projected" if pd.notna(p) else
                       "no NBA games in the last 3 seasons: not projected" for p in picks],
    }, index=rest.index)
    out = pd.concat([out, extra])
    out.index.name = "espn_id"
    out = out.reset_index()
    out["on_2026_27_roster"] = out["espn_id"].isin(ros["athlete_id"])
    return out.sort_values(["projected", "MIN"], ascending=[False, False]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir", type=Path, help="directory holding the downloaded parquet files")
    args = ap.parse_args()
    paths = fetch(args.from_dir)
    data = {k: pd.read_parquet(p) for k, p in paths.items()}
    df = build(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    box = data[f"player_box_{SEASONS[0]}"]
    meta = {
        "source": "2026-27 projections (model: 3 seasons of ESPN box scores + 2026-27 rosters)",
        "kind": "projections",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "box_score_seasons": [f"{y - 1}-{str(y)[2:]}" for y in SEASONS],
        "latest_box_score": str(box["game_date"].max()),
        "players_projected": int(df["projected"].sum()),
        "players_listed_without_stats": int((~df["projected"]).sum()),
        "params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(pj.ProjectionParams()).items()},
        "urls": {k: f"{BASE}/{v}" for k, v in FILES.items()},
    }
    OUT.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, default=str))
    usage = df.loc[df["projected"] & (df["team"] != "FA")].groupby("team")["team_usage_load"].first()
    print(f"wrote {OUT}: {meta['players_projected']} projected, {meta['players_listed_without_stats']} listed "
          f"without stats; team usage load {usage.min():.2f}-{usage.max():.2f} (1.00 = exactly 100%)")
    if not np.isfinite(df.loc[df["projected"], "PTS"]).all():
        raise SystemExit("some projections are not finite")


if __name__ == "__main__":
    main()
