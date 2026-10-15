"""Fantasy basketball draft assistant (Streamlit UI only; all logic lives in the other modules).

Run:  streamlit run app.py
"""

from __future__ import annotations

import html
import json
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import data_fetcher as dfm
import draft
import roster
import valuation as val
from schema import ADP_COLUMNS

STATE_PATH = dfm.RUNTIME_DIR / "draft_state.json"
SETTINGS_PATH = dfm.RUNTIME_DIR / "settings.json"

SOURCES = {
    "csv": "Projection CSV (upload)",
    "espn": "ESPN projections 2026-27",
    "last_season": "Last season 2025-26 actuals (bundled)",
    "nba_api": "Last season 2025-26 actuals (nba_api refresh)",
    "synthetic": "Synthetic test data (fake players)",
}
ADP_TARGET_LABELS = {"adp_sleeper": "Sleeper ADP", "adp_espn": "ESPN ADP", "adp_market": "Other / market rank"}
DEFAULT_SETTINGS = {
    "source": "last_season",
    "adp_compare": "auto",
    "template": "9-cat",
    "categories": list(val.CATEGORIES),
    "punts": [],
    "stat_mode": "per_game",
    "min_gp": 10,
    "steal_min_gap": 3.0,
    "steal_pct": 0.15,
    "points_weights": dict(val.ESPN_DEFAULT_POINTS),
    "starters": "PG, SG, SF, PF, C, G, F, UTIL x3",
    "use_sleeper_meta": True,
    "use_espn_meta": True,
    "use_ecr": False,
    "proj_mapping": None,
    "proj_season_totals": False,
    "adp_mapping": None,
    "adp_target": "adp_sleeper",
    "sleeper_draft_id": "",
    "sleeper_sync": False,
    "order": "My rank",
    "pos_filter": "All",
    "search_mode": "contains",
}
SEARCH_MODES = {"contains": "Best-ranked match first", "fuzzy": "Fuzzy (typo-tolerant)"}
POS_FILTERS = ["All", "PG", "SG", "SF", "PF", "C", "G", "F"]

st.set_page_config(page_title="Draft Assistant", page_icon="🏀", layout="wide")
ss = st.session_state


# ── Persistence helpers ───────────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def save_settings(s: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(SETTINGS_PATH)
    ss.saved_settings_json = json.dumps(s, sort_keys=True)


def save_settings_if_changed(s: dict) -> None:
    if json.dumps(s, sort_keys=True) != ss.get("saved_settings_json"):
        save_settings(s)


def persist() -> None:
    """Autosave the draft after every mutation."""
    draft.save_state(ss.draft, STATE_PATH)


def flash(kind: str, text: str) -> None:
    ss.setdefault("flash", []).append((kind, text))


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


# ── Boot / resume ─────────────────────────────────────────────────────────────

def boot() -> None:
    if ss.get("booted"):
        return
    ss.settings = load_settings()
    ss.saved_settings_json = json.dumps(ss.settings, sort_keys=True)
    ss.pending_resume = None
    try:
        saved = draft.load_state(STATE_PATH)
    except draft.DraftError as exc:
        flash("error", str(exc))
        saved = None
    if saved is not None and saved.picks:
        ss.pending_resume = saved
        ss.draft = draft.new_state(draft.DraftSettings(**asdict(saved.settings)))
    else:
        ss.draft = saved or draft.new_state()
    ss.booted = True


def resume_banner() -> None:
    saved: draft.DraftState = ss.pending_resume
    when = saved.updated_at.replace("T", " ")
    st.warning(f"**Saved draft found:** {len(saved.picks)} picks, {saved.settings.n_teams} teams, "
               f"last saved {when}. Resume it or start fresh (the old file is archived, never deleted).")
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("▶ Resume draft", type="primary", key="resume_yes"):
        ss.draft = saved
        ss.pending_resume = None
        st.rerun()
    if c2.button("Start fresh", key="resume_no"):
        archived = draft.archive_state(STATE_PATH)
        ss.draft = draft.reset(saved)
        ss.pending_resume = None
        persist()
        flash("info", f"Previous draft archived as {archived.name if archived else '(nothing)'}")
        st.rerun()


# ── Data assembly (cached) ────────────────────────────────────────────────────

def data_signature(s: dict) -> str:
    """Everything on disk that changes the dataset; part of every cache key."""
    files = [dfm.UPLOAD_DIR / "projections.csv", dfm.UPLOAD_DIR / "adp.csv", dfm.OVERRIDES_PATH,
             dfm.BUNDLED_LAST_SEASON]
    files += [dfm.CACHE_DIR / f"{k}.parquet" for k in
              ["espn_projections", "nba_api_last_season", "sleeper_players", "market_rank_ecr"]]
    keys = ["source", "use_sleeper_meta", "use_espn_meta", "use_ecr", "proj_mapping", "proj_season_totals",
            "adp_mapping", "adp_target"]
    return json.dumps({"files": [mtime(f) for f in files], **{k: s[k] for k in keys}}, sort_keys=True, default=str)


def _load_base(source: str, s: dict):
    if source == "last_season":
        return dfm.load_last_season()
    if source == "synthetic":
        return dfm.load_synthetic()
    if source in ("espn", "nba_api"):
        key = "espn_projections" if source == "espn" else "nba_api_last_season"
        snap = dfm.load_snapshot(key)
        if snap is None:
            raise dfm.SourceError(f"no {SOURCES[source]} snapshot yet: use the fetch button in the Data tab")
        return snap
    if source == "csv":
        path = dfm.UPLOAD_DIR / "projections.csv"
        if not path.exists():
            raise dfm.SourceError("no projections CSV uploaded yet (sidebar → Data)")
        raw = dfm.read_csv_any(path.read_bytes())
        mapping = s["proj_mapping"] or dfm.auto_map_columns(raw.columns)
        return dfm.load_projection_csv(raw, mapping, s["proj_season_totals"], label="Projection CSV")
    raise dfm.SourceError(f"unknown source {source!r}")


