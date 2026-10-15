import numpy as np
import pandas as pd
import pytest

import valuation as v
from data_fetcher import synthetic_players


def frame(rows):
    """rows: list of dicts with any stat columns; missing stats default to sensible values."""
    base = {"GP": 70, "MIN": 30, "PTS": 15, "REB": 5, "AST": 4, "3PM": 1.5, "STL": 1, "BLK": 0.5,
            "TO": 2, "FGM": 5, "FGA": 10, "FTM": 2, "FTA": 2.5}
    df = pd.DataFrame([{**base, **r} for r in rows])
    df["name"] = [r.get("name", f"Test Player {i:03d}") for i, r in enumerate(rows, 1)]
    return df


def all_pool(n):
    return np.ones(n, dtype=bool)


# ── z-score math ──────────────────────────────────────────────────────────────

def test_counting_z_uses_population_std():
    df = frame([{"PTS": 10}, {"PTS": 20}, {"PTS": 30}])
    z, _ = v.category_zscores(v.apply_stat_mode(df, "per_game"), all_pool(3), ["PTS"])
    sd = np.std([10, 20, 30])  # ddof=0
    assert z["PTS"].tolist() == pytest.approx([-10 / sd, 0, 10 / sd])
    assert z["PTS"].mean() == pytest.approx(0)
    assert z["PTS"].std(ddof=0) == pytest.approx(1)


def test_turnovers_are_inverted():
    df = frame([{"TO": 1}, {"TO": 2}, {"TO": 4}])
    z, _ = v.category_zscores(v.apply_stat_mode(df, "per_game"), all_pool(3), ["TO"])
    assert z["TO"].iloc[0] > z["TO"].iloc[1] > z["TO"].iloc[2]
    x = np.array([1, 2, 4.0])
    assert z["TO"].tolist() == pytest.approx(list(-(x - x.mean()) / x.std()))


def test_league_rate_is_volume_weighted_not_mean_of_percentages():
    df = frame([{"FGM": 1, "FGA": 1}, {"FGM": 4, "FGA": 10}])
    stats = v.apply_stat_mode(df, "per_game")
    assert v.league_rate(stats, all_pool(2), "FGM", "FGA") == pytest.approx(5 / 11)


def test_league_average_shooter_has_zero_impact_and_zero_z():
    df = frame([{"FGM": 6, "FGA": 10}, {"FGM": 4, "FGA": 10}, {"FGM": 7.5, "FGA": 15},
                {"FTM": 4, "FTA": 5}, {"FTM": 1, "FTA": 3}])
    stats = v.apply_stat_mode(df, "per_game")
    pool = all_pool(len(df))
    lg_fg = v.league_rate(stats, pool, "FGM", "FGA")
    assert lg_fg == pytest.approx(0.5)  # 27.5 / 55 with the three defaults at 5/10
    impact = v.pct_impact(stats, "FGM", "FGA", lg_fg)
    assert impact.iloc[2] == pytest.approx(0)  # 7.5/15 = exactly league average
    z, rates = v.category_zscores(stats, pool, ["FG%", "FT%"])
    assert z["FG%"].iloc[2] == pytest.approx(0)
    assert rates["FG%"] == pytest.approx(0.5)


def test_pct_impact_rewards_volume():
    df = frame([{"FGM": 5.5, "FGA": 10}, {"FGM": 11, "FGA": 20}, {"FGM": 3, "FGA": 10}])
    z, _ = v.category_zscores(v.apply_stat_mode(df, "per_game"), all_pool(3), ["FG%"])
    # Same 55% shooting, double the attempts -> bigger positive impact.
    assert z["FG%"].iloc[1] > z["FG%"].iloc[0] > 0 > z["FG%"].iloc[2]


def test_zero_variance_category_gives_zero_z():
    df = frame([{"BLK": 1}, {"BLK": 1}])
    z, _ = v.category_zscores(v.apply_stat_mode(df, "per_game"), all_pool(2), ["BLK"])
    assert z["BLK"].tolist() == [0, 0]


# ── Punts and totals ──────────────────────────────────────────────────────────

