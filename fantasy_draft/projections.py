"""2026-27 projections from real box scores plus current rosters (pure pandas, no Streamlit/IO).

How a player's line is built:
1. Baseline skill: the last three seasons, weighted 6/3/1 toward the most recent, per 100 possessions
   (so a fast team's numbers don't look better than they are). Lightly regressed toward the league
   average for the player's position group, then adjusted for age (young players improve, older
   ones decline a little).
2. Minutes: mostly last season's role, nudged toward 24 a game (heavy loads rarely repeat). Each
   2026-27 roster has 240 minutes a game: a roster more crowded than a typical team's trims bench
   minutes first, drafted rookies get minutes reserved. The backtest shows roster-based minute
   shifts are a weak signal, so only a quarter of that shift is applied.
3. Ball share: a team's players can only use 100% of its possessions. If the roster's combined
   usage is over budget (several ball-dominant players), everyone gives up the same number of usage
   points, so stars keep most of their shots and role players lose a bigger share; a team under
   budget hands out touches the same way. A player losing touches gets a little more efficient. Assists and rebounds get a
   milder version of the same treatment against the team's style (ball movement, rebounding).
4. Team style: pace (possessions per game) scales counting stats, and a shooter joining a heavy
   three-point team takes more threes (and vice versa).

Every adjustment is recorded in plain English in the `proj_notes` column.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from schema import NBA_TEAMS, normalize_team

REGULAR_SEASON = 2
SEASON_GAMES = 82
PER100 = ["FGA", "3PA", "FTA", "OREB", "DREB", "AST", "STL", "BLK", "TO"]


@dataclass(frozen=True)
class ProjectionParams:
    # Defaults tuned by backtest_projections.py on 2024-25 and 2025-26 (kept only if they helped both).
    season_weights: tuple[float, ...] = (6.0, 3.0, 1.0)   # most recent season first
    regress_minutes: float = 100.0      # league-average minutes blended into every rate
    pct_prior: tuple[float, float, float] = (60.0, 50.0, 30.0)  # 2PA, 3PA, FTA of league-average shooting
    team_context: bool = True           # False = baseline only (no roster/usage/style effects)
    age_effects: bool = True            # False = no aging curve (for backtests)
    usage_alpha: float = 1.0            # how much of a usage squeeze is passed on (0 = none, 1 = all)
    usage_gamma: float = -1.0           # -1: everyone gives up the same usage points; 0: same share
    assist_alpha: float = 0.25
    rebound_alpha: float = 0.25
    usage_eff_slope: float = 0.002      # 2P% gained per usage point given up
    three_beta: float = 0.35            # how strongly a player's 3PA share follows his team's system
    pace_regress: float = 0.3           # team pace regresses this much toward league average
    style_regress: float = 0.5          # team assist/rebound/3PA style regresses this much
    minutes_cap: float = 37.0
    minutes_weights: tuple[float, ...] = (3.0, 1.0, 0.0)  # season weights for minutes per game
    minutes_mode: str = "room"          # room: bench absorbs roster changes; proportional; none
    minutes_blend: float = 0.25         # share of the roster-based minutes change applied (weak signal)
    minutes_relative: bool = True       # compare each roster's minutes crunch to the median team's
    minutes_regress: float = 0.1        # big minutes loads shrink this much toward minutes_mean
    minutes_mean: float = 24.0
    rookie_minutes: tuple[tuple[int, float], ...] = ((3, 28.0), (10, 22.0), (20, 16.0), (30, 11.0), (60, 4.0))
    rookie_usage: float = 19.0
    availability_prior: float = 0.85
    availability_regress: float = 0.3


# ── Game logs with team context ───────────────────────────────────────────────

def game_logs(player_box: pd.DataFrame, team_box: pd.DataFrame) -> pd.DataFrame:
    """Regular-season player games joined to their team's and opponent's totals for that game."""
    pb = player_box[(player_box["season_type"] == REGULAR_SEASON) & (player_box["did_not_play"] != True)  # noqa: E712
                    & (player_box["minutes"].fillna(0) > 0) & player_box["athlete_id"].notna()].copy()
    pb["team"] = pb["team_abbreviation"].map(normalize_team)
    pb = pb[pb["team"].isin(NBA_TEAMS)]
    pb["athlete_id"] = pb["athlete_id"].astype("int64")
    tb = team_box[team_box["season_type"] == REGULAR_SEASON].copy()
    tb["team"] = tb["team_abbreviation"].map(normalize_team)
    tb = tb[tb["team"].isin(NBA_TEAMS)]
    tb = tb.rename(columns={
        "field_goals_made": "tFGM", "field_goals_attempted": "tFGA", "free_throws_attempted": "tFTA",
        "three_point_field_goals_attempted": "t3PA", "offensive_rebounds": "tOREB", "total_rebounds": "tREB",
        "total_turnovers": "tTO", "assists": "tAST"})
    cols = ["game_id", "team", "tFGM", "tFGA", "tFTA", "t3PA", "tOREB", "tREB", "tTO", "tAST"]
    tb = tb[cols].copy()
    for c in cols[2:]:
        tb[c] = pd.to_numeric(tb[c], errors="coerce").fillna(0.0)
    opp = tb.rename(columns={c: "o" + c[1:] for c in cols[2:]}).rename(columns={"team": "opp"})
    games = tb.merge(opp, on="game_id")
    games = games[games["team"] != games["opp"]]
    minutes = pb.groupby(["game_id", "team"])["minutes"].sum().rename("tMIN").reset_index()
    games = games.merge(minutes, on=["game_id", "team"])
    games["game_min"] = (games["tMIN"] / 5).clip(lower=48)
    poss_t = games["tFGA"] + 0.44 * games["tFTA"] - games["tOREB"] + games["tTO"]
    poss_o = games["oFGA"] + 0.44 * games["oFTA"] - games["oOREB"] + games["oTO"]
    games["poss"] = (poss_t + poss_o) / 2
    games["pace48"] = games["poss"] * 48 / games["game_min"]
    return pb.merge(games, on=["game_id", "team"], how="inner")


def team_styles(logs: pd.DataFrame) -> pd.DataFrame:
    """Per team: pace (possessions per 48), 3PA share of FGA, assists and rebounds per 100 possessions."""
    g = logs.drop_duplicates(["game_id", "team"])
    t = g.groupby("team").agg(poss=("poss", "sum"), pace48=("pace48", "mean"), fga=("tFGA", "sum"),
                              tpa=("t3PA", "sum"), ast=("tAST", "sum"), reb=("tREB", "sum"), games=("game_id", "nunique"))
    t["three_rate"] = t["tpa"] / t["fga"]
    t["ast100"] = 100 * t["ast"] / t["poss"]
    t["reb100"] = 100 * t["reb"] / t["poss"]
    return t[["pace48", "three_rate", "ast100", "reb100", "games"]]


def season_lines(logs: pd.DataFrame) -> pd.DataFrame:
    """One row per player for a season: totals, possessions on court, usage inputs, team context."""
    d = logs.copy()
    d["on_poss"] = d["pace48"] * d["minutes"] / 48
    d["usg_num"] = (d["field_goals_attempted"] + 0.44 * d["free_throws_attempted"] + d["turnovers"]) * d["game_min"]
    d["usg_den"] = d["minutes"] * (d["tFGA"] + 0.44 * d["tFTA"] + d["tTO"])
    d["three_ctx"] = d["t3PA"] / d["tFGA"].replace(0, np.nan) * d["minutes"]
    d["pace_ctx"] = d["pace48"] * d["minutes"]
    d = d.sort_values("game_date")
    g = d.groupby("athlete_id")
    out = pd.DataFrame({
        "name": g["athlete_display_name"].last(),
        "pos_group": g["athlete_position_abbreviation"].last(),
        "last_team": g["team"].last(),
        "GP": g["game_id"].nunique(),
        "MIN": g["minutes"].sum(),
        "poss": g["on_poss"].sum(),
        "FGM": g["field_goals_made"].sum(), "FGA": g["field_goals_attempted"].sum(),
        "3PM": g["three_point_field_goals_made"].sum(), "3PA": g["three_point_field_goals_attempted"].sum(),
        "FTM": g["free_throws_made"].sum(), "FTA": g["free_throws_attempted"].sum(),
        "OREB": g["offensive_rebounds"].sum(), "DREB": g["defensive_rebounds"].sum(),
        "AST": g["assists"].sum(), "STL": g["steals"].sum(), "BLK": g["blocks"].sum(), "TO": g["turnovers"].sum(),
        "usg_num": g["usg_num"].sum(), "usg_den": g["usg_den"].sum(),
        "pace_ctx": g["pace_ctx"].sum() / g["minutes"].sum(),
        "three_ctx": g["three_ctx"].sum() / g["minutes"].sum(),
    })
    out["team_games"] = d.groupby("team")["game_id"].nunique().reindex(out["last_team"]).to_numpy()
    out["pos_group"] = out["pos_group"].map(_pos_group)
    return out


def _pos_group(p) -> str:
    s = str(p or "").upper()
    if s in ("C", "F-C", "C-F", "PF/C"):
        return "C"
    if s in ("F", "SF", "PF", "G-F", "F-G"):
        return "F"
    return "G"


# ── Baseline skill (multi-season, regressed, age-adjusted) ────────────────────

def ages_on(rosters: list[pd.DataFrame], when: pd.Timestamp) -> pd.Series:
    """Age in years on `when`, by athlete_id, from roster files' birth dates (first file wins)."""
    r = pd.concat(rosters, ignore_index=True).dropna(subset=["athlete_id"])
    r = r.drop_duplicates("athlete_id", keep="first")
    dob = pd.to_datetime(r["date_of_birth"], errors="coerce", utc=True).dt.tz_localize(None)
    age = (when - dob).dt.days / 365.25
    return pd.Series(age.to_numpy(), index=r["athlete_id"].astype("int64").to_numpy(), name="age")


