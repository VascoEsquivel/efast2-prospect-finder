"""Recommended pick: plan, need weights, availability, wait cost, roster fit (synthetic players only)."""

import numpy as np
import pandas as pd
import pytest

import recommend as rc
import roster

CATS = ["PTS", "REB", "BLK"]
STARTERS = ["PG", "C", "UTIL"]


def players(rows):
    """rows: (id, positions, PTS, REB, BLK). Value = sum of z; rank by value."""
    df = pd.DataFrame([{"player_id": pid, "name": f"Test Player {pid}", "team": "BOS", "positions": pos,
                        "Z_PTS": a, "Z_REB": b, "Z_BLK": c, "MIN": 30.0} for pid, pos, a, b, c in rows])
    df["value"] = df[["Z_PTS", "Z_REB", "Z_BLK"]].sum(axis=1)
    df["rank"] = df["value"].rank(ascending=False, method="first").astype(int)
    df["adp"] = np.nan
    return df.set_index("player_id")


def table(rows):
    """rows: team -> (players, PTS_z, REB_z, BLK_z); ranks by z."""
    t = pd.DataFrame([{"team": k, "players": n, "PTS_z": a, "REB_z": b, "BLK_z": c} for k, (n, a, b, c) in rows.items()])
    t = t.set_index("team")
    for c in CATS:
        t[f"{c}_rank"] = t[f"{c}_z"].rank(ascending=False, method="min").astype(int)
    return t


def test_pick_plan_snake_and_waits():
    plan = rc.pick_plan(1, 3, 4, 3, set())  # 4 teams: slot 3 picks 3, 6, 11
    assert (plan.now_pick, plan.following, plan.on_clock) == (3, 6, False)
    assert plan.before_now == 2 and plan.before_following == 4 and plan.my_picks_left == 3
    on = rc.pick_plan(3, 3, 4, 3, {1, 2})
    assert on.on_clock and on.before_now == 0 and on.before_following == 2
    assert rc.pick_plan(None, 3, 4, 3, set()).now_pick is None
    last = rc.pick_plan(11, 3, 4, 3, set(range(1, 11)))
    assert last.now_pick == 11 and last.following is None


def test_need_weights_flat_before_my_first_pick_and_punts_zero():
    t = table({1: (0, 0, 0, 0), 2: (2, 1, 1, 1)})
    w = rc.category_weights(t, 1, CATS, punts=("BLK",))
    assert w == {"PTS": 1.0, "REB": 1.0, "BLK": 0.0}


def test_need_weights_favor_close_categories_over_locked_ones():
    # Late draft: I'm miles ahead in PTS, level in REB, so REB matters more.
    t = table({1: (12, 20.0, 0.0, 0.0), 2: (12, 0.0, 0.0, 0.0), 3: (12, 0.0, 0.5, 0.0)})
    w = rc.category_weights(t, 1, CATS, n_rounds=13)
    assert w["REB"] > 1.0 > w["PTS"]
    assert np.mean(list(w.values())) == pytest.approx(1.0, abs=0.15)


def test_availability_is_certain_on_the_clock_and_falls_with_picks():
    m = pd.Series([0.0, 5.0, 20.0])
    assert (rc.availability(m, 0) == 1).all()
    p = rc.availability(m, 6)
    assert p[0] < 0.1 and p[2] > 0.9 and p[0] < p[1] < p[2]


def test_wait_cost_rewards_the_scarce_position():
    # Two C-eligible players, five PG: if I wait 2 picks the other C is gone, PGs are plentiful.
    df = players([("c1", ["C"], 0, 3, 3), ("c2", ["C"], 0, 2, 2)] +
                 [(f"g{i}", ["PG"], 5 - i * 0.2, 0, 0) for i in range(5)])
    p_next = pd.Series({"c1": 0.0, "c2": 0.0, "g0": 0.0, "g1": 1, "g2": 1, "g3": 1, "g4": 1})
    drop, by_pos = rc.wait_costs(df["value"], df["positions"], p_next)
    assert by_pos["C"] > by_pos["PG"]
    assert drop["c1"] > drop["g0"] > 0


