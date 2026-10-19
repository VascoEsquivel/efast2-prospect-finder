"""Projection model math on synthetic box scores ("Test Player NNN" only, never real players)."""

import numpy as np
import pandas as pd
import pytest

import projections as pj

TEAMS = ["BOS", "PHI"]


def synthetic_season(seed: int = 0, n_games: int = 20, usage: dict[str, float] | None = None):
    """Two teams of 10 fake players playing each other; team totals are the sums of the player rows."""
    rng = np.random.default_rng(seed)
    usage = usage or {}
    players, teams = [], []
    for g in range(n_games):
        for t_i, team in enumerate(TEAMS):
            rows = []
            for k in range(10):
                pid = 100 * (t_i + 1) + k
                name = f"Test Player {pid:03d}"
                share = usage.get(name, 1.0)
                fga = rng.poisson(8 * share)
                tpa = min(fga, rng.poisson(3))
                rows.append({
                    "season_type": 2, "did_not_play": False, "minutes": 24.0, "athlete_id": pid,
                    "athlete_display_name": name, "athlete_position_abbreviation": "G" if k < 4 else "F" if k < 8 else "C",
                    "team_abbreviation": team, "game_id": g, "game_date": f"2026-01-{g + 1:02d}",
                    "field_goals_attempted": fga, "field_goals_made": rng.binomial(fga, 0.47),
                    "three_point_field_goals_attempted": tpa, "three_point_field_goals_made": rng.binomial(tpa, 0.36),
                    "free_throws_attempted": rng.poisson(2 * share), "offensive_rebounds": rng.poisson(1),
                    "defensive_rebounds": rng.poisson(3), "assists": rng.poisson(2 * share), "steals": rng.poisson(0.8),
                    "blocks": rng.poisson(0.5), "turnovers": rng.poisson(1.2 * share),
                })
            for r in rows:
                r["three_point_field_goals_made"] = min(r["three_point_field_goals_made"], r["field_goals_made"])
                r["free_throws_made"] = int(round(r["free_throws_attempted"] * 0.78))
            players += rows
            box = pd.DataFrame(rows)
            teams.append({"season_type": 2, "team_abbreviation": team, "game_id": g,
                          "field_goals_made": box["field_goals_made"].sum(), "field_goals_attempted": box["field_goals_attempted"].sum(),
                          "free_throws_attempted": box["free_throws_attempted"].sum(),
                          "three_point_field_goals_attempted": box["three_point_field_goals_attempted"].sum(),
                          "offensive_rebounds": box["offensive_rebounds"].sum(),
                          "total_rebounds": box["offensive_rebounds"].sum() + box["defensive_rebounds"].sum(),
                          "total_turnovers": box["turnovers"].sum(), "assists": box["assists"].sum()})
    return pd.DataFrame(players), pd.DataFrame(teams)


@pytest.fixture(scope="module")
def season():
    pb, tb = synthetic_season(usage={"Test Player 100": 2.5})  # one ball-dominant BOS guard
    logs = pj.game_logs(pb, tb)
    return logs, pj.season_lines(logs)


def test_team_usage_averages_twenty_percent(season):
    _, lines = season
    for team in TEAMS:
        t = lines[lines["last_team"] == team]
        assert 100 * t["usg_num"].sum() / t["usg_den"].sum() == pytest.approx(20.0)
    # The ball-dominant player really does use more possessions.
    usg = 100 * lines["usg_num"] / lines["usg_den"]
    assert usg.idxmax() == 100


def test_usage_factors_hit_the_budget_and_spare_the_stars():
    usg = np.array([32.0, 24.0, 18.0, 12.0])
    mins = np.array([34.0, 30.0, 28.0, 20.0])
    for gamma in (0.0, -1.0):
        p = pj.ProjectionParams(usage_alpha=1.0, usage_gamma=gamma)
        f = pj._usage_factors(usg, mins, 0.95, p)
        assert np.sum(usg * f * mins) == pytest.approx(0.95 * np.sum(usg * mins))
    same_share = pj._usage_factors(usg, mins, 0.95, pj.ProjectionParams(usage_gamma=0.0))
    assert np.allclose(same_share, same_share[0])
    same_points = pj._usage_factors(usg, mins, 0.95, pj.ProjectionParams(usage_gamma=-1.0))
    assert np.allclose(usg * (1 - same_points), usg[0] * (1 - same_points[0]))  # equal usage points given up
    assert same_points[0] > same_points[-1]  # the star keeps a bigger share of their shots


def test_allocate_minutes_fills_exactly_and_bench_moves_most():
    m = np.array([36.0, 34.0, 30.0, 26.0, 20.0, 14.0, 10.0, 8.0, 6.0])
    a = np.full(len(m), 0.9)
    assert np.sum(m * a) > 140  # roster is crowded
    out = pj._allocate_minutes(m, a, 140.0, 37.0)
    assert np.sum(out * a) == pytest.approx(140.0, abs=0.06)
    cut = m - out
    assert (cut > 0).all() and cut[-1] / m[-1] > cut[0] / m[0]


@pytest.mark.parametrize("pick,expected", [(1, 28.0), ("3", 28.0), (4, 22.0), (25.0, 11.0), (61, 0.0),
                                           (None, 0.0), (float("nan"), 0.0), ("n/a", 0.0)])
def test_rookie_reserve(pick, expected):
    assert pj.rookie_reserve(pick) == expected


def test_ages_on_uses_birth_dates():
    r = pd.DataFrame({"athlete_id": [1, 2], "date_of_birth": ["2000-02-01T08:00Z", None]})
    ages = pj.ages_on([r], pd.Timestamp("2027-02-01"))
    assert ages[1] == pytest.approx(27.0, abs=0.01)
    assert np.isnan(ages[2])


