"""Connecting to a Sleeper draft: link parsing, slot/team-name setup, picks matched by name (mocked API)."""

import json

import pandas as pd

import pytest
from streamlit.testing.v1 import AppTest

import data_fetcher as dfm
import draft as d
from tests.test_app_smoke import APP

DRAFT_ID = "1257000000000000001"


@pytest.mark.parametrize("text,expected", [
    (f"https://sleeper.com/draft/nba/{DRAFT_ID}", DRAFT_ID),
    (f"sleeper.com/draft/nba/{DRAFT_ID}?ref=share", DRAFT_ID),
    (f"https://sleeper.com/draft/{DRAFT_ID}", DRAFT_ID),
    (f"  {DRAFT_ID} ", DRAFT_ID),
    ("https://sleeper.com/leagues/123/team", None),
    ("hello", None),
    ("", None),
])
def test_parse_sleeper_draft_id(text, expected):
    assert dfm.parse_sleeper_draft_id(text) == expected


def sleeper_draft(order=True):
    return {"draft_id": DRAFT_ID, "type": "snake", "status": "pre_draft", "league_id": "999",
            "settings": {"teams": 10, "rounds": 14},
            "draft_order": {"u1": 1, "u2": 2, "me": 7} if order else None}


MY_ROSTER = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL"] + ["BN"] * 6 + ["IR", "IR"]
USERS = [{"user_id": "u1", "display_name": "alpha", "metadata": {"team_name": "Alpha Dogs"}},
         {"user_id": "u2", "display_name": "bravo", "metadata": {}},
         {"user_id": "me", "display_name": "you"}]


def test_draft_setup_finds_my_slot_and_team_names():
    s = dfm.sleeper_draft_setup(sleeper_draft(), "me", USERS)
    assert (s["teams"], s["rounds"], s["my_slot"]) == (10, 14, 7)
    assert s["team_names"][:2] == ["Alpha Dogs", "bravo"] and s["team_names"][6] == "you" and len(s["team_names"]) == 10
    no_order = dfm.sleeper_draft_setup(sleeper_draft(order=False), "me", USERS)
    assert no_order["my_slot"] is None and not no_order["has_order"] and no_order["team_names"] == []


def test_sleeper_picks_match_by_name_without_metadata_download():
    s = d.new_state(d.DraftSettings(n_teams=10, n_rounds=14))
    known = {"test player one": {"player_id": "espn:1", "name": "Test Player One", "positions": ["PG"], "team": "BOS"}}
    pick = {"pick_no": 1, "draft_slot": 1, "player_id": "4001",
            "metadata": {"first_name": "Test Player", "last_name": "One", "position": "PG", "team": "BOS"}}
    applied, _ = d.apply_sleeper_picks(s, [pick], {}.get, lambda name: known.get(name.lower()))
    assert applied[0].player_id == "espn:1" and not applied[0].unmatched


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name, path in {"RUNTIME_DIR": tmp_path, "RAW_DIR": tmp_path / "raw", "CACHE_DIR": tmp_path / "cache",
                       "UPLOAD_DIR": tmp_path / "raw" / "uploads"}.items():
        monkeypatch.setattr(dfm, name, path)
    monkeypatch.setattr(dfm, "fetch_sleeper_draft", lambda draft_id, timeout=8: sleeper_draft())
    monkeypatch.setattr(dfm, "fetch_sleeper_user", lambda username, timeout=8: {"user_id": "me"})
    monkeypatch.setattr(dfm, "fetch_sleeper_league_users", lambda league_id, timeout=8: USERS)
    monkeypatch.setattr(dfm, "fetch_sleeper_league", lambda league_id, timeout=8: {"roster_positions": MY_ROSTER})

    def no_network(*a, **k):
        raise ConnectionError("offline test")
    monkeypatch.setattr(dfm, "fetch_sleeper_players", no_network)
    monkeypatch.setattr(dfm, "fetch_espn", no_network)
    picks = [{"pick_no": 1, "draft_slot": 1, "player_id": "4046",
              "metadata": {"first_name": "Nikola", "last_name": "Jokic", "position": "C", "team": "DEN"}}]
    monkeypatch.setattr(dfm, "fetch_sleeper_draft_picks", lambda draft_id, timeout=4: picks)
    return tmp_path


