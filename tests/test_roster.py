import time

import numpy as np
import pytest

import roster as r
import valuation as v
from data_fetcher import synthetic_players


def p(name, positions, value=0.0):
    return {"player_id": name, "name": name, "positions": positions, "value": value, "team": "TST"}


def slots_of(assignment):
    return {pl["name"]: s for s, pl in assignment.starters if pl}


def test_greedy_would_fail_but_matcher_fills_both():
    # Greedy in value order puts A (PG/SG) at PG, then B (PG only) has nowhere to go.
    players = [p("A", ["PG", "SG"], 5), p("B", ["PG"], 3)]
    a = r.assign_roster(players, ["PG", "SG"])
    assert slots_of(a) == {"A": "SG", "B": "PG"}
    assert a.filled == 2 and a.bench == []


def test_multi_position_chain():
    # Only one perfect matching exists: C->C, D->PF, E->SF, F->SG, G->PG.
    players = [p("G", ["PG", "SG"], 1), p("F", ["SG", "SF"], 2), p("E", ["SF", "PF"], 3),
               p("D", ["PF", "C"], 4), p("C", ["C"], 5)]
    a = r.assign_roster(players, ["PG", "SG", "SF", "PF", "C"])
    assert slots_of(a) == {"G": "PG", "F": "SG", "E": "SF", "D": "PF", "C": "C"}


def test_flex_slots_and_bench_overflow():
    players = [p(f"P{i}", ["C"], 10 - i) for i in range(6)]
    a = r.assign_roster(players, ["PG", "C", "UTIL", "UTIL"])
    s = dict((slot, pl["name"] if pl else None) for slot, pl in a.starters if slot != "UTIL")
    assert s == {"PG": None, "C": "P0"}
    assert [pl["name"] for slot, pl in a.starters if slot == "UTIL"] == ["P1", "P2"]
    assert [pl["name"] for pl in a.bench] == ["P3", "P4", "P5"]


def test_value_breaks_ties_between_full_assignments():
    players = [p("Star", ["PG"], 9), p("Scrub", ["PG"], 1)]
    a = r.assign_roster(players, ["PG"])
    assert slots_of(a) == {"Star": "PG"} and a.bench[0]["name"] == "Scrub"


def test_specific_slot_preferred_over_util():
    a = r.assign_roster([p("Big", ["C"], 9)], r.DEFAULT_STARTERS)
    assert slots_of(a) == {"Big": "C"}
    a = r.assign_roster([p("Big", ["C"], 9), p("Big2", ["C"], 8)], r.DEFAULT_STARTERS)
    assert slots_of(a) == {"Big": "C", "Big2": "UTIL"}


def test_flexible_slot_left_open_before_specific_one():
    players = [p("Wing", ["SF", "PF"], 5), p("Wing2", ["SF", "PF"], 4)]
    a = r.assign_roster(players, ["SF", "PF", "F"])
    assert set(slots_of(a).values()) == {"SF", "PF"}


def test_unknown_positions_only_fit_flex():
    a = r.assign_roster([p("Mystery", [], 4)], ["PG", "UTIL"])
    assert slots_of(a) == {"Mystery": "UTIL"}


def test_g_and_f_slots():
    players = [p("pg", ["PG"]), p("pg2", ["PG"]), p("sf", ["SF"]), p("pf", ["PF"])]
    a = r.assign_roster(players, ["PG", "G", "SF", "F"])
    assert a.filled == 4
    assert slots_of(a)["pf"] == "F"


def brute_force_filled(players, starters, used=frozenset()):
    """Exhaustive reference: try every player (or nobody) in every slot."""
    if not starters:
        return 0
    slot, rest = starters[0], starters[1:]
    best = brute_force_filled(players, rest, used)
    for j, pl in enumerate(players):
        if j not in used and r.eligible(slot, pl["positions"]):
            best = max(best, 1 + brute_force_filled(players, rest, used | {j}))
    return best


@pytest.mark.parametrize("seed", range(8))
def test_matches_brute_force_on_random_small_rosters(seed):
    rng = np.random.default_rng(seed)
    choices = [["PG"], ["SG"], ["SF"], ["PF"], ["C"], ["PG", "SG"], ["SF", "PF"], ["PF", "C"], ["SG", "SF"]]
    players = [p(f"x{i}", choices[rng.integers(len(choices))], float(rng.random())) for i in range(5)]
    starters = ["PG", "SG", "SF", "PF", "C", "G"]
    assert r.assign_roster(players, starters).filled == brute_force_filled(players, starters)


def test_full_13_man_roster_is_fast():
    df = synthetic_players(13, seed=3)
    players = [{"player_id": i, "name": n, "positions": pos, "value": 1.0}
               for i, n, pos in zip(df.player_id, df.name, df.positions)]
    t = time.perf_counter()
    a = r.assign_roster(players, r.DEFAULT_STARTERS)
    assert time.perf_counter() - t < 0.5
    assert len(a.starters) == 10 and a.filled + len(a.bench) == 13
    table = r.roster_table(a, 13)
    # Every player appears once; an unfillable starting slot shows as an empty row.
    assert len(table) == 10 + len(a.bench)
    assert sorted(table.loc[table.player != "—", "player"]) == sorted(df.name)


def test_parse_starters():
    assert r.parse_starters("PG, SG, SF, PF, C, G, F, UTIL x3") == r.DEFAULT_STARTERS
    assert r.parse_starters("PG,UTIL,BN x3") == ["PG", "UTIL"]
    with pytest.raises(ValueError):
        r.parse_starters("PG, QB")


# ── Team category standings ───────────────────────────────────────────────────

def test_team_category_table_ranks():
    df = synthetic_players(60)
    out, info = v.valuate(df, v.ValuationSettings(n_teams=3, roster_size=4))
    valued = out.set_index("player_id")
    ids = list(valued.index)
    rosters = {1: ids[0:4], 2: ids[4:8], 3: ids[8:12]}
    cats = info["categories"]
    table = r.team_category_table(valued, rosters, cats)
    assert list(table.index) == [1, 2, 3]
    for c in ["PTS", "TO", "BLK"]:
        assert sorted(table[f"{c}_rank"]) == [1, 2, 3]
        best = table[f"{c}_z"].idxmax()
        assert table.loc[best, f"{c}_rank"] == 1
    # FG% ranks by the team's makes/attempts.
    fg = {t: valued.loc[i, "FGM"].sum() / valued.loc[i, "FGA"].sum() for t, i in rosters.items()}
    assert table["FG%_raw"].to_dict() == pytest.approx(fg)
    assert table.loc[max(fg, key=fg.get), "FG%_rank"] == 1


def test_team_with_no_picks_ranks_last():
    df = synthetic_players(30)
    out, info = v.valuate(df, v.ValuationSettings(n_teams=2, roster_size=3))
    valued = out.set_index("player_id")
    table = r.team_category_table(valued, {1: list(valued.index[:3]), 2: []}, info["categories"])
    assert (table.loc[2, [f"{c}_rank" for c in info["categories"]]] == 2).all()
    summary = r.my_category_summary(table, 1, info["categories"], punts=["FT%"])
    assert len(summary) == 9 and "FT% (punt)" in summary["cat"].tolist()