@st.cache_data(show_spinner="Loading players…", max_entries=8)
def assemble(sig: str, s_json: str):
    """Base source + Sleeper/ESPN metadata + ADP sources, all name-matched. Never raises."""
    s = json.loads(s_json)
    overrides = dfm.load_overrides()
    errors: list[str] = []
    metas: list[dfm.SourceMeta] = []
    reports: list[dfm.MatchReport] = []
    base = meta = None
    for source in [s["source"], "last_season", "synthetic"]:
        try:
            base, meta = _load_base(source, s)
            if source != s["source"]:
                meta.warnings.insert(0, f"FALLBACK: {SOURCES[s['source']]} unavailable, showing {SOURCES[source]}")
            break
        except Exception as exc:  # noqa: BLE001 - fall through to the next source
            errors.append(f"{SOURCES.get(source, source)}: {exc}")
    base = dfm.apply_overrides(base, overrides)
    metas.append(meta)

    def add(key: str, label: str, **kw):
        nonlocal base
        snap = dfm.load_snapshot(key)
        if snap is None:
            return
        other, m = snap
        base, rep = dfm.enrich(base, dfm.apply_overrides(other, overrides), label, **kw)
        metas.append(m)
        reports.append(rep)

    if s["use_sleeper_meta"]:
        add("sleeper_players", "Sleeper metadata", live_metadata=True)
    if s["use_espn_meta"] and s["source"] != "espn":
        add("espn_projections", "ESPN (ADP, positions)", adp_target="adp_espn", live_metadata=True)
    adp_path = dfm.UPLOAD_DIR / "adp.csv"
    if adp_path.exists() and s["adp_mapping"]:
        try:
            raw = dfm.read_csv_any(adp_path.read_bytes())
            adp_df, m = dfm.load_adp_csv(raw, s["adp_mapping"], label=f"ADP CSV ({ADP_TARGET_LABELS[s['adp_target']]})")
            base, rep = dfm.enrich(base, dfm.apply_overrides(adp_df, overrides), m.source, adp_target=s["adp_target"])
            metas.append(m)
            reports.append(rep)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ADP CSV: {exc}")
    if s["use_ecr"]:
        add("market_rank_ecr", "FantasyPros ECR", adp_target="adp_market")
    return base, metas, reports, errors


@st.cache_data(show_spinner=False, max_entries=16)
def valued_players(sig: str, s_json: str, vs_json: str, adp_col: str | None):
    base, metas, reports, errors = assemble(sig, s_json)
    vs = val.ValuationSettings(**{k: tuple(map(tuple, v)) if k == "points_weights" else
                                  (tuple(v) if isinstance(v, list) else v) for k, v in json.loads(vs_json).items()})
    out, info = val.valuate(base, vs, adp_col)
    return out, info, metas, reports, errors


def valuation_settings(s: dict, st_: draft.DraftSettings) -> val.ValuationSettings:
    cats = [c for c in s["categories"] if c in val.CATEGORY_TEMPLATES[s["template"]]]
    return val.ValuationSettings(
        n_teams=st_.n_teams, roster_size=st_.n_rounds, template=s["template"], categories=tuple(cats),
        punts=tuple(p for p in s["punts"] if p in cats), stat_mode=s["stat_mode"], min_gp=int(s["min_gp"]),
        points_weights=tuple(s["points_weights"].items()), steal_min_gap=float(s["steal_min_gap"]),
        steal_pct=float(s["steal_pct"]))


def pick_adp_column(s: dict, df: pd.DataFrame) -> str | None:
    if s["adp_compare"] == "none":
        return None
    if s["adp_compare"] in ADP_COLUMNS.values():
        return s["adp_compare"]
    for col in ["adp_espn", "adp_sleeper", "adp_market"]:
        if col in df and df[col].notna().any():
            return col
    return None


# ── Picks ─────────────────────────────────────────────────────────────────────

def resolve_picks(state: draft.DraftState, by_id: dict, by_key: dict) -> tuple[dict[int, str | None], list[draft.Pick]]:
    """Map each pick to a player_id in the current dataset (by id, then by name)."""
    resolved, missing = {}, []
    for p in state.picks:
        if p.player_id in by_id:
            resolved[p.pick_no] = p.player_id
        else:
            rec = by_key.get(dfm.normalize_name(p.name))
            resolved[p.pick_no] = rec["player_id"] if rec else None
            if rec is None:
                missing.append(p)
    return resolved, missing


def do_pick(pid: str) -> None:
    state: draft.DraftState = ss.draft
    rec = ss.player_by_id.get(pid)
    if rec is None:
        flash("error", f"unknown player id {pid}")
        return
    if pid in ss.drafted_now:
        flash("error", f"{rec['name']} is already drafted")
        return
    team = ss.get("w_team_override")
    team = None if team in (None, 0) else int(team)
    pick_no = int(ss.get("w_pick_override") or 0) or None
    source = "keeper" if ss.get("w_keeper") else "manual"
    try:
        p = draft.make_pick(state, rec, team=team, pick_no=pick_no, source=source)
    except draft.DraftError as exc:
        flash("error", str(exc))
        return
    persist()
    ss.w_team_override = 0
    ss.w_pick_override = 0
    ss.w_keeper = False
    ss.refocus = True
    flash("success", f"#{p.pick_no} {state.settings.team_name(p.team)}: {p.name}")


