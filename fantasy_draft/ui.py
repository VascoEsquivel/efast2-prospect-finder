"""Look and feel: theme CSS, cell colors and small HTML components for the dark "sporty" UI.

Presentation only. Nothing here reads or changes app state; app.py passes in what to draw.
"""

from __future__ import annotations

import html
import math

ACCENT = "#FF7A1A"   # basketball orange, the one bold accent
GOOD = "#22C55E"
BAD = "#F43F5E"
AMBER = "#FFB020"
POS_COLORS = {"PG": "#38BDF8", "SG": "#38BDF8", "SF": "#34D399", "PF": "#34D399", "C": ACCENT}

CSS = """
<style>
:root {
  --fda-accent: #FF7A1A; --fda-accent-soft: rgba(255,122,26,.12); --fda-amber: #FFB020;
  --fda-good: #22C55E; --fda-bad: #F43F5E; --fda-ink: #E7ECF5; --fda-muted: #8C9AB5;
  --fda-surface: #121B2D; --fda-surface-2: #182339; --fda-line: #22304B;
  --fda-display: 'Barlow Condensed', 'Arial Narrow', Inter, sans-serif;
}
.block-container { padding-top: 1.4rem !important; padding-bottom: 2.5rem !important; max-width: 100% !important; }
[data-testid="stHeader"] { background: transparent; }

/* Brand row */
.fda-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.fda-brand { display: flex; align-items: center; gap: 12px; }
.fda-ball { width: 38px; height: 38px; border-radius: 50%; flex: none;
  background: radial-gradient(circle at 32% 30%, #FFC48A 0%, #FF7A1A 45%, #B9470B 100%);
  box-shadow: 0 0 22px rgba(255,122,26,.45); position: relative; overflow: hidden; }
.fda-ball::before, .fda-ball::after { content: ""; position: absolute; border: 2px solid rgba(20,10,0,.55); border-radius: 50%; }
.fda-ball::before { inset: -2px 15px; }
.fda-ball::after { inset: 15px -2px; }
.fda-title { font-family: var(--fda-display); font-weight: 800; font-size: 31px; line-height: 1;
  letter-spacing: .05em; text-transform: uppercase; color: var(--fda-ink); }
.fda-title span { color: var(--fda-accent); }
.fda-tagline { color: var(--fda-muted); font-size: 12px; letter-spacing: .03em; margin-top: 3px; }
.fda-badges { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.fda-badge { font-size: 11.5px; padding: 4px 11px; border-radius: 999px; border: 1px solid var(--fda-line);
  background: var(--fda-surface); color: var(--fda-ink); white-space: nowrap; }
.fda-badge.warn { border-color: rgba(255,176,32,.55); color: #FFD27F; background: rgba(255,176,32,.08); }
.fda-badge.ok { border-color: rgba(34,197,94,.45); color: #86EFAC; background: rgba(34,197,94,.07); }
.fda-badge.live { border-color: rgba(56,189,248,.5); color: #7DD3FC; background: rgba(56,189,248,.07); }

/* Scoreboard strip */
.st-key-scoreboard { background: linear-gradient(180deg, #131D31 0%, #0E1627 100%);
  border: 1px solid var(--fda-line); border-radius: 18px; padding: 16px 18px 12px !important;
  box-shadow: 0 10px 34px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.04); }
.st-key-scoreboard [data-testid="stMetric"] { background: rgba(255,255,255,.025); border: 1px solid var(--fda-line);
  border-radius: 14px; padding: 9px 16px 8px; min-height: 96px; }
.st-key-scoreboard [data-testid="stMetricLabel"] .stMarkdownEllipsis, .st-key-scoreboard [data-testid="stMetricValue"] .stMarkdownEllipsis {
  overflow: hidden; text-overflow: ellipsis; }
[data-testid="stMetricLabel"] p { text-transform: uppercase; letter-spacing: .14em; font-size: 11px !important;
  color: var(--fda-muted) !important; font-weight: 700; }
.st-key-scoreboard [data-testid="stMetricValue"] p { font-family: var(--fda-display); font-weight: 700;
  font-size: 42px !important; line-height: 1.02; color: var(--fda-ink); }
.st-key-scoreboard .stButton button { min-height: 96px; border-radius: 14px; font-family: var(--fda-display);
  font-size: 18px; letter-spacing: .06em; text-transform: uppercase; font-weight: 700; }
[data-testid="stMetricDelta"] p { color: var(--fda-muted) !important; font-size: 12px !important; font-weight: 600; }
.st-key-clock_you [data-testid="stMetric"] { border-color: var(--fda-accent); background: var(--fda-accent-soft);
  animation: fda-pulse 1.6s ease-in-out infinite; }
.st-key-clock_you [data-testid="stMetricValue"] p { color: var(--fda-accent) !important; }
.st-key-next_soon [data-testid="stMetric"] { border-color: rgba(56,189,248,.45); }
.st-key-next_soon [data-testid="stMetricValue"] p { color: #7DD3FC !important; }
@keyframes fda-pulse {
  0%, 100% { box-shadow: 0 0 0 1px rgba(255,122,26,.35), 0 0 12px rgba(255,122,26,.20); }
  50% { box-shadow: 0 0 0 1px rgba(255,122,26,.80), 0 0 32px rgba(255,122,26,.55); }
}

/* The pick box: the most prominent control on the page */
.st-key-pick_search label p { font-size: 11px !important; letter-spacing: .14em; text-transform: uppercase;
  color: var(--fda-accent) !important; font-weight: 800; }
.st-key-pick_search [role="group"] { min-height: 60px; border: 2px solid rgba(255,122,26,.6) !important;
  border-radius: 14px !important; background: #0A1120 !important; box-shadow: 0 0 0 4px rgba(255,122,26,.07); }
.st-key-pick_search [role="group"]:focus-within { border-color: var(--fda-accent) !important;
  box-shadow: 0 0 0 4px rgba(255,122,26,.20), 0 0 30px rgba(255,122,26,.28); }
.st-key-pick_search input { font-size: 19px !important; font-weight: 500; }
.st-key-pick_search input::placeholder { color: #93A1BC; }
.st-key-scoreboard [data-testid="stExpander"] details { border-color: var(--fda-line); background: transparent; }
.st-key-scoreboard [data-testid="stExpander"] summary p { font-size: 12.5px; color: var(--fda-muted); }

/* Last-picks ticker */
.fda-ticker { display: flex; gap: 8px; align-items: center; overflow: hidden; margin: 10px 0 4px; }
.fda-ticker .lbl { font-size: 10.5px; letter-spacing: .14em; text-transform: uppercase; color: var(--fda-muted);
  font-weight: 800; white-space: nowrap; margin-right: 2px; }
.fda-chip { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 999px;
  background: var(--fda-surface); border: 1px solid var(--fda-line); font-size: 12.5px; white-space: nowrap; color: var(--fda-ink); }
.fda-chip .no { font-family: var(--fda-display); font-weight: 700; font-size: 14px; color: var(--fda-muted); }
.fda-chip .tm { color: var(--fda-muted); }
.fda-chip.mine { border-color: rgba(255,122,26,.65); background: var(--fda-accent-soft); }
.fda-chip.newest { box-shadow: 0 0 0 1px rgba(255,255,255,.08), 0 0 14px rgba(255,122,26,.18); }
.fda-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }

/* Tabs */
[data-testid="stTab"] p { font-family: var(--fda-display); font-size: 19px !important; letter-spacing: .07em;
  text-transform: uppercase; font-weight: 700; }

/* Cards */
.fda-card { background: var(--fda-surface); border: 1px solid var(--fda-line); border-radius: 16px;
  padding: 14px 16px 12px; margin-bottom: 14px; }
.fda-card-title { font-family: var(--fda-display); text-transform: uppercase; letter-spacing: .1em; font-size: 17px;
  font-weight: 700; color: var(--fda-muted); margin: 0 0 10px; display: flex; justify-content: space-between; align-items: baseline; }
.fda-card-title b { color: var(--fda-ink); font-weight: 700; }
.fda-card-title small { font-family: Inter, sans-serif; font-size: 11px; letter-spacing: .02em; text-transform: none; color: var(--fda-muted); }

/* My roster */
.fda-roster { width: 100%; border-collapse: separate; border-spacing: 0 5px; font-size: 13.5px; }
.fda-roster td { padding: 6px 8px; background: rgba(255,255,255,.02); }
.fda-roster td:first-child { border-radius: 9px 0 0 9px; width: 58px; }
.fda-roster td:last-child { border-radius: 0 9px 9px 0; }
.fda-slot { display: inline-block; min-width: 46px; text-align: center; font-family: var(--fda-display); font-weight: 700;
  font-size: 14px; letter-spacing: .05em; padding: 2px 8px; border-radius: 7px; background: var(--fda-surface-2); color: var(--fda-muted); }
.fda-slot.filled { color: #0A0F1C; background: var(--slot-color, #8C9AB5); }
.fda-roster .nm { color: var(--fda-ink); font-weight: 600; }
.fda-roster .empty { color: #4F5B75; }
.fda-roster .pos { color: var(--fda-muted); font-size: 12px; text-align: right; white-space: nowrap; }

/* Category standings */
.fda-stand { width: 100%; border-collapse: collapse; font-size: 13px; }
.fda-stand th { text-align: left; font-size: 10.5px; letter-spacing: .13em; text-transform: uppercase; color: var(--fda-muted);
  font-weight: 800; padding: 4px 6px 6px; border-bottom: 1px solid var(--fda-line); }
.fda-stand td { padding: 6px; border-bottom: 1px solid rgba(34,48,75,.55); color: var(--fda-ink); }
.fda-stand td.num { text-align: right; font-variant-numeric: tabular-nums; color: #C5CEDF; }
.fda-stand tr.punt td { opacity: .45; }
.fda-rank { display: inline-block; min-width: 54px; text-align: center; border-radius: 999px; padding: 2px 9px;
  font-weight: 800; font-size: 12px; font-variant-numeric: tabular-nums; }
.fda-meter { height: 6px; border-radius: 3px; background: var(--fda-surface-2); position: relative; min-width: 70px; }
.fda-meter i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; }
.fda-fit { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.fda-fit span { font-size: 11.5px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--fda-line); color: var(--fda-muted); }
.fda-fit span.up { border-color: rgba(255,122,26,.6); color: #FFB37A; }
.fda-fit span.off { opacity: .5; text-decoration: line-through; }

/* Draft board */
.fda-legend { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; font-size: 12px; color: var(--fda-muted); margin: 2px 0 10px; }
.fda-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
.fda-board-wrap { overflow-x: auto; }
.fda-board { width: 100%; min-width: 960px; border-collapse: separate; border-spacing: 3px; table-layout: fixed; font-size: 12px; }
.fda-board thead th { font-family: var(--fda-display); font-size: 14px; letter-spacing: .05em; text-transform: uppercase;
  color: var(--fda-muted); background: var(--fda-surface); border: 1px solid var(--fda-line); border-radius: 9px;
  padding: 7px 4px; text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fda-board thead th.me { color: #0A0F1C; background: var(--fda-accent); border-color: var(--fda-accent); }
.fda-board thead th.rd { width: 28px; background: transparent; border-color: transparent; }
.fda-board tbody th { font-family: var(--fda-display); font-size: 15px; color: var(--fda-muted); text-align: center; font-weight: 700; }
.fda-board tbody th span { display: block; font-size: 12px; color: #4F5B75; }
.fda-board td { background: rgba(255,255,255,.022); border: 1px solid rgba(34,48,75,.65); border-radius: 9px;
  height: 52px; vertical-align: top; padding: 5px 4px 4px; }
.fda-board td.me { background: rgba(255,122,26,.07); border-color: rgba(255,122,26,.32); }
.fda-board td.cur { border: 2px dashed var(--fda-accent); background: rgba(255,122,26,.13); }
.fda-board td.cur .slot { color: var(--fda-accent); font-weight: 800; }
.fda-board .slot { font-family: var(--fda-display); font-size: 14px; color: #3E4A63; }
.fda-pk { border-left: 3px solid var(--c, #8C9AB5); padding-left: 4px; margin-bottom: 3px; }
.fda-pk .nm { font-family: var(--fda-display); font-size: 15px; font-weight: 700; letter-spacing: .01em; color: var(--fda-ink);
  line-height: 1.08; word-break: normal; overflow-wrap: normal; display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden; text-overflow: ellipsis; }
.fda-pk .nm.mid { font-size: 13.5px; }
.fda-pk .nm.long { font-size: 11.5px; letter-spacing: -.01em; }
.fda-pk .meta { color: var(--fda-muted); font-size: 11px; margin-top: 1px; }

/* Buttons, tables, sidebar */
.stButton button[kind="primary"] { background: linear-gradient(90deg, #FF7A1A, #FF9A3C); color: #0A0F1C;
  border: none; font-weight: 800; letter-spacing: .02em; }
.stButton button[kind="primary"]:disabled { background: var(--fda-surface-2); color: var(--fda-muted); }
[data-testid="stDataFrame"] { border-radius: 12px; }
[data-testid="stSidebar"] h2 { font-family: var(--fda-display); text-transform: uppercase; letter-spacing: .1em;
  font-size: 18px !important; color: var(--fda-accent); padding-top: .6rem; }
[data-testid="stSidebar"] hr { margin: .6rem 0; }

/* Resume screen */
.fda-resume { background: linear-gradient(160deg, #15203A 0%, #0E1627 100%); border: 1px solid var(--fda-line);
  border-radius: 20px; padding: 26px 28px; margin: 18px 0 14px; max-width: 760px; }
.fda-resume h2 { font-family: var(--fda-display); font-size: 34px; letter-spacing: .04em; text-transform: uppercase; margin: 0 0 6px; }
.fda-resume p { color: var(--fda-muted); margin: 0; font-size: 14px; }
.fda-resume .big { color: var(--fda-ink); font-size: 16px; margin-top: 10px; }
.fda-resume .big b { color: var(--fda-accent); }

/* Selected-player stat line */
.fda-line { background: var(--fda-surface); border: 1px solid var(--fda-line); border-left: 3px solid var(--fda-accent);
  border-radius: 12px; padding: 10px 14px; margin: 4px 0 8px; font-size: 13px; }
.fda-line .who { font-family: var(--fda-display); font-size: 19px; font-weight: 700; letter-spacing: .03em; }
.fda-line .who small { color: var(--fda-muted); font-family: inherit; font-size: 13px; font-weight: 600; margin-left: 6px; }
.fda-line .stats { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 4px 0 2px; }
.fda-line .stats b { color: var(--fda-ink); font-size: 15px; }
.fda-line .stats span { color: var(--fda-muted); }
.fda-line .why { color: var(--fda-muted); margin-top: 4px; }
.fda-line .why b { color: var(--fda-accent); }
.fda-line.hint { color: var(--fda-muted); border-left-color: var(--fda-line); }
</style>
"""


