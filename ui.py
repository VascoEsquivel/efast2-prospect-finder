"""Look and feel: the "Broadcast" package, styled like NBA-on-TV graphics.

Score bug, news-crawl ticker, team-color stripes, broadcast draft tracker. Presentation only: nothing
here reads or changes app state; app.py passes in what to draw.

Player headshots and team logos load from ESPN's CDN. When an image can't load (offline, blocked,
no headshot), a team-colored initials tile shows instead, never a broken-image icon.
"""

from __future__ import annotations

import colorsys
import html
import json
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path

TEAMS_PATH = Path(__file__).resolve().parent / "data" / "bundled" / "teams.json"
HEADSHOT_URL = "https://a.espncdn.com/i/headshots/nba/players/full/{}.png"

RED = "#FF2D45"     # the "LIVE" red, the one brand accent
GOLD = "#FFC83D"    # you
GOOD = "#2EE58F"
BAD = "#FF5C74"
AMBER = "#FFB020"
NEUTRAL = "#8D96A8"
# Fantasy teams get a broadcast color each (by draft slot). Gold is kept for you.
FANTASY_PALETTE = ["#3D8BFF", "#22C7B8", "#A26BFF", "#FF7F3F", "#4CD06D", "#FF5EA9", "#33B7F2", "#A6D93A",
                   "#E57BF2", "#7480FF", "#FF9E80", "#5EE0C0", "#C9A4FF", "#8FD3FF", "#D4E157", "#FF8FC7"]

CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700;800&family=Big+Shoulders+Display:wght@700;800;900&display=swap");
:root {
  --bc-bg: #07090E; --bc-panel: #0F131C; --bc-panel2: #151A25; --bc-panel3: #1B2130;
  --bc-line: rgba(255,255,255,.07); --bc-line2: rgba(255,255,255,.13);
  --bc-ink: #F3F5F9; --bc-muted: #8D96A8; --bc-dim: #4E576A;
  --bc-red: #FF2D45; --bc-red2: #C10F2A; --bc-gold: #FFC83D; --bc-good: #2EE58F; --bc-bad: #FF5C74; --bc-amber: #FFB020;
  --bc-display: 'Big Shoulders Display', 'Barlow Condensed', 'Arial Narrow', sans-serif;
  --bc-cond: 'Barlow Condensed', 'Arial Narrow', sans-serif;
  --bc-body: Barlow, Inter, system-ui, sans-serif;
  --bc-sk: 12px;
  --bc-clock: #3D8BFF; --bc-clock-mono: "";
}
.stApp { background:
  radial-gradient(1100px 360px at 82% -140px, rgba(255,45,69,.14), transparent 70%),
  radial-gradient(900px 420px at 8% -180px, rgba(61,139,255,.10), transparent 70%), var(--bc-bg) !important; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.1rem !important; padding-bottom: 3rem !important; max-width: 100% !important; }
@keyframes bc-pulse { 0% { box-shadow: 0 0 0 0 rgba(255,45,69,.75); } 70% { box-shadow: 0 0 0 9px rgba(255,45,69,0); }
  100% { box-shadow: 0 0 0 0 rgba(255,45,69,0); } }