def on_search_pick() -> None:
    pid = ss.get("w_search")
    ss.w_search = None
    if pid:
        do_pick(pid)


def on_undo() -> None:
    p = draft.undo_last(ss.draft)
    if p:
        persist()
        flash("info", f"Undid #{p.pick_no} {p.name}")


def on_remove(pick_no: int) -> None:
    p = draft.remove_pick(ss.draft, pick_no)
    if p:
        persist()
        flash("info", f"Removed #{p.pick_no} {p.name}; the next entry fills pick #{p.pick_no}")


# ── Display helpers ───────────────────────────────────────────────────────────

def z_color(v, scale: float = 2.5) -> str:
    """Red (-scale) → white (0) → green (+scale) background."""
    if v is None or not np.isfinite(v):
        return ""
    t = max(-1.0, min(1.0, float(v) / scale))
    if t >= 0:
        r, g, b = (int(255 + (99 - 255) * t), int(255 + (190 - 255) * t), int(255 + (123 - 255) * t))
    else:
        t = -t
        r, g, b = (int(255 + (248 - 255) * t), int(255 + (105 - 255) * t), int(255 + (107 - 255) * t))
    return f"background-color: rgb({r},{g},{b}); color: #111"


def rank_color(rank, n) -> str:
    if rank is None or not n:
        return ""
    return z_color(2.5 * (1 - 2 * (rank - 1) / max(n - 1, 1)))


def pos_label(positions, src) -> str:
    s = "/".join(positions or [])
    return f"{s} ≈" if str(src).startswith("approx") and s else s


def player_label(rec: dict) -> str:
    # Accents stripped so typing "jokic" finds "Jokić".
    name = "".join(ch for ch in unicodedata.normalize("NFKD", rec["name"]) if not unicodedata.combining(ch))
    return f"{name} · {rec['team']} · {'/'.join(rec['positions'] or []) or '?'} · #{rec['rank']}"


# ── App ───────────────────────────────────────────────────────────────────────

boot()
if ss.pending_resume is not None:
    st.title("🏀 Draft Assistant")
    resume_banner()
    st.stop()