def test_recommend_fills_a_category_gap_and_explains_it():
    # My team (1) is far behind the field in BLK but fine in PTS; two equal-value candidates.
    avail = players([("pts", ["PG"], 3.0, 0.0, 0.0), ("blk", ["C"], 0.0, 0.0, 3.0)] +
                    [(f"x{i}", ["PG"], 0.5, 0.0, 0.0) for i in range(6)])
    t = table({1: (8, 6.0, 0.0, -1.0), 2: (8, 0.0, 0.0, 1.0), 3: (8, 0.0, 0.0, 1.5), 4: (8, 0.0, 0.0, 0.5)})
    plan = rc.pick_plan(33, 1, 4, 13, set(range(1, 33)))
    mine = [{"player_id": f"m{i}", "positions": ["PG"], "value": 1.0} for i in range(8)]
    r = rc.recommend(avail, plan, my_players=mine, starters=STARTERS, cats=CATS, table=t, me=1, n_rounds=13)
    assert r.top[0]["player_id"] == "blk"
    assert any("BLK" in why for why in r.top[0]["reasons"])
    assert r.gaps and r.gaps[0]["cat"] == "BLK" and r.gaps[0]["player_id"] == "blk"


def test_recommend_skips_players_without_stats_and_unlikely_to_last():
    avail = players([("a", ["PG"], 5, 0, 0), ("b", ["PG"], 4, 0, 0), ("c", ["C"], 1, 1, 1), ("d", ["C"], 0.5, 0.5, 0.5)])
    avail.loc["rookie"] = avail.loc["d"]
    avail.loc["rookie", ["MIN", "name"]] = [np.nan, "Test Player rookie"]
    plan = rc.PickPlan(now_pick=5, following=12, on_clock=False, before_now=2, before_following=8, my_picks_left=5)
    r = rc.recommend(avail, plan, my_players=[], starters=STARTERS, cats=CATS, is_points=False)
    ids = [t["player_id"] for t in r.top]
    assert "rookie" not in ids and "rookie" not in r.scores.index
    assert "a" not in ids  # the top two by rank go in the two picks before mine
    assert all(t["p_now"] >= 0.5 for t in r.top)
    assert r.scores.loc[["a", "b"], "gone_by_next"].all()  # list keeps them in place, flagged "gone by"
    assert not r.scores.loc[["c", "d"], "gone_by_next"].any()


def test_slot_bonus_only_when_a_starting_slot_is_open_for_him():
    avail = players([("g", ["PG"], 2, 0, 0), ("c", ["C"], 0, 1.9, 0)] + [(f"x{i}", ["PG"], 0.1, 0, 0) for i in range(4)])
    mine = [{"player_id": "m1", "positions": ["PG"], "value": 3.0}, {"player_id": "m2", "positions": ["SG"], "value": 2.0}]
    assert [s for s, p in roster.assign_roster(mine, STARTERS).starters if p is None] == ["C"]
    plan = rc.PickPlan(now_pick=9, following=None, on_clock=True, before_now=0, before_following=0, my_picks_left=1)
    r = rc.recommend(avail, plan, my_players=mine, starters=STARTERS, cats=CATS, is_points=False)
    assert r.scores.at["c", "fills"] and not r.scores.at["g", "fills"]
    assert r.top[0]["player_id"] == "c"  # 1.9 value + open C slot beats 2.0 value on the bench
    assert any("open starting slot" in why for why in r.top[0]["reasons"])


def test_points_mode_uses_value_and_has_no_category_gaps():
    avail = players([("a", ["PG"], 5, 0, 0), ("b", ["C"], 1, 1, 1)])
    avail["value"] = [20.0, 10.0]
    plan = rc.PickPlan(now_pick=1, following=8, on_clock=True, before_now=0, before_following=6, my_picks_left=3)
    r = rc.recommend(avail, plan, my_players=[], starters=STARTERS, cats=[], is_points=True)
    assert r.weights == {} and r.gaps == []
    assert r.top[0]["player_id"] == "a"


# ── Regressions from the review ───────────────────────────────────────────────