def test_trade_into_crowded_team_squeezes_shots(season):
    logs, lines = season
    base = pj.baseline([lines, lines, lines], pd.Series(dtype=float), pj.ProjectionParams(age_effects=False))
    rosters = pd.DataFrame({"athlete_id": base.index, "team": base["last_team"]})
    rosters.loc[rosters["athlete_id"] == 100, "team"] = "PHI"  # the BOS star moves to PHI
    proj = pj.project(base, rosters, pj.team_styles(logs))
    phi, bos = proj[proj["team"] == "PHI"], proj[proj["team"] == "BOS"]
    assert phi["team_usage_load"].iloc[0] > 1 > bos["team_usage_load"].iloc[0]
    assert (phi["usg_factor"] < 1).all() and (bos["usg_factor"] > 1).all()
    star = proj.loc[100]
    assert star["moved"] and "new team: BOS→PHI" in star["proj_notes"]
    assert "crowded" in proj.loc[200, "proj_notes"]
    # Without team context nothing is squeezed.
    flat = pj.project(base, rosters, pj.team_styles(logs), params=pj.ProjectionParams(team_context=False))
    assert (flat["usg_factor"] == 1).all()
    assert proj.loc[200, "FGA"] < flat.loc[200, "FGA"]


def test_projection_lines_are_internally_consistent(season):
    logs, lines = season
    base = pj.baseline([lines, lines], pd.Series(dtype=float))
    proj = pj.project(base, pd.DataFrame({"athlete_id": base.index, "team": base["last_team"]}), pj.team_styles(logs))
    assert np.allclose(proj["PTS"], 2 * proj["FGM"] + proj["3PM"] + proj["FTM"])
    assert (proj["FGM"] <= proj["FGA"]).all() and (proj["3PM"] <= proj["FGM"]).all()
    assert (proj["FTM"] <= proj["FTA"]).all()
    assert proj["GP"].between(1, pj.SEASON_GAMES).all()
    assert proj["name"].str.startswith("Test Player").all()


def test_unrostered_regulars_become_free_agents(season):
    logs, lines = season
    base = pj.baseline([lines], pd.Series(dtype=float))
    rosters = pd.DataFrame({"athlete_id": base.index[1:], "team": base["last_team"].iloc[1:]})
    proj = pj.project(base, rosters, pj.team_styles(logs))
    missing = base.index[0]
    assert proj.at[missing, "team"] == "FA"
    assert "unsigned" in proj.at[missing, "proj_notes"]
    assert proj.at[missing, "MIN"] == pytest.approx(0.6 * base.at[missing, "MPG_base"])


# ── Analyst projections for rookies ───────────────────────────────────────────

def test_rookies_take_complete_analyst_lines_and_keep_partial_ones_as_notes():
    import data_fetcher as dfm
    analysts = pd.DataFrame([
        {"name": "Test Rookie One", "source": "Site A", "url": "u", "as_of": "2026-10", "GP": 70, "MIN": 30, "PTS": 15,
         "REB": 5, "AST": 3, "STL": 1, "BLK": 0.5, "3PM": 1, "TO": 2, "FGA": 12, "FG%": 0.5, "FTA": 3, "FT%": 0.8},
        {"name": "Test Rookie One", "source": "Site B", "url": "u", "as_of": "2026-10", "PTS": 17, "REB": 6},
        {"name": "Test Rookie Two", "source": "Site B", "url": "u", "as_of": "2026-10", "PTS": 9, "REB": 4},
    ])
    for c in dfm.ANALYST_LINE:
        analysts[c] = pd.to_numeric(analysts.get(c), errors="coerce")
    analysts["name_key"] = analysts["name"].map(dfm.normalize_name)
    analysts["complete"] = analysts[dfm.ANALYST_LINE].notna().all(axis=1)
    df = pd.DataFrame({"name": ["Test Rookie One", "Test Rookie Two", "Test Vet"], "MIN": [np.nan, np.nan, 25.0],
                       "PTS": [np.nan, np.nan, 10.0], "proj_notes": ["2026 draft pick #3: no NBA stats yet, not projected",
                                                                     "2026 draft pick #9: no NBA stats yet, not projected", ""]})
    for c in ["GP", "REB", "AST", "STL", "BLK", "3PM", "TO", "FGA", "FGM", "FTA", "FTM"]:
        df[c] = np.nan
    out, n = dfm.apply_analyst_rookies(df, analysts)
    assert n == 1
    one, two = out.iloc[0], out.iloc[1]
    assert one["PTS"] == 15 and one["FGM"] == pytest.approx(6.0) and one["FTM"] == pytest.approx(2.4)
    assert "Site A projection" in one["proj_notes"] and "Site B 17 pts" in one["proj_notes"]
    assert "no NBA stats yet" not in one["proj_notes"]
    assert np.isnan(two["PTS"]) and "Site B 9 pts" in two["proj_notes"]  # partial line: a note, never stats
    assert out.iloc[2]["PTS"] == 10 and out.iloc[2]["proj_notes"] == ""


def test_bundled_analyst_lines_are_internally_consistent():
    import data_fetcher as dfm
    a = dfm.load_analyst_rookies()
    full = a[a["complete"]]
    assert len(full) >= 2
    pts = 2 * full["FGA"] * full["FG%"] + full["3PM"] + full["FTA"] * full["FT%"]
    assert np.allclose(pts, full["PTS"], atol=0.3)  # the published line adds up
    assert a["url"].str.startswith("https://").all() and a["source"].notna().all()
