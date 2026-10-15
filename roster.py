"""Roster slot assignment and per-team category standings. Pure functions, no Streamlit."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from valuation import INVERTED, PCT_CATS, apply_stat_mode

# ESPN standard: PG, SG, SF, PF, C, G, F, UTIL x3, then bench.
DEFAULT_STARTERS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "UTIL", "UTIL"]
ANY = frozenset({"PG", "SG", "SF", "PF", "C"})
SLOT_ELIGIBILITY = {
    "PG": frozenset({"PG"}), "SG": frozenset({"SG"}), "SF": frozenset({"SF"}),
    "PF": frozenset({"PF"}), "C": frozenset({"C"}),
    "G": frozenset({"PG", "SG"}), "F": frozenset({"SF", "PF"}),
    "G/F": frozenset({"PG", "SG", "SF", "PF"}), "SG/SF": frozenset({"SG", "SF"}),
    "PF/C": frozenset({"PF", "C"}), "F/C": frozenset({"SF", "PF", "C"}),
}
FLEX_SLOTS = {"UTIL", "BN"}  # accept anyone, including players with unknown positions
SLOT_ORDER = ["PG", "SG", "SF", "PF", "C", "G", "F", "G/F", "SG/SF", "PF/C", "F/C", "UTIL"]


def parse_starters(text: str) -> list[str]:
    """'PG, SG, UTIL x3' -> ['PG', 'SG', 'UTIL', 'UTIL', 'UTIL']. Unknown slot names are rejected."""
    slots: list[str] = []
    for tok in [t.strip().upper() for t in text.replace(";", ",").split(",") if t.strip()]:
        name, _, count = tok.partition("X")
        name = name.strip()
        n = int(count) if count.strip().isdigit() else 1
        if name not in SLOT_ELIGIBILITY and name not in FLEX_SLOTS:
            raise ValueError(f"unknown roster slot {name!r}")
        if name != "BN":
            slots.extend([name] * n)
    return slots


def eligible(slot: str, positions) -> bool:
    if slot in FLEX_SLOTS:
        return True
    return bool(SLOT_ELIGIBILITY.get(slot, frozenset()) & set(positions or []))


@dataclass
class Assignment:
    starters: list[tuple[str, dict | None]]  # (slot, player) in display order; None = empty
    bench: list[dict]

    @property
    def filled(self) -> int:
        return sum(p is not None for _, p in self.starters)


def assign_roster(players: list[dict], starters: list[str] = DEFAULT_STARTERS) -> Assignment:
    """Place players into starting slots, maximizing (slots filled, then starters' total value).

    Exact search, not greedy: memoized backtracking over the position-restricted slots
    (state = slot index + bitmask of used players). UTIL slots accept anyone, so once the
    restricted slots are chosen, filling UTIL with the best remaining players is optimal.
    Each player dict needs 'positions' and optionally 'value'.
    """
    n = len(players)
    if n > 20:
        raise ValueError("roster matcher supports at most 20 players")
    values = [float(p.get("value") or 0.0) for p in players]
    restricted = [s for s in starters if s not in FLEX_SLOTS]
    n_util = sum(1 for s in starters if s == "UTIL")
    elig = [[j for j in range(n) if eligible(s, players[j].get("positions"))] for s in restricted]
    # Most-constrained slots first keeps the search tiny.
    order = sorted(range(len(restricted)), key=lambda i: (len(elig[i]), i))
    by_value = sorted(range(n), key=lambda j: (-values[j], j))

    def util_fill(mask: int) -> tuple[int, float, tuple[int, ...]]:
        chosen = tuple(j for j in by_value if not mask >> j & 1)[:n_util]
        return len(chosen), sum(values[j] for j in chosen), chosen

    @lru_cache(maxsize=None)
    def best(k: int, mask: int) -> tuple[int, float, tuple[int, ...]]:
        """Best (filled, value, picks) for restricted slots order[k:] plus UTIL, given used `mask`."""
        if k == len(order):
            return util_fill(mask)
        options = [best(k + 1, mask)]  # leave this slot empty
        options[0] = (options[0][0], options[0][1], (-1,) + options[0][2])
        for j in elig[order[k]]:
            if not mask >> j & 1:
                f, val, picks = best(k + 1, mask | 1 << j)
                options.append((f + 1, val + values[j], (j,) + picks))
        return max(options, key=lambda o: (o[0], round(o[1], 9)))

    _, _, picks = best(0, 0)
    restricted_choice = dict(zip(order, picks[:len(order)]))
    util_choice = list(picks[len(order):])

    used: set[int] = set()
    slot_rows: list[tuple[str, dict | None]] = []
    r_idx = 0
    for s in starters:
        if s == "UTIL":
            j = util_choice.pop(0) if util_choice else -1
        elif s in FLEX_SLOTS:
            continue
        else:
            j = restricted_choice[r_idx]
            r_idx += 1
        slot_rows.append((s, players[j] if j >= 0 else None))
        if j >= 0:
            used.add(j)
    bench = [players[j] for j in by_value if j not in used]
    return Assignment(starters=slot_rows, bench=bench)


def roster_table(assignment: Assignment, roster_size: int) -> pd.DataFrame:
    """Rows of (slot, name, positions, team) with empty bench slots shown up to roster_size."""
    rows = [{"slot": s, "player": p["name"] if p else "—", "pos": "/".join(p.get("positions") or []) if p else "",
             "team": p.get("team", "") if p else ""} for s, p in assignment.starters]
    n_bench = max(roster_size - len(assignment.starters), len(assignment.bench))
    for i in range(n_bench):
        p = assignment.bench[i] if i < len(assignment.bench) else None
        rows.append({"slot": "BN", "player": p["name"] if p else "—",
                     "pos": "/".join(p.get("positions") or []) if p else "", "team": p.get("team", "") if p else ""})
    return pd.DataFrame(rows)


# ── Category standings across all teams ───────────────────────────────────────

def team_category_table(valued: pd.DataFrame, team_rosters: dict[int, list[str]], cats: list[str],
                        stat_mode: str = "per_game") -> pd.DataFrame:
    """One row per team: raw total, summed Z and rank (1 = best) per category.

    `valued` must be indexed by player_id and carry Z_<cat> columns. Counting categories rank
    by summed Z (TO already inverted); FG%/FT% rank by the team's sum(makes)/sum(attempts), so
    a team isn't rewarded just for having one more pick than another.
    """
    stats = apply_stat_mode(valued, stat_mode)
    rows = []
    for team, ids in sorted(team_rosters.items()):
        ids = [i for i in ids if i in valued.index]
        row: dict = {"team": team, "players": len(ids)}
        for c in cats:
            zc = f"Z_{c}"
            row[f"{c}_z"] = float(valued.loc[ids, zc].sum()) if ids and zc in valued else 0.0
            if c in PCT_CATS:
                m, a = PCT_CATS[c]
                att = stats.loc[ids, a].sum() if ids else 0.0
                row[f"{c}_raw"] = float(stats.loc[ids, m].sum() / att) if att > 0 else np.nan
            else:
                row[f"{c}_raw"] = float(stats.loc[ids, c].sum()) if ids else 0.0
        rows.append(row)
    table = pd.DataFrame(rows).set_index("team")
    for c in cats:
        key = f"{c}_raw" if c in PCT_CATS else f"{c}_z"
        ranked = table[key].where(table["players"] > 0)
        table[f"{c}_rank"] = ranked.rank(ascending=False, method="min", na_option="bottom").astype(int)
    return table


def my_category_summary(table: pd.DataFrame, my_team: int, cats: list[str], punts=()) -> pd.DataFrame:
    """Dashboard rows for one team: category, raw value, summed Z, rank of N."""
    n = len(table)
    rows = []
    for c in cats:
        r = table.loc[my_team] if my_team in table.index else None
        rows.append({
            "cat": c + (" (punt)" if c in punts else ""),
            "value": None if r is None else r[f"{c}_raw"],
            "z": 0.0 if r is None else r[f"{c}_z"],
            "rank": None if r is None else int(r[f"{c}_rank"]),
            "of": n,
            "lower_is_better": c in INVERTED,
        })
    return pd.DataFrame(rows)