def test_connect_button_sets_up_the_league_and_syncs_picks(runtime):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    box = next(t for t in at.text_input if t.label == "Sleeper draft link")
    box.set_value(f"https://sleeper.com/draft/nba/{DRAFT_ID}").run()
    next(t for t in at.text_input if t.label.startswith("Your Sleeper username")).set_value("someone").run()
    next(b for b in at.button if b.label == "Connect to Sleeper draft").click().run()
    assert not at.exception, [e.value for e in at.exception]
    state = d.load_state(runtime / "draft_state.json")
    settings = json.loads((runtime / "settings.json").read_text())
    assert (state.settings.n_teams, state.settings.n_rounds, state.settings.my_slot) == (10, 14, 7)
    assert state.settings.team_names[0] == "Alpha Dogs"
    assert settings["sleeper_sync"] and settings["sleeper_draft_id"] == DRAFT_ID
    assert settings["starters"] == "PG, SG, G, SF, PF, F, C, UTIL x2"  # from the league's roster settings
    # The live-sync fragment pulled Sleeper's pick #1 and matched it to the dataset by name.
    assert state.picks and state.picks[0].name == "Nikola Jokic" and not state.picks[0].unmatched


def test_espn_snapshot_projects_the_rookies_the_model_cannot(runtime):
    """With ESPN's projections fetched, stat-less rookies take ESPN's line (matched by ESPN id)."""
    proj, _ = dfm.load_projections()
    rookie = proj[proj["MIN"].isna() & proj["proj_notes"].str.contains("draft pick")].iloc[0]
    espn = dfm.finalize_schema(pd.DataFrame([{
        "name": rookie["name"], "espn_id": rookie["espn_id"], "team": rookie["team"], "positions": ["SF"],
        "GP": 70, "MIN": 28.0, "PTS": 14.0, "REB": 5.0, "AST": 2.0, "3PM": 1.5, "STL": 0.9, "BLK": 0.4, "TO": 1.6,
        "FGM": 5.2, "FGA": 11.5, "FTM": 2.1, "FTA": 2.7}]), "ESPN projections 2026-27")
    dfm.save_snapshot("espn_projections", espn, dfm.SourceMeta(source="ESPN projections 2026-27", kind="projections",
                                                               fetched_at="2026-10-19T12:00:00"))
    filled, n = dfm.fill_missing_stats(proj, espn, "ESPN projection")
    row = filled[filled["espn_id"] == rookie["espn_id"]].iloc[0]
    assert n == 1 and row["PTS"] == 14.0 and row["proj_notes"].startswith("Rookie: ESPN projection")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    options = at.selectbox(key="w_search").options  # labels like "Name · TEAM · POS · #rank"
    label = next(o for o in options if o.startswith(f"{rookie['name']} ·"))
    assert options.index(label) < len(options) - 100  # ranked with real numbers, no longer at the bottom


def test_starters_from_sleeper_roster_positions():
    assert dfm.starters_from_roster_positions(MY_ROSTER) == "PG, SG, G, SF, PF, F, C, UTIL x2"
    assert dfm.starters_from_roster_positions([]) == ""
    import roster
    assert len(roster.parse_starters(dfm.starters_from_roster_positions(MY_ROSTER))) == 9


def test_sleeper_rank_becomes_adp_and_unranked_is_blank():
    data = {"1": {"player_id": "1", "full_name": "Test Player 001", "team": "BOS", "fantasy_positions": ["PF", "C"],
                  "active": True, "search_rank": 12, "injury_status": "Questionable"},
            "2": {"player_id": "2", "full_name": "Test Player 002", "team": "NYK", "fantasy_positions": ["PG"],
                  "active": True, "search_rank": 9999999}}
    df = dfm.parse_sleeper_players(data).set_index("name")
    assert df.at["Test Player 001", "adp_sleeper"] == 12 and pd.isna(df.at["Test Player 002", "adp_sleeper"])
    assert df.at["Test Player 001", "positions"] == ["PF", "C"] and df.at["Test Player 001", "injury_status"] == "Questionable"
