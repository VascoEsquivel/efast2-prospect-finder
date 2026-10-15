import json

import numpy as np
import pandas as pd
import pytest

import data_fetcher as dfm
from schema import NBA_TEAMS, expand_positions, normalize_team


# ── Names ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,key", [
    ("Nikola Jokić", "nikola jokic"),
    ("Luka Dončić", "luka doncic"),
    ("Jaren Jackson Jr.", "jaren jackson"),
    ("Jaren Jackson Jr", "jaren jackson"),
    ("Trey Murphy III", "trey murphy"),
    ("Ronald Holland II", "ronald holland"),
    ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
    ("De'Aaron Fox", "deaaron fox"),
    ("D.J. Augustin", "dj augustin"),
    ("  OG  Anunoby ", "og anunoby"),
    ("Kristaps Porziņģis", "kristaps porzingis"),
    (None, ""),
])
def test_normalize_name(raw, key):
    assert dfm.normalize_name(raw) == key


def test_overrides_apply_to_every_source(tmp_path):
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"_comment": "x", "Nicolas Claxton": "Nic Claxton"}))
    ov = dfm.load_overrides(path)
    assert ov == {"nicolas claxton": "nic claxton"}
    assert dfm.name_key("Nicolas Claxton", ov) == dfm.name_key("Nic Claxton", ov) == "nic claxton"
    dfm.save_override("Herb Jones", "Herbert Jones", path)
    assert dfm.load_overrides(path)["herb jones"] == "herbert jones"


def test_committed_overrides_file_parses():
    assert len(dfm.load_overrides()) >= 3


# ── Schema helpers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("PG/SG", ["PG", "SG"]), ("G", ["PG", "SG"]), ("F", ["SF", "PF"]), ("C", ["C"]),
    ("F-C", ["PF", "C"]), (["C", "PF"], ["PF", "C"]), ("SG, G", ["SG"]), (["SG", "G", "F"], ["SG"]),
    ("", []), (None, []), ("QB", []),
])
def test_expand_positions(raw, expected):
    assert expand_positions(raw) == expected


@pytest.mark.parametrize("raw,team", [("GS", "GSW"), ("NY", "NYK"), ("UTAH", "UTA"), ("WSH", "WAS"),
                                      ("PHO", "PHX"), ("lal", "LAL"), (None, "FA"), ("STARS", "FA")])
def test_normalize_team(raw, team):
    assert normalize_team(raw) == team


def test_finalize_schema_and_unique_ids():
    df = pd.DataFrame({"name": ["Test Player 001", "Test Player 001", "Test Player 002"],
                       "team": ["T", "U", "GS"], "PTS": ["10", 12, None], "positions": ["PG/SG", "C", None]})
    out = dfm.finalize_schema(df, "x")
    assert out["player_id"].is_unique
    assert out["positions"].tolist() == [["PG", "SG"], ["C"], []]
    assert out["team"].tolist() == ["FA", "FA", "GSW"]
    assert out["PTS"].dtype == float and np.isnan(out["PTS"].iloc[2])
    assert "FG%" not in out  # percentages are never stored, only makes/attempts


# ── Sources available offline ─────────────────────────────────────────────────

def test_synthetic_players_are_obviously_fake():
    df = dfm.synthetic_players()
    assert len(df) == 300
    assert df["name"].str.fullmatch(r"Test Player \d{3}").all()
    assert df["player_id"].is_unique
    assert (df["FGM"] <= df["FGA"]).all() and (df["FTM"] <= df["FTA"]).all()


def test_bundled_last_season_is_real_and_labeled():
    df, meta = dfm.load_last_season()
    assert "not projections" in meta.source
    assert len(df) > 500 and df["espn_id"].notna().all()
    sga = df[df["name"] == "Shai Gilgeous-Alexander"].iloc[0]
    assert sga["PTS"] == pytest.approx(31.1, abs=0.3)
    assert sga["team"] == "OKC" and sga["positions"] == ["PG", "SG"]
    assert (df["positions_source"] == "approx (ESPN G/F/C)").all()
    assert set(df["team"]) <= set(NBA_TEAMS) | {"FA"}


# ── Snapshot fallback ─────────────────────────────────────────────────────────

