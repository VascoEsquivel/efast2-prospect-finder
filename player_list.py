"""Broadcast-style "best available" list: a clickable custom component (st.components.v2).

One row per available player: rank, headshot (team-colored initials when it can't load), name, NBA team
stripe, category heat chips, ADP flags and a one-click DRAFT button. Clicking a row opens the full stat
line and the reasons behind the projection. Clicking DRAFT sends the player id back to Python through a
trigger value; app.py drafts it to the team on the clock exactly like the search box does.

Presentation only: payload() turns the already-filtered, already-sorted table into display strings and
heat values. It never computes or invents stats: players without stats get no chips and no line.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

import ui

ROW_CAP = 200          # rows sent to the browser; the search box still finds everyone
POINT_STATS = ["PTS", "REB", "AST", "STL", "BLK", "TO"]
LINE_STATS = [("PTS", "{:.1f}"), ("REB", "{:.1f}"), ("AST", "{:.1f}"), ("3PM", "{:.1f}"), ("STL", "{:.1f}"),
              ("BLK", "{:.1f}"), ("TO", "{:.1f}"), ("MIN", "{:.1f}"), ("GP", "{:.0f}")]

CSS = r"""
:host { display: block; }
.cq { container-type: inline-size; }
.pl { --nc: 9; --ink: #F3F5F9; --muted: #8D96A8; --dim: #4E576A; --line: rgba(255,255,255,.07); --line2: rgba(255,255,255,.13);
  --red: #FF2D45; --gold: #FFC83D; --good: #2EE58F; --bad: #FF5C74; --amber: #FFB020;
  --display: 'Big Shoulders Display', 'Barlow Condensed', 'Arial Narrow', sans-serif;
  --cond: 'Barlow Condensed', 'Arial Narrow', sans-serif;
  --w-rk: 28px; --w-av: 40px; --w-val: 58px; --w-chip: 36px; --w-adp: 50px; --w-act: 76px; --gap: 8px;
  color: var(--ink); font-family: var(--cond); -webkit-font-smoothing: antialiased; }
.top { display: flex; align-items: stretch; gap: 0; min-height: 34px; margin-bottom: 0; }
.top .ttl { display: flex; align-items: center; padding: 0 22px 0 12px; background: var(--red); color: #fff;
  font: 900 17px/1 var(--display); letter-spacing: .1em; text-transform: uppercase; white-space: nowrap;
  clip-path: polygon(0 0, 100% 0, calc(100% - 11px) 100%, 0 100%); }
