# Fantasy Draft Assistant — guide for coding agents

A Streamlit app for live fantasy-basketball snake drafts: player values (9-cat z-scores or points),
a recommended pick, Sleeper live sync, and a crash-safe draft board. The user's league is a
14-team Sleeper points league picking 9th. README.md is the user guide; this file is for whoever
changes the code.

## Commands

```bash
cd fantasy_draft
python -m pytest -q                       # ~230 tests, ~40 s; must stay green
python tests/e2e_mock_draft.py --chromium /opt/pw-browsers/chromium-1194/chrome-linux/chrome
                                          # real browser: 156-pick mock + refresh/resume; must print E2E OK
python -m pyflakes *.py tests/*.py        # no warnings
streamlit run app.py                      # local app (FANTASY_DATA_DIR=... isolates runtime files)
```

Deploy = the `fantasy-draft-app` branch, which is this folder split out with `git subtree split`:
`sha=$(git subtree split -q --prefix=fantasy_draft) && git push origin $sha:refs/heads/fantasy-draft-app`.
Streamlit Cloud redeploys it. The repo root `.gitignore` ignores `*.csv` and `config.toml`;
`fantasy_draft/.gitignore` re-includes `data/bundled/*.csv` and `.streamlit/config.toml`. Check a
new data file with `git check-ignore -v` before assuming it ships.

## Module map

| Module | Role | Streamlit? |
|---|---|---|
| `app.py` | UI wiring only: sidebar, score bug, draft room, board, data tab | yes |
| `flow.py` | **The app's state machines** (see below) | no |
| `settings_store.py` | default settings, league defaults, versioned migrations, atomic save | no |
| `draft.py` | snake order, picks, undo/remove/reset, atomic JSON persistence, Sleeper pick sync | no |
| `valuation.py` | z-scores, points, stat modes, ADP flags, points presets | no |
| `recommend.py` | recommended pick: category need, value lost by waiting, roster fit | no |
| `roster.py` | lineup slot matcher, category standings, team-fit weights | no |
| `projections.py` | 2026-27 projection model (+ double-double / bonus-game rates) | no |
| `data_fetcher.py` | every data source → one schema; snapshots; name matching; Sleeper/ESPN APIs | no |
| `ui.py`, `player_list.py` | HTML/CSS pieces and the clickable list component | presentation |
| `build_*.py`, `backtest_projections.py` | rebuild bundled data, backtest the model | scripts |

Logic belongs in the pure modules, where it is unit-tested; `app.py` should only read their results.

## State machines (flow.py)

State is **derived, never stored twice**: each machine is a pure function of data the app already
keeps. The enum docstrings list every transition; `tests/test_flow.py` exercises each one.

| Machine | States | Derived from | Used for |
|---|---|---|---|
| `DraftPhase` | setup → drafting → complete, plus reopened after a removed pick | `DraftState.picks` | league-size lock, search box, score bug |
| `Clock` (from `flow.clock`) | pick, round, team on the clock, mine, my next pick, note | `DraftState` + my slot | score bug, recommendation, list |
| `SyncState` | off → invalid link / ready → live ⇄ failing | link text, toggle, last `Poll` (`ss.sleeper_poll`) | sidebar status line, header pill |
| `SourceKind` + `SourceStatus` | projections / actuals / synthetic; fallback_from; cached | `SourceMeta` | header pills, fallback banner |
| `BootAction` | new / use saved / ask to resume | draft file + settings migration | `app.boot()` |
| `ListOrder` | My rank, Recommended, ADP, Team fit (availability varies) | recommendation + team fit | list order; the saved preference survives while an order is unavailable |
| `SnapshotState` | missing / fresh / stale | snapshot age vs max age | Sleeper once-a-day refresh rule |
| settings version | v1 → v2 → v3 (current) | `settings_version` in settings.json | `settings_store.MIGRATIONS` |

When adding behavior that depends on "where we are", add or extend a state in `flow.py` first,
test its transitions, then read it from `app.py`. Don't sniff labels or warning text.

## Machine-readable page state

Every page renders `<div id="app-state" hidden data-…>` from `flow.page_state()`:
`phase, pick, round, clock-team, mine, next-mine, picks-made, picks-total, sync, source,
fallback, cached`. Read it with
`document.querySelector('#app-state').dataset` instead of scraping text. The e2e test asserts it.

Stable widget keys that tests and automation rely on (don't rename without updating them):
`pick_search` / `w_search` (type + Enter drafts), `resume_yes` / `resume_no`, `rec_draft_N`,
`w_team_override`, `w_pick_override`, `w_keeper`, `plist` (list component), the "↶ Undo" button
label, the "Order" selectbox label, and the first `[data-testid=stMetricValue]` (pick number /
"Done").

## Rules

- **Never invent stats for real players.** Rookies get numbers only from a complete published line
  (`data/bundled/analyst_rookies_2026_27.csv`, with source and URL) or ESPN; otherwise they stay
  stat-less. Synthetic data ("Test Player NNN") is for tests only.
- **Persist after every draft mutation** (`persist()` → atomic write). Reset/start fresh archive
  the old file; nothing is deleted.
- **Sleeper is authoritative** when a synced pick disagrees with a typed one.
- **No network on startup.** Fetches happen on button clicks or the Sleeper poll; failures fall back
  to the last snapshot (`fetch_with_fallback`).
- **Changing a default for existing users needs a migration** in `settings_store.py`: bump
  `SETTINGS_VERSION` and append to `MIGRATIONS`. Only rewrite values the user never changed.
- **Model changes need the backtest**: `python backtest_projections.py --from-dir <data>`; keep
  changes that help both seasons.