state: draft.DraftState = ss.draft
S: dict = ss.settings
has_picks = bool(state.picks)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("League")
    if has_picks:
        st.caption("Teams and rounds are locked while picks exist (reset the draft to change them).")
    c1, c2 = st.columns(2)
    n_teams = c1.number_input("Teams", 8, 16, state.settings.n_teams, disabled=has_picks)
    n_rounds = c2.number_input("Roster size", 5, 20, state.settings.n_rounds, disabled=has_picks,
                               help="Players per team = draft rounds")
    my_slot = st.number_input("My draft slot", 1, int(n_teams), min(state.settings.my_slot, int(n_teams)))
    with st.expander("Team names"):
        names_text = st.text_area("One per line, in draft-slot order", "\n".join(state.settings.team_names), height=150)
    new_names = [n.strip() for n in names_text.splitlines()]
    league_changed = (n_teams, n_rounds, my_slot, new_names) != (
        state.settings.n_teams, state.settings.n_rounds, state.settings.my_slot, state.settings.team_names)
    if league_changed:
        state.settings.n_teams, state.settings.n_rounds = int(n_teams), int(n_rounds)
        state.settings.my_slot, state.settings.team_names = int(my_slot), new_names
        persist()
    starters_text = st.text_input("Starting slots", S["starters"], help="e.g. PG, SG, SF, PF, C, G, F, UTIL x3")
    try:
        starters = roster.parse_starters(starters_text)
        S["starters"] = starters_text
    except ValueError as exc:
        st.error(str(exc))
        starters = roster.parse_starters(DEFAULT_SETTINGS["starters"])
    n_bench = state.settings.n_rounds - len(starters)
    st.caption(f"{len(starters)} starters + {max(n_bench, 0)} bench = {state.settings.n_rounds}")
    if n_bench < 0:
        st.error("Roster size is smaller than the number of starting slots.")

    st.header("Scoring")
    S["template"] = st.radio("Format", list(val.CATEGORY_TEMPLATES), index=list(val.CATEGORY_TEMPLATES).index(S["template"]),
                             horizontal=True)
    if S["template"] == "points":
        st.caption("Fantasy points per stat (prefilled with ESPN's default H2H points scoring).")
        w = pd.DataFrame({"stat": list(S["points_weights"]), "points": list(S["points_weights"].values())})
        edited = st.data_editor(w, hide_index=True, disabled=["stat"], key="w_points", width="stretch")
        S["points_weights"] = {r.stat: float(r.points) for r in edited.itertuples()}
        if st.button("Reset to ESPN default"):
            S["points_weights"] = dict(val.ESPN_DEFAULT_POINTS)
            save_settings(S)
            st.rerun()
    else:
        tmpl = val.CATEGORY_TEMPLATES[S["template"]]
        cats = [c for c in S["categories"] if c in tmpl] or list(tmpl)
        S["categories"] = st.multiselect("Categories", tmpl, cats)
        S["punts"] = st.multiselect("Punt", S["categories"], [p for p in S["punts"] if p in S["categories"]])
    S["stat_mode"] = st.radio("Stat mode", ["per_game", "season"], index=["per_game", "season"].index(S["stat_mode"]),
                              format_func={"per_game": "Per game", "season": "Season totals (× GP)"}.get,
                              horizontal=True)
    with st.expander("Valuation options"):
        S["min_gp"] = st.number_input("Min GP to enter the replacement pool", 0, 82, int(S["min_gp"]))
        S["steal_min_gap"] = st.number_input("STEAL/REACH minimum gap (picks)", 0.0, 50.0, float(S["steal_min_gap"]), 1.0)
        S["steal_pct"] = st.number_input("STEAL/REACH gap as share of ADP", 0.0, 1.0, float(S["steal_pct"]), 0.01)

    st.header("Data")
    S["source"] = st.selectbox("Player data", list(SOURCES), index=list(SOURCES).index(S["source"]),
                               format_func=SOURCES.get)
    if S["source"] == "csv":
        up = st.file_uploader("Projections CSV", type=["csv", "tsv", "txt"], key="w_proj_upload")
        if up is not None and up.getvalue() != ss.get("last_proj_upload"):
            ss.last_proj_upload = up.getvalue()
            dfm.save_upload("projections", up.getvalue())
            S["proj_mapping"] = dfm.auto_map_columns(dfm.read_csv_any(up.getvalue()).columns)
            flash("info", "Projections CSV saved; check the column mapping in the Data tab")
        S["proj_season_totals"] = st.checkbox("CSV values are season totals", S["proj_season_totals"])
    adp_opts = ["auto", *ADP_COLUMNS.values(), "none"]
    adp_names = {"auto": "Auto (first available)", "none": "None", **{v: k for k, v in ADP_COLUMNS.items()}}
    S["adp_compare"] = st.selectbox("Compare against", adp_opts, index=adp_opts.index(S["adp_compare"]),
                                    format_func=adp_names.get)
    with st.expander("ADP / metadata sources"):
        up = st.file_uploader("ADP CSV", type=["csv", "tsv", "txt"], key="w_adp_upload")
        S["adp_target"] = st.selectbox("This ADP file is", list(ADP_TARGET_LABELS),
                                       index=list(ADP_TARGET_LABELS).index(S["adp_target"]),
                                       format_func=ADP_TARGET_LABELS.get)
        if up is not None and up.getvalue() != ss.get("last_adp_upload"):
            ss.last_adp_upload = up.getvalue()
            dfm.save_upload("adp", up.getvalue())
            S["adp_mapping"] = dfm.auto_map_columns(dfm.read_csv_any(up.getvalue()).columns, dfm.ADP_TARGETS)
        S["use_sleeper_meta"] = st.checkbox("Use Sleeper positions / injuries / IDs", S["use_sleeper_meta"])
        S["use_espn_meta"] = st.checkbox("Use ESPN ADP / positions (when fetched)", S["use_espn_meta"])
        S["use_ecr"] = st.checkbox("Use FantasyPros ECR (points, 9/29) as market rank", S["use_ecr"])
        st.caption("Fetch or refresh these in the Data tab.")

    S["search_mode"] = st.radio(
        "Pick search", list(SEARCH_MODES), index=list(SEARCH_MODES).index(S["search_mode"]),
        format_func=SEARCH_MODES.get, horizontal=True,
        help="Best-ranked: the top suggestion is the best available player whose name contains what you typed. "
             "Fuzzy: tolerates typos, but may rank a weak player with a closer spelling first.")

    st.header("Sleeper live sync")
    S["sleeper_draft_id"] = st.text_input("Sleeper draft_id", S["sleeper_draft_id"]).strip()
    S["sleeper_sync"] = st.toggle("Poll picks every 5 s", S["sleeper_sync"], disabled=not S["sleeper_draft_id"])
    if S["sleeper_draft_id"] and st.button("Load teams/rounds from Sleeper", disabled=has_picks):
        try:
            d = dfm.fetch_sleeper_draft(S["sleeper_draft_id"])
            cfg_ = d.get("settings") or {}
            state.settings.n_teams = int(cfg_.get("teams") or state.settings.n_teams)
            state.settings.n_rounds = int(cfg_.get("rounds") or state.settings.n_rounds)
            persist()
            flash("success", f"Sleeper draft: {state.settings.n_teams} teams x {state.settings.n_rounds} rounds, "
                             f"type {d.get('type')}, status {d.get('status')}. Set your draft slot above.")
        except Exception as exc:  # noqa: BLE001
            flash("error", f"Could not load the Sleeper draft: {exc}")
        st.rerun()

save_settings_if_changed(S)

# ── Valuation ─────────────────────────────────────────────────────────────────
sig = data_signature(S)
s_json = json.dumps(S, sort_keys=True, default=str)
base_preview, *_ = assemble(sig, s_json)
adp_col = pick_adp_column(S, base_preview)
vs = valuation_settings(S, state.settings)
players, info, metas, reports, load_errors = valued_players(sig, s_json, json.dumps(asdict(vs)), adp_col)

recs = players[["player_id", "name", "name_key", "team", "positions", "sleeper_id", "espn_id", "rank"]].to_dict("records")
ss.player_by_id = {r["player_id"]: r for r in recs}
by_key: dict[str, dict] = {}
for r in sorted(recs, key=lambda r: r["rank"]):
    by_key.setdefault(r["name_key"], r)
resolved, unresolved = resolve_picks(state, ss.player_by_id, by_key)
drafted = {pid for pid in resolved.values() if pid}
ss.drafted_now = drafted

# ── Header strip ──────────────────────────────────────────────────────────────
for kind, text in ss.pop("flash", []):
    if kind == "error":
        st.error(text)
    else:
        st.toast(text, icon="✅" if kind == "success" else "ℹ️")