.top .cnt { display: flex; align-items: center; padding: 0 16px; font: 600 13px/1 var(--cond); letter-spacing: .06em; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.top .cnt b { color: #C9CFDB; font-weight: 700; }
.top .for { margin-left: auto; display: flex; align-items: center; gap: 8px; padding: 0 14px 0 20px; background: #F3F5F9; color: #0A0D14;
  font: 700 13px/1 var(--cond); letter-spacing: .08em; text-transform: uppercase; white-space: nowrap;
  clip-path: polygon(11px 0, 100% 0, 100% 100%, 0 100%); box-shadow: inset -5px 0 0 var(--c, var(--red)); }
.top .for b { font: 900 17px/1 var(--display); letter-spacing: .04em; }
.top .for i { width: 7px; height: 7px; border-radius: 50%; background: var(--red); animation: pulse 1.5s infinite; }
.top .for.done { background: #1B2130; color: var(--muted); box-shadow: none; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(255,45,69,.75); } 70% { box-shadow: 0 0 0 8px rgba(255,45,69,0); } 100% { box-shadow: 0 0 0 0 rgba(255,45,69,0); } }

.scroll { max-height: 760px; overflow-y: auto; overflow-x: hidden; background: #0B0E15; border-top: 2px solid var(--red);
  scrollbar-width: thin; scrollbar-color: #2A3243 transparent; overscroll-behavior: contain; }
.row { display: grid; align-items: center; column-gap: var(--gap); min-height: 54px; padding: 0 10px 0 0;
  grid-template-columns: var(--w-rk) var(--w-av) minmax(120px, 1fr) var(--w-val) calc(var(--nc) * var(--w-chip) + (var(--nc) - 1) * 2px) var(--w-adp) var(--w-act);
  grid-template-areas: "rk av who val chips adp act";
  border-bottom: 1px solid var(--line); box-shadow: inset 4px 0 0 var(--tc, #3A4150); cursor: pointer; position: relative;
  background: linear-gradient(90deg, color-mix(in srgb, var(--tc, #3A4150) 15%, transparent) 0, transparent 240px); }
.no-adp .row { grid-template-columns: var(--w-rk) var(--w-av) minmax(120px, 1fr) var(--w-val) calc(var(--nc) * var(--w-chip) + (var(--nc) - 1) * 2px) var(--w-act);
  grid-template-areas: "rk av who val chips act"; }
.no-adp .adp { display: none; }
.row:hover { background-color: #121722; }
.row.open { background-color: #141A26; }
.row.hd { position: sticky; top: 0; z-index: 3; min-height: 30px; cursor: default; background: #0B0E15; box-shadow: none;
  border-bottom: 1px solid var(--line2); }
.row.hd span { font: 700 11px/1 var(--cond); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.row.hd .chips span { text-align: center; letter-spacing: .04em; font-size: 11.5px; color: #AEB6C5; }
.row.hd .chips span.punt { color: var(--dim); text-decoration: line-through; }
.rk { grid-area: rk; text-align: right; font: 800 15px/1 var(--display); color: var(--muted); font-variant-numeric: tabular-nums; }
.av { grid-area: av; position: relative; width: 38px; height: 38px; display: grid; place-items: center; overflow: hidden;
  background: linear-gradient(150deg, var(--tc) 0%, var(--tc0) 100%); clip-path: polygon(16% 0, 100% 0, 84% 100%, 0 100%);
  font: 800 15px/1 var(--cond); color: #fff; letter-spacing: .02em; }
@keyframes img-in { to { opacity: 1; } }
.av img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 50% 18%; color: transparent;
  opacity: 0; animation: img-in 0s 3s forwards; }
.av.ok { color: transparent; }
.av.ok img { opacity: 1; animation: none; }
.av img::after { content: attr(data-i); position: absolute; inset: 0; display: grid; place-items: center; color: #fff;
  background: linear-gradient(150deg, var(--tc) 0%, var(--tc0) 100%); font: 800 15px/1 var(--cond); }
.av::before { content: ""; position: absolute; inset: 0; background: linear-gradient(160deg, rgba(255,255,255,.13) 0 38%, transparent 38%); }
.who { grid-area: who; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.nm { font: 600 17px/1.05 var(--cond); color: #C3C9D5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-transform: uppercase; letter-spacing: .015em; }
.nm b { font-weight: 800; color: var(--ink); }
.mt { display: flex; align-items: center; gap: 5px; font: 600 12px/1 var(--cond); letter-spacing: .05em; color: var(--muted);
  white-space: nowrap; overflow: hidden; min-width: 0; }
.mt .tmn { color: #C9CFDB; font-weight: 700; }
.lg { position: relative; width: 15px; height: 15px; flex: none; background: linear-gradient(180deg, var(--tc), var(--tc0));
  clip-path: polygon(42% 0, 100% 0, 58% 100%, 0 100%); }
.lg.ok { background: none; clip-path: none; }
.lg img { width: 100%; height: 100%; object-fit: contain; color: transparent; opacity: 0; animation: img-in 0s 3s forwards; }
.lg.ok img { opacity: 1; animation: none; }
.tag { flex: none; font: 800 10.5px/1 var(--cond); letter-spacing: .1em; padding: 3px 7px 2px; text-transform: uppercase;
  clip-path: polygon(3px 0, 100% 0, calc(100% - 3px) 100%, 0 100%); }
.tag.steal { background: var(--good); color: #04130B; }
.tag.reach { background: var(--bad); color: #1A0307; }
.tag.gone { background: rgba(255,176,32,.16); color: #FFC24D; }
.tag.inj { background: rgba(255,92,116,.16); color: #FF9AAA; }
.tag.ns { background: rgba(255,255,255,.07); color: var(--muted); }
.val { grid-area: val; text-align: right; font: 900 25px/1 var(--display); font-variant-numeric: tabular-nums; letter-spacing: .01em; }
.val small { display: block; margin-top: 3px; font: 700 11px/1 var(--cond); letter-spacing: .06em; color: var(--gold); }
.val.none { color: var(--dim); font-size: 18px; }
.chips { grid-area: chips; display: grid; grid-template-columns: repeat(var(--nc), 1fr); gap: 2px; }
.chips i { display: grid; place-items: center; height: 28px; font: 700 12px/1 var(--cond); font-style: normal;
  font-variant-numeric: tabular-nums; color: #7D8698; background: rgba(255,255,255,.03); }
.chips i.p { opacity: .32; }
.chips .nost { grid-column: 1 / -1; display: flex; align-items: center; justify-content: center; height: 28px;
  font: 600 12px/1 var(--cond); letter-spacing: .08em; text-transform: uppercase; color: var(--dim);
  background: repeating-linear-gradient(135deg, rgba(255,255,255,.025) 0 6px, transparent 6px 12px); }
.adp { grid-area: adp; text-align: right; font: 700 14px/1 var(--cond); color: #C9CFDB; font-variant-numeric: tabular-nums; }
.adp small { display: block; margin-top: 3px; font-size: 11px; color: var(--muted); }
.act { grid-area: act; display: flex; justify-content: flex-end; }
.dr { appearance: none; border: 0; cursor: pointer; height: 34px; width: 100%; max-width: 76px; padding: 0 10px 0 14px;
  background: var(--red); color: #fff; font: 900 15px/1 var(--display); letter-spacing: .12em; text-transform: uppercase;
  clip-path: polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%); transition: background .12s, transform .12s; }
.dr:hover { background: #FF4D62; transform: translateX(2px); }
.dr:focus-visible { outline: 2px solid #fff; outline-offset: -4px; }
.dr:disabled { background: #222937; color: var(--dim); cursor: default; transform: none; }
.row:not(:hover):not(.open) .dr:not(:disabled):not(:focus-visible) { background: transparent; box-shadow: inset 0 0 0 1.5px rgba(255,45,69,.75); color: #FF8796; }
.row.going { opacity: .45; pointer-events: none; }

.det { display: grid; grid-template-columns: 1fr; gap: 10px; padding: 12px 16px 14px calc(var(--w-rk) + var(--w-av) + 2 * var(--gap));
  background: #141A26; border-bottom: 1px solid var(--line2); box-shadow: inset 4px 0 0 var(--tc, #3A4150); }
.det .k { font: 800 11px/1 var(--cond); letter-spacing: .18em; text-transform: uppercase; color: var(--red); }
.det .stats { display: flex; flex-wrap: wrap; gap: 0; }
.det .stats div { padding: 2px 9px 0 0; margin: 0 9px 6px 0; border-right: 1px solid var(--line2); }
.det .stats div:last-child { border-right: 0; }
.det .stats b { display: block; font: 900 21px/1 var(--display); color: var(--ink); }
.det .stats span { font: 700 10.5px/1 var(--cond); letter-spacing: .14em; color: var(--muted); }
.det .why { font: 500 13.5px/1.4 var(--cond); letter-spacing: .02em; color: #C9CFDB; }
.det .why b { color: var(--gold); font-weight: 800; letter-spacing: .1em; text-transform: uppercase; margin-right: 6px; font-size: 12px; }
.det .mk { font: 600 12.5px/1.3 var(--cond); color: var(--muted); letter-spacing: .04em; }
.more { display: block; width: 100%; padding: 12px; border: 0; cursor: pointer; background: #10141D; color: #C9CFDB;
  font: 800 14px/1 var(--cond); letter-spacing: .14em; text-transform: uppercase; border-bottom: 1px solid var(--line); }
.more:hover { background: #161C29; color: #fff; }
.foot { padding: 10px 14px; font: 600 12px/1.3 var(--cond); letter-spacing: .05em; color: var(--muted); }
.empty { padding: 28px 16px; text-align: center; font: 700 14px/1.3 var(--cond); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }

@container (max-width: 760px) {
  .pl { --w-rk: 22px; --w-av: 36px; --w-val: 50px; --w-chip: 31px; --w-adp: 42px; --w-act: 64px; --gap: 6px; }
  .av { width: 34px; height: 34px; font-size: 13px; }
  .chips i { font-size: 11px; }
  .val { font-size: 22px; }
  .dr { font-size: 14px; letter-spacing: .08em; padding: 0 8px 0 12px; }
}
@container (max-width: 600px) {
  .pl { --w-rk: 20px; --w-av: 36px; --w-val: 48px; --w-act: 62px; --gap: 6px; }
  .scroll { max-height: none; overflow: visible; }
  .row, .no-adp .row { grid-template-columns: var(--w-rk) var(--w-av) minmax(0, 1fr) var(--w-val) var(--w-act);
    grid-template-areas: "rk av who val act" ". chips chips chips chips"; row-gap: 6px; padding: 8px 8px 8px 0; min-height: 0; }
  .adp { display: none; }
  .row.hd { padding: 6px 8px 6px 0; }
  .row.hd .chips span { font-size: 10.5px; }
  .chips i { height: 24px; font-size: 11px; }
  .chips .nost { height: 24px; }
  .nm { font-size: 16px; }
  .top .cnt { display: none; }
  .top .ttl { font-size: 15px; padding: 0 18px 0 10px; }
  .top .for { padding: 0 10px 0 16px; font-size: 11.5px; min-width: 0; }
  .top .for b { font-size: 15px; overflow: hidden; text-overflow: ellipsis; }
  .top .for .w { display: none; }
  .det { padding-left: 14px; }
}
"""

JS = r"""
const INIT = 40, STEP = 40, INIT_PHONE = 15;
// Phones stack two-line rows with no inner scroll, so start shorter: My team and Standings stay close.
const initLimit = (host) => (host.clientWidth && host.clientWidth < 600 ? INIT_PHONE : INIT);
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

function heat(h) {
  if (h === null || h === undefined || Math.abs(h) < 0.04) return '';
  const t = Math.max(-1, Math.min(1, h)), a = (0.12 + 0.6 * Math.abs(t)).toFixed(2);
  const bg = t > 0 ? `rgba(46,229,143,${a})` : `rgba(255,92,116,${a})`;
  const fg = Math.abs(t) > 0.45 ? '#FFFFFF' : (t > 0 ? '#BDF7DA' : '#FFC9D1');
  return `background:${bg};color:${fg};${Math.abs(t) > 0.6 ? 'font-weight:800;' : ''}`;
}
function valColor(h) {
  if (h === null || h === undefined) return '';
  const t = Math.max(-1, Math.min(1, h));
  if (t > 0.5) return 'color:#5CF0A8';
  if (t > 0.15) return 'color:#E9FFF3';
  if (t < -0.15) return 'color:#FF8E9E';
  return 'color:#C9CFDB';
}

function rowHtml(r, d, open) {
  const img = r.img ? `<img src="${esc(r.img)}" alt="" data-i="${esc(r.ini)}" loading="lazy">` : '';
  const logo = r.logo ? `<span class="lg"><img src="${esc(r.logo)}" alt="" loading="lazy"></span>` : '';
  const tags = [];
  if (r.flag === 'STEAL') tags.push(`<span class="tag steal">▲ Steal</span>`);
  if (r.flag === 'REACH') tags.push(`<span class="tag reach">▼ Reach</span>`);
  if (r.gone) tags.push(`<span class="tag gone">Gone by #${esc(d.next_pick)}</span>`);
  if (r.inj) tags.push(`<span class="tag inj">${esc(r.inj)}</span>`);
  if (r.ns) tags.push(`<span class="tag ns">No stats</span>`);
  const chips = r.ns ? `<span class="nost">No NBA stats yet</span>`
    : r.ch.map((c, i) => `<i class="${d.cols[i] && d.cols[i].punt ? 'p' : ''}" style="${heat(c[1])}">${esc(c[0])}</i>`).join('');
  const val = r.v ? `<span class="val" style="${valColor(r.vh)}">${esc(r.v)}${r.f ? `<small>${esc(d.flab)} ${esc(r.f)}</small>` : ''}</span>`
    : `<span class="val none">—</span>`;
  const adp = `<span class="adp">${r.adp ? esc(r.adp) : '<span style="color:#4E576A">—</span>'}${r.d ? `<small>${esc(r.d)}</small>` : ''}</span>`;
  const btn = `<span class="act"><button class="dr" type="button" data-draft="${esc(r.id)}" ${d.can_draft ? '' : 'disabled'}
      title="${d.can_draft ? 'Draft for ' + esc(d.clock) : 'Draft complete'}">Draft</button></span>`;
  let html = `<div class="row${open ? ' open' : ''}" data-row="${esc(r.id)}" style="--tc:${esc(r.tc)};--tc0:${esc(r.tc0)}">
    <span class="rk">${esc(r.rk)}</span><span class="av">${esc(r.ini)}${img}</span>
    <span class="who"><span class="nm">${esc(r.fn)} <b>${esc(r.ln)}</b></span>
      <span class="mt">${logo}<span class="tmn">${esc(r.tm)}</span><span>· ${esc(r.pos)}</span>${r.gp ? `<span>· ${esc(r.gp)} GP</span>` : ''}${tags.join('')}</span></span>
    ${val}<span class="chips">${chips}</span>${adp}${btn}</div>`;
  if (open) {
    const stats = r.line.length ? r.line.map((s) => `<div><b>${esc(s[1])}</b><span>${esc(s[0])}</span></div>`).join('')
      : `<div><span>No stats in this data source</span></div>`;
    const why = r.why ? `<div class="why"><b>Why</b>${esc(r.why)}</div>` : '';
    const mk = r.adp ? `<div class="mk">ADP ${esc(r.adp)}${r.d ? ` · ${esc(r.d)} vs your rank` : ''}${r.flag ? ` · ${esc(r.flag)}` : ''}</div>` : '';
    html += `<div class="det" style="--tc:${esc(r.tc)}"><span class="k">${esc(d.line_label)}</span><div class="stats">${stats}</div>${why}${mk}</div>`;
  }
  return html;
}

function render(st) {
  const d = st.data, root = st.root;
  root.style.setProperty('--nc', String(Math.max(1, d.cols.length)));
  root.classList.toggle('no-adp', !d.show_adp);
  const forHtml = d.can_draft
    ? `<span class="for" style="--c:${esc(d.clock_color)}"><i></i><span class="w">Drafting for</span><b>${esc(d.clock)}</b></span>`
    : `<span class="for done">Draft complete</span>`;
  st.top.innerHTML = `<span class="ttl">Best available</span>
    <span class="cnt"><b>${esc(d.total)}</b>&nbsp;players${d.pos !== 'All' ? ' · ' + esc(d.pos) : ''} · sorted by&nbsp;<b>${esc(d.order)}</b></span>${forHtml}`;
  const head = `<div class="row hd"><span class="rk">#</span><span></span><span class="who">Player</span>
    <span class="val" style="font:700 11px/1 var(--cond);letter-spacing:.14em">${esc(d.vlabel)}</span>
    <span class="chips">${d.cols.map((c) => `<span class="${c.punt ? 'punt' : ''}">${esc(c.k)}</span>`).join('')}</span>
    <span class="adp" style="font:700 11px/1 var(--cond);letter-spacing:.14em">ADP</span><span></span></div>`;
  const rows = d.rows.slice(0, st.limit);
  let body = rows.map((r) => rowHtml(r, d, st.open === r.id)).join('');
  if (!d.rows.length) body = `<div class="empty">No available players match this filter</div>`;
  if (d.rows.length > st.limit) {
    body += `<button class="more" type="button" data-more="1">Show ${Math.min(STEP, d.rows.length - st.limit)} more · ${d.rows.length - st.limit} left</button>`;
  } else if (d.total > d.rows.length) {
    body += `<div class="foot">Top ${d.rows.length} shown. Type any name in the search box to draft someone further down.</div>`;
  }
  st.list.innerHTML = head + body;
  // Headshot or logo can't load (offline, blocked, no headshot): drop the <img> so the team-colored
  // initials tile / team-color swatch underneath shows. Never a broken-image icon.
  // Loaded: mark the tile .ok (hides the initials, shows the image, which stays invisible while loading).
  const fail = (img) => { const p = img.parentElement; if (p) p.classList.add('x'); img.remove(); };
  const ok = (img) => { const p = img.parentElement; if (p) p.classList.add('ok'); };
  st.list.querySelectorAll('img').forEach((img) => {
    if (img.complete) { if (img.naturalWidth > 0) ok(img); else fail(img); return; }
    img.addEventListener('load', () => ok(img), { once: true });
    img.addEventListener('error', () => fail(img), { once: true });
  });
}

export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  const host = parentElement.querySelector('.pl');
  if (!host) return;
  let st = host.__bc;
  if (!st) {
    host.innerHTML = '<div class="top"></div><div class="scroll"></div>';
    st = host.__bc = { root: host, top: host.querySelector('.top'), list: host.querySelector('.scroll'),
                       open: null, limit: initLimit(host), sig: null, last: null };
    host.addEventListener('click', (e) => {
      const b = e.target.closest('[data-draft]');
      if (b) {
        e.stopPropagation();
        if (b.disabled) return;
        b.disabled = true; b.textContent = '···';
        const row = b.closest('.row'); if (row) row.classList.add('going');
        st.send(b.getAttribute('data-draft'));
        return;
      }
      if (e.target.closest('[data-more]')) { st.limit += STEP; render(st); return; }
      const row = e.target.closest('[data-row]');
      if (row && !row.classList.contains('hd')) {
        const id = row.getAttribute('data-row');
        st.open = st.open === id ? null : id;
        render(st);
      }
    });
  }
  st.send = (pid) => setTriggerValue('draft', pid);
  const key = JSON.stringify(data);
  if (key === st.last) return;
  st.last = key;
  st.data = data;
  if (st.sig !== data.sig) { st.sig = data.sig; st.limit = initLimit(host); st.open = null; st.list.scrollTop = 0; }
  render(st);
}
"""


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def stat_line(rec: dict) -> list[list[str]]:
    """The player's own stat line from the dataset, formatted. Missing stats are left out, never filled in."""
    out = []
    for key, fmt in LINE_STATS:
        v = _num(rec.get(key))
        if v is not None:
            out.append([key, fmt.format(v)])
    for pct, makes, att in [("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")]:
        m, a = _num(rec.get(makes)), _num(rec.get(att))
        if m is not None and a:
            out.append([pct, f"{m / a:.3f}"])
    return out


def payload(view: pd.DataFrame, *, gone, cats: list[str], punts: tuple, is_points: bool, fit_on: bool,
            next_pick: int | None, projected: bool, can_draft: bool, clock: str, clock_color: str,
            order: str, pos: str, sig: str) -> dict:
    """Everything the component draws, as JSON-able display values for the top ROW_CAP rows of `view`."""
    cols = POINT_STATS if is_points else list(cats)
    z_scale = max(2.5, 2.5 * len(cats) ** 0.5) if cats else 2.5
    head = view.head(ROW_CAP)
    gone_list = list(pd.Series(gone).to_numpy()[:ROW_CAP]) if gone is not None else [False] * len(head)
    has_adp = bool(pd.to_numeric(view["adp"], errors="coerce").notna().any()) if "adp" in view else False
    # Points mode shows raw per-game stats in the chips; shade them against the best available value.
    col_max = {}
    if is_points:
        for c in POINT_STATS:
            s = pd.to_numeric(view[c], errors="coerce") if c in view else pd.Series(dtype=float)
            col_max[c] = float(s.max()) if s.notna().any() and s.max() > 0 else 1.0
    rows = []
    for i, rec in enumerate(head.to_dict("records")):
        name = str(rec.get("name") or "")
        first, last = ui.split_name(name)
        team = str(rec.get("team") or "")
        tc, tc0 = ui.team_colors(team)
        gp = _num(rec.get("GP"))
        no_stats = gp is None or gp <= 0
        positions = rec.get("positions")
        pos_txt = "/".join(list(positions) if positions is not None else []) or "?"
        if str(rec.get("positions_source", "")).startswith("approx") and pos_txt != "?":
            pos_txt += " ≈"
        chips, v, vh, fit = [], "", None, ""
        if not no_stats:
            if is_points:
                for c in POINT_STATS:
                    x = _num(rec.get(c))
                    h = None if x is None else _clip((-1 if c == "TO" else 1) * x / col_max[c])
                    chips.append(["" if x is None else f"{x:.1f}", h])
                fp, fv = _num(rec.get("FP")), _num(rec.get("FP_value"))
                v = "" if fp is None else f"{fp:.1f}"
                vh = None if fv is None else _clip(fv / 15)
                fit = "" if fv is None else f"{fv:+.1f}"
            else:
                for c in cats:
                    z = _num(rec.get(f"Z_{c}"))
                    chips.append(["" if z is None else f"{z:+.1f}", None if z is None or c in punts else _clip(z / 2.5)])
                z = _num(rec.get("Z_total"))
                v = "" if z is None else f"{z:.2f}"
                vh = None if z is None else _clip(z / z_scale)
                if fit_on:
                    f = _num(rec.get("fit"))
                    fit = "" if f is None else f"{f:.2f}"
        adp, delta = _num(rec.get("adp")), _num(rec.get("delta"))
        inj = str(rec.get("injury_status") or "").strip()
        rows.append({
            "id": rec["player_id"], "rk": int(rec["rank"]), "fn": first, "ln": last, "ini": ui.initials(name),
            "tm": team, "tc": tc, "tc0": tc0, "img": ui.headshot_url(rec["player_id"], rec.get("espn_id")),
            "logo": ui.team_logo(team), "pos": pos_txt, "inj": inj, "gp": "" if no_stats else f"{gp:.0f}",
            "ns": no_stats, "v": v, "vh": vh, "f": fit, "ch": chips,
            "adp": "" if adp is None else f"{adp:.1f}", "d": "" if delta is None else f"{delta:+.0f}",
            "flag": str(rec.get("flag") or ""), "gone": bool(gone_list[i]) if i < len(gone_list) else False,
            "why": str(rec.get("proj_notes") or "").strip(), "line": [] if no_stats else stat_line(rec),
        })
    return {
        "sig": sig, "mode": "points" if is_points else "cats",
        "cols": [{"k": c, "punt": (not is_points) and c in punts} for c in cols],
        "vlabel": "FP" if is_points else ("Z / Fit" if fit_on else "Z"), "flab": "val" if is_points else "fit",
        "show_adp": has_adp, "can_draft": can_draft, "clock": clock, "clock_color": clock_color,
        "next_pick": next_pick, "total": int(len(view)), "order": order, "pos": pos,
        "line_label": "2026-27 projection" if projected else "Per game", "rows": rows,
    }


def render(data: dict, key: str, on_draft):
    comp = st.components.v2.component("bc_player_list", html='<div class="cq"><div class="pl"></div></div>', css=CSS, js=JS)
    return comp(key=key, data=data, on_draft_change=on_draft)