@keyframes bc-blink { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
@keyframes bc-sweep { 0% { transform: translateX(-120%) skewX(-20deg); } 55%, 100% { transform: translateX(320%) skewX(-20deg); } }
@keyframes bc-crawl { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@keyframes bc-cell { 0%, 100% { box-shadow: inset 0 0 0 2px rgba(255,45,69,.95), 0 0 0 0 rgba(255,45,69,.0); }
  50% { box-shadow: inset 0 0 0 2px rgba(255,45,69,.55), 0 0 18px 0 rgba(255,45,69,.35); } }

/* ── Masthead ── */
.st-key-masthead { gap: .1rem !important; }
.bc-mast { display: flex; align-items: center; justify-content: space-between; gap: 10px 18px; flex-wrap: wrap;
  padding: 0 0 9px; border-bottom: 1px solid var(--bc-line2); position: relative; }
.bc-mast::after { content: ""; position: absolute; left: 0; bottom: -2px; width: 168px; height: 3px; background: var(--bc-red);
  clip-path: polygon(0 0, 100% 0, calc(100% - 3px) 100%, 0 100%); }
.bc-brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.bc-live { display: inline-flex; align-items: center; gap: 7px; background: var(--bc-red); color: #fff; flex: none;
  font: 800 13px/1 var(--bc-cond); letter-spacing: .16em; padding: 8px 16px 7px 11px;
  clip-path: polygon(0 0, 100% 0, calc(100% - 9px) 100%, 0 100%); }
.bc-live i { width: 7px; height: 7px; border-radius: 50%; background: #fff; animation: bc-blink 1.3s ease-in-out infinite; }
.bc-word { font: 900 36px/.86 var(--bc-display); letter-spacing: .015em; text-transform: uppercase; color: var(--bc-ink); white-space: nowrap; }
.bc-word b { color: var(--bc-red); font-weight: 900; }
.bc-tagline { font: 600 11.5px/1.2 var(--bc-cond); letter-spacing: .2em; text-transform: uppercase; color: var(--bc-muted); margin-top: 4px; white-space: nowrap; }
.bc-tags { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px 0; }
.bc-tag { display: inline-flex; align-items: center; gap: 7px; margin-left: -2px; padding: 7px 15px 6px;
  background: var(--bc-panel2); color: #D5DAE4; font: 700 12px/1 var(--bc-cond); letter-spacing: .1em; text-transform: uppercase;
  clip-path: polygon(7px 0, 100% 0, calc(100% - 7px) 100%, 0 100%); white-space: nowrap; }
.bc-tag i { width: 6px; height: 6px; background: var(--bc-muted); flex: none; }
.bc-tag.ok i { background: var(--bc-good); } .bc-tag.warn i { background: var(--bc-amber); }
.bc-tag.warn { color: #FFD98A; }
.bc-tag.live i { background: var(--bc-red); border-radius: 50%; animation: bc-pulse 1.6s infinite; }
.st-key-masthead [data-testid="stCaptionContainer"] p { font: 500 12px/1.3 var(--bc-cond); letter-spacing: .06em;
  color: var(--bc-muted); text-transform: none; margin: 0; }
.st-key-masthead [data-testid="stCaptionContainer"] strong { color: #C9CFDB; font-weight: 700; letter-spacing: .08em; }

/* ── Score bug (st.metric segments) ── */
.st-key-scorebug { gap: 0 !important; margin-top: .35rem; filter: drop-shadow(0 14px 26px rgba(0,0,0,.45));
  container: bug / inline-size; --bc-bugh: 112px; }
.st-key-scorebug [data-testid="stHorizontalBlock"] { gap: 0 !important; flex-wrap: nowrap !important; align-items: stretch !important; }
.st-key-scorebug [data-testid="stColumn"] { min-width: 0 !important; }
.st-key-scorebug [data-testid="stColumn"] + [data-testid="stColumn"] { margin-left: -5px; }
.st-key-scorebug [data-testid="stMetric"] { position: relative; overflow: hidden; height: var(--bc-bugh); box-sizing: border-box;
  padding: 16px 22px 10px 30px; background: linear-gradient(180deg, #1C2333 0%, #0E121B 100%);
  clip-path: polygon(var(--bc-sk) 0, 100% 0, calc(100% - var(--bc-sk)) 100%, 0 100%); }
.st-key-scorebug [data-testid="stMetricLabel"] { min-height: 0 !important; height: auto !important; margin: 0 !important; }
.st-key-scorebug [data-testid="stMetricLabel"] > div { min-height: 0 !important; }
.st-key-scorebug [data-testid="stColumn"]:first-child [data-testid="stMetric"] {
  clip-path: polygon(0 0, 100% 0, calc(100% - var(--bc-sk)) 100%, 0 100%); box-shadow: inset 5px 0 0 var(--bc-red); padding-left: 24px; }
.st-key-scorebug [data-testid="stMetricLabel"] p { font: 800 12px/1 var(--bc-cond) !important; letter-spacing: .2em;
  text-transform: uppercase; color: var(--bc-muted) !important; }
.st-key-scorebug [data-testid="stMetricValue"], .st-key-scorebug [data-testid="stMetricValue"] p {
  font: 900 52px/.92 var(--bc-display) !important; color: var(--bc-ink); letter-spacing: .005em; text-transform: uppercase; }
.st-key-scorebug [data-testid="stMetricValue"] { margin-top: 5px; }
/* The pick value is only "#N" or "Done"; no uppercasing, so innerText stays exactly what the e2e test reads. */
.st-key-scorebug [data-testid="stColumn"]:first-child [data-testid="stMetricValue"],
.st-key-scorebug [data-testid="stColumn"]:first-child [data-testid="stMetricValue"] p { text-transform: none; }
.st-key-scorebug [data-testid="stMetricDelta"] { background: transparent !important; padding: 0 !important; margin-top: 3px; }
.st-key-scorebug [data-testid="stMetricDelta"] p { font: 700 12.5px/1.1 var(--bc-cond) !important; letter-spacing: .1em;
  text-transform: uppercase; color: var(--bc-muted) !important; }
.st-key-scorebug [data-testid="stMetricDelta"] svg { display: none; }
/* On the clock: the fantasy team's color, a live dot, a giant monogram watermark */
.st-key-clock_other [data-testid="stMetric"] { background:
  linear-gradient(100deg, color-mix(in srgb, var(--bc-clock) 78%, #000) 0%, color-mix(in srgb, var(--bc-clock) 30%, #06080D) 100%); }
.st-key-clock_other [data-testid="stMetricLabel"] p, .st-key-clock_other [data-testid="stMetricDelta"] p { color: rgba(255,255,255,.86) !important; }
.st-key-clock_other [data-testid="stMetricValue"] p { color: #fff; text-shadow: 0 2px 14px rgba(0,0,0,.35); }
.st-key-scorebug [data-testid="stMetric"]::after { content: var(--bc-clock-mono); position: absolute; right: 30px; top: 50%;
  transform: translateY(-50%); font: 900 104px/1 var(--bc-display); color: rgba(255,255,255,.0); pointer-events: none; }
.st-key-clock_other [data-testid="stMetric"]::after { color: rgba(255,255,255,.13); }
.st-key-clock_you [data-testid="stMetric"]::after { color: rgba(40,26,0,.13); }
.st-key-clock_other [data-testid="stMetricLabel"] p::before, .st-key-clock_you [data-testid="stMetricLabel"] p::before {
  content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--bc-red);
  margin-right: 8px; vertical-align: 1px; animation: bc-pulse 1.5s infinite; }
.st-key-clock_other [data-testid="stMetricLabel"] p::before { background: #fff; }
.st-key-clock_you [data-testid="stMetric"] { background: linear-gradient(100deg, #FFD966 0%, #FFB800 60%, #E89B00 100%); }
.st-key-clock_you [data-testid="stMetricLabel"] p, .st-key-clock_you [data-testid="stMetricDelta"] p { color: #3B2900 !important; }
.st-key-clock_you [data-testid="stMetricValue"] p { color: #140E00 !important; }
.st-key-clock_you [data-testid="stMetric"]::before { content: ""; position: absolute; top: 0; bottom: 0; left: 0; width: 26%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.55), transparent); animation: bc-sweep 2.8s ease-in-out infinite; }
.st-key-next_soon [data-testid="stMetricValue"] p { color: var(--bc-gold) !important; }
.st-key-next_soon [data-testid="stMetricLabel"] p { color: var(--bc-gold) !important; }
.st-key-scorebug .stButton, .st-key-scorebug .stButton button { height: var(--bc-bugh); width: 100%; }
.st-key-scorebug .stButton button { border: 0 !important; border-radius: 0 !important; background: linear-gradient(180deg, #1C2333, #0E121B);
  clip-path: polygon(var(--bc-sk) 0, 100% 0, 100% 100%, 0 100%); padding-left: 18px; }
.st-key-scorebug .stButton button p { font: 800 19px/1 var(--bc-display) !important; letter-spacing: .12em; text-transform: uppercase; color: var(--bc-ink); }
.st-key-scorebug .stButton button:hover:not(:disabled) { background: linear-gradient(180deg, var(--bc-red), var(--bc-red2)); }
.st-key-scorebug .stButton button:disabled p { color: var(--bc-dim); }
/* Round strip: the snake order of the current round, like a shot-clock bar under the bug */
.bc-round { display: flex; align-items: center; gap: 10px; margin: 4px 0 0; padding: 0 2px; }
.bc-round .rd { flex: none; font: 800 12px/1 var(--bc-cond); letter-spacing: .16em; color: var(--bc-muted); text-transform: uppercase; }
.bc-round .rd b { color: var(--bc-ink); }
.bc-round .segs { flex: 1; display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 3px; min-width: 0; }
.bc-round .segs i { height: 17px; display: grid; place-items: center; font: 700 10.5px/1 var(--bc-cond); font-style: normal;
  letter-spacing: .04em; color: var(--bc-dim); background: var(--bc-panel2); border-top: 2px solid var(--c, #333);
  clip-path: polygon(4px 0, 100% 0, calc(100% - 4px) 100%, 0 100%); overflow: hidden; white-space: nowrap; }
.bc-round .segs i.done { color: #C7CDD8; background: #222A3A; }
.bc-round .segs i.cur { background: var(--bc-red); color: #fff; border-top-color: #fff; animation: bc-blink 1.4s ease-in-out infinite; }
.bc-round .segs i.mine { color: var(--bc-gold); }
.bc-round .segs i.mine.cur { color: #fff; }
/* The bug sizes itself by its own width (sidebar open on a tablet, phones), not by the window */
@container bug (max-width: 820px) {
  .st-key-scorebug [data-testid="stHorizontalBlock"] { --bc-bugh: 100px; }
  .st-key-scorebug [data-testid="stMetric"] { padding: 14px 10px 8px 22px; }
  .st-key-scorebug [data-testid="stColumn"]:first-child [data-testid="stMetric"] { padding-left: 17px; }
  .st-key-scorebug [data-testid="stMetricValue"], .st-key-scorebug [data-testid="stMetricValue"] p { font-size: 42px !important; }
  .st-key-scorebug [data-testid="stMetricLabel"] p { font-size: 10.5px !important; letter-spacing: .15em; }
  .st-key-scorebug [data-testid="stMetricDelta"] p { font-size: 11px !important; letter-spacing: .07em; }
  .st-key-scorebug [data-testid="stMetric"]::after { font-size: 84px; right: 14px; }
}
@container bug (max-width: 640px) {
  .st-key-scorebug .stButton button { padding: 0 0 0 10px; min-width: 0; }
  .st-key-scorebug .stButton button p { font-size: 0 !important; letter-spacing: 0; }
  .st-key-scorebug .stButton button p::before { content: "↶"; font: 900 30px/1 var(--bc-display); }
  .st-key-next_soon [data-testid="stMetricLabel"] p, .st-key-next_later [data-testid="stMetricLabel"] p { font-size: 0 !important; }
  .st-key-next_soon [data-testid="stMetricLabel"] p::after, .st-key-next_later [data-testid="stMetricLabel"] p::after {
    content: "You next"; font-size: 10.5px; }
  .bc-round .rd .of { display: none; }
}
@container bug (max-width: 480px) {
  .st-key-scorebug [data-testid="stHorizontalBlock"] { --bc-sk: 8px; --bc-bugh: 86px; }
  .st-key-scorebug [data-testid="stMetric"] { padding: 10px 4px 5px 15px; }
  .st-key-scorebug [data-testid="stColumn"]:first-child [data-testid="stMetric"] { padding-left: 12px; }
  .st-key-scorebug [data-testid="stMetricLabel"] p { font-size: 9.5px !important; letter-spacing: .12em; }
  .st-key-next_soon [data-testid="stMetricLabel"] p, .st-key-next_later [data-testid="stMetricLabel"] p { font-size: 0 !important; }
  .st-key-next_soon [data-testid="stMetricLabel"] p::after, .st-key-next_later [data-testid="stMetricLabel"] p::after { font-size: 9.5px; }
  .st-key-scorebug [data-testid="stMetricValue"], .st-key-scorebug [data-testid="stMetricValue"] p { font-size: 33px !important; }
  .st-key-scorebug [data-testid="stMetricValue"] { margin-top: 2px; }
  .st-key-scorebug [data-testid="stMetricDelta"] { margin-top: 1px; }
  .st-key-scorebug [data-testid="stMetricDelta"] p { font-size: 9.5px !important; letter-spacing: .03em; }
  .st-key-clock_other [data-testid="stMetricLabel"] p::before, .st-key-clock_you [data-testid="stMetricLabel"] p::before {
    width: 6px; height: 6px; margin-right: 5px; }
  .st-key-scorebug [data-testid="stMetric"]::after { display: none; }
  .bc-round { gap: 6px; }
  .bc-round .segs i { font-size: 0; height: 9px; }
}

/* ── Draft bar: the search box is a lower third ── */
.st-key-draftbar { margin-top: .55rem; }
.st-key-pick_search [data-testid="stWidgetLabel"] { margin-bottom: 0; }
.st-key-pick_search [data-testid="stWidgetLabel"] p { display: inline-block; font: 800 12.5px/1 var(--bc-cond) !important;
  letter-spacing: .16em; text-transform: uppercase; color: #fff !important; background: var(--bc-red);
  padding: 7px 22px 6px 12px; clip-path: polygon(0 0, 100% 0, calc(100% - 9px) 100%, 0 100%); }
.st-key-pick_search .react-aria-ComboBox { transition: filter .15s; }
.st-key-pick_search .react-aria-ComboBox:focus-within { filter: drop-shadow(0 0 14px rgba(255,45,69,.55)); }
.st-key-pick_search [role="group"] { min-height: 62px; background: #F3F5F9 !important; border: 0 !important; border-radius: 0 !important;
  clip-path: polygon(0 0, 100% 0, calc(100% - 18px) 100%, 0 100%); box-shadow: inset 6px 0 0 var(--bc-red) !important; padding-right: 18px; }
.st-key-pick_search input { color: #0A0D14 !important; font: 600 21px/1.2 var(--bc-body) !important; padding-left: 20px !important;
  caret-color: var(--bc-red); }
.st-key-pick_search input::placeholder { color: #5D6576 !important; opacity: 1; font-weight: 500; }
.st-key-pick_search input:disabled::placeholder { color: #9AA1AE !important; }
.st-key-pick_search [role="group"] button, .st-key-pick_search [role="group"] svg { color: #0A0D14 !important; }
.st-key-draftbar [data-testid="stExpander"] details { border: 0; border-radius: 0; background: var(--bc-panel2); }
.st-key-draftbar [data-testid="stExpander"] summary { min-height: 62px; }
.st-key-draftbar [data-testid="stExpander"] summary p { font: 700 13px/1 var(--bc-cond); letter-spacing: .14em; text-transform: uppercase;
  color: #C9CFDB; white-space: nowrap; }
.st-key-draftbar { container: dbar / inline-size; position: relative; z-index: 20; }
/* Pick options drops down over the page instead of pushing the search box down (no layout jump mid-draft) */
@media (min-width: 641px) {
  .st-key-draftbar [data-testid="stExpander"], .st-key-draftbar [data-testid="stExpander"] details { position: relative; overflow: visible !important; }
  .st-key-draftbar [data-testid="stExpanderDetails"] { position: absolute; left: auto; right: 0; width: max(100%, 270px); top: 100%; z-index: 60;
    box-sizing: border-box;
    background: #151A25; padding: 12px 16px 14px !important; box-shadow: inset 0 3px 0 var(--bc-red), 0 22px 40px rgba(0,0,0,.6); }
}
@container dbar (max-width: 760px) {
  .st-key-draftbar [data-testid="stExpander"] summary { padding-left: 10px; padding-right: 8px; }
  .st-key-draftbar [data-testid="stExpander"] summary p { font-size: 12px; letter-spacing: .07em; }
  .st-key-pick_search input { font-size: 18px !important; }
}

/* ── Crawl ticker ── */
.bc-crawl-cq { container: crawl / inline-size; }
@container crawl (max-width: 700px) { .bc-crawl .new .lbl, .bc-crawl .new .by { display: none; } .bc-crawl .new { max-width: 64%; } }
.bc-crawl { display: flex; align-items: stretch; height: 40px; margin: 12px 0 2px; background: #0B0E15; white-space: nowrap;
  border-top: 1px solid var(--bc-line2); border-bottom: 1px solid var(--bc-line2); overflow: hidden; position: relative; }
.bc-crawl .tag { flex: none; display: flex; align-items: center; gap: 7px; padding: 0 22px 0 14px; background: var(--bc-red); color: #fff;
  font: 900 16px/1 var(--bc-display); letter-spacing: .12em; text-transform: uppercase; z-index: 3;
  clip-path: polygon(0 0, 100% 0, calc(100% - 12px) 100%, 0 100%); }
.bc-crawl .new { flex: none; display: flex; align-items: center; gap: 9px; padding: 0 26px 0 22px; margin-left: -10px; z-index: 2;
  background: #F3F5F9; color: #0A0D14; clip-path: polygon(12px 0, 100% 0, calc(100% - 12px) 100%, 0 100%); max-width: 46%; }
.bc-crawl .new .lbl { font: 800 11px/1 var(--bc-cond); letter-spacing: .16em; color: var(--bc-red); }
.bc-crawl .new .no { font: 900 18px/1 var(--bc-display); }
.bc-crawl .new .pl { font: 600 15px/1 var(--bc-cond); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bc-crawl .new .pl b { font-weight: 800; text-transform: uppercase; }
.bc-crawl .new .by { font: 700 12px/1 var(--bc-cond); color: #4B5263; letter-spacing: .06em; text-transform: uppercase; white-space: nowrap; }
.bc-crawl .track { flex: 1; min-width: 0; overflow: hidden; display: flex; align-items: center;
  -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 24px, #000 calc(100% - 48px), transparent);
          mask-image: linear-gradient(90deg, transparent 0, #000 24px, #000 calc(100% - 48px), transparent); }
.bc-crawl .run { display: inline-flex; align-items: center; white-space: nowrap; will-change: transform; }
.bc-crawl .run.go { animation: bc-crawl var(--dur, 40s) linear infinite; }
.bc-crawl .track:hover .run { animation-play-state: paused; }
.bc-crawl .it { display: inline-flex; align-items: center; gap: 8px; padding: 0 20px; font: 600 14px/1 var(--bc-cond);
  color: #D7DCE6; border-right: 1px solid var(--bc-line2); }
.bc-crawl .it .no { font: 800 15px/1 var(--bc-display); color: var(--bc-muted); }
.bc-crawl .it b { font-weight: 800; text-transform: uppercase; color: var(--bc-ink); letter-spacing: .02em; }
.bc-crawl .it .nba { font: 700 12px/1 var(--bc-cond); color: var(--bc-muted); letter-spacing: .06em; }
.bc-crawl .it.mine b, .bc-crawl .it.mine .no { color: var(--bc-gold); }
.bc-crawl .empty { padding: 0 18px; font: 600 14px/1 var(--bc-cond); letter-spacing: .06em; color: var(--bc-muted); text-transform: uppercase; }
.bc-ft { display: inline-block; font: 800 11px/1 var(--bc-cond); letter-spacing: .04em; padding: 3px 7px 2px; color: #07090E;
  background: var(--c, #8D96A8); clip-path: polygon(3px 0, 100% 0, calc(100% - 3px) 100%, 0 100%); white-space: nowrap; }
@media (prefers-reduced-motion: reduce) { .bc-crawl .run.go { animation: none; } .bc-crawl .track { overflow-x: auto; } }

/* ── "The pick is in" lower third: slides in from the right after each pick, holds, slides out ── */
@keyframes bc-l3 { 0% { transform: translateX(calc(100% + 48px)); opacity: 1; } 8% { transform: translateX(0); }
  84% { transform: translateX(0); opacity: 1; } 100% { transform: translateX(calc(100% + 48px)); opacity: 0; visibility: hidden; } }
@keyframes bc-l3-fade { 0%, 80% { opacity: 1; } 100% { opacity: 0; visibility: hidden; } }
@keyframes bc-wipe { from { clip-path: polygon(0 0, 0 0, 0 100%, 0 100%); } to { clip-path: polygon(0 0, 100% 0, calc(100% - 18px) 100%, 0 100%); } }
.bc-l3 { position: fixed; right: 28px; bottom: 26px; z-index: 999990; width: min(510px, calc(100vw - 32px)); pointer-events: none;
  animation: bc-l3 4.6s cubic-bezier(.2,.85,.25,1) forwards; filter: drop-shadow(0 20px 34px rgba(0,0,0,.65)); }
.bc-l3 .k { display: inline-flex; align-items: center; gap: 8px; background: var(--bc-red); color: #fff; padding: 8px 24px 7px 12px;
  font: 800 13px/1 var(--bc-cond); letter-spacing: .22em; text-transform: uppercase; clip-path: polygon(0 0, 100% 0, calc(100% - 10px) 100%, 0 100%); }
.bc-l3 .k i { width: 7px; height: 7px; border-radius: 50%; background: #fff; animation: bc-blink 1s ease-in-out infinite; }
.bc-l3.mine .k { background: var(--bc-gold); color: #1A1200; }
.bc-l3.mine .k i { background: #1A1200; }
.bc-l3 .bar { display: grid; grid-template-columns: auto auto minmax(0, 1fr); align-items: center; gap: 14px; padding: 12px 26px 12px 0;
  background: linear-gradient(100deg, #FFFFFF 0%, #E4E8EF 100%); color: #0A0D14; box-shadow: inset 7px 0 0 var(--tc);
  clip-path: polygon(0 0, 100% 0, calc(100% - 18px) 100%, 0 100%); animation: bc-wipe .55s .15s cubic-bezier(.2,.85,.25,1) both; }
.bc-l3 .no { display: flex; flex-direction: column; align-items: center; padding-left: 20px; font: 900 46px/.85 var(--bc-display); color: #0A0D14; }
.bc-l3 .no small { font: 800 10.5px/1 var(--bc-cond); letter-spacing: .2em; text-transform: uppercase; color: #5D6576; margin-bottom: 3px; }
.bc-l3 .who { display: flex; flex-direction: column; min-width: 0; }
.bc-l3 .fn { font: 700 15px/1 var(--bc-cond); letter-spacing: .14em; text-transform: uppercase; color: #4B5263; }
.bc-l3 .ln { font: 900 38px/.9 var(--bc-display); text-transform: uppercase; letter-spacing: .01em; color: #07090E;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.bc-l3 .meta { display: flex; align-items: center; gap: 6px; margin-top: 6px; font: 600 13px/1 var(--bc-cond); letter-spacing: .06em;
  text-transform: uppercase; color: #4B5263; white-space: nowrap; }
.bc-l3 .meta b { color: #0A0D14; font-weight: 800; }
.bc-l3 .strip { display: flex; align-items: center; gap: 9px; margin-right: 18px; padding: 8px 16px; background: #0B0E15; color: #C9CFDB;
  box-shadow: inset 0 3px 0 var(--fc); font: 700 12.5px/1 var(--bc-cond); letter-spacing: .12em; text-transform: uppercase; white-space: nowrap; overflow: hidden; }
.bc-l3 .strip > span:first-child { color: var(--bc-muted); }
.bc-l3 .strip b { color: #fff; font-weight: 800; overflow: hidden; text-overflow: ellipsis; }
.bc-l3 .strip .rd { margin-left: auto; color: var(--bc-muted); }
.bc-l3 .call { padding: 4px 9px 3px; font: 800 11.5px/1 var(--bc-cond); letter-spacing: .12em; clip-path: polygon(4px 0, 100% 0, calc(100% - 4px) 100%, 0 100%); }
.bc-l3 .call.good { background: var(--bc-good); color: #04130B; } .bc-l3 .call.bad { background: var(--bc-bad); color: #1A0307; }
@media (prefers-reduced-motion: reduce) { .bc-l3 { animation: bc-l3-fade 4.6s linear forwards; } .bc-l3 .bar { animation: none; } }
@media (max-width: 640px) {
  .bc-l3 { left: 10px; right: 10px; bottom: 12px; width: auto; }
  .bc-l3 .bar { gap: 10px; padding: 10px 18px 10px 0; }
  .bc-l3 .no { font-size: 34px; padding-left: 14px; }
  .bc-l3 .bc-av { --s: 48px !important; }
  .bc-l3 .ln { font-size: 29px; }
  .bc-l3 .fn { font-size: 13px; }
  .bc-l3 .strip { margin-right: 0; font-size: 11px; gap: 7px; padding: 7px 12px; }
  .bc-l3 .strip > span:first-child, .bc-l3 .strip .rd { display: none; }
  .bc-l3 .call { margin-left: auto; }
}

/* ── Avatars and logos (initials tile when the image can't load) ── */
.bc-av { position: relative; display: inline-grid; place-items: center; flex: none; width: var(--s, 34px); height: var(--s, 34px);
  background: linear-gradient(150deg, var(--tc, #3A4150) 0%, var(--tc0, #151A25) 100%); color: #fff; overflow: hidden;
  clip-path: polygon(16% 0, 100% 0, 84% 100%, 0 100%); font: 800 calc(var(--s, 34px) * .4)/1 var(--bc-cond); letter-spacing: .02em; }
/* The headshot stays invisible until it has actually loaded (img_script() marks the tile .ok), so a slow or
   blocked request shows the initials, never an empty tile. 3 s CSS fallback in case the script never runs. */
@keyframes bc-img-in { to { opacity: 1; } }
.bc-av img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 50% 18%;
  color: transparent; opacity: 0; animation: bc-img-in 0s 3s forwards; }
.bc-av.ok { color: transparent; }
.bc-av.ok img { opacity: 1; animation: none; }
.bc-av img::after { content: attr(data-i); position: absolute; inset: 0; display: grid; place-items: center;
  background: linear-gradient(150deg, var(--tc, #3A4150) 0%, var(--tc0, #151A25) 100%); color: #fff;
  font: 800 calc(var(--s, 34px) * .4)/1 var(--bc-cond); }
/* Team logo: a team-color slash shows until the logo has loaded (img_script() adds .ok) or if it never does */
.bc-lg { position: relative; display: inline-block; flex: none; width: var(--s, 16px); height: var(--s, 16px); vertical-align: -3px;
  background: linear-gradient(180deg, var(--tc, #8D96A8), color-mix(in srgb, var(--tc, #8D96A8) 45%, #000));
  clip-path: polygon(42% 0, 100% 0, 58% 100%, 0 100%); }
.bc-lg.ok { background: none; clip-path: none; }
.bc-lg img { width: 100%; height: 100%; object-fit: contain; color: transparent; opacity: 0; animation: bc-img-in 0s 3s forwards; }
.bc-lg.ok img { opacity: 1; animation: none; }

/* ── Tabs: slanted broadcast nav ── */
[data-testid="stTabs"] [role="tablist"] { gap: 2px; border-bottom: 2px solid var(--bc-line2); }
[data-testid="stTab"] { padding: 9px 22px 7px !important; margin-bottom: -2px; background: transparent;
  clip-path: polygon(10px 0, 100% 0, calc(100% - 10px) 100%, 0 100%); }
[data-testid="stTab"] p { font: 800 20px/1 var(--bc-display) !important; letter-spacing: .08em; text-transform: uppercase; color: var(--bc-muted); }
[data-testid="stTab"]:hover p { color: var(--bc-ink); }
[data-testid="stTab"][aria-selected="true"] { background: var(--bc-red); }
[data-testid="stTab"][aria-selected="true"] p { color: #fff !important; }
[data-testid="stTabs"] .react-aria-SelectionIndicator { display: none !important; }

/* ── Draft room list controls ── */
.st-key-listbar [data-testid="stWidgetLabel"] p { font: 700 11.5px/1 var(--bc-cond) !important; letter-spacing: .16em;
  text-transform: uppercase; color: var(--bc-muted) !important; }
.st-key-w_pos [role="radiogroup"] { gap: 0; flex-wrap: nowrap; }
.st-key-w_pos button[data-variant="segmented_control"] { min-height: 36px; padding: 0 13px; border-radius: 0 !important;
  border: 0 !important; background: var(--bc-panel2); margin-left: -1px;
  clip-path: polygon(6px 0, 100% 0, calc(100% - 6px) 100%, 0 100%); }
.st-key-w_pos button[data-variant="segmented_control"] p { font: 800 14.5px/1 var(--bc-cond); letter-spacing: .08em; color: #AEB6C5; }
.st-key-w_pos button[data-variant="segmented_control"][aria-checked="true"] { background: #F3F5F9; }
.st-key-w_pos button[data-variant="segmented_control"][aria-checked="true"] p { color: #07090E; }
.st-key-listbar [data-testid="stSelectbox"] [role="group"] { min-height: 36px; border-radius: 0; }
.st-key-listnote [data-testid="stCaptionContainer"] p { font-size: 12px; color: var(--bc-muted); }

/* ── Rail cards (no boxes inside boxes: one panel, hairline rows) ── */
.bc-card { background: linear-gradient(180deg, #11151F 0%, #0C1018 100%); padding: 0 0 8px; margin: 0 0 16px; position: relative;
  container-type: inline-size; }
.bc-card::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: var(--c, var(--bc-red)); }
.bc-card-hd { display: flex; align-items: flex-end; gap: 10px; padding: 14px 16px 10px; }
.bc-card-hd .k { font: 800 11px/1 var(--bc-cond); letter-spacing: .2em; text-transform: uppercase; color: var(--c, var(--bc-red)); }
.bc-card-hd .t { font: 900 26px/.9 var(--bc-display); text-transform: uppercase; color: var(--bc-ink); letter-spacing: .01em;
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bc-card-hd .stack { display: flex; flex-direction: column; gap: 5px; min-width: 0; flex: 1; }
.bc-card-hd .n { margin-left: auto; font: 900 30px/.85 var(--bc-display); color: var(--bc-ink); white-space: nowrap; }
.bc-card-hd .n small { font-size: 17px; color: var(--bc-muted); }
.bc-card-hd .sub { margin-left: auto; font: 600 11.5px/1.2 var(--bc-cond); letter-spacing: .06em; color: var(--bc-muted); text-align: right; }
.bc-prog { height: 4px; margin: 0 16px 8px; background: var(--bc-panel3); }
.bc-prog i { display: block; height: 100%; background: var(--c, var(--bc-red)); }

/* My roster */
.bc-ros .r { display: grid; grid-template-columns: 42px 30px minmax(0, 1fr); align-items: center; gap: 10px; padding: 5px 14px 5px 12px;
  min-height: 34px; border-top: 1px solid var(--bc-line); }
.bc-ros .who { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.bc-ros .slot { font: 800 13px/1 var(--bc-cond); letter-spacing: .06em; text-align: center; padding: 5px 0 4px; color: var(--bc-muted);
  background: var(--bc-panel3); clip-path: polygon(5px 0, 100% 0, calc(100% - 5px) 100%, 0 100%); }
.bc-ros .r.f .slot { background: #E9EDF4; color: #07090E; }
.bc-ros .r.f { box-shadow: inset 3px 0 0 var(--tc, transparent); }
.bc-ros .nm { font: 600 14px/1.1 var(--bc-cond); color: #C9CFDB; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bc-ros .nm b { font-weight: 800; color: var(--bc-ink); text-transform: uppercase; letter-spacing: .02em; }
.bc-ros .mt { font: 600 11px/1 var(--bc-cond); letter-spacing: .07em; color: var(--bc-muted); white-space: nowrap; }
.bc-ros .r.e .nm { color: var(--bc-dim); font-weight: 500; letter-spacing: .1em; }
.bc-ros .r.e .slot { background: transparent; box-shadow: inset 0 0 0 1px var(--bc-line2); }
.bc-ros .ph { width: 30px; height: 22px; }

/* Category race */
.bc-cat .r { display: grid; grid-template-columns: 34px 44px 40px minmax(30px, 1fr) 38px var(--fitw, 0px); align-items: center; gap: 6px;
  padding: 6px 14px; border-top: 1px solid var(--bc-line); }
.bc-cat .h { padding-top: 0; padding-bottom: 6px; border-top: 0; }
.bc-cat .h span { font: 700 10.5px/1 var(--bc-cond); letter-spacing: .14em; text-transform: uppercase; color: var(--bc-dim); }
.bc-cat .c { font: 800 15px/1 var(--bc-display); letter-spacing: .04em; color: var(--bc-ink); }
.bc-cat .v, .bc-cat .z { font: 600 13px/1 var(--bc-cond); font-variant-numeric: tabular-nums; text-align: right; color: #C9CFDB; }
.bc-cat .z.pos { color: #8EF0BF; } .bc-cat .z.neg { color: #FF9AAA; }
.bc-cat .bar { position: relative; height: 8px; background:
  repeating-linear-gradient(90deg, transparent 0, transparent calc(100% / var(--n) - 2px), #07090E calc(100% / var(--n) - 2px), #07090E calc(100% / var(--n))),
  var(--bc-panel3); }
.bc-cat .bar i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--tier); -webkit-mask: linear-gradient(#000, #000); }
.bc-cat .rk { font: 900 20px/1 var(--bc-display); text-align: right; color: var(--tier); }
.bc-cat .rk sup { font-size: 10px; letter-spacing: .04em; margin-left: 1px; }
.bc-cat .fit { font: 700 11.5px/1 var(--bc-cond); text-align: right; color: var(--bc-muted); }
.bc-cat .fit.up { color: var(--bc-gold); } .bc-cat .fit.off { color: var(--bc-dim); text-decoration: line-through; }
.bc-cat .r.punt { opacity: .42; }
.bc-cat .r.punt .rk { font: 800 11px/1 var(--bc-cond); letter-spacing: .12em; color: var(--bc-muted); }
.bc-cat .foot { padding: 8px 16px 2px; font: 500 11.5px/1.35 var(--bc-cond); letter-spacing: .03em; color: var(--bc-muted); }
.bc-fp { display: flex; align-items: baseline; gap: 14px; padding: 4px 16px 8px; }
.bc-fp .big { font: 900 54px/.9 var(--bc-display); color: var(--bc-ink); }
.bc-fp .rk { font: 900 26px/1 var(--bc-display); color: var(--tier); }
/* Narrow rail (tablet with the sidebar open): last names only, no avatars, so names never truncate to "Luka ..." */
@container (max-width: 250px) {
  .bc-ros .r { grid-template-columns: 36px minmax(0, 1fr); gap: 8px; padding-left: 10px; padding-right: 8px; }
  .bc-ros .bc-av, .bc-ros .ph, .bc-ros .nm .fn { display: none; }
  .bc-card-hd { padding-left: 12px; padding-right: 12px; }
  .bc-card-hd .t { font-size: 22px; }
  .bc-card-hd .sub { display: none; }
  .bc-cat .r { grid-template-columns: 32px 40px minmax(12px, 1fr) 30px var(--fitw, 0px); padding-left: 10px; padding-right: 10px; gap: 5px; }
  .bc-cat .v, .bc-cat .h span:nth-child(2) { display: none; }
}

/* ── Draft board: broadcast draft tracker ── */
.bc-legend { display: flex; gap: 8px 18px; flex-wrap: wrap; align-items: center; margin: 4px 0 12px;
  font: 600 13px/1 var(--bc-cond); letter-spacing: .05em; color: #AEB6C5; }
.bc-legend .sw { display: inline-block; width: 14px; height: 14px; margin-right: 6px; vertical-align: -2px; }
.bc-board-wrap { overflow-x: auto; padding-bottom: 6px; }
.bc-board { width: 100%; min-width: 980px; border-collapse: separate; border-spacing: 3px; table-layout: fixed; }
.bc-board thead th { padding: 0; vertical-align: bottom; }
.bc-board thead .tm { display: block; padding: 8px 7px 7px; background: var(--bc-panel2);
  border-top: 4px solid var(--c); text-align: left; overflow: hidden; }
.bc-board thead .tm .nm { font: 800 15px/1 var(--bc-display); letter-spacing: .05em; text-transform: uppercase; color: var(--bc-ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bc-board thead th.me .tm { background: linear-gradient(180deg, rgba(255,200,61,.28), rgba(255,200,61,.10)); }
.bc-board thead th.me .tm .nm { color: var(--bc-gold); }
.bc-board thead th.rdh { width: 40px; }
.bc-board tbody th { width: 40px; padding: 0; vertical-align: middle; position: sticky; left: 0; z-index: 2; background: var(--bc-bg); }
.bc-board tbody th .rdl { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 0;
  font: 900 20px/1 var(--bc-display); color: var(--bc-ink); }
.bc-board tbody th .rdl small { font: 700 9.5px/1 var(--bc-cond); letter-spacing: .14em; color: var(--bc-dim); }
.bc-board tbody th .rdl i { font-style: normal; font: 700 12px/1 var(--bc-cond); color: var(--bc-red); }
.bc-board td { height: 56px; padding: 0; vertical-align: top; background: #0D1119; }
.bc-board td.me { background: rgba(255,200,61,.055); }
.bc-board td .em { display: block; padding: 6px 7px; font: 700 13px/1 var(--bc-display); color: #2C3445; letter-spacing: .04em; }
.bc-board td.me .em { color: rgba(255,200,61,.45); }
.bc-board td.cur { background: rgba(255,45,69,.12); animation: bc-cell 1.6s ease-in-out infinite; }
.bc-board td.cur .em { color: #fff; }
.bc-board td.cur .em b { display: flex; align-items: center; gap: 5px; margin-top: 7px; font: 800 11px/1 var(--bc-cond);
  letter-spacing: .12em; color: var(--bc-red); }
.bc-board td.cur .em b::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--bc-red); animation: bc-pulse 1.5s infinite; }
.bc-pk { height: 100%; box-sizing: border-box; padding: 6px 6px 5px 8px; position: relative; overflow: hidden;
  background: linear-gradient(120deg, color-mix(in srgb, var(--tc) 34%, transparent) 0%, transparent 72%);
  box-shadow: inset 3px 0 0 var(--tc); }
.bc-pk + .bc-pk { border-top: 1px solid var(--bc-line2); }
.bc-pk .ln { font: 800 16px/1.02 var(--bc-display); letter-spacing: .02em; text-transform: uppercase; color: var(--bc-ink);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

.bc-pk .mt { margin-top: 3px; font: 600 11px/1 var(--bc-cond); letter-spacing: .04em; color: #AEB6C5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bc-pk .mt .no { color: var(--bc-muted); }
.bc-pk.k .ln::after { content: " K"; color: var(--bc-gold); font-size: 11px; }
.bc-pk.new { box-shadow: inset 3px 0 0 var(--tc), inset 0 0 0 1px rgba(255,255,255,.6); }
.bc-pk.new::after { content: "Just in"; position: absolute; right: 0; bottom: 0; padding: 3px 5px 2px 8px; background: var(--bc-red);
  color: #fff; font: 800 9px/1 var(--bc-cond); letter-spacing: .14em; text-transform: uppercase; clip-path: polygon(5px 0, 100% 0, 100% 100%, 0 100%); }
.bc-pk.um .mt::after { content: " ⚠"; color: var(--bc-amber); }

/* ── Resume slate ── */
.bc-resume { position: relative; margin: 26px 0 16px; max-width: 820px; padding: 26px 30px 24px;
  background: linear-gradient(110deg, #141A27 0%, #0B0E15 100%); box-shadow: inset 6px 0 0 var(--bc-red); }
.bc-resume .k { display: inline-flex; align-items: center; gap: 7px; font: 800 12px/1 var(--bc-cond); letter-spacing: .2em; color: #fff;
  background: var(--bc-red); padding: 7px 16px 6px 10px; clip-path: polygon(0 0, 100% 0, calc(100% - 8px) 100%, 0 100%); }
.bc-resume h2 { font: 900 64px/.9 var(--bc-display) !important; text-transform: uppercase; letter-spacing: .01em; margin: 14px 0 6px !important;
  padding: 0 !important; color: var(--bc-ink); }
.bc-resume .stats { display: flex; gap: 0; margin-top: 14px; flex-wrap: wrap; }
.bc-resume .stats div { padding: 4px 22px 2px 0; margin-right: 22px; border-right: 1px solid var(--bc-line2); }
.bc-resume .stats div:last-child { border-right: 0; }
.bc-resume .stats b { display: block; font: 900 40px/1 var(--bc-display); color: var(--bc-ink); }
.bc-resume .stats span { font: 700 11.5px/1 var(--bc-cond); letter-spacing: .16em; text-transform: uppercase; color: var(--bc-muted); }
.bc-resume p { margin: 0; font: 500 15px/1.4 var(--bc-body); color: var(--bc-muted); }

/* ── Streamlit chrome: buttons, sidebar, alerts, toasts, headings ── */
.stButton button, .stDownloadButton button, [data-testid="stFormSubmitButton"] button { border-radius: 2px; }
.stButton button p, .stDownloadButton button p, [data-testid="stFormSubmitButton"] button p {
  font-family: var(--bc-cond); font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 14px; }
.stButton button[kind="primary"] { background: var(--bc-red); border-color: var(--bc-red); color: #fff;
  clip-path: polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%); padding-left: 22px; padding-right: 22px; }
.stButton button[kind="primary"]:hover { background: #FF4A5F; border-color: #FF4A5F; }
[data-testid="stSidebar"] { background: #0A0D14; border-right: 1px solid var(--bc-line); }
[data-testid="stSidebar"] h2 { font: 900 21px/1 var(--bc-display) !important; letter-spacing: .1em; text-transform: uppercase;
  color: var(--bc-ink); padding: 1.1rem 0 .35rem !important; display: flex; align-items: center; gap: 9px; }
[data-testid="stSidebar"] h2::before { content: ""; width: 14px; height: 15px; background: var(--bc-red);
  clip-path: polygon(30% 0, 100% 0, 70% 100%, 0 100%); flex: none; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { font: 600 13px/1.2 var(--bc-cond); letter-spacing: .07em;
  text-transform: uppercase; color: #AEB6C5; }
/* Category chips: slim dark "stat tabs" with a white tick, not big red pills. Punted ones are struck out. */
[data-testid="stMultiSelectTagsContainer"] [data-tag] { background: #1B2130 !important; color: #E9EDF4 !important;
  border: 0 !important; border-radius: 0 !important; box-shadow: inset 3px 0 0 #E9EDF4; padding-left: 9px !important;
  clip-path: polygon(0 0, 100% 0, calc(100% - 5px) 100%, 0 100%); margin: 2px 3px 2px 0 !important; }
[data-testid="stMultiSelectTagsContainer"] [data-tag] > span { color: inherit !important; font: 700 13px/1.15 var(--bc-cond) !important;
  letter-spacing: .07em; }
[data-testid="stMultiSelectTagsContainer"] [data-tag] button { color: var(--bc-muted) !important; }
[data-testid="stMultiSelectTagsContainer"] [data-tag] button:hover { color: var(--bc-red) !important; }
.st-key-sb_punts [data-testid="stMultiSelectTagsContainer"] [data-tag] { background: rgba(255,45,69,.14) !important;
  color: #FF9AAA !important; box-shadow: inset 3px 0 0 var(--bc-red); }
.st-key-sb_punts [data-testid="stMultiSelectTagsContainer"] [data-tag] > span { text-decoration: line-through; text-decoration-thickness: 2px; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color: var(--bc-muted); font-size: 12.5px; }
[data-testid="stSidebar"] hr { margin: .6rem 0; }
.stMain h3 { font: 900 24px/1 var(--bc-display) !important; letter-spacing: .08em; text-transform: uppercase; display: flex;
  align-items: center; gap: 10px; }
.stMain h3::before { content: ""; width: 14px; height: 16px; background: var(--bc-red); clip-path: polygon(30% 0, 100% 0, 70% 100%, 0 100%); }
[data-testid="stToast"] { border-radius: 0 !important; background: #F3F5F9 !important; box-shadow: inset 6px 0 0 var(--bc-red), 0 12px 30px rgba(0,0,0,.5) !important; }
[data-testid="stToast"] p { color: #0A0D14 !important; font: 700 15px/1.2 var(--bc-cond) !important; letter-spacing: .03em; }
[data-testid="stToast"] button svg { color: #0A0D14 !important; }
[data-testid="stAlertContainer"] { border-radius: 0 !important; background: #121722 !important; box-shadow: inset 4px 0 0 var(--bc-muted); }
[data-testid="stAlertContainer"] p { color: #E3E7EF !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { background: rgba(255,176,32,.09) !important; box-shadow: inset 4px 0 0 var(--bc-amber); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) { background: rgba(255,45,69,.10) !important; box-shadow: inset 4px 0 0 var(--bc-red); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { background: rgba(46,229,143,.08) !important; box-shadow: inset 4px 0 0 var(--bc-good); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) { background: rgba(61,139,255,.09) !important; box-shadow: inset 4px 0 0 #3D8BFF; }
[data-testid="stExpander"] details { border-radius: 2px; }

/* ── Recommended pick ── */
.bc-rec { margin-bottom: 0; padding-bottom: 12px; }
.bc-rec .bc-card-hd .t { font-size: 22px; }
.bc-rec-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 0 18px; padding: 2px 16px 0; }
.bc-rec-main { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 14px; align-items: center; padding: 10px 14px 12px;
  background: linear-gradient(100deg, rgba(255,200,61,.10), rgba(255,200,61,0) 70%); box-shadow: inset 4px 0 0 var(--tc, var(--bc-gold)); }
.bc-rec-main .bc-av { grid-row: span 2; }
.bc-rec-main .nm { font: 600 15px/1 var(--bc-cond); color: #C9CFDB; letter-spacing: .04em; text-transform: uppercase;
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bc-rec-main .nm b { display: block; font: 900 30px/.95 var(--bc-display); color: var(--bc-ink); letter-spacing: .01em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bc-rec-main .nm b.long { font-size: 24px; }
.bc-rec .bc-card-hd .t { white-space: normal; line-height: 1; }
.bc-rec-main .mt { font: 600 12px/1 var(--bc-cond); letter-spacing: .08em; color: var(--bc-muted); text-transform: uppercase; }
.bc-rec-why { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.bc-rec-why li { font: 500 13.5px/1.25 var(--bc-body); color: #D5DAE4; padding-left: 16px; position: relative; }
.bc-rec-why li::before { content: ""; position: absolute; left: 2px; top: .45em; width: 7px; height: 7px; background: var(--bc-gold);
  clip-path: polygon(0 0, 100% 50%, 0 100%); }
.bc-rec-alt { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 10px; font: 600 12.5px/1.2 var(--bc-cond); letter-spacing: .04em;
  color: var(--bc-muted); }
.bc-rec-alt .k { font-weight: 800; letter-spacing: .16em; text-transform: uppercase; color: var(--bc-dim); }
.bc-rec-alt b { color: var(--bc-ink); font-weight: 800; text-transform: uppercase; }
.bc-rec-side { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.bc-rec-side .k { display: block; font: 800 11px/1 var(--bc-cond); letter-spacing: .18em; text-transform: uppercase; color: var(--bc-muted);
  margin-bottom: 6px; }
.bc-need { display: grid; grid-template-columns: 40px 34px minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 5px 0;
  border-top: 1px solid var(--bc-line); font: 600 13px/1.1 var(--bc-cond); color: #C9CFDB; }
.bc-need .c { font: 800 15px/1 var(--bc-display); color: var(--bc-ink); letter-spacing: .04em; }
.bc-need .r { font: 900 16px/1 var(--bc-display); color: var(--tier); text-align: right; }
.bc-need .r sup { font-size: 9px; }
.bc-need .w { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bc-need .w b { color: var(--bc-ink); font-weight: 800; text-transform: uppercase; }
.bc-need .z { color: #8EF0BF; font-variant-numeric: tabular-nums; }
.bc-need .ls { font: 800 10px/1 var(--bc-cond); letter-spacing: .12em; text-transform: uppercase; color: var(--bc-amber);
  background: rgba(255,176,32,.12); padding: 3px 6px 2px; white-space: nowrap; }
.bc-wait { display: grid; grid-template-columns: 26px minmax(0, 1fr) 34px; align-items: center; gap: 8px; padding: 3px 0;
  font: 800 13px/1 var(--bc-cond); letter-spacing: .06em; color: var(--bc-ink); }
.bc-wait .bar { height: 8px; background: var(--bc-panel3); position: relative; }
.bc-wait .bar i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--wc, var(--bc-muted)); }
.bc-wait .v { font: 700 12.5px/1 var(--bc-cond); text-align: right; color: var(--bc-muted); font-variant-numeric: tabular-nums; }
.bc-wait.hot .v { color: var(--bc-gold); }
.bc-rec-side.narrow { display: none; }
.bc-rec-side.narrow summary { cursor: pointer; font: 800 12px/1 var(--bc-cond); letter-spacing: .16em; text-transform: uppercase;
  color: var(--bc-gold); padding: 6px 0; list-style: none; }
.bc-rec-side.narrow summary::before { content: "▸ "; }
.bc-rec-side.narrow[open] summary::before { content: "▾ "; }
.bc-rec-side.narrow[open] { gap: 12px; }
.bc-wait-mini { display: none; font: 700 13px/1.4 var(--bc-cond); letter-spacing: .05em; color: var(--bc-muted); }
.bc-wait-mini b { color: var(--bc-ink); font-variant-numeric: tabular-nums; }
.bc-wait-mini .hot, .bc-wait-mini .hot b { color: var(--bc-gold); }
.bc-rec-empty { padding: 4px 16px 0; font: 500 13px/1.35 var(--bc-body); color: var(--bc-muted); }
@container (max-width: 640px) {
  .bc-rec-grid { grid-template-columns: minmax(0, 1fr); gap: 14px; padding-left: 12px; padding-right: 12px; }
  .bc-wait { display: none; }
  .bc-wait-mini { display: block; }
  .bc-rec-side.wide { display: none; }
  .bc-rec-side.narrow { display: flex; }
  .bc-rec-alt { margin-top: 8px; }
  .bc-rec-why li:nth-child(n+4) { display: none; }
  .bc-rec .bc-card-hd .t { font-size: 19px; }
  .bc-rec-main .nm b { font-size: 26px; }
}
.st-key-recbox { gap: 0 !important; margin-bottom: 14px; }
.st-key-recbox [data-testid="stHorizontalBlock"] { background: #0C1018; padding: 0 16px 14px; gap: 8px !important; flex-wrap: wrap; }
.st-key-recbox [data-testid="stColumn"] { width: auto !important; flex: 0 1 auto !important; min-width: 0 !important; }
.st-key-recbox button { border-radius: 0 !important; clip-path: polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%);
  padding: 4px 22px !important; min-height: 38px; }
.st-key-recbox button p { font: 800 15px/1 var(--bc-cond) !important; letter-spacing: .12em; text-transform: uppercase; }
.st-key-recbox button[kind="primary"] { background: var(--bc-gold) !important; border: 0 !important; }
.st-key-recbox button[kind="primary"] p { color: #1A1200 !important; }
.st-key-recbox button[kind="secondary"] { background: var(--bc-panel3) !important; border: 0 !important; }

/* Tablet with the sidebar open: the list column gets narrow, so the position filter takes its own row */
.st-key-listbar { container-type: inline-size; container-name: listbar; }
.st-key-w_pos [role="radiogroup"] { overflow-x: auto; scrollbar-width: none; }
@container listbar (max-width: 600px) {
  .st-key-listbar [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; row-gap: 10px !important; }
  .st-key-listbar [data-testid="stColumn"] { flex: 1 1 100% !important; width: 100% !important; min-width: 100% !important; }
}
@container listbar (max-width: 330px) {  /* very narrow list column (sidebar open on a ~900px screen): phone-size pills */
  .st-key-w_pos button[data-variant="segmented_control"] { padding: 0 7px; }
  .st-key-w_pos button[data-variant="segmented_control"] p { font-size: 13px; letter-spacing: .03em; }
}

/* ── Phones ── */
@media (max-width: 640px) {
  .block-container { padding-top: .7rem !important; padding-left: .8rem !important; padding-right: .8rem !important; }
  .bc-mast { flex-wrap: wrap; padding: 0 0 7px 40px; }
  .bc-word { font-size: 30px; }
  .bc-live { padding: 6px 12px 5px 8px; font-size: 11px; }
  .bc-tagline { display: none; }
  .bc-tags { flex-wrap: nowrap; overflow: hidden; justify-content: flex-start; }
  .bc-tag { padding: 6px 11px 5px; font-size: 10.5px; }
  .bc-tag.opt { display: none; }
  .st-key-masthead [data-testid="stCaptionContainer"] p { font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .st-key-pick_search [role="group"] { min-height: 54px; }
  .st-key-pick_search input { font-size: 17px !important; padding-left: 14px !important; }
  .st-key-pick_search [data-testid="stWidgetLabel"] p { font-size: 11px !important; }
  .st-key-draftbar [data-testid="stExpander"] summary { min-height: 38px; padding-top: 6px; padding-bottom: 6px; }
  .st-key-draftbar { gap: .4rem; }
  .bc-crawl { height: 36px; margin-top: 8px; }
  .bc-crawl .tag { font-size: 13px; padding: 0 14px 0 10px; }
  .bc-crawl .new { max-width: 72%; padding: 0 18px 0 18px; }
  .bc-crawl .new .lbl, .bc-crawl .new .by { display: none; }
  [data-testid="stTab"] { padding: 8px 12px 6px !important; }
  [data-testid="stTab"] p { font-size: 16px !important; letter-spacing: .05em; }
  /* Position filter and order share one row, so the list starts higher */
  .st-key-listbar [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 8px !important; align-items: flex-end !important; }
  .st-key-listbar [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; }
  .st-key-listbar [data-testid="stColumn"]:first-child { flex: 1 1 auto !important; }
  .st-key-listbar [data-testid="stColumn"]:last-child { flex: 0 0 102px !important; }
  .st-key-listbar [data-testid="stWidgetLabel"] { display: none; }
  .st-key-w_pos [role="radiogroup"] { overflow-x: auto; scrollbar-width: none; }
  .st-key-w_pos button[data-variant="segmented_control"] { padding: 0 8px; min-height: 36px; flex: none; }
  .st-key-w_pos button[data-variant="segmented_control"] p { font-size: 13px; letter-spacing: .04em; }
  .st-key-listbar [data-testid="stSelectbox"] [role="group"] { min-height: 36px; }
  .st-key-recbox [data-testid="stHorizontalBlock"] { padding: 0 12px 12px; }
  .st-key-recbox [data-testid="stColumn"] { flex: 1 1 calc(50% - 4px) !important; width: auto !important; }
  .st-key-recbox [data-testid="stColumn"]:first-child { flex-basis: 100% !important; }
  .st-key-recbox button { width: 100%; padding: 4px 10px !important; }
  .st-key-recbox button p { font-size: 13.5px !important; letter-spacing: .08em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bc-resume h2 { font-size: 46px !important; }
  .bc-board { min-width: 900px; }
}
</style>
"""


def css() -> str:
    return CSS


IMG_SCRIPT = """<script>
// Headshots/logos in st.html blocks: mark the tile .ok once the image has loaded, or drop the image on error
// so the team-colored initials / color swatch shows. Installed once per page; watches for new blocks.
// (No angle-bracket tag names in this script: DOMPurify drops a script whose text contains one.)
(() => {
  if (window.__bcImg) return; window.__bcImg = true;
  const SEL = '.bc-av img, .bc-lg img';
  const wire = (img) => {
    if (img.__bc) return; img.__bc = true;
    const p = img.parentElement; if (!p) return;
    const ok = () => p.classList.add('ok');
    const bad = () => { p.classList.add('x'); img.remove(); };
    if (img.complete) { (img.naturalWidth > 0 ? ok : bad)(); return; }
    img.addEventListener('load', ok, { once: true });
    img.addEventListener('error', bad, { once: true });
  };
  const scan = (n) => { if (n.matches && n.matches(SEL)) wire(n); if (n.querySelectorAll) n.querySelectorAll(SEL).forEach(wire); };
  scan(document);
  new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => { if (n.nodeType === 1) scan(n); })))
    .observe(document.body, { childList: true, subtree: true });
})();
</script>"""


def img_script() -> str:
    return IMG_SCRIPT


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


# ── Colors ────────────────────────────────────────────────────────────────────

def _rgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _lum(rgb) -> float:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def readable(hex_: str, min_lum: float = 0.16) -> str:
    """Lighten a (team) color in HSL until it reads on the near-black background; keeps its hue."""
    try:
        r, g, b = _rgb(hex_)
    except (ValueError, IndexError):
        return NEUTRAL
    h, light, s = colorsys.rgb_to_hls(r, g, b)
    while _lum(colorsys.hls_to_rgb(h, light, s)) < min_lum and light < 0.9:
        light += 0.02
        s = min(1.0, s * 1.02) if s > 0.05 else s
    return _hex(colorsys.hls_to_rgb(h, light, s))


@lru_cache(maxsize=1)
def nba_teams() -> dict[str, dict]:
    try:
        teams = json.loads(TEAMS_PATH.read_text())["teams"]
    except (OSError, ValueError, KeyError):
        return {}
    for t in teams.values():
        base = t.get("color") or "#3A4150"
        t["tc"] = readable(base)                  # stripe / text color on dark
        t["tc0"] = readable(base, 0.015)          # deep end of the gradient
    return teams


def team_colors(abbr: str) -> tuple[str, str]:
    t = nba_teams().get(str(abbr or "").upper())
    return (t["tc"], t["tc0"]) if t else ("#596274", "#1B2130")


def team_logo(abbr: str) -> str:
    t = nba_teams().get(str(abbr or "").upper())
    return t.get("logo", "") if t else ""


def espn_id_of(player_id: str | None, espn_id=None) -> str:
    """ESPN numeric id from the dataset's espn_id column or an "espn:<id>" player id; '' if none."""
    for cand in (espn_id, str(player_id or "").split("espn:", 1)[1] if str(player_id or "").startswith("espn:") else None):
        if cand is None:
            continue
        try:
            if isinstance(cand, float) and math.isnan(cand):
                continue
            s = str(int(float(cand)))
        except (TypeError, ValueError):
            continue
        if s.isdigit() and s != "0":
            return s
    return ""


def headshot_url(player_id: str | None, espn_id=None) -> str:
    eid = espn_id_of(player_id, espn_id)
    return HEADSHOT_URL.format(eid) if eid else ""


def initials(name: str) -> str:
    parts = [p for p in str(name or "").replace(".", " ").split() if p[:1].isalpha()]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    last = parts[-1] if parts[-1].upper().rstrip(".") not in {"JR", "SR", "II", "III", "IV"} or len(parts) < 3 else parts[-2]
    return (parts[0][0] + last[0]).upper()


def fantasy_color(slot: int, me: int | None = None) -> str:
    if me is not None and slot == me:
        return GOLD
    return FANTASY_PALETTE[(int(slot) - 1) % len(FANTASY_PALETTE)]


def monogram(name: str, slot: int | None = None) -> str:
    """'Team 7' -> 'T7'; 'Necro Squad' -> 'NS'; 'Ballers' -> 'BAL'."""
    words = str(name or "").split()
    if len(words) == 2 and words[0].lower() == "team" and words[1].isdigit():
        return f"T{words[1]}"
    letters = [w[0] for w in words if w[:1].isalnum()]
    if len(letters) >= 2:
        return "".join(letters[:3]).upper()
    if words:
        return words[0][:3].upper()
    return f"T{slot}" if slot else "?"


def tier_color(rank: int | None, n: int) -> str:
    if not rank or not n:
        return NEUTRAL
    frac = (rank - 1) / max(n - 1, 1)
    return GOOD if frac <= 0.25 else (AMBER if frac <= 0.6 else BAD)


def ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def nice_time(iso: str) -> str:
    """'2026-10-16T23:05:30+00:00' -> 'Oct 16, 23:05 UTC'. Falls back to the raw text."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    tz = ""
    if dt.tzinfo is not None:
        off = dt.utcoffset()
        tz = " UTC" if off is not None and off.total_seconds() == 0 else dt.strftime(" %z")
    return f"{dt:%b} {dt.day}, {dt:%H:%M}{tz}"


# ── Small pieces ──────────────────────────────────────────────────────────────

def avatar_html(name: str, nba_team: str, player_id: str | None = None, espn_id=None, size: int = 34) -> str:
    tc, tc0 = team_colors(nba_team)
    ini = esc(initials(name))
    url = headshot_url(player_id, espn_id)
    img = f'<img src="{esc(url)}" alt="" data-i="{ini}" loading="lazy">' if url else ""
    return f'<span class="bc-av" style="--s:{size}px;--tc:{tc};--tc0:{tc0}">{ini}{img}</span>'


def logo_html(nba_team: str, size: int = 16) -> str:
    tc, _ = team_colors(nba_team)
    url = team_logo(nba_team)
    img = f'<img src="{esc(url)}" alt="" loading="lazy">' if url else ""
    return f'<span class="bc-lg" style="--s:{size}px;--tc:{tc}">{img}</span>'


def fantasy_chip(name: str, slot: int, me: int) -> str:
    return f'<span class="bc-ft" style="--c:{fantasy_color(slot, me)}">{esc(monogram(name, slot))}</span>'


def short_label(name: str) -> str:
    """'K. George': first initial + last name, so two players who share a last name stay distinct."""
    first, last = split_name(name)
    return f"{first[0]}. {last}" if first else last


def split_name(name: str) -> tuple[str, str]:
    """('Trey', 'Murphy III'): first name, rest."""
    parts = str(name or "").split()
    if len(parts) < 2:
        return "", str(name or "")
    return parts[0], " ".join(parts[1:])


# ── HTML components ───────────────────────────────────────────────────────────

def header_html(tags: list[tuple[str, str]]) -> str:
    """Masthead. tags are (text, kind) with kind in {"", "warn", "ok", "live"} (+ " opt" = hide on phones)."""
    chips = "".join(f'<span class="bc-tag {k}"><i></i>{esc(t)}</span>' for t, k in tags)
    return (f'<div class="bc-mast"><div class="bc-brand"><span class="bc-live"><i></i>LIVE</span><div>'
            f'<div class="bc-word">Draft <b>Room</b></div>'
            f'<div class="bc-tagline">Fantasy basketball draft center · 2026-27</div></div></div>'
            f'<div class="bc-tags">{chips}</div></div>')


def clock_style_html(color: str, mono: str) -> str:
    """Per-run CSS variables for the score bug (team color + watermark monogram)."""
    return f'<style>:root{{--bc-clock:{color};--bc-clock-mono:{json.dumps(mono)}}}</style>'


def round_strip_html(rnd: int, segs: list[dict], pick_in_round: int, n: int) -> str:
    """segs: {mono, color, done, cur, mine, title} in pick order for the current round."""
    cells = "".join(
        f'<i class="{" ".join(c for c, on in (("done", s["done"]), ("cur", s["cur"]), ("mine", s["mine"])) if on)}" '
        f'style="--c:{s["color"]}" title="{esc(s["title"])}">{esc(s["mono"])}</i>' for s in segs)
    return (f'<div class="bc-round"><span class="rd">Round <b>{rnd}</b></span><div class="segs">{cells}</div>'
            f'<span class="rd"><span class="of">Pick </span><b>{pick_in_round}</b>/{n}</span></div>')


def ticker_html(picks: list[dict]) -> str:
    """picks: newest first, each {no, team, slot, me_slot, name, nba_team, pos, mine}."""
    if not picks:
        return ('<div class="bc-crawl-cq"><div class="bc-crawl"><div class="tag">Picks</div>'
                '<div class="track"><span class="empty">No picks yet · type a name in the box above and press Enter</span></div></div></div>')

    def item(p):
        first, last = split_name(p["name"])
        return (f'<span class="it{" mine" if p.get("mine") else ""}"><span class="no">#{p["no"]}</span>'
                f'{fantasy_chip(p["team"], p["slot"], p["me_slot"])}{logo_html(p.get("nba_team", ""), 15)}'
                f'<span>{esc(first)} <b>{esc(last)}</b></span><span class="nba">{esc(p.get("nba_team", ""))}</span></span>')

    newest, rest = picks[0], picks[1:]
    first, last = split_name(newest["name"])
    new = (f'<div class="new"><span class="lbl">JUST IN</span><span class="no">#{newest["no"]}</span>'
           f'{fantasy_chip(newest["team"], newest["slot"], newest["me_slot"])}'
           f'<span class="pl">{esc(first)} <b>{esc(last)}</b></span>'
           f'<span class="by">{esc(newest.get("nba_team", ""))}</span></div>')
    items = "".join(item(p) for p in rest)
    moving = len(rest) >= 4
    run = (f'<div class="run go" style="--dur:{max(24, 6 * len(rest))}s">{items}{items}</div>' if moving
           else f'<div class="run">{items}</div>')
    return f'<div class="bc-crawl-cq"><div class="bc-crawl"><div class="tag">Picks</div>{new}<div class="track">{run}</div></div></div>'


def lower_third_html(p: dict, team: str, me: int, n_teams: int) -> str:
    """'THE PICK IS IN' lower third for the pick just made: slides in, holds, slides out (CSS only).

    p: {no, slot, name, nba_team, pos, player_id, espn_id, rank, keeper}. The value call compares the pick
    number with the app's own rank for the player; nothing else is computed here.
    """
    first, last = split_name(p["name"])
    tc, tc0 = team_colors(p.get("nba_team", ""))
    mine = p["slot"] == me
    rnd = (p["no"] - 1) // n_teams + 1
    in_rnd = p["no"] - (rnd - 1) * n_teams
    call = ""
    try:
        rank = int(p.get("rank"))
    except (TypeError, ValueError):
        rank = None
    if rank and not p.get("keeper"):
        diff = p["no"] - rank
        if diff >= max(3, round(0.15 * p["no"])):
            call = f'<span class="call good">Value +{diff}</span>'
        elif diff <= -max(6, round(0.3 * p["no"])):
            call = f'<span class="call bad">Reach \u2212{-diff}</span>'
    pos = "/".join(p.get("pos") or []) or "?"
    head = "Your pick is in" if mine else ("Keeper" if p.get("keeper") else "The pick is in")
    return (f'<div class="bc-l3{" mine" if mine else ""}" style="--tc:{tc};--tc0:{tc0};--fc:{fantasy_color(p["slot"], me)}" '
            f'role="status" aria-live="polite"><div class="k"><i></i>{esc(head)}</div>'
            f'<div class="bar"><span class="no"><small>Pick</small>{p["no"]}</span>'
            f'{avatar_html(p["name"], p.get("nba_team", ""), p.get("player_id"), p.get("espn_id"), 62)}'
            f'<div class="who"><span class="fn">{esc(first)}</span><span class="ln">{esc(last)}</span>'
            f'<span class="meta">{logo_html(p.get("nba_team", ""), 16)}<b>{esc(p.get("nba_team", ""))}</b> · {esc(pos)}'
            f'{f" · my rank #{rank}" if rank else ""}</span></div></div>'
            f'<div class="strip"><span>Selected by</span>{fantasy_chip(team, p["slot"], me)}<b>{esc(team)}</b>'
            f'<span class="rd">Round {rnd} · pick {in_rnd}</span>{call}</div></div>')


def roster_html(rows: list[tuple[str, dict | None]], title: str, subtitle: str = "", n_have: int = 0, n_total: int = 0) -> str:
    """rows: (slot, player or None) in display order. Player dicts have name, team, positions, player_id, espn_id."""
    out = []
    for slot, p in rows:
        if p:
            tc, _ = team_colors(p.get("team", ""))
            first, last = split_name(p["name"])
            pos = "/".join(p.get("positions") or []) or "?"
            out.append(f'<div class="r f" style="--tc:{tc}"><span class="slot">{esc(slot)}</span>'
                       f'{avatar_html(p["name"], p.get("team", ""), p.get("player_id"), p.get("espn_id"), 30)}'
                       f'<span class="who"><span class="nm"><span class="fn">{esc(first)} </span><b>{esc(last)}</b></span>'
                       f'<span class="mt">{esc(p.get("team", ""))} · {esc(pos)}</span></span></div>')
        else:
            out.append(f'<div class="r e"><span class="slot">{esc(slot)}</span><span class="ph"></span>'
                       f'<span class="nm">Open</span></div>')
    pct = 100 * n_have / n_total if n_total else 0
    return (f'<div class="bc-card bc-ros" style="--c:{GOLD}"><div class="bc-card-hd"><div class="stack"><span class="k">My team</span>'
            f'<span class="t">{esc(title)}</span></div><span class="n">{n_have}<small>/{n_total}</small></span></div>'
            f'<div class="bc-prog"><i style="width:{pct:.0f}%"></i></div>{"".join(out)}</div>')


def standings_html(rows: list[dict], n: int, fit: dict[str, float] | None = None) -> str:
    """rows: {cat, value_text, z, rank, punt}. One line per category: value, ΣZ, standing bar, rank (+ team-fit weight)."""
    fitw = "30px" if fit else "0px"
    head = (f'<div class="r h"><span>Cat</span><span style="text-align:right">Mine</span><span style="text-align:right">ΣZ</span>'
            f'<span>Standing</span><span style="text-align:right">Rank</span>{"<span style=text-align:right>Fit</span>" if fit else "<span></span>"}</div>')
    body = []
    for r in rows:
        tier = tier_color(r["rank"], n)
        pct = 0 if not r["rank"] else 100 * (n - r["rank"] + 1) / n
        if r["punt"]:
            rank = '<span class="rk">PUNT</span>'
        elif r["rank"]:
            rank = f'<span class="rk">{r["rank"]}<sup>{ordinal(r["rank"])}</sup></span>'
        else:
            rank = '<span class="rk">—</span>'
        zc = "pos" if r["z"] > 0.05 else "neg" if r["z"] < -0.05 else ""
        fit_cell = "<span></span>"
        if fit:
            w = fit.get(r["cat"])
            if w is not None:
                fit_cell = f'<span class="fit {"up" if w > 1 else "off" if w == 0 else ""}">×{w:g}</span>'
        body.append(f'<div class="r{" punt" if r["punt"] else ""}" style="--tier:{tier}"><span class="c">{esc(r["cat"])}</span>'
                    f'<span class="v">{esc(r["value_text"])}</span><span class="z {zc}">{r["z"]:+.2f}</span>'
                    f'<span class="bar" style="--n:{n}"><i style="width:{pct:.0f}%"></i></span>{rank}{fit_cell}</div>')
    foot = ('<div class="foot">Fit = how much each category counts in the Team-fit order '
            '(×0 punted or locked up, ×1.5 still up for grabs).</div>') if fit else ""
    return (f'<div class="bc-card bc-cat" style="--fitw:{fitw}"><div class="bc-card-hd"><div class="stack"><span class="k">Category race</span>'
            f'<span class="t">Standings</span></div><span class="sub">vs {n} teams<br>1st = best</span></div>'
            f'{head}{"".join(body)}{foot}</div>')


def points_card_html(fp: float, rank: int, n: int, title: str) -> str:
    tier = tier_color(rank, n)
    return (f'<div class="bc-card bc-cat"><div class="bc-card-hd"><div class="stack"><span class="k">Projected fantasy points</span>'
            f'<span class="t">{esc(title)}</span></div></div><div class="bc-fp" style="--tier:{tier}"><span class="big">{fp:.1f}</span>'
            f'<span class="rk">{rank}<sup>{ordinal(rank)}</sup> of {n}</span></div></div>')


def board_html(n_teams: int, n_rounds: int, cells: dict[tuple[int, int], list[dict]], me: int,
               cur_cell: tuple[int, int] | None, team_name, latest: int | None = None) -> str:
    """cells: (round, team) -> picks {pick_no, name, positions, nba_team, keeper, unmatched}. latest = newest pick_no."""
    head = "".join(
        f'<th class="{"me" if t == me else ""}" title="{esc(team_name(t))}"><div class="tm" style="--c:{fantasy_color(t, me)}">'
        f'<span class="nm">{esc(team_name(t))}{" ★" if t == me else ""}</span></div></th>'
        for t in range(1, n_teams + 1))
    rows = []
    for r in range(1, n_rounds + 1):
        tds = []
        for t in range(1, n_teams + 1):
            cls = (["me"] if t == me else []) + (["cur"] if cur_cell == (r, t) else [])
            picks = cells.get((r, t), [])
            if picks:
                inner = []
                for p in picks:
                    tc, _ = team_colors(p["nba_team"])
                    _, last = short_name(p["name"])
                    kcls = " k" if p["keeper"] else ""
                    ucls = (" um" if p["unmatched"] else "") + (" new" if p["pick_no"] == latest else "")
                    inner.append(
                        f'<div class="bc-pk{kcls}{ucls}" style="--tc:{tc}" title="{esc(p["name"])} · {esc(p["nba_team"])} · pick #{p["pick_no"]}">'
                        f'<div class="ln" style="font-size:{name_px(last)}px">{esc(last)}</div>'
                        f'<div class="mt"><span class="no">#{p["pick_no"]}</span> {esc(p["nba_team"] or "")}'
                        f' · {esc("/".join(p["positions"]) or "?")}</div></div>')
                tds.append(f'<td class="{" ".join(cls)}">{"".join(inner)}</td>')
            else:
                slot = (r - 1) * n_teams + (t if r % 2 else n_teams + 1 - t)
                extra = "<b>On the clock</b>" if cur_cell == (r, t) else ""
                tds.append(f'<td class="{" ".join(cls)}"><span class="em">#{slot}{extra}</span></td>')
        rows.append(f'<tr><th><div class="rdl"><small>RD</small>{r}<i>{"→" if r % 2 else "←"}</i></div></th>{"".join(tds)}</tr>')
    legend = ('<div class="bc-legend">'
              '<span><span class="sw" style="background:linear-gradient(90deg,#3D8BFF 0 3px,rgba(61,139,255,.3) 3px)"></span>Stripe = NBA team color</span>'
              f'<span><span class="sw" style="background:rgba(255,200,61,.3);box-shadow:inset 0 3px 0 {GOLD}"></span>Your column</span>'
              f'<span><span class="sw" style="box-shadow:inset 0 0 0 2px {RED};background:rgba(255,45,69,.15)"></span>On the clock</span>'
              '<span>→ ← snake direction</span><span><b style="color:#FFC83D">K</b> keeper</span><span>⚠ not in dataset</span></div>')
    return (f'{legend}<div class="bc-board-wrap"><table class="bc-board"><thead><tr><th class="rdh"></th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def short_name(name: str) -> tuple[str, str]:
    """('V.', 'Wembanyama') for board cells; suffixes stay with the last name ('Jackson Jr.')."""
    parts = str(name).split()
    if len(parts) < 2:
        return "", str(name)
    return f"{parts[0][0]}.", " ".join(parts[1:])


def name_px(last: str) -> float:
    """Board cells are ~62px of text wide (12 teams); shrink the condensed caps so the longest word fits.

    Names wrap at spaces ("Butler III") and hyphens ("Gilgeous-Alexander"), so size by the longest piece.
    """
    longest = max((len(w) for w in last.replace("-", "- ").split()), default=0)
    return round(min(16.0, max(9.5, 114 / max(longest, 1))), 1)


def resume_html(n_picks: int, n_teams: int, when: str) -> str:
    return (f'<div class="bc-resume"><span class="k">● Draft saved</span><h2>Welcome back</h2>'
            f'<p>Your draft was saved automatically after every pick.</p>'
            f'<div class="stats"><div><b>{n_picks}</b><span>Picks in</span></div><div><b>{n_teams}</b><span>Teams</span></div>'
            f'<div><b>{esc(nice_time(when))}</b><span>Last saved</span></div></div></div>')


def recommend_html(rec, n_teams: int, team_label: str) -> str:
    """Recommended-pick card: top pick with reasons, alternates, weakest categories, value lost by waiting."""
    plan = rec.plan
    if plan.now_pick is None:
        return ""
    when = "on the clock now" if plan.on_clock else f"in {plan.before_now} pick{'s' if plan.before_now != 1 else ''}"
    head = (f'<div class="bc-card-hd"><div class="stack"><span class="k">Recommended pick · {esc(team_label)}</span>'
            f'<span class="t">For your pick #{plan.now_pick} · {esc(when)}</span></div></div>')
    if not rec.top:
        return f'<div class="bc-card bc-rec" style="--c:{GOLD}">{head}<div class="bc-rec-empty">No projected players left to recommend.</div></div>'
    t = rec.top[0]
    first, last = split_name(t["name"])
    tc, _ = team_colors(t.get("team", ""))
    why = "".join(f"<li>{esc(r)}</li>" for r in t["reasons"][:4])
    alts = "".join(f'<span><b>{esc(short_label(a["name"]))}</b> {esc("/".join(a["positions"]))}</span>'
                   for a in rec.top[1:3])
    main = (f'<div><div class="bc-rec-main" style="--tc:{tc}">'
            f'{avatar_html(t["name"], t.get("team", ""), t["player_id"], None, 52)}'
            f'<span class="nm">{esc(first)}<b{" class=long" if len(last) > 12 else ""}>{esc(last)}</b></span>'
            f'<span class="mt">{esc(t.get("team", ""))} · {esc("/".join(t["positions"]) or "?")}</span></div>'
            f'<ul class="bc-rec-why">{why}</ul>'
            + (f'<div class="bc-rec-alt"><span class="k">Also</span>{alts}</div>' if alts else "") + '</div>')
    side = []
    if rec.gaps:
        rows = []
        for g in rec.gaps:
            rows.append(f'<div class="bc-need" style="--tier:{tier_color(g["rank"], g["of"])}"><span class="c">{esc(g["cat"])}</span>'
                        f'<span class="r">{g["rank"]}<sup>{ordinal(g["rank"])}</sup></span>'
                        f'<span class="w">Best: <b>{esc(short_label(g["name"]))}</b> <span class="z">{g["z"]:+.1f}</span></span>'
                        + ('<span class="ls">Long shot</span>' if g.get("long_shot") else "<span></span>") + '</div>')
        side.append(f'<div><span class="k">Your weakest categories</span>{"".join(rows)}</div>')
    if rec.wait_cost and plan.following:
        top = max(rec.wait_cost.values()) or 1.0
        hot = [pos for pos, v in rec.wait_cost.items() if v >= top - 0.05]  # ties (PG/SG share players) all light up
        bars = "".join(
            f'<div class="bc-wait{" hot" if pos in hot and v >= 0.5 else ""}" style="--wc:{GOLD if pos in hot and v >= 0.5 else NEUTRAL}">'
            f'<span>{pos}</span><span class="bar"><i style="width:{max(0.0, v) / top * 100:.0f}%"></i></span>'
            f'<span class="v">{v:+.1f}</span></div>' for pos, v in rec.wait_cost.items())
        chips = " · ".join(f'<span{" class=hot" if pos in hot and v >= 0.5 else ""}>{pos} <b>{v:+.1f}</b></span>'
                           for pos, v in rec.wait_cost.items())
        side.append(f'<div><span class="k">Value lost by waiting until #{plan.following}</span>{bars}'
                    f'<div class="bc-wait-mini">{chips}</div></div>')
    # Phones get the same panels folded away (summary line), so the list starts sooner.
    side_html = (f'<div class="bc-rec-side wide">{"".join(side)}</div>'
                 f'<details class="bc-rec-side narrow"><summary>Team needs &amp; position timing</summary>{"".join(side)}</details>'
                 if side else "<div></div>")
    return f'<div class="bc-card bc-rec" style="--c:{GOLD}">{head}<div class="bc-rec-grid">{main}{side_html}</div></div>'
