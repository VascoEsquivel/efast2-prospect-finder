"""End-to-end UI checks with Streamlit's AppTest (no browser, no network)."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import data_fetcher as dfm
import draft

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """Isolated runtime dir (draft state, settings, caches) for each test."""
    for name, path in {"RUNTIME_DIR": tmp_path, "RAW_DIR": tmp_path / "raw", "CACHE_DIR": tmp_path / "cache",
                       "UPLOAD_DIR": tmp_path / "raw" / "uploads"}.items():
        monkeypatch.setattr(dfm, name, path)
    return tmp_path


def start(runtime, league: tuple[int, int, int] | None = None, **settings) -> AppTest:
    """league=(teams, rounds, my slot) pins the draft size; settings are written to settings.json as given."""
    if settings:
        (runtime / "settings.json").write_text(json.dumps(settings))
    if league:
        n, r, slot = league
        draft.save_state(draft.new_state(draft.DraftSettings(n_teams=n, n_rounds=r, my_slot=slot)),
                         runtime / "draft_state.json")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def metrics(at) -> dict:
    return {m.label: m.value for m in at.metric}


def draft_via_search(at, n: int) -> None:
    for _ in range(n):
        box = at.selectbox(key="w_search")
        box.set_value(box.options[0]).run()  # the top-ranked available player, like "jok" + Enter
        assert not at.exception, [e.value for e in at.exception]


def test_boots_offline_with_synthetic_data(runtime):
    at = start(runtime, source="synthetic")
    assert metrics(at)["Pick"] == "#1"
    assert "Synthetic" in at.caption[0].value
    assert len(at.selectbox(key="w_search").options) == 300


def test_search_box_drafts_refresh_resume_loses_nothing(runtime):
    at = start(runtime, league=(12, 13, 1), source="synthetic", settings_version=3)
    draft_via_search(at, 15)
    assert metrics(at)["Pick"] == "#16"
    saved = draft.load_state(runtime / "draft_state.json")
    assert len(saved.picks) == 15
    assert [p.team for p in saved.picks[:13]] == list(range(1, 13)) + [12]

    # Browser refresh = brand-new session.
    at2 = AppTest.from_file(APP, default_timeout=60)
    at2.run()
    assert any("Saved draft found" in w.value for w in at2.warning)
    at2.button(key="resume_yes").click().run()
    assert metrics(at2)["Pick"] == "#16"
    restored = draft.load_state(runtime / "draft_state.json")
    assert [(p.pick_no, p.player_id) for p in restored.picks] == [(p.pick_no, p.player_id) for p in saved.picks]
    draft_via_search(at2, 1)
    assert len(draft.load_state(runtime / "draft_state.json").picks) == 16


def test_undo_and_start_fresh_archives(runtime):
    at = start(runtime, source="synthetic")
    draft_via_search(at, 3)
    at.button[[b.label for b in at.button].index("↶ Undo")].click().run()
    assert metrics(at)["Pick"] == "#3"
    at2 = AppTest.from_file(APP, default_timeout=60)
    at2.run()
    at2.button(key="resume_no").click().run()
    assert metrics(at2)["Pick"] == "#1"
    assert list(runtime.glob("draft_state.2*.json")), "old draft should be archived, not deleted"


def test_points_mode_and_settings_persist(runtime):
    at = start(runtime, source="synthetic", template="points")
    assert not at.exception
    assert any("Replacement level" in c.value for c in at.caption)
    assert json.loads((runtime / "settings.json").read_text())["template"] == "points"


def test_missing_source_falls_back_to_bundled_data(runtime):
    at = start(runtime, source="espn")
    assert any("FALLBACK" in e.value for e in at.error)
    assert "2026-27 PROJECTIONS" in at.caption[0].value
    assert metrics(at)["Pick"] == "#1"


def test_fresh_install_uses_2026_27_projections(runtime):
    at = start(runtime)
    assert "2026-27 PROJECTIONS" in at.caption[0].value
    options = at.selectbox(key="w_search").options
    assert len(options) > 600  # projected players plus rookies listed without stats
    assert any("Tyrese Maxey" in o for o in options)


def test_old_default_source_migrates_once(runtime):
    at = start(runtime, source="last_season")  # saved before projections existed
    assert "2026-27 PROJECTIONS" in at.caption[0].value
    at2 = start(runtime, source="last_season", settings_version=2)  # chosen on purpose since then
    assert "LAST-SEASON" in at2.caption[0].value


def test_team_fit_unlocks_after_three_of_my_picks(runtime):
    at = start(runtime, league=(12, 13, 1), source="synthetic", template="9-cat", settings_version=3)
    order = lambda: next(s for s in at.selectbox if s.label == "Order")  # noqa: E731
    draft_via_search(at, 24)  # slot 1 picks at #1 and #24
    assert "Team fit" not in order().options
    draft_via_search(at, 1)   # pick #25 is my third
    assert "Team fit" in order().options
    order().set_value("Team fit").run()
    assert not at.exception
    assert any("Team-fit weights" in c.value for c in at.caption)


def test_empty_espn_snapshot_does_not_crash_the_app(runtime):
    dfm.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    pd.DataFrame().to_parquet(dfm.CACHE_DIR / "espn_projections.parquet")  # left by a bad fetch
    at = start(runtime)  # default: last season + "use ESPN ADP" on
    assert metrics(at)["Pick"] == "#1"


def test_new_install_is_set_up_for_my_sleeper_league(runtime):
    at = start(runtime)
    state = draft.load_state(runtime / "draft_state.json") if (runtime / "draft_state.json").exists() else None
    assert metrics(at)["Pick"] == "#1"
    assert any("14 teams" in str(getattr(e, "value", "")) for e in at.main) or state is None or state.settings.n_teams == 14
    lineup = next(t for t in at.text_input if t.label == "Starting slots")
    assert lineup.value == "PG, SG, G, SF, PF, F, C, UTIL x2"
    assert next(s for s in at.selectbox if s.label == "Scoring preset").value == "Sleeper (your league)"


def test_old_saved_defaults_move_to_my_league_once(runtime):
    # Saved before v3: classic defaults everywhere, no picks yet.
    at = start(runtime, league=(12, 13, 1), source="proj_2027", settings_version=2, template="9-cat",
               starters="PG, SG, SF, PF, C, G, F, UTIL x3")
    saved = json.loads((runtime / "settings.json").read_text())
    assert saved["template"] == "points" and saved["starters"] == "PG, SG, G, SF, PF, F, C, UTIL x2"
    teams = next(n for n in at.number_input if n.label == "Teams")
    assert teams.value == 14 and next(n for n in at.number_input if n.label == "My draft slot").value == 9
    # A choice made after the move is kept.
    at2 = start(runtime, league=(10, 13, 3), source="proj_2027", settings_version=3, template="9-cat")
    assert next(n for n in at2.number_input if n.label == "Teams").value == 10