def test_fetch_falls_back_to_snapshot_when_api_dies(tmp_data):
    good = dfm.synthetic_players(10)
    meta = dfm.SourceMeta(source="X", kind="projections", fetched_at="2026-10-14T20:00:00")
    df, m = dfm.fetch_with_fallback("x", lambda: (good, meta))
    assert not m.from_cache and (tmp_data / "cache" / "x.parquet").exists()

    def dead():
        raise ConnectionError("API down")
    df2, m2 = dfm.fetch_with_fallback("x", dead)
    assert m2.from_cache and "API down" in m2.warnings[0]
    assert df2["positions"].tolist() == good["positions"].tolist()  # lists survive parquet
    with pytest.raises(dfm.SourceError, match="no cached snapshot"):
        dfm.fetch_with_fallback("never_fetched", dead)


def test_network_sources_fail_cleanly_offline(tmp_data):
    with pytest.raises(dfm.SourceError):
        dfm.fetch_espn()
    with pytest.raises(dfm.SourceError):
        dfm.fetch_sleeper_players()
    with pytest.raises(dfm.SourceError):
        dfm.fetch_market_rank_ecr()


def test_save_raw_keeps_newest(tmp_data):
    for i in range(5):
        dfm.save_raw("thing", {"i": i}, keep=2)
    assert len(list((tmp_data / "raw").glob("thing_*.json"))) <= 2


# ── CSV import ────────────────────────────────────────────────────────────────

def test_auto_map_common_headers():
    cols = ["Player", "Team", "Pos", "GP", "MPG", "PTS", "TRB", "AST", "3PTM", "STL", "BLK", "TOV",
            "FGM", "FGA", "FT%", "FTA", "ADP"]
    m = dfm.auto_map_columns(cols)
    assert m["name"] == "Player" and m["positions"] == "Pos" and m["MIN"] == "MPG"
    assert m["REB"] == "TRB" and m["3PM"] == "3PTM" and m["TO"] == "TOV" and m["FT%"] == "FT%"
    assert m["FTM"] is None and m["adp"] == "ADP"


def test_load_projection_csv_derives_makes_from_percentages():
    csv = ("Player,Pos,GP,MIN,PTS,REB,AST,3PM,STL,BLK,TO,FG%,FGA,FT%,FTA\n"
           "Test Player 001,PG/SG,70,34,25,5,7,3,1.2,0.4,3,47.5%,18,0.88,6\n")
    raw = dfm.read_csv_any(csv.encode("utf-8-sig"))
    df, meta = dfm.load_projection_csv(raw, dfm.auto_map_columns(raw.columns))
    row = df.iloc[0]
    assert row["FGM"] == pytest.approx(0.475 * 18) and row["FTM"] == pytest.approx(0.88 * 6)
    assert row["positions"] == ["PG", "SG"] and row["positions_source"] == "csv"
    assert any("derived" in w for w in meta.warnings)


def test_load_projection_csv_season_totals_become_per_game():
    csv = "Name\tGP\tMIN\tPTS\tFGM\tFGA\tFTM\tFTA\nTest Player 002\t50\t1500\t1000\t400\t800\t100\t125\n"
    raw = dfm.read_csv_any(csv)
    df, _ = dfm.load_projection_csv(raw, dfm.auto_map_columns(raw.columns), season_totals=True)
    assert df.iloc[0][["MIN", "PTS", "FGM", "FGA"]].tolist() == [30, 20, 8, 16]
    with pytest.raises(ValueError, match="GP"):
        dfm.load_projection_csv(raw.drop(columns="GP"), dfm.auto_map_columns(raw.drop(columns="GP").columns),
                                season_totals=True)


def test_load_adp_csv():
    raw = pd.DataFrame({"Player Name": ["Test Player 001", "Test Player 002", ""], "Avg Pick": ["1.4", "x", "3"]})
    m = dfm.auto_map_columns(raw.columns, dfm.ADP_TARGETS)
    adp, meta = dfm.load_adp_csv(raw, m)
    assert adp[["name", "adp"]].values.tolist() == [["Test Player 001", 1.4]]


# ── Matching ──────────────────────────────────────────────────────────────────