meta = metas[0]
cur = state.current_pick()
N = state.settings.n_teams
me = state.settings.my_slot
src_line = f"**{meta.source}** · fetched {meta.fetched_at.replace('T', ' ')}" + (" · ⚠ cached snapshot" if meta.from_cache else "")
st.caption(src_line + (f" · ADP: {next(k for k, v in ADP_COLUMNS.items() if v == adp_col)}" if adp_col else " · no ADP loaded"))
for w in meta.warnings:
    if w.startswith("FALLBACK"):
        st.error(w)
if "ACTUALS" in meta.source:
    st.info("Valuing on **last season's actual stats** (not 2026-27 projections). Upload projections or fetch ESPN for real projections.", icon="📅")

h1, h2, h3, h4, h5 = st.columns([1.1, 1.4, 1.5, 4.2, 0.8])
if cur is None:
    h1.metric("Pick", "Done")
    h2.metric("On the clock", "—")
    clock_team = None
    next_mine = None
else:
    r = draft.round_of(cur, N)
    h1.metric("Pick", f"#{cur}", f"Rd {r}.{cur - (r - 1) * N}", delta_color="off", delta_arrow="off")
    clock_team = draft.team_for_pick(cur, N)
    h2.metric("On the clock", ("🟢 YOU" if clock_team == me else state.settings.team_name(clock_team)))
    next_mine = state.next_pick_for_team(me)
    if state.reopened_slot():
        h2.caption(f"refilling reopened pick #{cur}")
h3.metric("Your next pick", f"#{next_mine}" if next_mine else "—",
          f"in {next_mine - cur} picks" if next_mine and cur else None, delta_color="off", delta_arrow="off")

available = players[~players["player_id"].isin(drafted)].sort_values("rank")
with h4.container(key="pick_search"):
    st.selectbox(
        "Draft a player", available["player_id"].tolist(), index=None, key="w_search", on_change=on_search_pick,
        format_func=lambda pid: player_label(ss.player_by_id[pid]), label_visibility="collapsed",
        filter_mode=S["search_mode"],
        placeholder=(f"Type a few letters + Enter to draft for {state.settings.team_name(clock_team)}"
                     if clock_team else "Draft complete"), disabled=cur is None)
h5.button("↶ Undo", on_click=on_undo, disabled=not state.picks, width="stretch")
with h4.expander("Pick options: traded pick / keeper / out of order"):
    o1, o2, o3 = st.columns(3)
    o1.selectbox("Team", [0, *range(1, N + 1)], key="w_team_override",
                 format_func=lambda t: "On the clock" if t == 0 else state.settings.team_name(t))
    o2.number_input("Pick # (0 = current)", 0, state.total_picks, key="w_pick_override")
    o3.checkbox("Keeper", key="w_keeper")
recent = sorted(state.picks, key=lambda p: p.seq)[-4:][::-1]
if recent:
    st.caption("Last picks: " + " · ".join(f"#{p.pick_no} {state.settings.team_name(p.team)}: {p.name}" for p in recent))

# ── Sleeper sync (fragment polls without rerunning the whole app) ─────────────
if S["sleeper_sync"] and S["sleeper_draft_id"]:
    sleeper_meta = dfm.load_snapshot("sleeper_players")
    by_sleeper = {r["sleeper_id"]: r for r in recs if r["sleeper_id"]}
    sl_to_name = {}
    if sleeper_meta is not None:
        sl_to_name = dict(zip(sleeper_meta[0]["sleeper_id"], sleeper_meta[0]["name"]))

    def resolve_sleeper(sid: str):
        rec = by_sleeper.get(sid) or (by_key.get(dfm.name_key(sl_to_name[sid], dfm.load_overrides()))
                                      if sid in sl_to_name else None)
        return dict(rec) if rec else None

    @st.fragment(run_every=5)
    def sleeper_sync():
        try:
            picks = dfm.fetch_sleeper_draft_picks(S["sleeper_draft_id"], timeout=4)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"⚠ Sleeper sync failed at {datetime.now():%H:%M:%S}: {str(exc)[:120]}. Manual entry still works.")
            return
        applied, msgs = draft.apply_sleeper_picks(ss.draft, picks, resolve_sleeper)
        if applied:
            persist()
            for m in msgs:
                flash("info", m)
            flash("success", f"Sleeper: {len(applied)} new pick(s)")
            st.rerun(scope="app")
        st.caption(f"🔄 Sleeper sync: {len(picks)} picks seen · checked {datetime.now():%H:%M:%S}")

    sleeper_sync()

if unresolved:
    st.warning(f"{len(unresolved)} pick(s) aren't in the current dataset (still on the board, excluded from totals): "
               + ", ".join(p.name for p in unresolved[:8]))

# ── Team rosters & standings ──────────────────────────────────────────────────
valued = players.set_index("player_id")
team_ids = {t: [resolved[p.pick_no] for p in state.team_picks(t) if resolved.get(p.pick_no)] for t in range(1, N + 1)}
cats = info.get("categories", [])
table_t = roster.team_category_table(valued, team_ids, cats, vs.stat_mode) if cats and not vs.is_points else None
fit_weights = None
if table_t is not None and len(team_ids.get(me, [])) >= 3:
    fit_weights = roster.team_fit_weights(table_t, me, cats, vs.punts)

tab_room, tab_board, tab_data = st.tabs(["Draft Room", "Draft Board", "Data"])