def test_punt_multiplier_removes_category_from_total():
    df = synthetic_players(120)
    cats = v.CATEGORIES
    full = v.iterate_pool(v.apply_stat_mode(df, "per_game"), cats, v.multipliers(cats, []), 60)
    punted = v.iterate_pool(v.apply_stat_mode(df, "per_game"), cats, v.multipliers(cats, ["FT%"]), 60)
    expected = punted.z[[c for c in cats if c != "FT%"]].sum(axis=1)
    assert punted.total.to_numpy() == pytest.approx(expected.to_numpy())
    assert "FT%" in punted.z  # still computed and shown, just weighted 0
    assert not np.allclose(full.total, punted.total)


def test_multipliers():
    assert v.multipliers(["PTS", "FT%", "TO"], ["FT%"]) == {"PTS": 1.0, "FT%": 0.0, "TO": 1.0}


def test_templates_and_toggles():
    assert "TO" not in v.active_categories("8-cat (no TO)")
    assert len(v.active_categories("9-cat")) == 9
    assert v.active_categories("9-cat", ["PTS", "REB", "BLK"]) == ["PTS", "REB", "BLK"]
    assert v.active_categories("points") == []


# ── Replacement pool ──────────────────────────────────────────────────────────

def test_pool_converges_and_is_stable():
    df = synthetic_players(300)
    stats = v.apply_stat_mode(df, "per_game")
    cats = v.CATEGORIES
    res = v.iterate_pool(stats, cats, v.multipliers(cats, []), pool_size=12 * 13)
    assert res.converged and 1 <= res.iterations <= 10
    assert res.pool.sum() == 156
    # Pool = exactly the top 156 by Z_total computed against that same pool.
    top = res.total.sort_values(ascending=False).index[:156]
    assert set(np.flatnonzero(res.pool)) == set(top)


def test_pool_respects_iteration_cap():
    df = synthetic_players(300)
    res = v.iterate_pool(v.apply_stat_mode(df, "per_game"), v.CATEGORIES, {}, 156, max_iter=1)
    assert res.iterations <= 1


def test_pool_larger_than_player_count_uses_everyone():
    df = synthetic_players(50)
    res = v.iterate_pool(v.apply_stat_mode(df, "per_game"), v.CATEGORIES, {}, 156)
    assert res.pool.all() and res.converged


def test_min_gp_keeps_low_gp_players_out_of_pool():
    df = synthetic_players(200)
    df.loc[0, ["GP", "MIN"]] = [3, 40]  # most minutes, but only 3 games
    res = v.iterate_pool(v.apply_stat_mode(df, "per_game"), v.CATEGORIES, {}, 100, min_gp=10)
    assert not res.pool[0]
    assert res.pool.sum() == 100


def test_pool_changes_with_league_size():
    df = synthetic_players(300)
    stats = v.apply_stat_mode(df, "per_game")
    small = v.iterate_pool(stats, v.CATEGORIES, {}, 8 * 13)
    big = v.iterate_pool(stats, v.CATEGORIES, {}, 16 * 13)
    assert small.pool.sum() == 104 and big.pool.sum() == 208


# ── Stat mode ─────────────────────────────────────────────────────────────────

def test_season_totals_scale_counting_stats_and_makes_by_gp():
    df = frame([{"GP": 50, "PTS": 20, "FGM": 8, "FGA": 16}, {"GP": 80, "PTS": 18}])
    season = v.apply_stat_mode(df, "season")
    assert season["PTS"].tolist() == [1000, 1440]
    assert season["FGM"].iloc[0] == 400 and season["FGA"].iloc[0] == 800
    assert season["GP"].tolist() == [50, 80]


def test_season_mode_rewards_availability():
    df = frame([{"GP": 40, "PTS": 25}, {"GP": 80, "PTS": 22}, {"GP": 70, "PTS": 10}])
    s_pg = v.ValuationSettings(n_teams=1, roster_size=3, categories=("PTS",))
    s_season = v.ValuationSettings(n_teams=1, roster_size=3, categories=("PTS",), stat_mode="season")
    pg, _ = v.valuate(df, s_pg)
    season, _ = v.valuate(df, s_season)
    assert pg["rank"].tolist()[:2] == [1, 2]
    assert season["rank"].tolist()[:2] == [2, 1]