def age_skill_factor(age: float) -> float:
    if not np.isfinite(age):
        return 1.0
    if age < 26:
        return 1 + 0.012 * (26 - age)
    if age > 30:
        return 1 - 0.01 * (age - 30)
    return 1.0


def age_minutes_factor(age: float) -> float:
    if not np.isfinite(age):
        return 1.0
    if age < 24:
        return min(1.08, 1 + 0.02 * (24 - age))
    if age > 31:
        return max(0.75, 1 - 0.015 * (age - 31))
    return 1.0


def baseline(seasons: list[pd.DataFrame], ages: pd.Series, params: ProjectionParams = ProjectionParams()) -> pd.DataFrame:
    """Weighted, regressed, age-adjusted rates. `seasons` = season_lines(), most recent first."""
    w = np.asarray(params.season_weights, dtype=float)
    w = w / w.sum()  # weights sum to 1, so regress_minutes is in real minutes of a full sample
    frames = []
    for i, s in enumerate(seasons[: len(w)]):
        f = s.copy()
        f["athlete_id"] = f.index.to_numpy()
        f = f.reset_index(drop=True)
        f["w"] = w[i]
        f["season_idx"] = i
        frames.append(f)
    allp = pd.concat(frames, ignore_index=True)
    allp["2PA"] = allp["FGA"] - allp["3PA"]
    allp["2PM"] = allp["FGM"] - allp["3PM"]
    wcols = PER100 + ["poss", "MIN", "GP", "2PA", "2PM", "3PM", "FTM", "usg_num", "usg_den"]
    for c in wcols:
        allp["w_" + c] = allp[c] * allp["w"]
    allp["w_games"] = allp["team_games"].fillna(SEASON_GAMES) * allp["w"]
    mw = params.minutes_weights
    allp["mw"] = [mw[i] if i < len(mw) else 0.0 for i in allp["season_idx"]]
    allp["mw_MIN"] = allp["MIN"] * allp["mw"]
    allp["mw_GP"] = allp["GP"] * allp["mw"]
    allp["w_pace"] = allp["pace_ctx"] * allp["MIN"] * allp["w"]
    allp["w_three"] = allp["three_ctx"] * allp["MIN"] * allp["w"]
    allp = allp[allp["w"] > 0]  # seasons with zero weight carry no information
    latest = allp.sort_values("season_idx").groupby("athlete_id").first()
    agg = allp.groupby("athlete_id")[[c for c in allp.columns if c.startswith(("w_", "mw_"))]].sum()

    # League means by position group (from all player-seasons used), per 100 possessions.
    grp = allp.assign(pg=allp["pos_group"]).groupby("pg")
    means = pd.DataFrame({c: grp["w_" + c].sum() / grp["w_poss"].sum() * 100 for c in PER100})
    lg_2p = allp["w_2PM"].sum() / allp["w_2PA"].sum()
    lg_3p = allp["w_3PM"].sum() / allp["w_3PA"].sum()
    lg_ft = allp["w_FTM"].sum() / allp["w_FTA"].sum()
    lg_usg = 100 * allp["w_usg_num"].sum() / allp["w_usg_den"].sum()
    lg_pace = (allp["w_pace"].sum() / (allp["MIN"] * allp["w"]).sum())

    out = pd.DataFrame(index=agg.index)
    out["name"] = latest["name"]
    out["pos_group"] = latest["pos_group"]
    out["last_team"] = latest["last_team"]
    out["seasons_used"] = allp.groupby("athlete_id")["season_idx"].count()
    out["played_last_season"] = latest["season_idx"] == 0
    r_poss = params.regress_minutes * lg_pace / 48
    for c in PER100:
        mean = means[c].reindex(out["pos_group"]).to_numpy()
        out[c + "_100"] = 100 * (agg["w_" + c] + r_poss * mean / 100) / (agg["w_poss"] + r_poss)
    k2, k3, kf = params.pct_prior
    out["P2"] = (agg["w_2PM"] + k2 * lg_2p) / (agg["w_2PA"] + k2)
    out["P3"] = (agg["w_3PM"] + k3 * lg_3p) / (agg["w_3PA"] + k3)
    out["PFT"] = (agg["w_FTM"] + kf * lg_ft) / (agg["w_FTA"] + kf)
    usg_raw = 100 * agg["w_usg_num"] / agg["w_usg_den"].replace(0, np.nan)
    min_w = agg["w_MIN"]
    out["USG"] = (usg_raw.fillna(lg_usg) * min_w + lg_usg * params.regress_minutes) / (min_w + params.regress_minutes)
    out["MPG_base"] = (agg["mw_MIN"] / agg["mw_GP"].replace(0, np.nan)).fillna(agg["w_MIN"] / agg["w_GP"])
    avail = (agg["w_GP"] / agg["w_games"]).clip(0, 1)
    out["avail"] = (1 - params.availability_regress) * avail + params.availability_regress * params.availability_prior
    out["pace_old"] = agg["w_pace"] / agg["w_MIN"]
    out["three_old"] = agg["w_three"] / agg["w_MIN"]

    age = pd.to_numeric(ages.reindex(out.index), errors="coerce")
    out["age"] = age
    skill = age.map(age_skill_factor).fillna(1.0) if params.age_effects else pd.Series(1.0, index=out.index)
    mins = age.map(age_minutes_factor).fillna(1.0) if params.age_effects else pd.Series(1.0, index=out.index)
    for c in ["FGA_100", "3PA_100", "FTA_100", "OREB_100", "DREB_100", "AST_100", "STL_100", "BLK_100", "TO_100"]:
        out[c] = out[c] * skill
    out["USG"] = out["USG"] * skill
    out["MPG_base"] = out["MPG_base"] * mins
    out["MPG_base"] = (out["MPG_base"] + params.minutes_regress * (params.minutes_mean - out["MPG_base"])) \
        .clip(upper=params.minutes_cap)
    older = (age - 32).clip(lower=0).fillna(0)
    out["avail"] = (out["avail"] - 0.01 * older).clip(0.3, 0.97)
    out["age_skill"] = skill
    out.attrs["league"] = {"pace": lg_pace, "usg": lg_usg, "P2": lg_2p, "P3": lg_3p, "PFT": lg_ft}
    return out


