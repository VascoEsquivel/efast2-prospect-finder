# Fantasy Basketball Draft Assistant (2026-27)

A Streamlit app for live snake drafts. It does four things:
- values players with category z-scores (9-cat, 8-cat or points);
- flags ADP steals and reaches;
- tracks every pick on a board that survives refreshes and crashes;
- shows how your roster ranks in each category against every other team.

Run it on your laptop, or deploy it free on Streamlit Community Cloud and open it from any device.

## Run it on your computer

You need Python 3.11 or newer ([python.org/downloads](https://www.python.org/downloads/)). On Windows, tick "Add python.exe to PATH" in the installer.

**Get the code** in either of two ways:
- **Download:** open https://github.com/VascoEsquivel/efast2-prospect-finder/archive/refs/heads/fantasy-draft-app.zip and unzip it. On Windows, use right-click → Extract All.
- **Clone:** `git clone -b fantasy-draft-app https://github.com/VascoEsquivel/efast2-prospect-finder fantasy-draft`

**Launch it with one step.** The first run creates `.venv` and installs everything, which takes a minute or two. Later runs start in seconds and only reinstall if `requirements.txt` changes.

| Your computer | How to launch |
|---|---|
| Mac | Double-click `start.command`. If macOS blocks it, right-click → Open the first time. |
| Windows | Double-click `start.bat`. If "Windows protected your PC" appears, click More info → Run anyway. |
| Any terminal | `python3 start.py`, or `py -3 start.py` on Windows. |

Your browser opens http://localhost:8501. Keep the launcher window open while you draft; closing it, or pressing Ctrl+C, stops the app.

The app works with **no network at all**: it starts on the bundled 2026-27 projections. The bundled 2025-26 stats and synthetic test data are also available offline.

<details><summary>Manual setup instead of the launcher</summary>

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
</details>

## Deploy on Streamlit Community Cloud (free)

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Click **Create app → Deploy a public app from GitHub**, then set:
   - **Repository:** `VascoEsquivel/efast2-prospect-finder`
   - **Branch:** `fantasy-draft-app`
   - **Main file path:** `app.py`
3. Optional: under **Advanced settings**, pick Python 3.12.
4. Click **Deploy**. Every push to the `fantasy-draft-app` branch updates the app automatically.

The `fantasy-draft-app` branch holds this app at its top level, separate from the EFAST2 tool on `main`, because Streamlit Cloud only reads the theme file (`.streamlit/config.toml`) at the top of the branch it deploys. The branch is generated from the `fantasy_draft/` folder of the development branch with `git subtree split`.

Things to know on Streamlit Cloud:
- **Files reset when the app restarts.** Your saved draft is lost if the app restarts mid-draft. Use **Download draft backup** now and then; **Restore draft backup** brings it back.
- **The app sleeps after 12 hours without visitors.** Open it a few minutes before your draft.
- **Who can see it:** this repo is public, so anyone with the app's link can open it, and they'd be using the same saved draft as you. The URL is hard to guess. To lock it down, move the app to a private repo; Streamlit Cloud apps from private repos start out private.
- **Network access:** Streamlit's servers aren't behind the network block this app was built in, so the ESPN and Sleeper buttons may work there. Run the probe first to confirm the endpoints.

### Before draft day

1. **Probe the APIs from your machine**: `python probe_endpoints.py` (add `--draft-id <id>` for a Sleeper draft).
   - It saves every raw response to `data/raw/` and writes `data/raw/probe_report.txt`.
   - The ESPN and Sleeper parsers were written against documented and community response shapes, in a sandbox that could not reach those hosts. This run is their first real check. If anything looks off, paste the report to Claude.
2. **Load real projections**: upload a projections CSV (sidebar → Data) or click **Fetch ESPN projections + ADP** (Data tab).
3. **Data tab → Refresh Sleeper metadata**: real position eligibility, injuries and Sleeper IDs.
4. **Set your league** in the sidebar: teams, roster size, your draft slot, starting slots, categories and punts.
5. **Do a mock**: type picks in, refresh the browser mid-way, and click Resume. (`python tests/e2e_mock_draft.py` automates this.)

## Data sources

The header always shows which source is loaded and when it was fetched. The **Data** tab lists every source, its fetch time, whether it came from a cached snapshot, and any warnings.

| Source | What it gives | How to load | Status |
|---|---|---|---|
| **2026-27 projections (bundled)** | Projections for 556 players built from 3 seasons of real box scores and the 2026-27 rosters (trades, ball share, team style); rookies listed without stats | Default source; no network needed | Backtested (below) |
| **Projection CSV** | Per-game or season projections from any site | Sidebar → Player data → *Projection CSV*, upload, then check the column mapping in the Data tab | Tested |
| **ADP CSV** | ADP from Sleeper, ESPN or anywhere else | Sidebar → *ADP / metadata sources* → upload and pick what it is | Tested |
| **ESPN projections** | 2026-27 projections, ADP, positions, injuries | Data tab → *Fetch ESPN projections + ADP* | Parser tested on documented shape; **verify with the probe** |
| **Sleeper players** | IDs, positions, injuries, current teams | Data tab → *Refresh Sleeper metadata* (fetched at most once a day) | Parser tested on documented shape; **verify with the probe** |
| **Sleeper live sync** | Picks from a Sleeper draft, every 5 s | Sidebar → Sleeper draft_id → *Poll picks* | Tested with fixtures; **verify with the probe** |
| **Last season 2025-26 (bundled)** | Real per-game actuals for 582 players, with 2026-27 teams | Sidebar → Player data | Verified |
| **Last season via nba_api** | Same season from NBA.com | Data tab → *Refresh last season (nba_api)* | Parser tested; endpoint unverified |
| **FantasyPros ECR** | Expert consensus *rank* for ESPN points leagues (2026-09-29) | Sidebar → *Use FantasyPros ECR*, then Data tab → fetch | Verified (rank, **not ADP**) |
| **Synthetic test data** | 300 fake "Test Player NNN" players | Sidebar → Player data | For tests and offline demo only |

### Projection CSV

- Common headers are auto-mapped: `Player/Name`, `Pos`, `GP`, `MIN/MPG`, `PTS`, `REB/TRB`, `AST`, `3PM/3PTM/FG3M`, `STL`, `BLK`, `TO/TOV`, `FGM/FGA`, `FTM/FTA`, `FG%/FT%` and `ADP`. Fix any mapping in the Data tab.
- FG% and FT% are **never averaged**; makes and attempts are always used.
  - If the file has `FG%` and `FGA` but no `FGM`, makes are derived as FG% × FGA (same for FT).
  - Without makes or attempts, that category scores 0 for everyone, and the app warns you.
- Tick *CSV values are season totals* if the file isn't per game. The app then divides by GP.
- The uploaded file is copied to `data/raw/uploads/`, so it survives a browser refresh.

### ESPN (season id 2027 = 2026-27)

- **Request:** `GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/2027/segments/0/leaguedefaults/3?view=kona_player_info`. It falls back to `fantasy.espn.com`.
- **Filter header:** `x-fantasy-filter: {"players":{"limit":1000,"sortPercOwned":…,"sortDraftRanks":…,"filterStatsForTopScoringPeriodIds":{"value":2,"additionalValue":["002027","102027","002026"]}}}`.
- **Projection entry:** stat entry `id == "102027"` (statSourceId 1, statSplitTypeId 0) is the full-season projection; `002026` is last season's actuals.
- **Stat IDs:** taken from the `espn-api` package:

  | ID | Stat | ID | Stat |
  |---|---|---|---|
  | 0 | PTS | 13 | FGM |
  | 1 | BLK | 14 | FGA |
  | 2 | STL | 15 | FTM |
  | 3 | AST | 16 | FTA |
  | 6 | REB | 17 | 3PM |
  | 11 | TO | 40 | MIN |
  |  |  | 42 | GP |

  **Self-check:** after a fetch, the Data tab compares ESPN's own 2025-26 actuals with the bundled box-score averages for the same ESPN player IDs. Each ID gets ✅ or ❌, and a mismatched ID gets a suggestion for the ID that matches better.
- **ADP:** `player.ownership.averageDraftPosition`. A value of 0 is treated as missing.

### Sleeper

- `api.sleeper.app/v1/players/nba` is a large metadata dump. It has no projections or ADP that I could confirm.
- `probe_endpoints.py` tries the undocumented `/v1/projections/nba/regular/<year>` and `/v1/stats/nba/…` URLs. If they work for you, they can be added as a source.
- Until then, "Sleeper ADP" means an ADP CSV you import and mark as *Sleeper ADP*.
- **Live sync:** paste the draft_id (the number in the Sleeper draft URL).
  - Before any picks, click *Load teams/rounds from Sleeper*, then set your slot.
  - Turn on *Poll picks every 5 s*. Picks are matched by Sleeper ID (from the metadata refresh), then by name. Sleeper is treated as authoritative if it disagrees with a manual entry.

### Bundled 2026-27 projections

`data/bundled/projections_2026_27.parquet` is built by `build_projections.py` with the model in `projections.py`. It uses only real data: ESPN box scores for 2023-24, 2024-25 and 2025-26 (every regular-season game, with each team's and opponent's totals) and the 2026-27 rosters, both from the sportsdataverse releases.

How each line is built:
1. **Skill.** The last three seasons, weighted 6/3/1 toward the most recent. Rates are per 100 possessions, so a fast team doesn't inflate them. They're lightly regressed to the position average, then adjusted for age: under 26 improves a little, over 30 declines 1% a year.
2. **Minutes.** Mostly last season's role, nudged 10% toward 24 a game, since heavy loads rarely repeat. A roster more crowded than a typical team's trims bench minutes first, and first-round rookies get minutes reserved. Roster-based minute shifts turned out to be a weak signal in the backtest, so only a quarter of the shift is applied.
3. **Ball share.** A team's players can only use 100% of its possessions: summed usage × minutes = 5 players × 100%. When a roster's combined usage is over that budget, everyone gives up the **same number of usage points**. Stars keep most of their shots and role players lose a bigger share. Players losing touches get slightly more efficient. A team under budget hands out touches the same way. Example: Philadelphia (Maxey, Jaylen Brown, Embiid, Edgecombe, LeBron) is the most crowded roster at 108% of its possessions. Maxey goes from 28.3 points last season to a projected 25.1, ranking 4th in 9-cat on steals, threes, FT% and low turnovers. Boston, after losing Brown, is at 90%, so Tatum, White and Pritchard get more shots.
4. **Team style.** The new team's pace scales counting stats. Assists and rebounds are checked against how much the team moves the ball and rebounds. A shooter joining a heavy three-point team takes more threes. These are mild, because the backtest found them close to neutral.

Every adjustment is written into the **Why** column. Select a row to see the full stat line with the reasons above the table.

**Backtest** (`python backtest_projections.py`): each test season is predicted only from the three seasons before it, using who actually played where. The comparison covers players with 25+ games, about 370 per season. Lower is better for error.

| Predicting | Version | Points error | Minutes error | Rebounds | Assists | 9-cat value rank corr. |
|---|---|---|---|---|---|---|
| 2024-25 | last season as-is | 2.33 | 3.91 | 0.82 | 0.64 | 0.838 |
| 2024-25 | **this model** | **2.18** | **3.69** | **0.76** | **0.60** | **0.846** |
| 2025-26 | last season as-is | 2.84 | 4.59 | 0.97 | 0.73 | 0.738 |
| 2025-26 | **this model** | **2.55** | **4.28** | **0.85** | **0.70** | **0.741** |

On teams whose combined usage was 5%+ over or under budget, the roster step cut points error further than the skill-and-age model alone: 2.40 → 2.34 in 2024-25 and 2.69 → 2.59 in 2025-26.

Be honest with yourself about what the backtest says:
- **Ranking barely improves.** Fantasy value ranking is only slightly better than last season as-is. Most of the gain is in the size of each stat.
- **Team changes are hard.** For players who changed teams, the roster step helped in 2024-25: points error 2.39 → 2.27. In 2025-26 it was a wash: 2.94 → 2.95, with value ranking slightly worse. Trades are noisy.
- **Every setting earned its place.** Each one was tuned on both seasons, and a change was kept only if it helped in both.

Limits:
- **Rookies aren't projected.** 2026 draftees and players with no NBA games are listed so you can enter picks, but with **no stats** (they rank last). The app never invents stats for real players. Use a projection CSV or ESPN if you want rookie projections.
- **Rosters are as of the file date.** Moves after then aren't included. Sleeper/ESPN metadata can update a player's team label, but the projection still uses the bundled roster.
- **Positions are approximate** (G/F/C), as with the last-season data.

Rebuild it with `python build_projections.py`, which downloads from GitHub releases. Run that after the rosters file updates.

### Bundled last-season data

`data/bundled/last_season_2025_26.parquet` holds **real** 2025-26 regular-season per-game stats, built by `build_last_season.py`:
- **Source:** ESPN box scores scraped by the [sportsdataverse/hoopR](https://github.com/sportsdataverse/sportsdataverse-data/releases) project: `player_box_2026.parquet`, regular-season games 2025-10-21 → 2026-04-12, with the All-Star game, play-in and playoffs excluded.
- **Teams:** taken from `rosters_2027.parquet` (2026-27 rosters, published around 2026-08-19).

Its limits:
- **Positions are approximate.** ESPN's box data only lists G/F/C, which the app expands to PG/SG, SF/PF and C. These are shown with a `≈` in the table. Load Sleeper or ESPN for real eligibility.
- Players who **missed all of 2025-26** (e.g. Haliburton, Lillard) and **2026 rookies** are missing. The app never invents stats for real players. The Data tab's unmatched list shows who is missing compared with your ADP/rank source.
- Players with few games can rank high per game. Check the GP column, switch to season totals, or raise *Min GP* (default 10, which only keeps them out of the replacement pool).

Rebuild it with `python build_last_season.py`, which downloads from GitHub releases.

## Valuation

- **Replacement pool:**
  - Seed with the top N×R players by minutes (among those with at least *Min GP*).
  - Compute each category's mean and population σ over the pool, z-score everyone, sum to Z_total, and take the new top N×R.
  - Repeat until membership stops changing (at most 10 passes). The iteration count is shown under the table.
- **Counting categories:** z = (x − μ)/σ. **Turnovers are inverted.**
- **FG% / FT%:** the league rate is ΣFGM/ΣFGA over the pool. Impact = FGM − lg% × FGA, which is 0 for a league-average shooter, and that impact is what gets z-scored.
- **Punts:** a punted category gets multiplier 0. It's still shown (column suffix `·p`) but adds nothing to Z_total.
- **Stat mode:** *season totals* multiplies every counting stat and every make/attempt by GP.
- **Points leagues:** fantasy points use editable weights, prefilled with ESPN's default H2H scoring (PTS 1, 3PM 1, FGM 2, FGA −1, FTM 1, FTA −1, REB 1, AST 2, STL 4, BLK 4, TO −2). Value = FP − the FP of the (N×R+1)th best player.
- **ADP:** Δ = ADP − my rank, so positive means value.
  - 🟢 STEAL if Δ ≥ max(3, 0.15 × ADP); 🔴 REACH if Δ ≤ −max(3, 0.15 × ADP). Both numbers are configurable.
  - ⏳ marks available players whose ADP is earlier than your next pick (if you're on the clock, the pick after this one).

## During the draft

- **Fast entry:** click the search box, type a few letters, and press **Enter**. That drafts the top suggestion to the team on the clock, and the cursor returns to the box.
  - In the default *Best-ranked match first* mode, the top suggestion is the best-ranked available player whose name (or team) contains what you typed: `mark` → Markkanen, `jok` → Jokić.
  - *Fuzzy* mode tolerates typos, but it can float a weak player with a closer spelling to the top. In a recorded mock with 4-letter last-name queries, it picked the intended player only 86/156 times, versus 156/156 for the default mode.
- **Or** select a row in the table and click **Draft …**.
- **Pick options:** record a traded pick (another team), a keeper (any pick number), or an out-of-order pick.
- **Undo** removes the most recently entered pick. In the Data tab, **Remove pick** reopens any slot (the next entry fills it), and **Reset draft** needs a confirmation tick.
- **Draft Board:** teams are columns and rounds are rows. Your column is yellow and the current pick has an orange outline.
- **My team:** the roster by slot comes from an exact backtracking matcher (multi-position aware, not greedy). Category standings show your rank among all N teams (1 = best) in each category.

### Nothing gets lost

- Every pick is written atomically to `data/draft_state.json`. On the next start (refresh, crash, reboot) you get **Resume** or **Start fresh**. Start fresh archives the old file as `draft_state.<timestamp>.json`; nothing is deleted.
- Sidebar settings live in `data/settings.json`, and uploaded CSVs in `data/raw/uploads/`.
- Teams and rounds are locked once picks exist.
- If you switch data source mid-draft, picks are re-matched by ID, then by name. A pick that can't be matched stays on the board, flagged ⚠.

## If an API breaks mid-draft

The app makes network calls **only when you click a fetch button** (or poll, when Sleeper sync is on). Every fetch saves a normalized snapshot to `data/cache/`. On any later failure the snapshot is used, with a "⚠ cached snapshot" note.

| Symptom | What to do |
|---|---|
| ESPN fetch fails or returns few players | Keep drafting on the last snapshot. Otherwise switch *Player data* to your projection CSV or last season. Run `probe_endpoints.py` later to see what changed. |
| ESPN stat-ID check shows ❌ | Follow the suggested ID in the Data tab: edit `ESPN_STAT_IDS` in `data_fetcher.py`. |
| Sleeper sync errors | It keeps retrying every 5 s; turn it off and enter picks manually. Picks synced so far are already saved. |
| Sleeper metadata unavailable | Positions fall back to the CSV, ESPN, or approximate G/F/C. |
| Names don't match between sources | Data tab → *Unmatched names* → add an override (saved to `data/name_overrides.json`). |
| Everything is down | The bundled 2026-27 projections, 2025-26 data and synthetic data need no network. |

## Tests

```bash
pytest -q                                     # math, draft logic, parsers, matcher, offline UI smoke tests
python tests/e2e_mock_draft.py               # real browser: 12x13 mock via the search box + mid-draft refresh
```

The e2e script needs `pip install playwright && playwright install chromium`. It runs its own server on a temp folder, so your real draft is untouched.

## Files

```
start.py / start.command / start.bat   one-step setup + launch (local)
app.py               Streamlit UI wiring
ui.py                dark theme CSS, cell colors and HTML cards (presentation only)
.streamlit/config.toml  theme (dark navy + basketball orange, Inter / Barlow Condensed)
data_fetcher.py      every source -> one normalized DataFrame; name matching; snapshots
valuation.py         z-scores, replacement pool, points, ADP flags (pure)
draft.py             snake order, picks, undo, persistence, Sleeper pick sync (pure)
roster.py            slot matcher, team category standings, team-fit weights (pure)
schema.py            column names, team/position normalization
probe_endpoints.py   checks every endpoint from your machine
projections.py       2026-27 projection model: multi-season baseline, minutes, usage budget, team style (pure)
build_projections.py rebuilds the bundled 2026-27 projections
backtest_projections.py  scores the model on 2024-25 and 2025-26 against naive "last season"
build_last_season.py rebuilds the bundled 2025-26 data
data/bundled/        committed 2026-27 projections and real 2025-26 snapshot
data/name_overrides.json
data/raw/ data/cache/ data/draft_state.json data/settings.json   (runtime, git-ignored)
```