# ── Draft Room ────────────────────────────────────────────────────────────────
with tab_room:
    left, right = st.columns([3, 2])
    with left:
        f1, f2 = st.columns([3, 2])
        S["pos_filter"] = f1.segmented_control("Position", POS_FILTERS, default=S["pos_filter"], key="w_pos") or "All"
        orders = ["My rank", "ADP"] + (["Team fit"] if fit_weights else [])
        S["order"] = f2.selectbox("Order", orders, index=orders.index(S["order"]) if S["order"] in orders else 0,
                                  help="Team fit unlocks after your 3rd pick")
        view = available.copy()
        if fit_weights:
            view["fit"] = roster.team_fit_scores(view.set_index("player_id"), fit_weights).to_numpy()
        if S["pos_filter"] != "All":
            want = {"G": {"PG", "SG"}, "F": {"SF", "PF"}}.get(S["pos_filter"], {S["pos_filter"]})
            view = view[view["positions"].map(lambda ps: bool(want & set(ps)))]
        if S["order"] == "ADP":
            view = view.sort_values("adp", na_position="last")
        elif S["order"] == "Team fit" and fit_weights:
            view = view.sort_values("fit", ascending=False)
        gone = val.likely_gone(view["adp"], next_mine)
        table = pd.DataFrame({
            "Rk": view["rank"], "Player": view["name"], "Team": view["team"],
            "Pos": [pos_label(p, s) for p, s in zip(view["positions"], view["positions_source"])],
            "Inj": view["injury_status"], "GP": view["GP"],
        })
        z_cols = []
        if vs.is_points:
            table["FP"] = view["FP"]
            table["Value"] = view["FP_value"]
            for c in ["PTS", "REB", "AST", "STL", "BLK", "TO"]:
                table[c] = view[c]
        else:
            table["Z"] = view["Z_total"]
            if fit_weights:
                table["Fit"] = view["fit"]
            for c in cats:
                table[c + ("·p" if c in vs.punts else "")] = view[f"Z_{c}"]
                z_cols.append(c + ("·p" if c in vs.punts else ""))
        table["ADP"] = view["adp"]
        table["Δ"] = view["delta"]
        table["Flag"] = view["flag"].map({"STEAL": "🟢 STEAL", "REACH": "🔴 REACH"}).fillna("")
        table["Next"] = np.where(gone, f"⏳ gone by #{next_mine}", "")
        # Market columns sit right after the value so STEAL/REACH is visible without scrolling.
        lead = ["Rk", "Player", "Team", "Pos", "Z", "Fit", "FP", "Value", "ADP", "Δ", "Flag", "Next", "Inj", "GP"]
        table = table[[c for c in lead if c in table] + [c for c in table if c not in lead]].reset_index(drop=True)
        styler = table.style.map(z_color, subset=z_cols)
        if not vs.is_points:
            styler = styler.map(z_color, subset=["Z"] + (["Fit"] if fit_weights else []),
                                scale=max(2.5, 2.5 * len(cats) ** 0.5))
        cfg = {c: st.column_config.NumberColumn(c, format="%.2f", width="small") for c in z_cols}
        cfg.update({
            "Rk": st.column_config.NumberColumn(width=42),
            "Team": st.column_config.TextColumn(width=48),
            "Pos": st.column_config.TextColumn(width=64),
            "Flag": st.column_config.TextColumn(width=84),
            "Next": st.column_config.TextColumn(width=110),
            "Z": st.column_config.NumberColumn(format="%.2f", width=58),
            "Fit": st.column_config.NumberColumn(format="%.2f", width="small", help="Z re-weighted for your team"),
            "GP": st.column_config.NumberColumn(format="%d", width="small"),
            "ADP": st.column_config.NumberColumn(format="%.1f", width=56),
            "Δ": st.column_config.NumberColumn(format="%+.0f", width=48),
            "FP": st.column_config.NumberColumn(format="%.1f"),
            "Value": st.column_config.NumberColumn(format="%+.1f"),
            **{c: st.column_config.NumberColumn(format="%.1f") for c in ["PTS", "REB", "AST", "STL", "BLK", "TO"]},
        })
        event = st.dataframe(styler, hide_index=True, height=620, column_config=cfg, on_select="rerun", placeholder="",
                             selection_mode="single-row", key=f"avail_{len(state.picks)}_{S['pos_filter']}_{S['order']}")
        rows = event.selection.rows if event is not None else []
        sel_pid = view["player_id"].iloc[rows[0]] if rows else None
        b1, b2 = st.columns([2, 3])
        b1.button(f"Draft {ss.player_by_id[sel_pid]['name']}" if sel_pid else "Select a row to draft",
                  type="primary", disabled=sel_pid is None or cur is None, on_click=do_pick, args=(sel_pid,))
        if not vs.is_points:
            lr = info.get("league_rates", {})
            b2.caption(f"Pool: top {vs.pool_size} · {info.get('iterations')} iterations"
                       f"{'' if info.get('converged') else ' (not converged)'} · lg FG% {lr.get('FG%', 0):.3f} · "
                       f"FT% {lr.get('FT%', 0):.3f}. ≈ = approximate positions; ·p = punted.")
        else:
            b2.caption(f"Replacement level: {info.get('replacement_fp', 0):.1f} FP ({vs.stat_mode.replace('_', ' ')}).")
        if fit_weights:
            st.caption("Team-fit weights (0 = punted or locked up, 1.5 = competitive middle): "
                       + " · ".join(f"{c} {w:g}" for c, w in fit_weights.items()))

    with right:
        st.subheader(f"My team · {state.settings.team_name(me)}")
        mine = [dict(ss.player_by_id[pid], value=float(valued.at[pid, "value"])) for pid in team_ids.get(me, [])]
        assignment = roster.assign_roster(mine, starters)
        st.dataframe(roster.roster_table(assignment, state.settings.n_rounds), hide_index=True, width="stretch")
        if vs.is_points:
            fp = {t: float(valued.loc[ids, "FP"].sum()) if ids else 0.0 for t, ids in team_ids.items()}
            order_ = sorted(fp, key=fp.get, reverse=True)
            st.metric("My projected FP", f"{fp.get(me, 0):.1f}", f"rank {order_.index(me) + 1} of {N}",
                      delta_color="off", delta_arrow="off")
        elif table_t is not None:
            summary = roster.my_category_summary(table_t, me, cats, vs.punts)
            show = pd.DataFrame({
                "Cat": summary["cat"],
                "Mine": [("" if v is None or pd.isna(v) else (f"{v:.3f}" if c in val.PCT_CATS else f"{v:.1f}"))
                         for v, c in zip(summary["value"], cats)],
                "ΣZ": summary["z"],
                "Rank": [f"{r} / {N}" if r else "—" for r in summary["rank"]],
            })
            st.caption("Category standings across all tracked teams (1 = best). FG%/FT% rank by team makes/attempts.")
            st.dataframe(show.style.apply(lambda col: [rank_color(r, N) for r in summary["rank"]], subset=["Rank"]),
                         hide_index=True, width="stretch",
                         column_config={"ΣZ": st.column_config.NumberColumn(format="%+.2f")})