# ── Team context on the target rosters ────────────────────────────────────────

def rookie_reserve(pick, params: ProjectionParams = ProjectionParams()) -> float:
    try:
        pick = float(pick)  # some draft files store pick numbers as text
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(pick):
        return 0.0
    for upto, minutes in params.rookie_minutes:
        if pick <= upto:
            return minutes
    return 0.0


def _allocate_minutes(m: np.ndarray, a: np.ndarray, available: float, cap: float) -> np.ndarray:
    """Shift minutes so sum(m * a) == available; bench players absorb most of the change."""
    room = np.clip(40 - m, 4, None)  # low-minute players have the most room to move
    out = m.copy()
    for _ in range(25):
        gap = available - float(np.sum(out * a))
        if abs(gap) < 0.05:
            break
        free = (out < cap) if gap > 0 else (out > 0.35 * m)
        if not free.any():
            break
        step = gap / float(np.sum(room[free] * a[free]))
        out[free] = out[free] + step * room[free]
        out = np.clip(out, 0.35 * m, np.minimum(cap, np.maximum(m * 1.35, m + 4)))
    return out


def _usage_factors(usg: np.ndarray, mins: np.ndarray, k: float, params: ProjectionParams) -> np.ndarray:
    """Per-player shot-volume multipliers that move the roster's total usage by k ** alpha.

    usage_gamma = -1 (default): every player gives up the same number of usage points, so a star at
    30% usage loses a smaller share of their shots than a role player at 15%. The backtest favors this
    slightly over gamma = 0 (everyone loses the same share); gamma > 0 would hit the stars hardest.
    """
    total = float(np.sum(usg * mins))
    weight = usg ** (1 + params.usage_gamma)
    denom = float(np.sum(weight * mins))
    if total <= 0 or denom <= 0:
        return np.ones_like(usg)
    c = total * (k ** params.usage_alpha - 1) / denom
    return np.clip(1 + c * usg ** params.usage_gamma, 0.75, 1.2)