def test_csv_without_minutes_still_recommends_and_empty_pool_is_safe():
    avail = players([("a", ["PG"], 3, 0, 0), ("b", ["C"], 0, 2, 2), ("c", ["PG"], 1, 0, 0)])
    avail["MIN"] = np.nan  # a projections CSV with no minutes column
    for plan in [rc.PickPlan(1, 8, True, 0, 6, 3), rc.PickPlan(3, 6, False, 2, 4, 3)]:
        r = rc.recommend(avail, plan, my_players=[], starters=STARTERS, cats=CATS)
        assert r.top, plan
    empty = rc.recommend(avail.iloc[0:0], rc.PickPlan(1, 8, True, 0, 6, 3), my_players=[], starters=STARTERS, cats=CATS)
    assert empty.top == [] and "order_key" in empty.scores
    assert rc.availability(pd.Series(dtype=float), 3).empty


def test_wait_cost_is_small_for_a_player_who_will_still_be_there():
    df = players([("c1", ["C"], 0, 3, 3), ("c2", ["C"], 0, 1, 1)])
    sure = rc.wait_costs(df["value"], df["positions"], pd.Series({"c1": 1.0, "c2": 1.0}))[0]
    gone = rc.wait_costs(df["value"], df["positions"], pd.Series({"c1": 0.0, "c2": 1.0}))[0]
    assert sure["c1"] == pytest.approx(0.0, abs=1e-9)  # he is still there next time: nothing lost
    assert gone["c1"] == pytest.approx(4.0)             # 6 now vs the 2 left


def test_back_to_back_turn_picks_measure_waiting_to_the_next_real_wait():
    plan = rc.pick_plan(4, 4, 4, 4, {1, 2, 3})  # 4 teams: slot 4 picks 4, 5, 12, 13
    assert (plan.now_pick, plan.following) == (4, 12)
    assert plan.before_now == 0 and plan.before_following == 6  # picks 6-11 by the others


def test_market_order_without_adp_ignores_my_punts():
    df = players([("blk", ["C"], 0, 0, 6), ("pts", ["PG"], 3, 0, 0)])
    df["rank"] = [2, 1]  # my punt-BLK ranking puts the shot blocker second
    order = rc.market_order(df, CATS)
    assert order["blk"] < order["pts"]  # other managers still value the blocks


def test_roster_matcher_handles_more_than_twenty_players():
    many = [{"player_id": f"p{i}", "positions": ["PG"], "value": float(i)} for i in range(22)]
    a = roster.assign_roster(many, STARTERS)
    assert len([p for _, p in a.starters if p]) + len(a.bench) == 22
    avail = players([("c", ["C"], 0, 2, 0), ("g", ["PG"], 1, 0, 0)])
    r = rc.recommend(avail, rc.PickPlan(30, None, True, 0, 0, 1), my_players=many, starters=STARTERS, cats=CATS)
    assert r.top


def test_gaps_only_name_players_likely_to_last_when_planning_ahead():
    avail = players([("star", ["C"], 0, 0, 5), ("ok", ["C"], 0, 0, 2)] + [(f"x{i}", ["PG"], 4 - i * 0.1, 0, 0) for i in range(4)])
    t = table({1: (5, 3.0, 2.0, -3.0), 2: (5, 0, 0, 2.0), 3: (5, 0, 0, 1.0), 4: (5, 0, 0, 1.5)})
    avail["adp"] = [1.0, 40.0] + [2.0 + i for i in range(4)] + []
    avail = pd.concat([avail, players([(f"f{i}", ["SF"], 0.1, 0.1, 0.0) for i in range(30)])])
    avail.loc[avail.index.str.startswith("f"), "adp"] = range(10, 40)
    plan = rc.PickPlan(now_pick=24, following=25, on_clock=False, before_now=6, before_following=6, my_picks_left=5)
    r = rc.recommend(avail, plan, my_players=[{"player_id": "m", "positions": ["PG"], "value": 1}],
                     starters=STARTERS, cats=CATS, table=t, me=1, n_rounds=13)
    blk = next(g for g in r.gaps if g["cat"] == "BLK")
    assert blk["player_id"] == "ok"  # the elite shot blocker goes in the 6 picks before mine
