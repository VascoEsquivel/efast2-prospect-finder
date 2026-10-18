"""Fantasy basketball draft assistant (Streamlit UI only; all logic lives in the other modules).

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import data_fetcher as dfm
import draft
import player_list
import roster
import ui
import valuation as val
from schema import ADP_COLUMNS

STATE_PATH = dfm.RUNTIME_DIR / "draft_state.json"
SETTINGS_PATH = dfm.RUNTIME_DIR / "settings.json"

SOURCES = {
    "proj_2027": "2026-27 projections (bundled model)",
    "csv": "Projection CSV (upload)",
    "espn": "ESPN projections 2026-27",
    "last_season": "Last season 2025-26 actuals (bundled)",
    "nba_api": "Last season 2025-26 actuals (nba_api refresh)",
    "synthetic": "Synthetic test data (fake players)",
}
ADP_TARGET_LABELS = {"adp_sleeper": "Sleeper ADP", "adp_espn": "ESPN ADP", "adp_market": "Other / market rank"}
DEFAULT_SETTINGS = {
    "settings_version": 2,
    "source": "proj_2027",
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

st.set_page_config(page_title="Draft Room", page_icon="🏀", layout="wide")
st.html(ui.css())
ss = st.session_state


# ── Persistence helpers ───────────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    if saved and saved.get("settings_version", 1) < 2:
        # v2 made the bundled 2026-27 projections the default; move anyone still on the old default.
        if saved.get("source") == "last_season":
            saved["source"] = "proj_2027"
        saved["settings_version"] = 2
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
    st.html(ui.resume_html(len(saved.picks), saved.settings.n_teams, saved.updated_at))
    st.warning("Saved draft found. Resume it, or start fresh (the old draft is archived, never deleted).")
    c1, c2, _ = st.columns([1, 1, 4], gap="small")
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
             dfm.BUNDLED_LAST_SEASON, dfm.BUNDLED_PROJECTIONS]
    files += [dfm.CACHE_DIR / f"{k}.parquet" for k in
              ["espn_projections", "nba_api_last_season", "sleeper_players", "market_rank_ecr"]]
    keys = ["source", "use_sleeper_meta", "use_espn_meta", "use_ecr", "proj_mapping", "proj_season_totals",
            "adp_mapping", "adp_target"]
    return json.dumps({"files": [mtime(f) for f in files], **{k: s[k] for k in keys}}, sort_keys=True, default=str)


def _load_base(source: str, s: dict):
    if source == "proj_2027":
        return dfm.load_projections()
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


@st.cache_data(show_spinner="Loading players…", max_entries=8, ttl="1h")
def assemble(sig: str, s_json: str):
    """Base source + Sleeper/ESPN metadata + ADP sources, all name-matched. Never raises."""
    s = json.loads(s_json)
    overrides = dfm.load_overrides()
    errors: list[str] = []
    metas: list[dfm.SourceMeta] = []
    reports: list[dfm.MatchReport] = []
    base = meta = None
    for source in dict.fromkeys([s["source"], "proj_2027", "last_season", "synthetic"]):
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
        try:
            snap = dfm.load_snapshot(key)
            if snap is None:
                return
            other, m = snap
            base, rep = dfm.enrich(base, dfm.apply_overrides(other, overrides), label, **kw)
        except Exception as exc:  # noqa: BLE001 - a bad secondary source must never take the app down
            errors.append(f"{label}: skipped ({type(exc).__name__}: {exc})")
            return
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


@st.cache_data(show_spinner=False, max_entries=16, ttl="1h")
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
    # Announced by the "THE PICK IS IN" lower third on the next run (instead of a toast).
    ss.just_in = {"no": p.pick_no, "slot": p.team, "name": p.name, "nba_team": rec.get("team") or "",
                  "pos": list(rec.get("positions") or []), "player_id": pid, "espn_id": rec.get("espn_id"),
                  "rank": rec.get("rank"), "keeper": source == "keeper"}


def on_search_pick() -> None:
    pid = ss.get("w_search")
    ss.w_search = None
    if pid:
        do_pick(pid)
        ss.refocus = True  # straight back into the box for the next pick


def on_list_draft() -> None:
    """DRAFT button in the best-available list (custom component trigger)."""
    result = ss.get("plist")
    pid = getattr(result, "draft", None) if result is not None else None
    if pid:
        do_pick(str(pid))


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

def player_label(rec: dict) -> str:
    # Accents stripped so typing "jokic" finds "Jokić".
    name = "".join(ch for ch in unicodedata.normalize("NFKD", rec["name"]) if not unicodedata.combining(ch))
    return f"{name} · {rec['team']} · {'/'.join(rec['positions'] or []) or '?'} · #{rec['rank']}"


# ── App ───────────────────────────────────────────────────────────────────────

boot()
if ss.pending_resume is not None:
    st.html(ui.header_html([]))
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
        with st.container(key="sb_punts"):  # keyed container (not widget) so punted chips can be styled apart
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

    st.header("Draft backup")
    st.caption("Hosted apps (Streamlit Cloud) wipe files when they restart. Grab a backup now and then.")
    st.download_button("💾 Download draft backup", data=draft.to_backup(state, S), on_click="ignore",
                       file_name=f"draft_backup_{datetime.now():%Y%m%d-%H%M}.json", mime="application/json",
                       width="stretch", key="w_backup_download")
    backup_file = st.file_uploader("Restore a backup", type=["json"], key="w_restore_upload")
    if backup_file is not None and st.button("Restore this backup", width="stretch", key="w_restore_go"):
        try:
            restored, restored_settings = draft.from_backup(backup_file.getvalue())
        except draft.DraftError as exc:
            st.error(str(exc))
        else:
            draft.archive_state(STATE_PATH)
            ss.draft = restored
            S.update({k: v for k, v in restored_settings.items() if k in DEFAULT_SETTINGS})
            persist()
            save_settings(S)
            flash("success", f"Restored a draft with {len(restored.picks)} picks (the previous one was archived)")
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
team_name = state.settings.team_name
tags = []
if "ACTUALS" in meta.source:
    tags.append(("2025-26 actuals, not projections", "warn"))
elif meta.kind == "projections":
    tags.append(("2026-27 projections", "ok"))
if meta.from_cache:
    tags.append(("Cached snapshot", "warn"))
adp_label = next((k for k, v in ADP_COLUMNS.items() if v == adp_col), None)
tags.append((f"ADP: {adp_label}" if adp_label else "No ADP loaded", ("ok" if adp_label else "") + " opt"))
if S["sleeper_sync"] and S["sleeper_draft_id"]:
    tags.append(("Sleeper live", "live"))
tags.append((f"{N} teams · {state.settings.n_rounds} rds · slot {me}", "opt"))
with st.container(key="masthead"):
    st.html(ui.header_html(tags))
    st.caption(f"**{meta.source}** · updated {ui.nice_time(meta.fetched_at)}"
               + (" · ⚠ cached snapshot" if meta.from_cache else ""))
for w in meta.warnings:
    if w.startswith("FALLBACK"):
        st.error(w)

available = players[~players["player_id"].isin(drafted)].sort_values("rank")
clock_team = draft.team_for_pick(cur, N) if cur else None
next_mine = state.next_pick_for_team(me) if cur else None
clock_label = ("YOU" if clock_team == me else team_name(clock_team)) if clock_team else "—"
clock_color = ui.fantasy_color(clock_team, me) if clock_team else ui.NEUTRAL
st.html(ui.clock_style_html(clock_color, ui.monogram(team_name(clock_team), clock_team) if clock_team else ""))

# ── Score bug: pick · on the clock · your next pick · undo ────────────────────
with st.container(key="scorebug"):
    h1, h2, h3, h5 = st.columns([1.0, 1.75, 1.05, 0.56], vertical_alignment="center")
    if cur is None:
        h1.metric("Pick", "Done", "draft complete", delta_color="off", delta_arrow="off")
        with h2.container(key="clock_done"):
            st.metric("On the clock", "—")
    else:
        r = draft.round_of(cur, N)
        h1.metric("Pick", f"#{cur}", f"Round {r} · pick {cur - (r - 1) * N}", delta_color="off", delta_arrow="off")
        if state.reopened_slot():
            clock_note = "reopened pick"
        elif clock_team == me:
            clock_note = "make your pick"
        elif cur < state.total_picks:
            up = draft.team_for_pick(cur + 1, N)
            clock_note = "you're up next" if up == me else f"up next: {team_name(up)}"
        else:
            clock_note = "last pick"
        with h2.container(key="clock_you" if clock_team == me else "clock_other"):
            st.metric("On the clock", clock_label, clock_note, delta_color="off", delta_arrow="off")
    soon = bool(next_mine and cur and next_mine - cur <= 3)
    with h3.container(key="next_soon" if soon else "next_later"):
        if next_mine and cur:
            wait = next_mine - cur
            note = "on the clock now" if wait == 0 else f"in {wait} pick{'s' if wait != 1 else ''}"
        else:
            note = None
        st.metric("Your next pick", f"#{next_mine}" if next_mine else "—", note, delta_color="off", delta_arrow="off")
    h5.button("↶ Undo", on_click=on_undo, disabled=not state.picks, width="stretch", help="Take back the last pick")
    if cur:
        rnd = draft.round_of(cur, N)
        filled = state.filled()
        segs = []
        for no in range((rnd - 1) * N + 1, min(rnd * N, state.total_picks) + 1):
            t = draft.team_for_pick(no, N)
            segs.append({"mono": ui.monogram(team_name(t), t), "color": ui.fantasy_color(t, me), "done": no in filled,
                         "cur": no == cur, "mine": t == me,
                         "title": f"#{no} {team_name(t)}" + (f": {filled[no].name}" if no in filled else "")})
        st.html(ui.round_strip_html(rnd, segs, cur - (rnd - 1) * N, N))

# ── Draft bar: the search box (type a few letters + Enter drafts the top match) ──
with st.container(key="draftbar"):
    s1, s2 = st.columns([3.2, 1], vertical_alignment="bottom")
    with s1.container(key="pick_search"):
        st.selectbox(
            f"Draft for {clock_label} · type a name, press Enter" if clock_team else "Draft complete",
            available["player_id"].tolist(), index=None,
            key="w_search", on_change=on_search_pick, format_func=lambda pid: player_label(ss.player_by_id[pid]),
            filter_mode=S["search_mode"],
            placeholder=(f"Search players to draft for {clock_label}…" if clock_team else "Draft complete"),
            disabled=cur is None)
    with s2.expander("Pick options"):
        st.selectbox("Team", [0, *range(1, N + 1)], key="w_team_override",
                     format_func=lambda t: "On the clock" if t == 0 else team_name(t))
        st.number_input("Pick # (0 = current)", 0, state.total_picks, key="w_pick_override",
                        help="For keepers and out-of-order picks")
        st.checkbox("Keeper", key="w_keeper")
recent = sorted(state.picks, key=lambda p: p.seq)[-12:][::-1]
just_in = ss.pop("just_in", None)
if just_in and not any(p.pick_no == just_in["no"] and p.name == just_in["name"] for p in state.picks):
    just_in = None  # undone or replaced before this run
st.html(ui.ticker_html([{"no": p.pick_no, "team": team_name(p.team), "slot": p.team, "me_slot": me, "name": p.name,
                         "nba_team": p.nba_team, "pos": p.positions, "mine": p.team == me} for p in recent])
        + (ui.lower_third_html(just_in, team_name(just_in["slot"]), me, N) if just_in else ""))

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
    left, right = st.columns([2.3, 1], gap="medium")
    with left:
        with st.container(key="listbar"):
            f1, f2 = st.columns([3, 1.3], vertical_alignment="bottom")
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
        player_list.render(player_list.payload(
            view, gone=gone, cats=cats, punts=vs.punts, is_points=vs.is_points, fit_on=bool(fit_weights),
            next_pick=next_mine, projected=meta.kind == "projections", can_draft=cur is not None,
            clock=clock_label, clock_color=clock_color, order=S["order"], pos=S["pos_filter"],
            sig=f"{S['pos_filter']}|{S['order']}|{vs.is_points}"), key="plist", on_draft=on_list_draft)
        with st.container(key="listnote"):
            if not vs.is_points:
                lr = info.get("league_rates", {})
                st.caption(f"Click a player for the full stat line{' and why the projection moved' if meta.kind == 'projections' else ''}"
                           f" · DRAFT picks for the team on the clock (Pick options overrides it). "
                           f"Z = value over the top-{vs.pool_size} pool ({info.get('iterations')} iterations"
                           f"{'' if info.get('converged') else ', not converged'}; lg FG% {lr.get('FG%', 0):.3f}, "
                           f"FT% {lr.get('FT%', 0):.3f}). ≈ = approximate positions; struck-out categories are punted.")
            else:
                st.caption(f"Replacement level: {info.get('replacement_fp', 0):.1f} FP ({vs.stat_mode.replace('_', ' ')}). "
                           "Chips show the per-game stat line; val = FP over replacement.")
            if fit_weights:
                st.caption("Team-fit weights (0 = punted or locked up, 1.5 = competitive middle): "
                           + " · ".join(f"{c} {w:g}" for c, w in fit_weights.items()))

    with right:
        mine = [dict(ss.player_by_id[pid], value=float(valued.at[pid, "value"])) for pid in team_ids.get(me, [])]
        assignment = roster.assign_roster(mine, starters)
        slots = list(assignment.starters) + [("BN", p) for p in assignment.bench]
        slots += [("BN", None)] * max(0, state.settings.n_rounds - len(mine) - sum(p is None for _, p in assignment.starters))
        st.html(ui.roster_html(slots, team_name(me), n_have=len(mine), n_total=state.settings.n_rounds))
        if vs.is_points:
            fp = {t: float(valued.loc[ids, "FP"].sum()) if ids else 0.0 for t, ids in team_ids.items()}
            order_ = sorted(fp, key=fp.get, reverse=True)
            st.html(ui.points_card_html(fp.get(me, 0.0), order_.index(me) + 1, N, vs.stat_mode.replace("_", " ")))
        elif table_t is not None:
            summary = roster.my_category_summary(table_t, me, cats, vs.punts)
            st.html(ui.standings_html([{
                "cat": c, "punt": c in vs.punts, "z": float(z), "rank": rk,
                "value_text": "" if v is None or pd.isna(v) else (f"{v:.3f}" if c in val.PCT_CATS else f"{v:.1f}"),
            } for c, v, z, rk in zip(cats, summary["value"], summary["z"], summary["rank"])], N, fit_weights))

# ── Draft Board ───────────────────────────────────────────────────────────────
board_cells: dict[tuple[int, int], list[dict]] = {}
for p in state.picks:
    board_cells.setdefault((draft.round_of(p.pick_no, N), p.team), []).append(
        {"pick_no": p.pick_no, "name": p.name, "positions": p.positions, "nba_team": p.nba_team,
         "keeper": p.source == "keeper", "unmatched": p.unmatched})


with tab_board:
    st.html(ui.board_html(N, state.settings.n_rounds, board_cells, me,
                          (draft.round_of(cur, N), draft.team_for_pick(cur, N)) if cur else None,
                          state.settings.team_name, latest=max(state.picks, key=lambda p: p.seq).pick_no if state.picks else None))

# ── Data tab ──────────────────────────────────────────────────────────────────
with tab_data:
    st.subheader("Sources")
    for e in load_errors:
        st.warning(e)
    st.dataframe(pd.DataFrame([{
        "source": m.source, "kind": m.kind, "fetched": ui.nice_time(m.fetched_at), "players": m.n_players,
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
        age = dfm.snapshot_age_hours("sleeper_players")
        if age is not None and age < 24:
            flash("info", f"Sleeper metadata is {age:.1f} h old; it refreshes at most once a day.")
            st.rerun()
        run_fetch("Sleeper", dfm.fetch_sleeper_players)
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

    if (dfm.UPLOAD_DIR / "adp.csv").exists():
        st.subheader("ADP CSV column mapping")
        raw_adp = dfm.read_csv_any((dfm.UPLOAD_DIR / "adp.csv").read_bytes())
        adp_map = S["adp_mapping"] or dfm.auto_map_columns(raw_adp.columns, dfm.ADP_TARGETS)
        with st.form("adp_mapping_form"):
            cols_ = st.columns(len(dfm.ADP_TARGETS))
            new_adp_map = {}
            options = [None, *raw_adp.columns]
            for i, t in enumerate(dfm.ADP_TARGETS):
                cur_col = adp_map.get(t)
                new_adp_map[t] = cols_[i].selectbox(t, options, index=options.index(cur_col) if cur_col in options else 0,
                                                    key=f"adpmap_{t}")
            if st.form_submit_button("Apply ADP mapping"):
                S["adp_mapping"] = new_adp_map
                save_settings(S)
                st.cache_data.clear()
                st.rerun()
        st.dataframe(raw_adp.head(5), width="stretch")

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

# Headshot/logo load handling for the st.html blocks (initials until an image has really loaded).
st.html(ui.img_script(), unsafe_allow_javascript=True)

# Put the cursor back in the search box right after a pick, for rapid entry.
if ss.pop("refocus", False):
    st.html("""<script>
setTimeout(() => {
  // Phones skip this: focusing would pop the keyboard over the list.
  if (window.matchMedia('(max-width: 640px)').matches) return;
  const el = document.querySelector('.st-key-pick_search input'); if (el) { el.focus(); }
}, 250);
</script>""", unsafe_allow_javascript=True)