def project(base: pd.DataFrame, rosters: pd.DataFrame, styles: pd.DataFrame,
            rookies: pd.DataFrame | None = None, params: ProjectionParams = ProjectionParams()) -> pd.DataFrame:
    """Per-game projections for every rostered player with NBA history.

    rosters: athlete_id, team (2026-27); rookies: team, pick (drafted players without NBA stats).
    styles: team_styles() of the most recent season.
    """
    lg = base.attrs["league"]
    lg_style = styles.mean(numeric_only=True)
    r = rosters.drop_duplicates("athlete_id", keep="last").set_index("athlete_id")
    df = base.join(r[["team"]], how="left")
    # Not on any roster: keep last season's regulars as unsigned free agents, drop the rest.
    df = df[df["team"].notna() | (df["played_last_season"] & (df["MPG_base"] * df["avail"] * SEASON_GAMES >= 600))]
    df["team"] = df["team"].fillna("FA").map(normalize_team)
    df["moved"] = df["team"] != df["last_team"]
    reserve = (rookies.groupby("team")["pick"].apply(lambda s: sum(rookie_reserve(p, params) for p in s))
               if rookies is not None and len(rookies) else pd.Series(dtype=float))
    # Roster files list 18-21 players a team (two-ways, camp invites), so every team's expected minutes
    # overshoot 240. Only crowding relative to a typical team counts: the median team is left as is.
    teams = df[df["team"] != "FA"]
    need = (teams["MPG_base"] * teams["avail"]).groupby(teams["team"]).sum()
    need = need / (240.0 - reserve.reindex(need.index).fillna(0.0))
    typical = float(need.median()) if len(need) else 1.0
    rows = []
    for team, grp in df.groupby("team"):
        g = grp.copy()
        a = g["avail"].to_numpy()
        m = g["MPG_base"].to_numpy()
        rk = float(reserve.get(team, 0.0)) if team != "FA" else 0.0
        if params.team_context and team != "FA" and params.minutes_mode != "none":
            available = (240.0 - rk) * (typical if params.minutes_relative else 1.0)
            if params.minutes_mode == "proportional":
                total = float(np.sum(m * a))
                alloc = np.minimum(m * available / total, params.minutes_cap) if total > 0 else m
            else:
                alloc = _allocate_minutes(m, a, available, params.minutes_cap)
            m_new = m + params.minutes_blend * (alloc - m)
        else:
            m_new = m * (0.6 if team == "FA" else 1.0)
        g["MPG"] = m_new
        st = styles.loc[team] if team in styles.index else lg_style
        pace_new = (1 - params.pace_regress) * st["pace48"] + params.pace_regress * lg["pace"]
        g["pace_new"] = pace_new
        if params.team_context and team != "FA":
            mins_a = m_new * a
            # Every minute, the five players on the floor use 100% of possessions: sum(USG x minutes) = 4800
            # per 240 minutes. Rookies' reserved minutes take their share first.
            budget = 4800 * (np.sum(mins_a) + rk) / 240 - params.rookie_usage * rk
            usage_load = float(np.sum(g["USG"] * mins_a)) / budget if budget > 0 else 1.0
            k_usg = (1.0 / usage_load) if usage_load > 0 else 1.0
            ast_target = (1 - params.style_regress) * st["ast100"] + params.style_regress * lg_style["ast100"]
            reb_target = (1 - params.style_regress) * st["reb100"] + params.style_regress * lg_style["reb100"]
            share = np.sum(mins_a) / 240  # fraction of the team's minutes these players cover
            ast_now = float(np.sum(g["AST_100"] * mins_a)) / 48
            reb_now = float(np.sum((g["OREB_100"] + g["DREB_100"]) * mins_a)) / 48
            k_ast = (ast_target * share / ast_now) if ast_now > 0 else 1.0
            k_reb = (reb_target * share / reb_now) if reb_now > 0 else 1.0
            three_new = (1 - params.style_regress) * st["three_rate"] + params.style_regress * lg_style["three_rate"]
        else:
            usage_load, k_usg, k_ast, k_reb, three_new = 1.0, 1.0, 1.0, 1.0, np.nan
        g["team_usage_load"] = usage_load
        g["usg_factor"] = _usage_factors(g["USG"].to_numpy(), m_new * a, k_usg, params) if params.team_context else 1.0
        g["ast_factor"] = np.clip(k_ast ** params.assist_alpha, 0.8, 1.2) if params.team_context else 1.0
        g["reb_factor"] = np.clip(k_reb ** params.rebound_alpha, 0.85, 1.15) if params.team_context else 1.0
        if params.team_context and team != "FA" and np.isfinite(three_new):
            three_old_reg = (1 - params.style_regress) * g["three_old"] + params.style_regress * lg_style["three_rate"]
            g["three_factor"] = np.clip((three_new / three_old_reg) ** params.three_beta, 0.85, 1.15)
        else:
            g["three_factor"] = 1.0
        if not params.team_context:
            g["pace_new"] = g["pace_old"]
        # Usage competition: who on this roster eats the most possessions.
        g["usage_mins"] = g["USG"] * m_new * a
        top = g.sort_values("usage_mins", ascending=False)
        g["top_teammates"] = [", ".join(top.loc[top.index != i, "name"].head(3)) for i in g.index]
        rows.append(g)
    out = pd.concat(rows)
    return _per_game(out, lg, params)


