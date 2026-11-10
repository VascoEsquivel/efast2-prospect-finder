"""Every documented transition of the app's state machines (flow.py, settings_store.py)."""

import json

import pytest

import draft as d
import flow
import settings_store as store
import valuation as val


def player(i):
    return {"player_id": f"p{i}", "name": f"Test Player {i:03d}", "positions": ["PG"], "team": "BOS"}


def league(n=4, r=3, slot=2):
    return d.new_state(d.DraftSettings(n_teams=n, n_rounds=r, my_slot=slot))


# ── DraftPhase ────────────────────────────────────────────────────────────────

def test_draft_phase_walks_setup_drafting_reopened_complete():
    s = league(n=2, r=2)
    assert flow.draft_phase(s) is flow.DraftPhase.SETUP
    d.make_pick(s, player(1))
    assert flow.draft_phase(s) is flow.DraftPhase.DRAFTING
    for i in (2, 3, 4):
        d.make_pick(s, player(i))
    assert flow.draft_phase(s) is flow.DraftPhase.COMPLETE
    d.remove_pick(s, 2)
    assert flow.draft_phase(s) is flow.DraftPhase.REOPENED
    d.make_pick(s, player(5))  # fills the hole
    assert flow.draft_phase(s) is flow.DraftPhase.COMPLETE
    d.remove_pick(s, 4)  # the last pick: nothing after it, so just drafting again
    assert flow.draft_phase(s) is flow.DraftPhase.DRAFTING
    while s.picks:
        d.undo_last(s)
    assert flow.draft_phase(s) is flow.DraftPhase.SETUP
    d.make_pick(s, player(6))
    assert flow.draft_phase(d.reset(s)) is flow.DraftPhase.SETUP


def test_clock_notes_match_every_situation():
    names = lambda t: f"Team {t}"  # noqa: E731
    s = league(n=4, r=2, slot=2)          # snake: 1 2 3 4 | 4 3 2 1
    c = flow.clock(s)
    assert (c.pick, c.round, c.pick_in_round, c.team, c.mine) == (1, 1, 1, 1, False)
    assert c.note(names) == "you're up next" and c.next_mine == 2 and c.picks_until_mine == 1 and c.mine_soon
    assert not c.league_locked and c.can_draft
    d.make_pick(s, player(1))
    c = flow.clock(s)
    assert c.mine and c.note(names) == "make your pick" and c.next_mine == 7 and c.league_locked
    d.make_pick(s, player(2))
    assert flow.clock(s).note(names) == "up next: Team 4"
    for i in range(3, 8):
        d.make_pick(s, player(i))
    c = flow.clock(s)
    assert c.pick == 8 and c.is_last_pick and c.note(names) == "last pick" and c.next_mine is None
    d.remove_pick(s, 3)
    assert flow.clock(s).note(names) == "reopened pick"
    d.make_pick(s, player(9))
    d.make_pick(s, player(10))
    c = flow.clock(s)
    assert c.phase is flow.DraftPhase.COMPLETE and c.pick is None and not c.can_draft
    assert c.note(names) == "draft complete" and c.picks_until_mine is None and not c.mine_soon


# ── SyncState ─────────────────────────────────────────────────────────────────

DRAFT_ID = "1257000000000000001"
LINK = f"https://sleeper.com/draft/nba/{DRAFT_ID}"


@pytest.mark.parametrize("link,draft_id,enabled,last,state", [
    ("", None, False, None, flow.SyncState.OFF),
    ("   ", None, True, None, flow.SyncState.OFF),
    ("not a link", None, False, None, flow.SyncState.INVALID_LINK),
    (LINK, DRAFT_ID, False, None, flow.SyncState.READY),
    (LINK, DRAFT_ID, False, flow.Poll(False, "12:00:00", error="boom"), flow.SyncState.READY),  # toggled off
    (LINK, DRAFT_ID, True, None, flow.SyncState.LIVE),            # first poll pending
    (LINK, DRAFT_ID, True, flow.Poll(True, "12:00:05", 37), flow.SyncState.LIVE),
    (LINK, DRAFT_ID, True, flow.Poll(False, "12:00:10", error="timeout"), flow.SyncState.FAILING),
])
def test_sync_status_states(link, draft_id, enabled, last, state):
    st = flow.sync_status(link, draft_id, enabled, last)
    assert st.state is state and st.label and st.detail


def test_sync_status_messages_carry_the_poll_result():
    assert "37 picks seen" in flow.sync_status(LINK, DRAFT_ID, True, flow.Poll(True, "12:00:05", 37)).detail
    failing = flow.sync_status(LINK, DRAFT_ID, True, flow.Poll(False, "12:00:10", error="timeout"))
    assert "12:00:10" in failing.detail and "timeout" in failing.detail and failing.tone == "warn"


# ── SourceStatus ──────────────────────────────────────────────────────────────

class Meta:
    def __init__(self, kind, source="x", from_cache=False, fallback_from=""):
        self.kind, self.source, self.from_cache, self.fallback_from = kind, source, from_cache, fallback_from


@pytest.mark.parametrize("kind,expected,tag", [
    ("projections", flow.SourceKind.PROJECTIONS, "2026-27 projections"),
    ("last_season_actuals", flow.SourceKind.ACTUALS, "2025-26 actuals, not projections"),
    ("synthetic", flow.SourceKind.SYNTHETIC, "Test data"),
    ("metadata", flow.SourceKind.UNKNOWN, None),
])
def test_source_status_kinds_and_tags(kind, expected, tag):
    st = flow.source_status(Meta(kind))
    assert st.kind is expected
    assert (tag in [t for t, _ in st.tags]) if tag else st.tags == []


