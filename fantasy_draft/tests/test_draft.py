import json

import pytest

import draft as d


def player(i, **kw):
    return {"player_id": f"p{i}", "name": f"Test Player {i:03d}", "positions": ["PG"], "team": "TST", **kw}


# ── Snake order ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pick,team", [(1, 1), (12, 12), (13, 12), (24, 1), (25, 1), (36, 12), (37, 12)])
def test_snake_order_12_teams(pick, team):
    assert d.team_for_pick(pick, 12) == team


def test_round_of():
    assert [d.round_of(p, 12) for p in (1, 12, 13, 24, 25)] == [1, 1, 2, 2, 3]


@pytest.mark.parametrize("n", range(8, 17))
def test_snake_every_team_once_per_round(n):
    for r in range(1, 14):
        teams = [d.team_for_pick(p, n) for p in range((r - 1) * n + 1, r * n + 1)]
        assert sorted(teams) == list(range(1, n + 1))
        assert teams == (list(range(1, n + 1)) if r % 2 else list(range(n, 0, -1)))


@pytest.mark.parametrize("n", [8, 12, 16])
def test_picks_for_team_matches_team_for_pick(n):
    for team in range(1, n + 1):
        for p in d.picks_for_team(team, n, 13):
            assert d.team_for_pick(p, n) == team


# ── Making picks ──────────────────────────────────────────────────────────────

def test_make_pick_advances_and_assigns_snake_team():
    s = d.new_state(d.DraftSettings(n_teams=12, n_rounds=13, my_slot=5))
    for i in range(1, 14):
        d.make_pick(s, player(i))
    assert s.current_pick() == 14
    assert s.filled()[13].team == 12
    assert s.filled()[12].team == 12
    assert s.on_the_clock() == 11


def test_cannot_draft_same_player_twice():
    s = d.new_state()
    d.make_pick(s, player(1))
    with pytest.raises(d.DraftError, match="already drafted"):
        d.make_pick(s, player(1))
    assert len(s.picks) == 1


def test_team_override_and_keeper_pick_number():
    s = d.new_state(d.DraftSettings(n_teams=12, n_rounds=13))
    d.make_pick(s, player(1), team=7)  # traded pick
    assert s.filled()[1].team == 7
    d.make_pick(s, player(99), team=3, pick_no=150, source="keeper")  # keeper slotted late
    assert s.current_pick() == 2
    with pytest.raises(d.DraftError, match="already filled"):
        d.make_pick(s, player(5), pick_no=150)
    with pytest.raises(d.DraftError, match="outside"):
        d.make_pick(s, player(6), pick_no=157)


def test_undo_removes_most_recently_entered_not_highest_pick():
    s = d.new_state()
    d.make_pick(s, player(1))
    d.make_pick(s, player(2), pick_no=50, source="keeper")
    d.make_pick(s, player(3))  # fills pick 2, entered last
    undone = d.undo_last(s)
    assert undone.player_id == "p3"
    assert s.current_pick() == 2
    assert d.undo_last(s).player_id == "p2"
    assert d.undo_last(s).player_id == "p1"
    assert d.undo_last(s) is None


def test_remove_specific_pick_reopens_slot():
    s = d.new_state()
    for i in range(1, 8):
        d.make_pick(s, player(i))
    removed = d.remove_pick(s, 3)
    assert removed.player_id == "p3"
    assert s.current_pick() == 3
    assert s.reopened_slot()
    d.make_pick(s, player(30))
    assert s.filled()[3].player_id == "p30"
    assert s.current_pick() == 8
    assert not s.reopened_slot()


def test_draft_complete():
    s = d.new_state(d.DraftSettings(n_teams=8, n_rounds=2))
    for i in range(16):
        d.make_pick(s, player(i))
    assert s.current_pick() is None
    with pytest.raises(d.DraftError, match="complete"):
        d.make_pick(s, player(100))


def test_reset_keeps_settings():
    s = d.new_state(d.DraftSettings(n_teams=10, n_rounds=14, my_slot=4, team_names=["A"]))
    d.make_pick(s, player(1))
    fresh = d.reset(s)
    assert fresh.picks == [] and fresh.settings == s.settings


# ── Next pick ─────────────────────────────────────────────────────────────────