def test_match_by_id_then_name_and_namesakes_by_team():
    base = dfm.finalize_schema(pd.DataFrame({
        "name": ["Jalen Williams", "Jalen Williams", "Nikola Jokic", "Test Player 009"],
        "team": ["OKC", "DEN", "DEN", "BOS"], "espn_id": [None, None, "3112335", None]}), "base")
    other = dfm.finalize_schema(pd.DataFrame({
        "name": ["Nikola Jokić", "Jalen Williams", "Unknown Rookie", "N. Jokic"],
        "team": ["DEN", "DEN", "BOS", "DEN"], "espn_id": [None, None, None, "3112335"]}), "other")
    m = dfm.match_players(base, other)
    assert m.iloc[1] == 1  # namesake resolved by team
    assert m.iloc[3] == 2  # id match wins over the odd spelling
    assert np.isnan(m.iloc[0])  # Jokic already taken by the id match
    assert np.isnan(m.iloc[2])


def test_enrich_reports_unmatched_and_respects_position_priority():
    base, _ = dfm.load_last_season()
    sleeper = dfm.finalize_schema(pd.DataFrame({
        "name": ["Shai Gilgeous-Alexander", "Totally Unknown Guy", "Nikola Jokić"],
        "sleeper_id": ["9001", "9002", "9003"], "team": ["OKC", "BOS", "DEN"],
        "positions": [["PG"], ["SF"], ["C", "PF"]], "positions_source": "sleeper",
        "injury_status": ["", "", "Questionable"]}), "Sleeper")
    out, rep = dfm.enrich(base, sleeper, "Sleeper", live_metadata=True)
    sga = out[out["name"] == "Shai Gilgeous-Alexander"].iloc[0]
    assert sga["positions"] == ["PG"] and sga["positions_source"] == "sleeper" and sga["sleeper_id"] == "9001"
    assert out[out["name"] == "Nikola Jokic"].iloc[0]["injury_status"] == "Questionable"
    assert rep.matched == 2 and rep.unmatched_other["name"].tolist() == ["Totally Unknown Guy"]
    assert len(rep.unmatched_base) == len(base) - 2


def test_enrich_copies_adp_and_suggests_names():
    base = dfm.synthetic_players(20)
    adp = dfm.finalize_adp(pd.DataFrame({"name": ["Test Player 003", "Test Playr 004"], "adp": [5, 9]}))
    out, rep = dfm.enrich(base, adp, "ADP", adp_target="adp_sleeper")
    assert out.loc[out["name"] == "Test Player 003", "adp_sleeper"].iloc[0] == 5
    assert rep.unmatched_other["suggestions"].iloc[0].startswith("Test Player 004")


# ── ESPN parser (fixture shaped like the documented kona_player_info response) ─

def espn_fixture(ref: pd.DataFrame, swap: tuple[str, str] | None = None) -> dict:
    """Build an ESPN-shaped payload from known per-game numbers. NOT a captured live response."""
    inv = {v: k for k, v in dfm.ESPN_STAT_IDS.items()}
    players = []
    for _, r in ref.iterrows():
        avg = {inv[c]: float(r[c]) for c in ["PTS", "BLK", "STL", "AST", "REB", "TO", "FGM", "FGA",
                                             "FTM", "FTA", "3PM", "MIN"]}
        if swap:
            a, b = swap
            avg[a], avg[b] = avg[b], avg[a]
        gp = float(r["GP"])
        totals = {k: v * gp for k, v in avg.items()} | {"42": gp}
        proj_avg = {k: v * 1.05 for k, v in avg.items()}
        players.append({"id": int(r["espn_id"]), "player": {
            "id": int(r["espn_id"]), "fullName": r["name"], "proTeamId": 25, "defaultPositionId": 1,
            "eligibleSlots": [0, 1, 5, 11, 12, 13], "injuryStatus": "ACTIVE",
            "ownership": {"averageDraftPosition": 12.3, "percentOwned": 99.9},
            "stats": [
                {"id": "002026", "seasonId": 2026, "statSourceId": 0, "statSplitTypeId": 0, "scoringPeriodId": 0,
                 "stats": totals, "averageStats": avg},
                {"id": "102027", "seasonId": 2027, "statSourceId": 1, "statSplitTypeId": 0, "scoringPeriodId": 0,
                 "stats": {k: v * 70 for k, v in proj_avg.items()} | {"42": 70.0}, "averageStats": proj_avg},
                {"id": "012027", "seasonId": 2027, "statSourceId": 0, "statSplitTypeId": 1, "stats": {}},
            ]}})
    players.append({"id": 1, "player": {"id": 1, "fullName": "No Projection", "stats": []}})
    return {"players": players}