def test_source_status_fallback_and_cache():
    st = flow.source_status(Meta("projections", "2026-27 PROJECTIONS", True, "ESPN projections 2026-27"))
    assert st.fallback_from == "ESPN projections 2026-27" and st.cached
    assert ("Cached snapshot", "warn") in st.tags


# ── BootAction ────────────────────────────────────────────────────────────────

def test_boot_actions():
    old = store.OLD_DEFAULT_LEAGUE
    assert flow.boot_action(None, False, old) is flow.BootAction.NEW
    unused_old = league(*old)
    assert flow.boot_action(unused_old, True, old) is flow.BootAction.NEW          # moved during the v3 migration
    assert flow.boot_action(unused_old, False, old) is flow.BootAction.USE_SAVED   # chosen on purpose since then
    assert flow.boot_action(league(10, 13, 3), True, old) is flow.BootAction.USE_SAVED
    started = league(*old)
    d.make_pick(started, player(1))
    assert flow.boot_action(started, True, old) is flow.BootAction.ASK_RESUME      # picks are never thrown away


# ── ListOrder ─────────────────────────────────────────────────────────────────

def test_list_orders_and_a_preference_that_survives_being_unavailable():
    assert flow.list_orders(False, False) == ["My rank", "ADP"]
    assert flow.list_orders(True, True) == ["My rank", "Recommended", "ADP", "Team fit"]
    # Recommended chosen, then it disappears (my last pick is in): the list shows My rank...
    pref = "Recommended"
    orders = flow.list_orders(False, False)
    shown = flow.effective_order(pref, orders)
    assert shown == "My rank"
    # ...and the untouched selectbox keeps returning My rank, which must not overwrite the preference.
    pref = flow.next_preferred_order(pref, shown, "My rank")
    assert pref == "Recommended"
    assert flow.effective_order(pref, flow.list_orders(True, False)) == "Recommended"  # back after Undo
    assert flow.next_preferred_order(pref, "Recommended", "ADP") == "ADP"            # a real change sticks


# ── SnapshotState ─────────────────────────────────────────────────────────────

def test_snapshot_states():
    assert flow.snapshot_state(None, 24) is flow.SnapshotState.MISSING
    assert flow.snapshot_state(1.5, 24) is flow.SnapshotState.FRESH
    assert flow.snapshot_state(30, 24) is flow.SnapshotState.STALE


# ── Page state for automation ─────────────────────────────────────────────────

def test_page_state_is_flat_strings():
    s = league()
    attrs = flow.page_state(flow.clock(s), flow.sync_status("", None, False, None),
                            flow.source_status(Meta("projections")), 0, s.total_picks)
    assert attrs["phase"] == "setup" and attrs["pick"] == "1" and attrs["sync"] == "off" and attrs["source"] == "projections"
    assert all(isinstance(v, str) for v in attrs.values())


# ── Settings migrations ───────────────────────────────────────────────────────

def test_fresh_install_gets_defaults_without_a_migration():
    loaded = store.migrate({})
    assert loaded.settings == store.DEFAULT_SETTINGS and not loaded.changed and not loaded.moved_to_my_league


def test_v1_file_walks_every_migration_and_keeps_user_choices():
    v1 = {"source": "last_season", "template": "9-cat", "starters": store.OLD_DEFAULT_STARTERS, "unknown_key": 1}
    loaded = store.migrate(v1)
    s = loaded.settings
    assert loaded.applied == [2, 3] and loaded.moved_to_my_league and loaded.changed
    assert s["settings_version"] == store.SETTINGS_VERSION and s["source"] == "proj_2027"
    assert s["template"] == "points" and s["points_weights"] == val.SLEEPER_LEAGUE_POINTS and s["stat_mode"] == "games"
    assert "unknown_key" not in s
    custom = store.migrate({"settings_version": 2, "template": "9-cat", "points_weights": {"PTS": 3.0},
                            "starters": "PG, C, UTIL", "stat_mode": "season"}).settings
    assert custom["template"] == "9-cat" and custom["starters"] == "PG, C, UTIL" and custom["stat_mode"] == "season"


def test_current_version_is_untouched_and_migrations_are_ordered():
    loaded = store.migrate({"settings_version": store.SETTINGS_VERSION, "template": "9-cat"})
    assert loaded.applied == [] and loaded.settings["template"] == "9-cat"
    versions = [v for v, _ in store.MIGRATIONS]
    assert versions == sorted(versions) and versions[-1] == store.SETTINGS_VERSION


def test_load_and_save_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    assert store.load(path).settings == store.DEFAULT_SETTINGS  # missing file
    path.write_text("{not json")
    assert store.load(path).settings == store.DEFAULT_SETTINGS  # corrupt file
    s = dict(store.DEFAULT_SETTINGS, order="ADP")
    store.save(path, s)
    assert json.loads(path.read_text())["order"] == "ADP" and store.load(path).settings["order"] == "ADP"


def test_points_preset_names():
    assert val.points_preset_name(val.SLEEPER_LEAGUE_POINTS) == "Sleeper (your league)"
    assert val.points_preset_name({**val.ESPN_DEFAULT_POINTS, "DD": 0.0}) == "ESPN default"  # zeros ignored
    assert val.points_preset_name({"PTS": 1.0}) == "Custom"