# ── Draft Board ───────────────────────────────────────────────────────────────
POS_TINT = {"PG": "#dbe8ff", "SG": "#dbe8ff", "SF": "#dcf5e3", "PF": "#dcf5e3", "C": "#ffe7d1"}


def board_html() -> str:
    by_cell: dict[tuple[int, int], list[draft.Pick]] = {}
    for p in state.picks:
        by_cell.setdefault((draft.round_of(p.pick_no, N), p.team), []).append(p)
    cur_cell = (draft.round_of(cur, N), draft.team_for_pick(cur, N)) if cur else None
    head = "".join(f'<th class="{"me" if t == me else ""}">{html.escape(state.settings.team_name(t))}'
                   f'{" (you)" if t == me else ""}</th>' for t in range(1, N + 1))
    rows = []
    for r in range(1, state.settings.n_rounds + 1):
        cells = []
        for t in range(1, N + 1):
            classes = ["me"] if t == me else []
            if cur_cell == (r, t):
                classes.append("cur")
            picks = by_cell.get((r, t), [])
            if picks:
                inner = "".join(
                    f'<div class="pk" style="background:{POS_TINT.get((p.positions or [""])[0], "#eee")}">'
                    f'<b>{html.escape(p.name)}</b><br><span class="sub">#{p.pick_no} · {"/".join(p.positions)}'
                    f'{" · " + html.escape(p.nba_team) if p.nba_team else ""}{" · K" if p.source == "keeper" else ""}'
                    f'{" · ⚠" if p.unmatched else ""}</span></div>' for p in picks)
            else:
                slot = (r - 1) * N + (t if r % 2 else N + 1 - t)
                inner = f'<span class="sub">#{slot}</span>'
            cells.append(f'<td class="{" ".join(classes)}">{inner}</td>')
        arrow = "→" if r % 2 else "←"
        rows.append(f"<tr><th>{r} {arrow}</th>{''.join(cells)}</tr>")
    return f"""<style>
.board {{ border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 12px; color: #111; }}
.board th, .board td {{ border: 1px solid #bbb; padding: 2px 3px; vertical-align: top; overflow: hidden; }}
.board thead th {{ background: #f1f3f5; position: sticky; top: 0; }}
.board tbody th {{ background: #f1f3f5; width: 46px; }}
.board td {{ background: #fff; height: 40px; }}
.board .me {{ background: #fff4c2 !important; }}
.board .cur {{ outline: 3px solid #e8590c; outline-offset: -3px; }}
.board .pk {{ border-radius: 3px; padding: 1px 3px; margin-bottom: 1px; }}
.board .sub {{ color: #555; font-size: 11px; }}
</style><table class="board"><thead><tr><th>Rd</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"""


with tab_board:
    st.html(board_html())