def test_next_pick_for_team():
    s = d.new_state(d.DraftSettings(n_teams=12, n_rounds=13, my_slot=5))
    assert s.next_pick_for_team(5) == 5
    for i in range(1, 5):
        d.make_pick(s, player(i))
    # Team 5 is on the clock at pick 5, so its *next* pick is 20 (12 + 13 - 5).
    assert s.current_pick() == 5
    assert s.next_pick_for_team(5) == 20
    d.make_pick(s, player(5))
    assert s.next_pick_for_team(5) == 20
    assert s.next_pick_for_team(12) == 12


def test_next_pick_skips_keeper_filled_slots():
    s = d.new_state(d.DraftSettings(n_teams=12, n_rounds=13, my_slot=1))
    d.make_pick(s, player(1), pick_no=24, team=1, source="keeper")
    d.make_pick(s, player(2))  # team 1 takes pick 1
    assert s.next_pick_for_team(1) == 25


# ── Persistence ───────────────────────────────────────────────────────────────

def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "draft_state.json"
    s = d.new_state(d.DraftSettings(n_teams=12, n_rounds=13, my_slot=7, team_names=["Me"]))
    for i in range(1, 30):
        d.make_pick(s, player(i, positions=["PG", "SG"]))
    d.undo_last(s)
    d.save_state(s, path)
    loaded = d.load_state(path)
    assert loaded.settings == s.settings
    assert [(p.pick_no, p.player_id, p.team, p.positions) for p in loaded.picks] == \
           [(p.pick_no, p.player_id, p.team, p.positions) for p in s.picks]
    assert loaded.current_pick() == s.current_pick() == 29
    d.make_pick(loaded, player(500))
    assert loaded.picks[-1].seq > max(p.seq for p in s.picks)


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "draft_state.json"
    s = d.new_state()
    for i in range(5):
        d.make_pick(s, player(i))
        d.save_state(s, path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["draft_state.json"]
    assert len(json.loads(path.read_text())["picks"]) == 5


def test_save_failure_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "draft_state.json"
    s = d.new_state()
    d.make_pick(s, player(1))
    d.save_state(s, path)
    d.make_pick(s, player(2))

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(d.os, "replace", boom)
    with pytest.raises(OSError):
        d.save_state(s, path)
    assert len(json.loads(path.read_text())["picks"]) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["draft_state.json"]


def test_corrupt_file_is_moved_aside_not_deleted(tmp_path):
    path = tmp_path / "draft_state.json"
    path.write_text("{not json")
    with pytest.raises(d.DraftError, match="unreadable"):
        d.load_state(path)
    assert not path.exists()
    assert len(list(tmp_path.glob("draft_state.corrupt-*.json"))) == 1


def test_archive_state(tmp_path):
    path = tmp_path / "draft_state.json"
    d.save_state(d.new_state(), path)
    dest = d.archive_state(path)
    assert dest.exists() and not path.exists()
    assert d.load_state(path) is None


# ── Sleeper sync ──────────────────────────────────────────────────────────────

def sleeper_pick(no, slot, pid, first="Test", last="Guy"):
    return {"pick_no": no, "draft_slot": slot, "round": 1, "player_id": pid,
            "metadata": {"first_name": first, "last_name": last, "position": "PG", "team": "BOS"}}


def test_apply_sleeper_picks_is_idempotent_and_maps_ids():
    s = d.new_state(d.DraftSettings(n_teams=12, n_rounds=13))
    known = {"111": player(1), "222": player(2)}
    picks = [sleeper_pick(1, 1, "111"), sleeper_pick(2, 2, "222"), sleeper_pick(3, 3, "333", "Unknown", "Rookie")]
    applied, msgs = d.apply_sleeper_picks(s, picks, known.get)
    assert [p.player_id for p in applied] == ["p1", "p2", "sl:333"]
    assert s.filled()[3].unmatched and s.filled()[3].name == "Unknown Rookie"
    assert any("not in the loaded dataset" in m for m in msgs)
    again, _ = d.apply_sleeper_picks(s, picks, known.get)
    assert again == [] and len(s.picks) == 3


def test_sleeper_overrides_conflicting_manual_pick():
    s = d.new_state(d.DraftSettings(n_teams=12, n_rounds=13))
    d.make_pick(s, player(9))  # manual pick 1 = p9, but Sleeper says p1
    d.make_pick(s, player(1))  # manual pick 2 = p1
    applied, msgs = d.apply_sleeper_picks(s, [sleeper_pick(1, 1, "111")], {"111": player(1)}.get)
    assert s.filled()[1].player_id == "p1"
    assert 2 not in s.filled()
    assert len(msgs) == 2
