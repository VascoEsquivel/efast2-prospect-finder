"""Recommended pick: player value, what my team needs, and what will be gone by my next pick (pure).

Three signals, all in the same units as the player list's value (z-score sum, or FP over replacement):

1. Category need (category leagues). How many matchups one more unit of a category would swing
   against the other teams, using every team's projected end-of-draft total: a category where I'm
   near the pack gets more weight than one I've locked up or can't catch. Slightly behind counts a
   bit more than slightly ahead, so empty categories get filled. Punted categories weigh 0. With few
   picks made, the weights stay close to 1 (best player available).
2. Position timing ("value over next available"). For each position: the best need-weighted value I
   can expect to still find at my following pick, given who the other teams will likely take first
   (by ADP when loaded, otherwise by rank). A player whose position dries up before then gets a bonus.
3. Roster fit. Late in the draft, players who fill an open starting slot get a small bonus.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import draft
import roster

FANTASY_POSITIONS = ["PG", "SG", "SF", "PF", "C"]
NEED_SHIFT = 0.5        # peak leverage when slightly behind the field (in units of the spread)
WAIT_WEIGHT = 0.5       # how much of the "value lost by waiting" is added to a player's score
SLOT_WEIGHT = 0.4
EXPECTATION_DEPTH = 40  # players per position used for the expected best-at-next-pick


@dataclass
class PickPlan:
    now_pick: int | None          # the pick the recommendation is for (current pick if I'm on the clock)
    following: int | None         # my pick after that
    on_clock: bool
    before_now: int               # other teams' picks before now_pick
    before_following: int         # other teams' picks before `following`
    my_picks_left: int            # including now_pick


@dataclass
class Recommendation:
    plan: PickPlan
    weights: dict[str, float]                 # category -> need weight (empty in points leagues)
    scores: pd.DataFrame                      # per available player: score parts and availability
    top: list[dict]                           # best few picks, with reasons
    gaps: list[dict] = field(default_factory=list)       # weakest categories and who fills them
    wait_cost: dict[str, float] = field(default_factory=dict)  # position -> value lost by waiting
    open_slots: list[str] = field(default_factory=list)


# ── Which picks are mine ──────────────────────────────────────────────────────

def pick_plan(cur: int | None, my_slot: int, n_teams: int, n_rounds: int, filled: set[int]) -> PickPlan:
    if cur is None:
        return PickPlan(None, None, False, 0, 0, 0)
    mine = [p for p in draft.picks_for_team(my_slot, n_teams, n_rounds) if p >= cur and p not in filled]
    if not mine:
        return PickPlan(None, None, False, 0, 0, 0)
    now = mine[0]
    following = mine[1] if len(mine) > 1 else None
    open_picks = [p for p in range(cur, (following or now) + 1) if p not in filled]
    before_now = sum(1 for p in open_picks if p < now)
    before_following = sum(1 for p in open_picks if p < following and p != now) if following else before_now
    return PickPlan(now, following, now == cur, before_now, before_following, len(mine))


# ── Signal 1: category need ───────────────────────────────────────────────────

def category_weights(table: pd.DataFrame | None, me: int, cats: list[str], punts=(),
                     n_rounds: int = 13) -> dict[str, float]:
    """Need weight per category (mean 1 over non-punted categories, punted = 0)."""
    live = [c for c in cats if c not in punts]
    flat = {c: (1.0 if c in live else 0.0) for c in cats}
    if table is None or me not in table.index or not live or table.loc[me, "players"] == 0 or len(table) < 2:
        return flat
    n = table["players"].astype(float)
    remaining = (n_rounds - n).clip(lower=0)
    gains = {}
    for c in live:
        z = table[f"{c}_z"].astype(float)
        base = float(z.sum() / n.sum()) if n.sum() > 0 else 0.0
        proj = z + remaining * base  # unfilled roster spots assumed average draftees
        g = 0.0
        for j in table.index:
            if j == me:
                continue
            spread = math.sqrt(remaining[me] + remaining[j] + 2.0)  # z-scores have spread 1 per player
            d = (proj[me] - proj[j]) / spread + NEED_SHIFT
            g += math.exp(-0.5 * d * d) / spread
        gains[c] = g
    mean = float(np.mean(list(gains.values()))) or 1.0
    return {c: (float(np.clip(gains[c] / mean, 0.25, 2.5)) if c in gains else 0.0) for c in cats}


# ── Signal 2: who is still there at my next pick ──────────────────────────────

def market_order(avail: pd.DataFrame) -> pd.Series:
    """0-based position in the order other teams are expected to draft (ADP if mostly known, else rank)."""
    adp = pd.to_numeric(avail.get("adp"), errors="coerce") if "adp" in avail else pd.Series(np.nan, index=avail.index)
    rank = pd.to_numeric(avail["rank"], errors="coerce").fillna(1e6)
    key = adp.fillna(1000 + rank) if adp.notna().sum() >= 30 else rank
    order = key.rank(method="first") - 1
    return order.astype(float)


def availability(market: pd.Series, k: int) -> pd.Series:
    """Chance each player survives k picks by other teams (normal noise around their market spot)."""
    if k <= 0:
        return pd.Series(1.0, index=market.index)
    sigma = 1.5 + 0.18 * market
    x = (market + 0.5 - k) / sigma
    return pd.Series(0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2))), index=market.index)


def _expected_best(values: np.ndarray, probs: np.ndarray, floor: float) -> float:
    """E[max value among players still there], players sorted by value desc, independent survival."""
    exp, none_left = 0.0, 1.0
    for v, p in zip(values, probs):
        exp += none_left * p * v
        none_left *= 1 - p
    return exp + none_left * floor


def wait_costs(score: pd.Series, positions: pd.Series, avail_next: pd.Series,
               avail_now: pd.Series | None = None) -> tuple[pd.Series, dict[str, float]]:
    """Per player: value lost if I skip him now and take the best at his easiest position later.

    Also returns, per position, the drop from the expected best at the pick I'm making (the best
    available, if I'm on the clock) to the expected best at my following pick.
    """
    drop = pd.Series(np.inf, index=score.index)
    drop_pos = pd.Series("", index=score.index, dtype=object)  # the position he is easiest to replace at
    by_pos: dict[str, float] = {}
    floor = float(score.quantile(0.2)) if len(score) else 0.0
    for pos in FANTASY_POSITIONS:
        elig = score[positions.map(lambda ps, p=pos: p in ps)].sort_values(ascending=False)
        if elig.empty:
            continue
        top = elig.head(EXPECTATION_DEPTH)
        vals, probs = top.to_numpy(), avail_next.reindex(top.index).fillna(1.0).to_numpy()
        exp_all = _expected_best(vals, probs, floor)
        now_probs = np.ones(len(vals)) if avail_now is None else avail_now.reindex(top.index).fillna(1.0).to_numpy()
        by_pos[pos] = float(_expected_best(vals, now_probs, floor) - exp_all)
        for i, pid in enumerate(top.index):  # leaving this player out changes the expectation
            d = float(vals[i] - _expected_best(np.delete(vals, i), np.delete(probs, i), floor))
            if d < drop[pid]:
                drop[pid], drop_pos[pid] = d, pos
        rest = elig.index[EXPECTATION_DEPTH:]
        d_rest = elig[rest] - exp_all
        better = d_rest < drop[rest]
        drop[rest[better]] = d_rest[better]
        drop_pos[rest[better]] = pos
    drop[np.isinf(drop)] = 0.0
    drop.attrs["pos"] = drop_pos
    return drop, by_pos


# ── Putting it together ───────────────────────────────────────────────────────

def recommend(avail: pd.DataFrame, plan: PickPlan, *, my_players: list[dict], starters: list[str],
              cats: list[str], punts=(), table: pd.DataFrame | None = None, me: int = 1,
              n_rounds: int = 13, is_points: bool = False, top_n: int = 3) -> Recommendation:
    """Score every available player with stats for my team's next pick.

    avail: valued players indexed by player_id (value, rank, adp, positions, name, team, Z_<cat>, MIN).
    """
    pool = avail[avail["MIN"].notna() & avail["value"].notna()] if "MIN" in avail else avail[avail["value"].notna()]
    weights = {} if is_points else category_weights(table, me, cats, punts, n_rounds)
    if is_points or not cats:
        need = pool["value"].astype(float)
    else:
        need = sum(w * pool[f"Z_{c}"].fillna(0.0) for c, w in weights.items() if w and f"Z_{c}" in pool)
        need = need if isinstance(need, pd.Series) else pd.Series(0.0, index=pool.index)
    market = market_order(pool)
    p_now = availability(market, plan.before_now)
    if plan.following:
        p_next = availability(market, plan.before_following)
        drop, by_pos = wait_costs(need, pool["positions"], p_next, p_now)
    else:
        p_next = pd.Series(0.0, index=pool.index)
        drop, by_pos = pd.Series(0.0, index=pool.index), {}

    current = roster.assign_roster(my_players, starters)
    open_slots = [slot for slot, p in current.starters if p is None]
    pressure = len(open_slots) / max(1, plan.my_picks_left) if open_slots else 0.0
    fills_cache: dict[tuple, bool] = {}

    def fills(ps) -> bool:
        key = tuple(ps)
        if key not in fills_cache:
            trial = roster.assign_roster(my_players + [{"player_id": "_cand", "positions": list(ps), "value": 0.0}], starters)
            fills_cache[key] = trial.filled > current.filled
        return fills_cache[key]

    fill = pool["positions"].map(fills)
    slot_adj = np.where(fill, SLOT_WEIGHT * pressure, -SLOT_WEIGHT * pressure) if open_slots else 0.0
    score = need + WAIT_WEIGHT * drop.clip(lower=0) + slot_adj
    scores = pd.DataFrame({"name": pool["name"], "need": need, "wait": drop, "slot": slot_adj, "fills": fill,
                           "score": score, "p_now": p_now, "p_next": p_next, "market": market,
                           "wait_pos": drop.attrs.get("pos", pd.Series("", index=pool.index))})
    # List order: players likely to last to my pick first, then by score.
    scores["order_key"] = scores["score"] - np.where(plan.on_clock | (scores["p_now"] >= 0.5), 0.0, 1000.0)
    usable = scores if plan.on_clock else scores[scores["p_now"] >= 0.5]  # planning ahead: likely to last
    best = usable.sort_values(["score", "name"], ascending=[False, True]).head(top_n)
    contenders = scores.sort_values("score", ascending=False).head(25)
    slot_matters = bool(open_slots) and not contenders["fills"].all()
    trailing = _trailing(table, me, cats, punts, weights)
    top = [_explain(pid, row, pool, trailing, plan, by_pos, need, slot_matters) for pid, row in best.iterrows()]
    gaps = [] if is_points or table is None else _gaps(pool, table, me, cats, punts, weights)
    return Recommendation(plan, weights, scores, top, gaps, by_pos, open_slots)


def _trailing(table: pd.DataFrame | None, me: int, cats: list[str], punts, weights: dict[str, float]) -> list[str]:
    """Categories where my team sits in the bottom half and adding more still pays (weight >= 1)."""
    if table is None or me not in table.index or table.loc[me, "players"] == 0:
        return []
    half = len(table) / 2
    return [c for c in cats if c not in punts and weights.get(c, 0) >= 1.0 and int(table.loc[me, f"{c}_rank"]) > half]


def _explain(pid, row, pool: pd.DataFrame, trailing: list[str], plan: PickPlan,
             by_pos: dict[str, float], need: pd.Series, slot_matters: bool) -> dict:
    p = pool.loc[pid]
    reasons = []
    contrib = sorted(((float(p.get(f"Z_{c}", 0) or 0), c) for c in trailing), reverse=True)
    helps = [c for z, c in contrib if z >= 0.5][:3]
    if helps:
        reasons.append(f"Helps {', '.join(helps)}, where your team trails")
    if plan.following and row["wait"] >= 0.5:
        alt = row["wait_pos"] or "/".join(p["positions"])
        typical = float(np.mean(list(by_pos.values()))) if by_pos else 0.0
        # Only call a position scarce when it drops clearly faster than the others.
        dries = alt in by_pos and by_pos[alt] >= 0.8 and by_pos[alt] >= 1.25 * typical + 0.3
        reasons.append(f"{row['wait']:.1f} better than the best {alt} likely left at #{plan.following}"
                       + (f": {alt}s are drying up" if dries else ""))
    if slot_matters and row["fills"]:
        reasons.append("Fills an open starting slot")
    if not plan.on_clock and row["p_now"] < 0.9:
        reasons.append(f"About {row['p_now']:.0%} likely to last to #{plan.now_pick}")
    elif plan.following and row["p_next"] < 0.25:
        reasons.append(f"Likely gone before #{plan.following}")
    if int(need.rank(ascending=False, method="min")[pid]) == 1:
        reasons.append("Best value left for your team")
    if not reasons:
        reasons.append("Best mix of value and fit")
    return {"player_id": pid, "name": p["name"], "team": p.get("team", ""), "positions": list(p["positions"]),
            "score": float(row["score"]), "need": float(row["need"]), "wait": float(row["wait"]),
            "p_now": float(row["p_now"]), "reasons": reasons}


def _gaps(pool: pd.DataFrame, table: pd.DataFrame, me: int, cats: list[str], punts, weights: dict[str, float],
          n: int = 3) -> list[dict]:
    """Weakest non-punted categories (by rank) and the best available contributor in each."""
    if me not in table.index or table.loc[me, "players"] == 0:
        return []
    live = [c for c in cats if c not in punts and f"Z_{c}" in pool]
    ranked = sorted(live, key=lambda c: (-int(table.loc[me, f"{c}_rank"]), -weights.get(c, 1.0)))
    worth = pool[pool["value"] >= pool["value"].nlargest(80).min()] if len(pool) > 80 else pool
    out = []
    for c in ranked[:n]:
        if int(table.loc[me, f"{c}_rank"]) <= max(1, len(table) // 3):
            break  # already top third: not a gap
        fit = (worth[f"Z_{c}"] + 0.35 * worth["value"]).sort_values(ascending=False)
        if fit.empty:
            continue
        pid = fit.index[0]
        out.append({"cat": c, "rank": int(table.loc[me, f"{c}_rank"]), "of": len(table), "player_id": pid,
                    "name": worth.at[pid, "name"], "team": worth.at[pid, "team"] if "team" in worth else "",
                    "z": float(worth.at[pid, f"Z_{c}"]),
                    # Far behind with few picks left: more of it barely moves the standings.
                    "long_shot": weights.get(c, 1.0) < 0.7})
    return out
