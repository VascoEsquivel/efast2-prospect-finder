"""Player valuation: category z-scores over an iterated replacement pool, points leagues, ADP flags.

Pure functions on pandas/numpy only (no Streamlit, no I/O), so everything here is unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CATEGORIES = ["FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"]
PCT_CATS = {"FG%": ("FGM", "FGA"), "FT%": ("FTM", "FTA")}
INVERTED = {"TO"}
CATEGORY_TEMPLATES = {
    "9-cat": CATEGORIES,
    "8-cat (no TO)": [c for c in CATEGORIES if c != "TO"],
    "points": [],
}
# ESPN's default H2H points scoring (introduced 2020). Editable in the sidebar.
ESPN_DEFAULT_POINTS = {
    "PTS": 1.0, "3PM": 1.0, "FGM": 2.0, "FGA": -1.0, "FTM": 1.0, "FTA": -1.0,
    "REB": 1.0, "AST": 2.0, "STL": 4.0, "BLK": 4.0, "TO": -2.0,
}
SCALED_STATS = ["MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "FGM", "FGA", "FTM", "FTA"]
NEEDED_STATS = ["GP", *SCALED_STATS]


@dataclass(frozen=True)
class ValuationSettings:
    n_teams: int = 12
    roster_size: int = 13
    template: str = "9-cat"
    categories: tuple[str, ...] = tuple(CATEGORIES)  # active categories (subset of the template)
    punts: tuple[str, ...] = ()
    stat_mode: str = "per_game"                      # per_game | season
    min_gp: int = 0
    points_weights: tuple[tuple[str, float], ...] = tuple(ESPN_DEFAULT_POINTS.items())
    steal_min_gap: float = 3.0
    steal_pct: float = 0.15

    @property
    def pool_size(self) -> int:
        return self.n_teams * self.roster_size

    @property
    def is_points(self) -> bool:
        return self.template == "points"


@dataclass
class PoolResult:
    z: pd.DataFrame          # one column per category
    total: pd.Series         # Z_total (punts weighted 0)
    pool: np.ndarray         # bool mask of the replacement pool
    iterations: int
    converged: bool
    league_rates: dict = field(default_factory=dict)  # FG% / FT% of the pool


def active_categories(template: str, enabled: list[str] | None = None) -> list[str]:
    base = CATEGORY_TEMPLATES[template]
    return [c for c in base if enabled is None or c in enabled]


def multipliers(cats: list[str], punts) -> dict[str, float]:
    return {c: 0.0 if c in set(punts) else 1.0 for c in cats}


def apply_stat_mode(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Return the stat columns used for valuation. 'season' = per-game x GP (makes/attempts too)."""
    stats = pd.DataFrame(index=df.index)
    for c in NEEDED_STATS:
        stats[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    stats = stats.astype(float)
    if mode == "season":
        gp = stats["GP"].fillna(0.0)
        for c in SCALED_STATS:
            stats[c] = stats[c] * gp
    elif mode != "per_game":
        raise ValueError(f"unknown stat mode {mode!r}")
    return stats


def league_rate(stats: pd.DataFrame, pool: np.ndarray, makes: str, attempts: str) -> float:
    """Pool-wide rate = sum(makes) / sum(attempts), NOT the mean of individual percentages."""
    a = stats.loc[pool, attempts].fillna(0).sum()
    return float(stats.loc[pool, makes].fillna(0).sum() / a) if a > 0 else 0.0


def pct_impact(stats: pd.DataFrame, makes: str, attempts: str, rate: float) -> pd.Series:
    """Makes above what a league-average shooter would convert on the same attempts."""
    return stats[makes].fillna(0) - rate * stats[attempts].fillna(0)


def category_zscores(stats: pd.DataFrame, pool: np.ndarray, cats: list[str]) -> tuple[pd.DataFrame, dict]:
    """z-score every player against the pool's mean and population std, per category."""
    z = pd.DataFrame(index=stats.index)
    rates = {}
    for c in cats:
        if c in PCT_CATS:
            m, a = PCT_CATS[c]
            rates[c] = league_rate(stats, pool, m, a)
            x = pct_impact(stats, m, a, rates[c])
        else:
            x = stats[c].fillna(0)
        mu = x[pool].mean()
        sd = x[pool].std(ddof=0)
        col = (x - mu) / sd if sd and sd > 0 else x * 0.0
        z[c] = -col if c in INVERTED else col
    return z, rates


def _top_mask(values: pd.Series, eligible: np.ndarray, k: int) -> np.ndarray:
    """Boolean mask of the k highest `values` among eligible rows (stable, deterministic)."""
    v = np.where(eligible, values.to_numpy(dtype=float), -np.inf)
    order = np.argsort(-v, kind="stable")
    k = min(k, int(eligible.sum()))
    mask = np.zeros(len(v), dtype=bool)
    mask[order[:k]] = True
    return mask


def iterate_pool(stats: pd.DataFrame, cats: list[str], mults: dict[str, float], pool_size: int,
                 min_gp: float = 0, max_iter: int = 10) -> PoolResult:
    """Iterate pool -> z -> Z_total -> pool until membership stops changing (max `max_iter` passes).

    Seeded with the top `pool_size` players by minutes. Players under `min_gp` games never
    enter the pool but are still valued against it.
    """
    gp = stats["GP"]
    eligible = (gp.isna() | (gp >= min_gp)).to_numpy()
    if not eligible.any():
        eligible = np.ones(len(stats), dtype=bool)
    pool = _top_mask(stats["MIN"].fillna(0), eligible, pool_size)
    weights = pd.Series({c: mults.get(c, 1.0) for c in cats}, dtype=float)

    iterations, converged = 0, False
    while True:
        z, rates = category_zscores(stats, pool, cats)
        total = (z * weights).sum(axis=1) if cats else pd.Series(0.0, index=stats.index)
        if iterations >= max_iter:
            break
        iterations += 1
        new_pool = _top_mask(total, eligible, pool_size)
        if np.array_equal(new_pool, pool):
            converged = True
            break
        pool = new_pool
    return PoolResult(z=z, total=total, pool=pool, iterations=iterations, converged=converged, league_rates=rates)


def fantasy_points(stats: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    fp = pd.Series(0.0, index=stats.index)
    for stat, w in weights.items():
        if stat in stats:
            fp = fp + w * stats[stat].fillna(0)
    return fp


def points_values(stats: pd.DataFrame, weights: dict[str, float], pool_size: int) -> pd.DataFrame:
    """FP and value over replacement, where replacement is the (pool_size + 1)th best FP."""
    fp = fantasy_points(stats, weights)
    ranked = fp.sort_values(ascending=False, kind="stable").to_numpy()
    if len(ranked) == 0:
        replacement = 0.0
    elif len(ranked) > pool_size:
        replacement = float(ranked[pool_size])
    else:
        replacement = float(ranked[-1])
    return pd.DataFrame({"FP": fp, "FP_value": fp - replacement, "FP_replacement": replacement})


def custom_rank(values: pd.Series, names: pd.Series) -> pd.Series:
    """1 = best. Ties broken by name so ranks are stable across reruns."""
    order = pd.DataFrame({"v": values.fillna(-np.inf), "n": names.astype(str)}).sort_values(
        ["v", "n"], ascending=[False, True], kind="stable").index
    return pd.Series(np.arange(1, len(order) + 1), index=order).reindex(values.index)


def adp_flags(adp: pd.Series, rank: pd.Series, min_gap: float = 3.0, pct: float = 0.15) -> pd.DataFrame:
    """delta = ADP - custom rank. Positive = I like him earlier than the market (value).

    STEAL if delta >= max(min_gap, pct*ADP); REACH if delta <= -max(min_gap, pct*ADP).
    """
    adp = pd.to_numeric(adp, errors="coerce")
    delta = adp - rank
    threshold = np.maximum(min_gap, pct * adp)
    flag = np.where(delta >= threshold, "STEAL", np.where(delta <= -threshold, "REACH", ""))
    flag = np.where(adp.isna(), "", flag)
    return pd.DataFrame({"delta": delta, "flag": flag}, index=adp.index)


def likely_gone(adp: pd.Series, next_pick: int | None) -> pd.Series:
    """True when the market usually takes the player before my next pick."""
    adp = pd.to_numeric(adp, errors="coerce")
    if next_pick is None:
        return pd.Series(False, index=adp.index)
    return (adp < next_pick).fillna(False).astype(bool)


def valuate(players: pd.DataFrame, s: ValuationSettings, adp_col: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Value every player. Returns (players + value columns, info about the pool/run).

    Added columns: Z_<cat> per active category, Z_total (or FP, FP_value for points), value,
    rank, in_pool, FG%/FT% (derived from makes/attempts), and ADP delta/flag when adp_col is set.
    """
    stats = apply_stat_mode(players, s.stat_mode)
    out = players.copy()
    info: dict = {"pool_size": s.pool_size, "stat_mode": s.stat_mode}
    if s.is_points:
        pv = points_values(stats, dict(s.points_weights), s.pool_size)
        out["FP"] = pv["FP"]
        out["FP_value"] = pv["FP_value"]
        out["value"] = pv["FP_value"]
        out["in_pool"] = custom_rank(pv["FP"], players["name"]) <= s.pool_size
        info.update(replacement_fp=float(pv["FP_replacement"].iloc[0]) if len(pv) else 0.0)
    else:
        cats = [c for c in s.categories if c in CATEGORY_TEMPLATES[s.template]]
        res = iterate_pool(stats, cats, multipliers(cats, s.punts), s.pool_size, s.min_gp)
        for c in cats:
            out[f"Z_{c}"] = res.z[c]
        out["Z_total"] = res.total
        out["value"] = res.total
        out["in_pool"] = res.pool
        info.update(categories=cats, punts=list(s.punts), iterations=res.iterations,
                    converged=res.converged, league_rates=res.league_rates)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["FG%"] = np.where(stats["FGA"] > 0, stats["FGM"] / stats["FGA"], np.nan)
        out["FT%"] = np.where(stats["FTA"] > 0, stats["FTM"] / stats["FTA"], np.nan)
    out["rank"] = custom_rank(out["value"], players["name"])
    if adp_col and adp_col in out:
        flags = adp_flags(out[adp_col], out["rank"], s.steal_min_gap, s.steal_pct)
        out["adp"] = pd.to_numeric(out[adp_col], errors="coerce")
        out["delta"] = flags["delta"]
        out["flag"] = flags["flag"]
    else:
        out["adp"] = np.nan
        out["delta"] = np.nan
        out["flag"] = ""
    return out, info