@pytest.fixture(scope="module")
def ref_players():
    df, _ = dfm.load_last_season()
    return df[df["GP"] >= 60].head(40)


def test_parse_espn_picks_projection_entry(ref_players):
    df, warnings = dfm.parse_espn(espn_fixture(ref_players), 2027)
    assert len(df) == len(ref_players)
    first = ref_players.iloc[0]
    row = df[df["espn_id"] == first["espn_id"]].iloc[0]
    assert row["PTS"] == pytest.approx(first["PTS"] * 1.05)  # projection, not last season's actuals
    assert row["GP"] == 70 and row["adp_espn"] == 12.3
    assert row["positions"] == ["PG", "SG"] and row["team"] == "OKC" and row["injury_status"] == ""
    assert row["player_id"] == f"espn:{first['espn_id']}"
    assert "1 ESPN players had no 2027 projection" in warnings[0]


def test_espn_entry_selection_by_source_and_split_ids():
    entries = [{"id": "weird", "seasonId": 2027, "statSourceId": 1, "statSplitTypeId": 0, "stats": {"0": 1}}]
    assert dfm.espn_stat_entry(entries, 2027, "projection") is entries[0]
    assert dfm.espn_stat_entry(entries, 2027, "actual") is None


def test_espn_totals_fallback_when_no_average_stats():
    pg = dfm.espn_per_game({"stats": {"0": 1400, "42": 70}})
    assert pg["PTS"] == 20 and pg["GP"] == 70


def test_verify_espn_stat_map_flags_swapped_ids(ref_players):
    ok = dfm.verify_espn_stat_map(espn_fixture(ref_players), ref_players)
    assert (ok["status"] == "✅ verified").all()
    bad = dfm.verify_espn_stat_map(espn_fixture(ref_players, swap=("1", "2")), ref_players)
    by_id = bad.set_index("stat_id")
    assert by_id.loc["1", "status"] == "❌ mismatch" and by_id.loc["1", "best_match_id"] == "2"
    assert by_id.loc["0", "status"] == "✅ verified"


# ── Sleeper parser (fixture in the documented players/nba shape) ──────────────

def test_parse_sleeper_players():
    data = {
        "1001": {"player_id": "1001", "full_name": "Test Player 001", "team": "BOS", "active": True,
                 "fantasy_positions": ["PG", "SG"], "position": "PG", "injury_status": "Questionable",
                 "espn_id": 4433134},
        "1002": {"player_id": "1002", "first_name": "Test", "last_name": "Player 002", "team": None,
                 "active": False, "fantasy_positions": ["C"]},
        "1003": {"player_id": "1003", "full_name": "Test Player 003", "team": "NY", "active": True,
                 "fantasy_positions": None, "position": "F"},
    }
    df = dfm.parse_sleeper_players(data)
    assert df["name"].tolist() == ["Test Player 001", "Test Player 003"]
    first = df.iloc[0]
    assert first["espn_id"] == "4433134" and first["sleeper_id"] == "1001" and first["positions"] == ["PG", "SG"]
    assert df.iloc[1]["team"] == "NYK" and df.iloc[1]["positions"] == ["SF", "PF"]


def test_nba_api_frame_parser():
    raw = pd.DataFrame({"PLAYER_ID": [1628983], "PLAYER_NAME": ["Shai Gilgeous-Alexander"], "TEAM_ABBREVIATION": ["OKC"],
                        "GP": [68], "MIN": [33.3], "PTS": [31.1], "REB": [4.3], "AST": [6.6], "FG3M": [1.7],
                        "STL": [1.4], "BLK": [0.8], "TOV": [2.2], "FGM": [10.8], "FGA": [19.4], "FTM": [7.9],
                        "FTA": [9.0]})
    df = dfm.finalize_schema(dfm.parse_nba_api_frame(raw), "nba_api")
    assert df.iloc[0]["player_id"] == "nba:1628983" and df.iloc[0]["3PM"] == 1.7 and df.iloc[0]["TO"] == 2.2