# ── Data tab ──────────────────────────────────────────────────────────────────
with tab_data:
    st.subheader("Sources")
    for e in load_errors:
        st.warning(e)
    st.dataframe(pd.DataFrame([{
        "source": m.source, "kind": m.kind, "fetched": m.fetched_at.replace("T", " "), "players": m.n_players,
        "cached snapshot": "yes ⚠" if m.from_cache else "", "origin": m.origin, "notes": " | ".join(m.warnings),
    } for m in metas]), hide_index=True, width="stretch")

    st.markdown("**Fetch / refresh** (network calls happen only when you click; the app keeps working from disk snapshots if an API dies)")
    f1, f2, f3, f4 = st.columns(4)

    def run_fetch(label: str, fn):
        with st.spinner(f"Fetching {label}…"):
            try:
                _, m = fn()
            except Exception as exc:  # noqa: BLE001
                st.error(f"{label} failed: {exc}")
                return
        if m.from_cache:
            flash("error", f"{label}: live fetch failed, kept the cached snapshot. {m.warnings[0]}")
        else:
            flash("success", f"{label}: {m.n_players} players fetched")
        st.cache_data.clear()
        st.rerun()

    if f1.button("Fetch ESPN projections + ADP"):
        run_fetch("ESPN", lambda: dfm.fetch_with_fallback("espn_projections", dfm.fetch_espn))
    if f2.button("Refresh Sleeper metadata"):
        run_fetch("Sleeper", lambda: dfm.fetch_sleeper_players(force=True))
    if f3.button("Fetch FantasyPros ECR"):
        run_fetch("FantasyPros ECR", dfm.fetch_market_rank_ecr)
    if f4.button("Refresh last season (nba_api)"):
        run_fetch("nba_api", lambda: dfm.fetch_with_fallback("nba_api_last_season", dfm.fetch_nba_api_last_season))
    age = dfm.snapshot_age_hours("sleeper_players")
    st.caption("Sleeper metadata: " + ("not fetched yet" if age is None else f"{age:.1f} h old (refetch at most daily)"))

    st.subheader("Unmatched names")
    if not reports:
        st.caption("No secondary sources loaded, so nothing to match.")
    for rep in reports:
        um = rep.unmatched_other
        if "adp" in um and um["adp"].notna().any():
            um = um[um["adp"] <= 2 * vs.pool_size]
        with st.expander(f"{rep.source}: matched {rep.matched}/{rep.total} · {len(um)} unmatched shown", expanded=len(um) > 0 and len(um) < 40):
            st.dataframe(um, hide_index=True, width="stretch")
            top_missing = rep.unmatched_base.merge(players[["player_id", "rank"]], on="player_id")
            top_missing = top_missing[top_missing["rank"] <= vs.pool_size].sort_values("rank")
            if len(top_missing):
                st.caption(f"Top-{vs.pool_size} players this source doesn't have:")
                st.dataframe(top_missing[["rank", "name", "team"]], hide_index=True)

    with st.form("override_form"):
        st.markdown("**Add a name override** (alias → name in the main dataset). Applies to every source.")
        oc1, oc2 = st.columns(2)
        alias = oc1.text_input("Name as it appears in the other source")
        canonical = oc2.selectbox("Player in the main dataset", sorted(players["name"]), index=None)
        if st.form_submit_button("Save override") and alias and canonical:
            dfm.save_override(alias, canonical)
            st.cache_data.clear()
            st.rerun()

    if S["source"] == "csv" and (dfm.UPLOAD_DIR / "projections.csv").exists():
        st.subheader("Projection CSV column mapping")
        raw = dfm.read_csv_any((dfm.UPLOAD_DIR / "projections.csv").read_bytes())
        mapping = S["proj_mapping"] or dfm.auto_map_columns(raw.columns)
        with st.form("mapping_form"):
            cols_ = st.columns(4)
            new_map = {}
            options = [None, *raw.columns]
            for i, t in enumerate(dfm.PROJECTION_TARGETS):
                cur_col = mapping.get(t)
                new_map[t] = cols_[i % 4].selectbox(t, options, index=options.index(cur_col) if cur_col in options else 0,
                                                    key=f"map_{t}")
            if st.form_submit_button("Apply mapping"):
                S["proj_mapping"] = new_map
                save_settings(S)
                st.cache_data.clear()
                st.rerun()
        st.dataframe(raw.head(8), width="stretch")

    st.subheader("ESPN stat-ID mapping")
    raw_path = dfm.latest_raw("espn_kona_player_info")
    stat_map = pd.DataFrame({"stat_id": list(dfm.ESPN_STAT_IDS), "column": list(dfm.ESPN_STAT_IDS.values())})
    if raw_path is None:
        st.caption("Source: espn-api basketball constants. Fetch ESPN to verify it against 2025-26 box scores.")
        st.dataframe(stat_map, hide_index=True)
    else:
        try:
            check = dfm.verify_espn_stat_map(json.loads(raw_path.read_text()), dfm.load_last_season()[0])
            st.caption(f"Checked {raw_path.name}: ESPN's 2025-26 actuals vs bundled box-score averages, same ESPN ids.")
            st.dataframe(check, hide_index=True)
        except Exception as exc:  # noqa: BLE001
            st.error(f"could not verify stat map: {exc}")
            st.dataframe(stat_map, hide_index=True)

    st.subheader("Picks")
    if state.picks:
        pk = pd.DataFrame([{"pick": p.pick_no, "team": state.settings.team_name(p.team), "player": p.name,
                            "pos": "/".join(p.positions), "source": p.source, "entered": p.ts[11:]}
                           for p in state.picks])
        st.dataframe(pk, hide_index=True, width="stretch", height=260)
        rc1, rc2 = st.columns(2)
        to_remove = rc1.selectbox("Remove a specific pick", [p.pick_no for p in state.picks],
                                  format_func=lambda n: f"#{n} {state.filled()[n].name}")
        rc1.button("Remove pick", on_click=on_remove, args=(to_remove,))
        confirm = rc2.checkbox("I really want to reset the whole draft")
        if rc2.button("Reset draft", disabled=not confirm, type="secondary"):
            archived = draft.archive_state(STATE_PATH)
            ss.draft = draft.reset(state)
            persist()
            flash("info", f"Draft reset; old draft archived as {archived.name if archived else '-'}")
            st.rerun()
    st.caption(f"Draft autosaves to {STATE_PATH} after every pick.")

save_settings_if_changed(S)

# Put the cursor back in the search box right after a pick, for rapid entry.
if ss.pop("refocus", False):
    st.html("""<script>
setTimeout(() => { const el = document.querySelector('.st-key-pick_search input'); if (el) { el.focus(); } }, 250);
</script>""", unsafe_allow_javascript=True)
