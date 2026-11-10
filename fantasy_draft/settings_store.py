"""App settings: defaults, the versioned migration chain, and atomic load/save (no Streamlit).

Settings are one flat JSON object in data/settings.json. Its `settings_version` field is a small
state machine: a saved file moves forward through MIGRATIONS one version at a time, and only
values the user never changed are rewritten.

    v1 --(v2: bundled projections become the default source)--> v2
    v2 --(v3: set up for the user's 14-team Sleeper points league)--> v3   [current]

To change a default for existing users, add a migration: bump SETTINGS_VERSION, write a
function that takes the saved dict and returns it upgraded, and append it to MIGRATIONS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import valuation as val

SETTINGS_VERSION = 3
# The league this app is set up for (Sleeper, 14 teams, 15 rounds, picking 9th). Connect to Sleeper overrides it.
MY_LEAGUE = {"n_teams": 14, "n_rounds": 15, "my_slot": 9}
OLD_DEFAULT_LEAGUE = (12, 13, 1)  # (teams, rounds, slot) of drafts created before v3
OLD_DEFAULT_STARTERS = "PG, SG, SF, PF, C, G, F, UTIL x3"

DEFAULT_SETTINGS = {
    "settings_version": SETTINGS_VERSION,
    "source": "proj_2027",
    "adp_compare": "auto",
    "template": "points",
    "categories": list(val.CATEGORIES),
    "punts": [],
    "stat_mode": "games",
    "min_gp": 10,
    "steal_min_gap": 3.0,
    "steal_pct": 0.15,
    "points_weights": dict(val.SLEEPER_LEAGUE_POINTS),
    "starters": "PG, SG, G, SF, PF, F, C, UTIL x2",
    "use_sleeper_meta": True,
    "use_espn_meta": True,
    "use_ecr": False,
    "proj_mapping": None,
    "proj_season_totals": False,
    "adp_mapping": None,
    "adp_target": "adp_sleeper",
    "sleeper_draft_id": "",
    "sleeper_username": "",
    "sleeper_sync": False,
    "order": "My rank",
    "pos_filter": "All",
    "search_mode": "contains",
}


def _to_v2(s: dict) -> dict:
    """v2 made the bundled 2026-27 projections the default; move anyone still on the old default."""
    if s.get("source") == "last_season":
        s["source"] = "proj_2027"
    return s


def _to_v3(s: dict) -> dict:
    """v3 set the app up for the user's Sleeper league; only untouched old defaults are replaced."""
    if s.get("starters", OLD_DEFAULT_STARTERS) == OLD_DEFAULT_STARTERS:
        s["starters"] = DEFAULT_SETTINGS["starters"]
    if s.get("stat_mode", "per_game") == "per_game":
        s["stat_mode"] = "games"
    if s.get("template", "9-cat") == "9-cat" and s.get("points_weights", val.ESPN_DEFAULT_POINTS) == val.ESPN_DEFAULT_POINTS:
        s["template"] = "points"
        s["points_weights"] = dict(val.SLEEPER_LEAGUE_POINTS)
    return s


# (version reached, upgrade function), in order.
MIGRATIONS: list[tuple[int, Callable[[dict], dict]]] = [(2, _to_v2), (3, _to_v3)]


@dataclass
class Loaded:
    settings: dict
    applied: list[int] = field(default_factory=list)  # versions migrated through on this load

    @property
    def changed(self) -> bool:
        """The file is out of date and should be written back once."""
        return bool(self.applied)

    @property
    def moved_to_my_league(self) -> bool:
        """This load applied v3: an unused old-default draft should also move to MY_LEAGUE."""
        return 3 in self.applied


def migrate(saved: dict) -> Loaded:
    """Upgrade a saved settings dict to SETTINGS_VERSION and fill in defaults. Pure.

    An empty dict (no file yet) is a fresh install: defaults, nothing migrated.
    Unknown keys are dropped; missing keys take their default.
    """
    s = dict(saved)
    applied: list[int] = []
    if s:
        version = int(s.get("settings_version", 1))
        for target, upgrade in MIGRATIONS:
            if version < target:
                s = upgrade(s)
                s["settings_version"] = version = target
                applied.append(target)
    return Loaded({**DEFAULT_SETTINGS, **{k: v for k, v in s.items() if k in DEFAULT_SETTINGS}}, applied)


def load(path: Path) -> Loaded:
    try:
        saved = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    return migrate(saved if isinstance(saved, dict) else {})


def save(path: Path, settings: dict) -> None:
    """Atomic write (temp file + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=1))
    tmp.replace(path)
