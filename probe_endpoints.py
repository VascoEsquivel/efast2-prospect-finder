"""Probe every data endpoint from your own machine and report what actually comes back.

The app's ESPN and Sleeper parsers follow documented/community response shapes, but they
were built in a sandbox that could not reach those hosts. Run this once before your draft:

    python probe_endpoints.py                    # ESPN, Sleeper, nba_api, FantasyPros ECR
    python probe_endpoints.py --draft-id 123...  # also check a Sleeper draft's picks endpoint

Every raw response is saved to data/raw/probe_*.json and the report to data/raw/probe_report.txt.
If something looks wrong, paste the report into a chat with Claude.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

import data_fetcher as dfm

LINES: list[str] = []


def say(msg: str = "") -> None:
    print(msg)
    LINES.append(msg)


def get(url: str, headers: dict | None = None, params: dict | None = None, timeout: float = 20):
    h = {"User-Agent": dfm.USER_AGENT, "Accept": "application/json", **(headers or {})}
    return requests.get(url, headers=h, params=params, timeout=timeout)


def save(name: str, payload) -> Path:
    dfm.RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = dfm.RAW_DIR / f"probe_{name}.json"
    path.write_text(json.dumps(payload))
    return path


# ── ESPN ──────────────────────────────────────────────────────────────────────

def espn_variants(season: int) -> dict[str, dict | None]:
    return {
        "app_default": dfm.espn_filter(season, 1000),
        "draft_ranks_only": {"players": {"limit": 1000, "sortDraftRanks": {"sortPriority": 100, "sortAsc": True,
                                                                          "value": "STANDARD"}}},
        "no_filter": None,
    }


def describe_espn(data, season: int) -> tuple[int, int]:
    players = data.get("players", []) if isinstance(data, dict) else data
    with_proj = sum(1 for e in players if dfm.espn_stat_entry(e.get("player", e).get("stats", []), season))
    say(f"    players: {len(players)}; with a {season} projection entry: {with_proj}")
    if not players:
        return 0, 0
    ids = Counter(str(s.get("id")) for e in players[:50] for s in e.get("player", e).get("stats", []))
    say(f"    stat entry ids (first 50 players): {dict(ids.most_common(8))}")
    sample = next((e for e in players if "Joki" in e.get("player", e).get("fullName", "")), players[0])
    pl = sample.get("player", sample)
    say(f"    sample: {pl.get('fullName')} id={pl.get('id')} proTeamId={pl.get('proTeamId')} "
        f"defaultPositionId={pl.get('defaultPositionId')} eligibleSlots={pl.get('eligibleSlots')} "
        f"injuryStatus={pl.get('injuryStatus')}")
    say(f"    player keys: {sorted(pl.keys())}")
    say(f"    ownership: {pl.get('ownership')}")
    for s in pl.get("stats", []):
        say(f"      entry id={s.get('id')} season={s.get('seasonId')} source={s.get('statSourceId')} "
            f"split={s.get('statSplitTypeId')} period={s.get('scoringPeriodId')} "
            f"n_stats={len(s.get('stats') or {})} has_avg={bool(s.get('averageStats'))}")
    proj = dfm.espn_stat_entry(pl.get("stats", []), season)
    if proj:
        avg = proj.get("averageStats") or {}
        named = {dfm.ESPN_STAT_NAMES.get(k, k): round(v, 2) for k, v in avg.items()}
        say(f"    projection averageStats (named by current map): {named}")
        say(f"    projection stats['42'] (GP): {(proj.get('stats') or {}).get('42')}")
    adps = sorted(((e.get("player", e).get("ownership") or {}).get("averageDraftPosition") or 0,
                   e.get("player", e).get("fullName")) for e in players)
    adps = [a for a in adps if a[0] > 0][:10]
    say(f"    top ADP: {adps}")
    return len(players), with_proj


def probe_espn(season: int) -> None:
    say(f"== ESPN fantasy, season id {season}")
    best = None
    for host in dfm.ESPN_HOSTS:
        for vname, filt in espn_variants(season).items():
            url = host + dfm.ESPN_PATH.format(season=season)
            headers = {"x-fantasy-filter": json.dumps(filt)} if filt else {}
            try:
                resp = get(url, headers, {"view": "kona_player_info"})
            except requests.RequestException as exc:
                say(f"  {host} [{vname}]: FAILED {type(exc).__name__}: {exc}")
                continue
            say(f"  {host} [{vname}]: HTTP {resp.status_code}, {len(resp.content):,} bytes")
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except ValueError:
                say("    not JSON")
                continue
            path = save(f"espn_{host.split('//')[1].split('.')[0]}_{vname}", data)
            say(f"    saved {path.name}")
            n, n_proj = describe_espn(data, season)
            if best is None or n_proj > best[0]:
                best = (n_proj, data)
    if best and best[0]:
        say("  stat-ID check (ESPN 2025-26 actuals vs bundled box scores, same player ids):")
        check = dfm.verify_espn_stat_map(best[1], dfm.load_last_season()[0], season=season - 1)
        for line in check.to_string(index=False).splitlines():
            say("    " + line)
        df, warnings = dfm.parse_espn(best[1], season)
        say(f"  app parser: {len(df)} players parsed; warnings: {warnings}")
        if len(df):
            cols = ["name", "team", "positions", "GP", "MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO",
                    "FGM", "FGA", "FTM", "FTA", "adp_espn"]
            say(df.sort_values("adp_espn").head(8)[cols].round(2).to_string(index=False))


# ── Sleeper ───────────────────────────────────────────────────────────────────

def probe_sleeper(draft_id: str | None) -> None:
    say("== Sleeper")
    season = None
    try:
        state = get("https://api.sleeper.app/v1/state/nba").json()
        save("sleeper_state_nba", state)
        season = state.get("season")
        say(f"  state/nba: {state}")
    except Exception as exc:  # noqa: BLE001
        say(f"  state/nba FAILED: {exc}")
    try:
        resp = get(dfm.SLEEPER_PLAYERS_URL, timeout=60)
        data = resp.json()
        save("sleeper_players_nba", data)
        players = list(data.values())
        active = [p for p in players if p.get("active")]
        say(f"  players/nba: HTTP {resp.status_code}, {len(resp.content):,} bytes, {len(players)} players, {len(active)} active")
        sample = next((p for p in players if p.get("last_name") == "Jokic"), players[0])
        say(f"    keys: {sorted(sample.keys())}")
        say(f"    sample: { {k: sample.get(k) for k in ['player_id', 'full_name', 'team', 'position', 'fantasy_positions', 'injury_status', 'espn_id', 'search_rank', 'status']} }")
        say(f"    active with espn_id: {sum(1 for p in active if p.get('espn_id'))}/{len(active)}")
        say(f"    fantasy_positions seen: {Counter(tuple(p.get('fantasy_positions') or []) for p in active).most_common(10)}")
        df = dfm.parse_sleeper_players(data)
        merged, rep = dfm.enrich(dfm.load_last_season()[0], df, "Sleeper", live_metadata=True)
        say(f"    matched to bundled 2025-26 players: {rep.matched}; bundled players unmatched: {len(rep.unmatched_base)}")
    except Exception as exc:  # noqa: BLE001
        say(f"  players/nba FAILED: {exc}")

    years = sorted({str(season) if season else "2026", "2026", "2027"})
    candidates = [f"https://api.sleeper.app/v1/projections/nba/regular/{y}" for y in years]
    candidates += [f"https://api.sleeper.com/projections/nba/{y}?season_type=regular" for y in years]
    candidates += [f"https://api.sleeper.app/v1/stats/nba/regular/{y}" for y in years]
    for url in candidates:
        try:
            resp = get(url)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
            size = len(body) if isinstance(body, (list, dict)) else 0
            say(f"  {url}: HTTP {resp.status_code}, {len(resp.content):,} bytes, {size} entries")
            if size:
                name = url.split("//")[1].replace("/", "_").replace("?", "_")
                save(f"sleeper_{name}", body)
                first = body[0] if isinstance(body, list) else next(iter(body.values()))
                say(f"    first entry: {json.dumps(first)[:400]}")
        except Exception as exc:  # noqa: BLE001
            say(f"  {url}: FAILED {type(exc).__name__}: {exc}")

    if draft_id:
        try:
            d = get(dfm.SLEEPER_DRAFT_URL.format(draft_id=draft_id)).json()
            save("sleeper_draft", d)
            say(f"  draft {draft_id}: type={d.get('type')} status={d.get('status')} settings={d.get('settings')} "
                f"draft_order={d.get('draft_order')}")
            picks = dfm.fetch_sleeper_draft_picks(draft_id, timeout=10)
            save("sleeper_draft_picks", picks)
            say(f"  picks: {len(picks)}; first: {json.dumps(picks[0])[:400] if picks else '-'}")
        except Exception as exc:  # noqa: BLE001
            say(f"  draft {draft_id} FAILED: {exc}")


# ── Others ────────────────────────────────────────────────────────────────────

def probe_nba_api() -> None:
    say("== nba_api LeagueDashPlayerStats 2025-26")
    try:
        df, meta = dfm.fetch_nba_api_last_season()
        say(f"  {meta.n_players} players; sample:")
        say(df.sort_values("PTS", ascending=False).head(5)[["name", "team", "GP", "MIN", "PTS", "REB", "AST"]]
            .round(1).to_string(index=False))
    except Exception as exc:  # noqa: BLE001
        say(f"  FAILED {type(exc).__name__}: {exc}")


def probe_ecr() -> None:
    say("== FantasyPros ECR snapshot (GitHub)")
    try:
        df, meta = dfm.fetch_market_rank_ecr()
        say(f"  {meta.source}: {meta.n_players} players, from_cache={meta.from_cache}")
    except Exception as exc:  # noqa: BLE001
        say(f"  FAILED {type(exc).__name__}: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=2027, help="ESPN season id (2027 = the 2026-27 season)")
    ap.add_argument("--draft-id", help="a Sleeper draft_id to test live sync against")
    args = ap.parse_args()
    pd.set_option("display.width", 200)
    probe_espn(args.season)
    say()
    probe_sleeper(args.draft_id)
    say()
    probe_nba_api()
    say()
    probe_ecr()
    dfm.RAW_DIR.mkdir(parents=True, exist_ok=True)
    (dfm.RAW_DIR / "probe_report.txt").write_text("\n".join(LINES) + "\n")
    print(f"\nreport saved to {dfm.RAW_DIR / 'probe_report.txt'}")


if __name__ == "__main__":
    main()