def _per_game(g: pd.DataFrame, lg: dict, params: ProjectionParams) -> pd.DataFrame:
    poss = g["pace_new"] * g["MPG"] / 48
    usg_f = g["usg_factor"]
    fga = g["FGA_100"] * usg_f * poss / 100
    tpa = np.minimum(g["3PA_100"] * usg_f * g["three_factor"] * poss / 100, fga * 0.95)
    two_pa = fga - tpa
    d_usg = g["USG"] * (usg_f - 1)  # usage points gained (+) or lost (-)
    p2 = (g["P2"] - params.usage_eff_slope * d_usg).clip(0.3, 0.75)
    p3 = (g["P3"] - params.usage_eff_slope / 2 * d_usg).clip(0.2, 0.5)
    fta = g["FTA_100"] * usg_f * poss / 100
    out = pd.DataFrame(index=g.index)
    out["name"] = g["name"]
    out["team"] = g["team"]
    out["pos_group"] = g["pos_group"]
    out["GP"] = (SEASON_GAMES * g["avail"]).round()
    out["MIN"] = g["MPG"]
    out["FGA"] = fga
    out["FGM"] = two_pa * p2 + tpa * p3
    out["3PM"] = tpa * p3
    out["FTA"] = fta
    out["FTM"] = fta * g["PFT"]
    out["PTS"] = 2 * out["FGM"] + out["3PM"] + out["FTM"]
    out["REB"] = (g["OREB_100"] + g["DREB_100"]) * g["reb_factor"] * poss / 100
    out["AST"] = g["AST_100"] * g["ast_factor"] * poss / 100
    out["STL"] = g["STL_100"] * poss / 100
    out["BLK"] = g["BLK_100"] * poss / 100
    out["TO"] = g["TO_100"] * usg_f * poss / 100
    for c in ["USG", "usg_factor", "ast_factor", "reb_factor", "three_factor", "team_usage_load", "MPG_base",
              "pace_old", "pace_new", "last_team", "moved", "age", "age_skill", "seasons_used",
              "played_last_season", "top_teammates"]:
        out[c] = g[c]
    out["proj_notes"] = [notes(row) for _, row in out.iterrows()]
    return out