def test_percentages_are_derived_from_makes_and_attempts():
    df = frame([{"FGM": 9, "FGA": 18, "FTM": 0, "FTA": 0}])
    out, _ = v.valuate(df, v.ValuationSettings(n_teams=1, roster_size=1))
    assert out["FG%"].iloc[0] == pytest.approx(0.5)
    assert np.isnan(out["FT%"].iloc[0])


# ── Points leagues ────────────────────────────────────────────────────────────

def test_points_replacement_is_the_next_best_player():
    df = frame([{"PTS": p, "REB": 0, "AST": 0, "3PM": 0, "STL": 0, "BLK": 0, "TO": 0,
                 "FGM": 0, "FGA": 0, "FTM": 0, "FTA": 0} for p in [30, 25, 20, 15, 10]])
    s = v.ValuationSettings(n_teams=2, roster_size=2, template="points", categories=())
    out, info = v.valuate(df, s)
    assert info["replacement_fp"] == 10  # 2x2 = 4 rostered, 5th best replaces
    assert out["FP_value"].tolist() == [20, 15, 10, 5, 0]
    assert out["rank"].tolist() == [1, 2, 3, 4, 5]


def test_espn_default_points_formula():
    df = frame([{"PTS": 20, "3PM": 2, "FGM": 8, "FGA": 16, "FTM": 2, "FTA": 3, "REB": 5, "AST": 4,
                 "STL": 1, "BLK": 1, "TO": 2}])
    fp = v.fantasy_points(v.apply_stat_mode(df, "per_game"), v.ESPN_DEFAULT_POINTS)
    assert fp.iloc[0] == 20 + 2 + 16 - 16 + 2 - 3 + 5 + 8 + 4 + 4 - 4


def test_points_season_mode_multiplies_by_gp():
    df = frame([{"GP": 50}, {"GP": 70}])
    pg = v.fantasy_points(v.apply_stat_mode(df, "per_game"), v.ESPN_DEFAULT_POINTS)
    season = v.fantasy_points(v.apply_stat_mode(df, "season"), v.ESPN_DEFAULT_POINTS)
    assert season.tolist() == pytest.approx((pg * df["GP"]).tolist())


# ── ADP comparison ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("adp,rank,delta,flag", [
    (40, 30, 10, "STEAL"),    # threshold max(3, 6) = 6
    (40, 35, 5, ""),          # 5 < 6
    (10, 6, 4, "STEAL"),      # threshold max(3, 1.5) = 3
    (10, 8, 2, ""),
    (10, 13, -3, "REACH"),    # -3 <= -3
    (100, 120, -20, "REACH"),  # threshold 15
    (100, 110, -10, ""),
    (5, 5, 0, ""),
])
def test_delta_sign_and_thresholds(adp, rank, delta, flag):
    out = v.adp_flags(pd.Series([adp]), pd.Series([rank]))
    assert out["delta"].iloc[0] == delta
    assert out["flag"].iloc[0] == flag


def test_delta_thresholds_are_configurable_and_missing_adp_is_blank():
    out = v.adp_flags(pd.Series([40.0, np.nan]), pd.Series([35, 1]), min_gap=5, pct=0.0)
    assert out["flag"].tolist() == ["STEAL", ""]
    assert np.isnan(out["delta"].iloc[1])


def test_likely_gone_before_next_pick():
    adp = pd.Series([3.0, 19.9, 20.0, 45.0, np.nan])
    assert v.likely_gone(adp, 20).tolist() == [True, True, False, False, False]
    assert not v.likely_gone(adp, None).any()


def test_valuate_ranks_and_adp_columns():
    df = synthetic_players(300)
    out, info = v.valuate(df, v.ValuationSettings(), adp_col="adp_espn")
    assert sorted(out["rank"]) == list(range(1, 301))
    assert out.loc[out["rank"] == 1, "Z_total"].iloc[0] == out["Z_total"].max()
    assert info["converged"]
    has_adp = out["adp"].notna()
    assert (out.loc[has_adp, "delta"] == out.loc[has_adp, "adp"] - out.loc[has_adp, "rank"]).all()
    assert set(out["flag"]) <= {"STEAL", "REACH", ""}