def css() -> str:
    return CSS


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


# ── Table cell styles (pandas Styler) ─────────────────────────────────────────

def z_cell(v, scale: float = 2.5) -> str:
    """Green for positive z, red for negative, stronger with size; readable text on the dark theme."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v):
        return ""
    t = max(-1.0, min(1.0, v / scale))
    if abs(t) < 0.04:
        return "color: #AEB8CC"
    rgb = "34,197,94" if t > 0 else "244,63,94"
    weight = " font-weight: 700;" if abs(t) >= 0.6 else ""
    return f"background-color: rgba({rgb},{0.10 + 0.55 * abs(t):.2f}); color: #F4F7FC;{weight}"


def flag_cell(v) -> str:
    s = str(v)
    if "STEAL" in s:
        return "color: #4ADE80; font-weight: 800"
    if "REACH" in s:
        return "color: #FB7185; font-weight: 800"
    return ""


def next_cell(v) -> str:
    return "color: #FFC24D; font-weight: 700" if v else ""


def muted_cell(_v) -> str:
    return "color: #8C9AB5"


def rank_colors(rank: int | None, n: int) -> tuple[str, str]:
    """(background, text) for a 1..n rank: green near the top, amber mid, red at the bottom."""
    if not rank or not n:
        return "rgba(140,154,181,.15)", "#8C9AB5"
    frac = (rank - 1) / max(n - 1, 1)
    if frac <= 0.25:
        return "rgba(34,197,94,.20)", "#86EFAC"
    if frac <= 0.6:
        return "rgba(255,176,32,.16)", "#FFD27F"
    return "rgba(244,63,94,.18)", "#FDA4AF"


# ── HTML components ───────────────────────────────────────────────────────────

def header_html(badges: list[tuple[str, str]]) -> str:
    """Brand row; badges are (text, kind) with kind in {"", "warn", "ok", "live"}."""
    chips = "".join(f'<span class="fda-badge {k}">{esc(t)}</span>' for t, k in badges)
    return (f'<div class="fda-header"><div class="fda-brand"><div class="fda-ball"></div><div>'
            f'<div class="fda-title">Draft <span>Room</span></div>'
            f'<div class="fda-tagline">Fantasy basketball · 2026-27 · category z-scores, ADP steals &amp; reaches</div>'
            f'</div></div><div class="fda-badges">{chips}</div></div>')


def ticker_html(picks: list[dict]) -> str:
    """picks: newest first, each {no, team, name, pos, mine}."""
    if not picks:
        return ('<div class="fda-ticker"><span class="lbl">Last picks</span>'
                '<span class="fda-chip"><span class="tm">No picks yet: type a name above and press Enter</span></span></div>')
    chips = []
    for i, p in enumerate(picks):
        color = POS_COLORS.get((p.get("pos") or [""])[0], "#8C9AB5")
        cls = "fda-chip" + (" mine" if p.get("mine") else "") + (" newest" if i == 0 else "")
        chips.append(f'<span class="{cls}"><span class="fda-dot" style="background:{color}"></span>'
                     f'<span class="no">#{p["no"]}</span>{esc(p["name"])}<span class="tm">{esc(p["team"])}</span></span>')
    return f'<div class="fda-ticker"><span class="lbl">Last picks</span>{"".join(chips)}</div>'


def roster_html(rows: list[tuple[str, dict | None]], title: str, subtitle: str = "") -> str:
    """rows: (slot, player or None) in display order."""
    trs = []
    for slot, p in rows:
        if p:
            color = POS_COLORS.get((p.get("positions") or [""])[0], "#8C9AB5")
            pos = "/".join(p.get("positions") or []) or "?"
            trs.append(f'<tr><td><span class="fda-slot filled" style="--slot-color:{color}">{esc(slot)}</span></td>'
                       f'<td class="nm">{esc(p["name"])}</td><td class="pos">{esc(pos)} · {esc(p.get("team", ""))}</td></tr>')
        else:
            trs.append(f'<tr><td><span class="fda-slot">{esc(slot)}</span></td><td class="empty">open</td><td></td></tr>')
    sub = f"<small>{esc(subtitle)}</small>" if subtitle else ""
    return (f'<div class="fda-card"><div class="fda-card-title"><span>My team · <b>{esc(title)}</b></span>{sub}</div>'
            f'<table class="fda-roster">{"".join(trs)}</table></div>')


def standings_html(rows: list[dict], n: int, fit: dict[str, float] | None = None) -> str:
    """rows: {cat, value_text, z, rank, punt}. One line per category with a rank pill and meter."""
    body = []
    for r in rows:
        bg, fg = rank_colors(r["rank"], n)
        pct = 0 if not r["rank"] else 100 * (n - r["rank"] + 1) / n
        rank_txt = f'{r["rank"]} / {n}' if r["rank"] else "—"
        body.append(
            f'<tr class="{"punt" if r["punt"] else ""}"><td><b>{esc(r["cat"])}</b>{" · punt" if r["punt"] else ""}</td>'
            f'<td class="num">{esc(r["value_text"])}</td><td class="num">{r["z"]:+.2f}</td>'
            f'<td><div class="fda-meter"><i style="width:{pct:.0f}%;background:{fg}"></i></div></td>'
            f'<td style="text-align:right"><span class="fda-rank" style="background:{bg};color:{fg}">{rank_txt}</span></td></tr>')
    fit_html = ""
    if fit:
        tags = "".join(f'<span class="{"up" if w > 1 else "off" if w == 0 else ""}">{esc(c)} ×{w:g}</span>' for c, w in fit.items())
        fit_html = f'<div class="fda-fit">{tags}</div>'
    return (f'<div class="fda-card"><div class="fda-card-title"><span>Category standings</span>'
            f'<small>1 = best of {n} teams</small></div><table class="fda-stand"><thead><tr><th>Cat</th>'
            f'<th style="text-align:right">Mine</th><th style="text-align:right">ΣZ</th><th>Standing</th>'
            f'<th style="text-align:right">Rank</th></tr></thead><tbody>{"".join(body)}</tbody></table>{fit_html}</div>')


def points_card_html(fp: float, rank: int, n: int, title: str) -> str:
    bg, fg = rank_colors(rank, n)
    return (f'<div class="fda-card"><div class="fda-card-title"><span>Projected fantasy points</span><small>{esc(title)}</small></div>'
            f'<div style="display:flex;align-items:baseline;gap:14px"><div style="font-family:var(--fda-display);font-size:44px;'
            f'font-weight:700">{fp:.1f}</div><span class="fda-rank" style="background:{bg};color:{fg}">rank {rank} / {n}</span></div></div>')


def board_html(n_teams: int, n_rounds: int, cells: dict[tuple[int, int], list[dict]], me: int,
               cur_cell: tuple[int, int] | None, team_name) -> str:
    """cells: (round, team) -> picks {pick_no, name, positions, nba_team, keeper, unmatched}."""
    head = "".join(f'<th class="{"me" if t == me else ""}" title="{esc(team_name(t))}">{esc(team_name(t))}'
                   f'{" ★" if t == me else ""}</th>' for t in range(1, n_teams + 1))
    rows = []
    for r in range(1, n_rounds + 1):
        tds = []
        for t in range(1, n_teams + 1):
            cls = (["me"] if t == me else []) + (["cur"] if cur_cell == (r, t) else [])
            picks = cells.get((r, t), [])
            if picks:
                inner = "".join(
                    f'<div class="fda-pk" style="--c:{POS_COLORS.get((p["positions"] or [""])[0], "#8C9AB5")}" '
                    f'title="{esc(p["name"])} · {esc(p["nba_team"])} · pick #{p["pick_no"]}">'
                    f'<div class="nm {name_size(short_name(p["name"])[1])}">{esc(short_name(p["name"])[1])}</div><div class="meta">{esc(short_name(p["name"])[0])} '
                    f'#{p["pick_no"]} · {esc("/".join(p["positions"]) or "?")}'
                    f'{" · K" if p["keeper"] else ""}{" · ⚠" if p["unmatched"] else ""}</div></div>' for p in picks)
            else:
                slot = (r - 1) * n_teams + (t if r % 2 else n_teams + 1 - t)
                inner = f'<span class="slot">#{slot}{" · on the clock" if cur_cell == (r, t) else ""}</span>'
            tds.append(f'<td class="{" ".join(cls)}">{inner}</td>')
        rows.append(f'<tr><th>{r}<span>{"→" if r % 2 else "←"}</span></th>{"".join(tds)}</tr>')
    legend = (f'<div class="fda-legend"><span><i style="background:{POS_COLORS["PG"]}"></i>Guards</span>'
              f'<span><i style="background:{POS_COLORS["SF"]}"></i>Forwards</span>'
              f'<span><i style="background:{POS_COLORS["C"]}"></i>Centers</span>'
              f'<span><i style="background:rgba(255,122,26,.35)"></i>Your column</span>'
              f'<span><i style="border:2px dashed {ACCENT};width:8px;height:8px"></i>On the clock</span>'
              f'<span>K = keeper · ⚠ = not in dataset</span></div>')
    return (f'{legend}<div class="fda-board-wrap"><table class="fda-board"><thead><tr><th class="rd"></th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def short_name(name: str) -> tuple[str, str]:
    """('V.', 'Wembanyama') for board cells; suffixes stay with the last name ('Jackson Jr.')."""
    parts = str(name).split()
    if len(parts) < 2:
        return "", str(name)
    return f"{parts[0][0]}.", " ".join(parts[1:])


def name_size(last: str) -> str:
    """Board names use a condensed font; shrink longer ones so they fit a 12-team column."""
    longest = max((len(w) for w in last.split()), default=0)  # names may wrap at spaces ("Butler III")
    return "" if longest <= 7 else "mid" if longest <= 9 else "long"


def resume_html(n_picks: int, n_teams: int, when: str) -> str:
    return (f'<div class="fda-resume"><h2>Welcome back</h2><p>Your draft was saved automatically.</p>'
            f'<p class="big"><b>{n_picks}</b> picks · <b>{n_teams}</b> teams · last saved {esc(when)}</p></div>')


def player_line_html(p, projected: bool) -> str:
    """Stat line for the selected row, with the reasons behind a projection."""
    def num(key):
        try:
            v = float(p.get(key))
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    label = "2026-27 projection" if projected else "per game"
    cells = []
    for key, fmt in [("PTS", "{:.1f}"), ("REB", "{:.1f}"), ("AST", "{:.1f}"), ("3PM", "{:.1f}"), ("STL", "{:.1f}"),
                     ("BLK", "{:.1f}"), ("TO", "{:.1f}"), ("MIN", "{:.1f}"), ("GP", "{:.0f}")]:
        v = num(key)
        if v is not None:
            cells.append(f"<div><b>{fmt.format(v)}</b> <span>{key}</span></div>")
    for pct, makes, att in [("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")]:
        m, a = num(makes), num(att)
        if m is not None and a:
            cells.append(f"<div><b>{m / a:.3f}</b> <span>{pct}</span></div>")
    stats = "".join(cells) or '<div><span>No stats in this data source.</span></div>'
    notes = str(p.get("proj_notes") or "").strip()
    why = f'<div class="why"><b>Why:</b> {esc(notes)}</div>' if notes else ""
    return (f'<div class="fda-line"><div class="who">{esc(p.get("name"))}<small>{esc(p.get("team"))} · {label}</small></div>'
            f'<div class="stats">{stats}</div>{why}</div>')


def player_line_hint(projected: bool) -> str:
    extra = " and why it moved (new team, ball share, minutes, pace, age)" if projected else ""
    return f'<div class="fda-line hint">Select a row to see the full stat line{extra}.</div>'
