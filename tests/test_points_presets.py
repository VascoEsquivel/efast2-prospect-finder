"""Points presets (ESPN default, Sleeper league) and the per-game event rates behind DD/TD/bonuses."""

import json

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import data_fetcher as dfm
import projections as pj
import valuation as v
from tests.test_app_smoke import APP


def logs(rows):
    """rows: (athlete_id, points, rebounds, assists, steals, blocks) per game."""
    return pd.DataFrame(rows, columns=["athlete_id", "points", "rebounds", "assists", "steals", "blocks"])


def test_event_rates_count_real_games_when_projection_matches_history():
    games = [(1, 12, 11, 2, 1, 0)] * 5 + [(1, 8, 4, 2, 1, 0)] * 4 + [(1, 41, 10, 10, 1, 0)]  # 6 DD, 1 TD, one 40+
    proj = pd.DataFrame({"PTS": [np.mean([g[1] for g in games])], "REB": [np.mean([g[2] for g in games])],
                         "AST": [np.mean([g[3] for g in games])], "STL": [1.0], "BLK": [0.0]}, index=[1])
    r = pj.event_rates([logs(games)], proj, weights=(1.0,)).loc[1]
    assert r["DD"] == pytest.approx(0.6)   # a triple-double also counts as a double-double
    assert r["TD"] == pytest.approx(0.1)
    assert r["PTS40"] == pytest.approx(0.1) and r["PTS50"] == 0
    assert r["AST15"] == 0 and r["REB20"] == 0


def test_event_rates_follow_a_bigger_projected_role():
    games = [(1, 30, 5, 5, 1, 0), (1, 36, 5, 5, 1, 0), (1, 24, 5, 5, 1, 0), (1, 34, 5, 5, 1, 0)]
    base = pd.DataFrame({"PTS": [31.0], "REB": [5.0], "AST": [5.0], "STL": [1.0], "BLK": [0.0]}, index=[1])
    more = base.assign(PTS=[40.0])
    assert pj.event_rates([logs(games)], base, weights=(1.0,)).at[1, "PTS40"] == 0
    assert pj.event_rates([logs(games)], more, weights=(1.0,)).at[1, "PTS40"] > 0
    assert np.isnan(pj.event_rates([logs(games)], base.rename(index={1: 2}), weights=(1.0,)).at[2, "DD"])  # no games


def test_sleeper_preset_scores_bonuses_and_season_totals_scale_them():
    df = pd.DataFrame({"name": ["Test Player 001", "Test Player 002"], "GP": [70.0, 70.0], "MIN": [34.0, 34.0],
                       "PTS": [20.0, 20.0], "REB": [10.0, 10.0], "AST": [5.0, 5.0], "3PM": [2.0, 2.0], "STL": [1.0, 1.0],
                       "BLK": [1.0, 1.0], "TO": [3.0, 3.0], "FGM": [8.0, 8.0], "FGA": [16.0, 16.0], "FTM": [2.0, 2.0],
                       "FTA": [3.0, 3.0], "OREB": [2.0, 2.0], "DD": [0.5, 0.0], "TD": [0.1, 0.0], "PTS40": [0.0, 0.0]})
    w = v.SLEEPER_LEAGUE_POINTS
    per_game = v.fantasy_points(v.apply_stat_mode(df, "per_game"), w)
    base = 20 * 0.5 + 2 * 0.5 + 10 + 2 * 0.5 + 5 + 1 * 2 + 1 * 2 - 3
    assert per_game[1] == pytest.approx(base)
    assert per_game[0] == pytest.approx(base + 0.5 * 1 + 0.1 * 2)
    season = v.fantasy_points(v.apply_stat_mode(df, "season"), w)
    assert season[0] == pytest.approx(70 * per_game[0])
    assert v.POINTS_PRESETS["Sleeper (your league)"]["TECH"] == -2.0


def test_bundled_sources_carry_the_points_extras():
    proj, _ = dfm.load_projections()
    projected = proj[proj["MIN"].notna() & ~proj["proj_notes"].str.startswith("Rookie:")]  # analyst rookie lines lack DD
    assert projected["DD"].notna().all() and projected["OREB"].notna().all()
    jokic = proj[proj["name"] == "Nikola Jokic"].iloc[0]
    assert jokic["DD"] > 0.5 and jokic["TD"] > 0.1
    last, _ = dfm.load_last_season()
    assert last["DD"].notna().sum() > 500 and last["TECH"].isna().all()  # no per-player technicals in the data


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name, path in {"RUNTIME_DIR": tmp_path, "RAW_DIR": tmp_path / "raw", "CACHE_DIR": tmp_path / "cache",
                       "UPLOAD_DIR": tmp_path / "raw" / "uploads"}.items():
        monkeypatch.setattr(dfm, name, path)
    return tmp_path


def test_app_runs_the_sleeper_preset_and_says_what_it_cannot_count(runtime):
    (runtime / "settings.json").write_text(json.dumps({"template": "points", "settings_version": 2,
                                                         "points_weights": v.SLEEPER_LEAGUE_POINTS}))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    preset = next(s for s in at.selectbox if s.label == "Scoring preset")
    assert preset.value == "Sleeper (your league)"
    captions = " ".join(c.value for c in at.caption)
    assert "Technical foul" in captions and "Flagrant foul" in captions and "Double-double" not in captions.split("worth 0")[-1][:200]
    preset.set_value("ESPN default").run()
    assert not at.exception
    assert json.loads((runtime / "settings.json").read_text())["points_weights"] == v.ESPN_DEFAULT_POINTS