def notes(r) -> str:
    """Plain-English list of the adjustments that moved this player the most."""
    parts = []
    if r["moved"] and r["team"] != "FA":
        parts.append(f"new team: {r['last_team']}→{r['team']}")
    if r["team"] == "FA":
        parts.append("unsigned: minutes cut 40%")
    if abs(r["usg_factor"] - 1) >= 0.03:
        how = "crowded" if r["usg_factor"] < 1 else "more touches"
        parts.append(f"shots ×{r['usg_factor']:.2f} ({how}: {r['top_teammates']})")
    if abs(r["MIN"] - r["MPG_base"]) >= 1.5:
        parts.append(f"minutes {r['MPG_base']:.0f}→{r['MIN']:.0f}")
    if abs(r["ast_factor"] - 1) >= 0.05:
        parts.append(f"assists ×{r['ast_factor']:.2f}")
    if abs(r["reb_factor"] - 1) >= 0.05:
        parts.append(f"rebounds ×{r['reb_factor']:.2f}")
    pace = r["pace_new"] / r["pace_old"] - 1 if r["pace_old"] else 0
    if abs(pace) >= 0.02:
        parts.append(f"pace {pace:+.0%}")
    if abs(r["three_factor"] - 1) >= 0.04:
        parts.append(f"3PT system ×{r['three_factor']:.2f}")
    if abs(r["age_skill"] - 1) >= 0.03:
        parts.append(f"age {r['age']:.0f}: {r['age_skill'] - 1:+.0%}")
    if not r["played_last_season"]:
        parts.append("missed 2025-26: built from earlier seasons")
    return "; ".join(parts)
