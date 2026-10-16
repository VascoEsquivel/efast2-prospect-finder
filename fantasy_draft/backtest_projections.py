"""Backtest the projection model on past seasons it never saw, then compare to what happened.

Usage:
    python backtest_projections.py                  # download data from GitHub releases
    python backtest_projections.py --from-dir DIR   # use files already downloaded

For each target season (2024-25 and 2025-26) the model only sees the three seasons before it, plus
who ended up playing where that season (the "rosters"). Compared on the same players (25+ games):
  naive      last season's per-game stats carried forward (what "last season" data gives you)
  base       weighted 3-season baseline, regressed, no aging, no team context
  base+age   baseline plus the aging curve
  full       + rosters: minutes, usage/assist/rebound budgets, pace and 3PT system
Reported: mean absolute error per stat (lower is better) and how well each version ranks 9-cat fantasy
value (Spearman correlation with actual value, top-100 overlap), for everyone, for players who
changed teams, and for teams whose combined usage was 5%+ over or under budget.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import data_fetcher as dfm
import projections as pj
import valuation as v

BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
TARGETS = (2025, 2026)          # season end years to backtest
STATS = ["MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO"]


def files_needed() -> dict[str, str]:
    years = range(min(TARGETS) - 3, max(TARGETS) + 1)
    out = {f"player_box_{y}": f"espn_nba_player_boxscores/player_box_{y}.parquet" for y in years}
    out |= {f"team_box_{y}": f"espn_nba_team_boxscores/team_box_{y}.parquet" for y in years}
    out |= {f"draft_{y - 1}": f"espn_nba_draft/draft_{y - 1}.parquet" for y in TARGETS}
    # Roster files carry birth dates (for ages); recent ones cover nearly everyone who played lately.
    out |= {f"rosters_{y}": f"espn_nba_rosters/rosters_{y}.parquet" for y in (2026, 2027)}
    return out


def load(from_dir: Path | None) -> dict[str, pd.DataFrame]:
    d = from_dir or dfm.RAW_DIR / "sportsdataverse"
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, rel in files_needed().items():
        path = d / Path(rel).name
        if not path.exists():
            resp = requests.get(f"{BASE}/{rel}", timeout=300)
            resp.raise_for_status()
            path.write_bytes(resp.content)
        out[key] = pd.read_parquet(path)
    return out


@dataclass
class Case:
    """Everything the model may see for one target season, plus the answer."""
    target: int
    prior: list[pd.DataFrame]
    rosters: pd.DataFrame
    ages: pd.Series
    rookies: pd.DataFrame
    styles: pd.DataFrame
    actual: pd.DataFrame
    naive: pd.DataFrame


def per_game(lines: pd.DataFrame) -> pd.DataFrame:
    g = lines["GP"].replace(0, np.nan)
    out = pd.DataFrame({"name": lines["name"], "team": lines["last_team"], "GP": lines["GP"], "MIN": lines["MIN"] / g})
    for c in ["FGM", "FGA", "3PM", "FTM", "FTA", "AST", "STL", "BLK", "TO"]:
        out[c] = lines[c] / g
    out["REB"] = (lines["OREB"] + lines["DREB"]) / g
    out["PTS"] = (2 * lines["FGM"] + lines["3PM"] + lines["FTM"]) / g
    return out


def fantasy_value(df: pd.DataFrame) -> pd.Series:
    out, _ = v.valuate(df.assign(name=df["name"].astype(str)), v.ValuationSettings(min_gp=0))
    return out["Z_total"]


def prepare(data: dict[str, pd.DataFrame], target: int) -> Case:
    logs = {y: pj.game_logs(data[f"player_box_{y}"], data[f"team_box_{y}"]) for y in range(target - 3, target + 1)}
    prior = [pj.season_lines(logs[y]) for y in (target - 1, target - 2, target - 3)]
    lines = pj.season_lines(logs[target])
    # "Rosters" for the target season: each player's main team that season (minutes-weighted).
    main_team = logs[target].groupby(["athlete_id", "team"])["minutes"].sum().reset_index() \
        .sort_values("minutes").drop_duplicates("athlete_id", keep="last")
    rosters = main_team[["athlete_id", "team"]]
    ages = pj.ages_on([data["rosters_2027"], data["rosters_2026"]], pd.Timestamp(f"{target}-02-01"))
    draft = data[f"draft_{target - 1}"].copy()
    team_by_name = {dfm.normalize_name(n): t for n, t in zip(lines["name"], lines["last_team"])}
    draft["team"] = draft["athlete_display_name"].map(lambda n: team_by_name.get(dfm.normalize_name(n)))
    rookies = draft.dropna(subset=["team"]).rename(columns={"overall_pick": "pick"})[["team", "pick"]]
    actual = per_game(lines)
    actual["Z"] = fantasy_value(actual)
    naive = per_game(prior[0])
    naive["Z"] = fantasy_value(naive)
    return Case(target, prior, rosters, ages, rookies, pj.team_styles(logs[target - 1]), actual, naive)


def run(case: Case, params: pj.ProjectionParams) -> pd.DataFrame:
    base = pj.baseline(case.prior, case.ages, params)
    proj = pj.project(base, case.rosters, case.styles, case.rookies, params)
    proj["Z"] = fantasy_value(proj)
    return proj


def score(pred: pd.DataFrame, actual: pd.DataFrame, ids) -> dict:
    p, a = pred.loc[ids], actual.loc[ids]
    res = {f"MAE {c}": float(np.mean(np.abs(p[c] - a[c]))) for c in STATS}
    fg_ok = a["FGA"] >= 5
    res["MAE FG%"] = float(np.mean(np.abs(p.loc[fg_ok, "FGM"] / p.loc[fg_ok, "FGA"] - a.loc[fg_ok, "FGM"] / a.loc[fg_ok, "FGA"])))
    zp, za = pred.loc[ids, "Z"], actual.loc[ids, "Z"]
    res["value rank corr"] = float(zp.rank().corr(za.rank()))
    top_a = set(za.sort_values(ascending=False).index[:100])
    top_p = set(zp.sort_values(ascending=False).index[:100])
    res["top-100 overlap"] = len(top_a & top_p)
    return res


def common_ids(case: Case, variants: dict[str, pd.DataFrame]) -> pd.Index:
    ids = case.actual.index[case.actual["GP"] >= 25].intersection(case.naive.index)
    for vdf in variants.values():
        ids = ids.intersection(vdf.index)
    return ids


VARIANTS = {
    "base": pj.ProjectionParams(team_context=False, age_effects=False),
    "base+age": pj.ProjectionParams(team_context=False),
    "full model": pj.ProjectionParams(),
}


def report(case: Case) -> None:
    variants = {"naive (last season)": case.naive} | {label: run(case, p) for label, p in VARIANTS.items()}
    full = variants["full model"]
    ids = common_ids(case, variants)
    moved = [i for i in ids if full.at[i, "moved"]]
    crowded = [i for i in ids if abs(full.at[i, "team_usage_load"] - 1) >= 0.05]
    season = f"{case.target - 1}-{str(case.target)[2:]}"
    for title, sub in [(f"All players ({len(ids)})", ids), (f"Changed teams ({len(moved)})", moved),
                       (f"On teams with a usage budget off by 5%+ ({len(crowded)})", crowded)]:
        table = pd.DataFrame({label: score(vdf, case.actual, sub) for label, vdf in variants.items()})
        print(f"\n=== {season}: {title} ===")
        print(table.round(3).to_string())
    # Did the usage squeeze point the right way? Compare predicted vs actual points for squeezed players.
    sq = [i for i in ids if full.at[i, "usg_factor"] < 0.97]
    if sq:
        b = variants["base+age"]
        err_base = np.mean(np.abs(b.loc[sq, "PTS"] - case.actual.loc[sq, "PTS"]))
        err_full = np.mean(np.abs(full.loc[sq, "PTS"] - case.actual.loc[sq, "PTS"]))
        print(f"\n{season}: players the model squeezed (shots x<0.97, n={len(sq)}): "
              f"points MAE base+age {err_base:.2f} -> full {err_full:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir", type=Path)
    args = ap.parse_args()
    data = load(args.from_dir)
    pd.set_option("display.width", 200)
    for target in TARGETS:
        report(prepare(data, target))


if __name__ == "__main__":
    main()
