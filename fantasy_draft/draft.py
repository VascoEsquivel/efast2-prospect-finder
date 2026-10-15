"""Snake draft state: pick order, entering/undoing picks, and crash-safe JSON persistence.

No Streamlit imports. The app keeps one DraftState in st.session_state and calls save_state()
after every mutation, so a browser refresh or crash loses nothing.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1


class DraftError(ValueError):
    """A pick that cannot be applied (player already drafted, slot filled, draft over...)."""


# ── Snake order ───────────────────────────────────────────────────────────────

def round_of(pick_no: int, n_teams: int) -> int:
    return math.ceil(pick_no / n_teams)


def team_for_pick(pick_no: int, n_teams: int) -> int:
    """Team (1..N) that owns overall pick `pick_no` (1-indexed) in a snake draft."""
    if pick_no < 1:
        raise ValueError("pick numbers start at 1")
    r = round_of(pick_no, n_teams)
    pos = pick_no - (r - 1) * n_teams
    return pos if r % 2 == 1 else n_teams + 1 - pos


def picks_for_team(team: int, n_teams: int, n_rounds: int) -> list[int]:
    """All overall pick numbers a team owns under plain snake order."""
    out = []
    for r in range(1, n_rounds + 1):
        pos = team if r % 2 == 1 else n_teams + 1 - team
        out.append((r - 1) * n_teams + pos)
    return out


# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class Pick:
    pick_no: int
    team: int
    player_id: str
    name: str
    positions: list[str] = field(default_factory=list)
    nba_team: str = ""
    source: str = "manual"  # manual | keeper | sleeper
    seq: int = 0            # entry order, used by undo
    ts: str = ""
    unmatched: bool = False  # True when the player isn't in the loaded dataset (e.g. Sleeper-only)


@dataclass
class DraftSettings:
    n_teams: int = 12
    n_rounds: int = 13
    my_slot: int = 1
    team_names: list[str] = field(default_factory=list)

    def team_name(self, team: int) -> str:
        if 0 < team <= len(self.team_names) and self.team_names[team - 1].strip():
            return self.team_names[team - 1].strip()
        return f"Team {team}"


@dataclass
class DraftState:
    settings: DraftSettings = field(default_factory=DraftSettings)
    picks: list[Pick] = field(default_factory=list)
    next_seq: int = 1
    created_at: str = ""
    updated_at: str = ""
    sleeper_draft_id: str = ""

    @property
    def total_picks(self) -> int:
        return self.settings.n_teams * self.settings.n_rounds

    def filled(self) -> dict[int, Pick]:
        return {p.pick_no: p for p in self.picks}

    def drafted_ids(self) -> set[str]:
        return {p.player_id for p in self.picks}

    def current_pick(self) -> int | None:
        """Lowest unfilled pick number, or None when the draft is complete."""
        filled = self.filled()
        for p in range(1, self.total_picks + 1):
            if p not in filled:
                return p
        return None

    def on_the_clock(self) -> int | None:
        cur = self.current_pick()
        return None if cur is None else team_for_pick(cur, self.settings.n_teams)

    def reopened_slot(self) -> bool:
        """True when the current pick sits before a later filled pick (a removed pick is being refilled)."""
        cur = self.current_pick()
        return cur is not None and any(p.pick_no > cur for p in self.picks)

    def team_picks(self, team: int) -> list[Pick]:
        return sorted((p for p in self.picks if p.team == team), key=lambda p: p.pick_no)

    def next_pick_for_team(self, team: int) -> int | None:
        """The team's next unfilled snake pick. If the team is on the clock now, the one after."""
        cur = self.current_pick()
        if cur is None:
            return None
        filled = self.filled()
        start = cur + 1 if team_for_pick(cur, self.settings.n_teams) == team else cur
        for p in picks_for_team(team, self.settings.n_teams, self.settings.n_rounds):
            if p >= start and p not in filled:
                return p
        return None


def new_state(settings: DraftSettings | None = None) -> DraftState:
    now = _now()
    return DraftState(settings=settings or DraftSettings(), created_at=now, updated_at=now)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Mutations (validate first, then mutate) ───────────────────────────────────

def make_pick(state: DraftState, player: dict, team: int | None = None, pick_no: int | None = None,
              source: str = "manual") -> Pick:
    """Draft `player` (needs player_id and name) at `pick_no` (default: current pick).

    `team` defaults to the snake owner of that pick; pass it to record traded picks or keepers.
    """
    pid = str(player["player_id"])
    if pid in state.drafted_ids():
        existing = next(p for p in state.picks if p.player_id == pid)
        raise DraftError(f"{player.get('name', pid)} was already drafted at pick #{existing.pick_no}")
    if pick_no is None:
        pick_no = state.current_pick()
        if pick_no is None:
            raise DraftError("the draft is complete")
    if not 1 <= pick_no <= state.total_picks:
        raise DraftError(f"pick #{pick_no} is outside 1..{state.total_picks}")
    if pick_no in state.filled():
        raise DraftError(f"pick #{pick_no} is already filled by {state.filled()[pick_no].name}")
    n = state.settings.n_teams
    team = team_for_pick(pick_no, n) if team is None else int(team)
    if not 1 <= team <= n:
        raise DraftError(f"team {team} is outside 1..{n}")

    pick = Pick(
        pick_no=pick_no, team=team, player_id=pid, name=str(player.get("name", pid)),
        positions=list(player.get("positions") or []), nba_team=str(player.get("team") or ""),
        source=source, seq=state.next_seq, ts=_now(), unmatched=bool(player.get("unmatched", False)),
    )
    state.picks.append(pick)
    state.picks.sort(key=lambda p: p.pick_no)
    state.next_seq += 1
    state.updated_at = pick.ts
    return pick


def undo_last(state: DraftState) -> Pick | None:
    """Remove the most recently *entered* pick."""
    if not state.picks:
        return None
    last = max(state.picks, key=lambda p: p.seq)
    state.picks.remove(last)
    state.updated_at = _now()
    return last


def remove_pick(state: DraftState, pick_no: int) -> Pick | None:
    """Remove a specific pick; its slot reopens and becomes the current pick."""
    target = state.filled().get(pick_no)
    if target is None:
        return None
    state.picks.remove(target)
    state.updated_at = _now()
    return target


def reset(state: DraftState) -> DraftState:
    return new_state(DraftSettings(**asdict(state.settings)))


# ── Persistence ───────────────────────────────────────────────────────────────

def to_dict(state: DraftState) -> dict:
    d = asdict(state)
    d["schema_version"] = SCHEMA_VERSION
    return d


def from_dict(d: dict) -> DraftState:
    if d.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported draft file version {d.get('schema_version')!r}")
    settings = DraftSettings(**d["settings"])
    picks = [Pick(**p) for p in d.get("picks", [])]
    state = DraftState(settings=settings, picks=sorted(picks, key=lambda p: p.pick_no),
                       next_seq=int(d.get("next_seq", len(picks) + 1)),
                       created_at=d.get("created_at", ""), updated_at=d.get("updated_at", ""),
                       sleeper_draft_id=d.get("sleeper_draft_id", ""))
    state.next_seq = max([state.next_seq] + [p.seq + 1 for p in picks])
    return state


def save_state(state: DraftState, path: Path) -> None:
    """Atomic write: a crash mid-save leaves the previous file intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(to_dict(state), f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_state(path: Path) -> DraftState | None:
    """Load a saved draft, or None if there isn't one.

    A corrupt file is moved aside (never deleted) and DraftError explains where it went.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        return from_dict(json.loads(path.read_text()))
    except (ValueError, KeyError, TypeError) as exc:
        aside = path.with_name(f"{path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        os.replace(path, aside)
        raise DraftError(f"saved draft was unreadable ({exc}); moved it to {aside.name}") from exc


def archive_state(path: Path) -> Path | None:
    """Move the saved draft aside (for 'start fresh'). Returns the archive path."""
    path = Path(path)
    if not path.exists():
        return None
    dest = path.with_name(f"{path.stem}.{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    os.replace(path, dest)
    return dest


def to_backup(state: DraftState, settings: dict | None = None) -> bytes:
    """A downloadable snapshot of the draft (and optionally the app settings)."""
    return json.dumps({"kind": "draft-assistant-backup", "draft": to_dict(state), "settings": settings or {}},
                      indent=1).encode()


def from_backup(data: bytes | str) -> tuple[DraftState, dict]:
    """Parse a file made by to_backup (or a bare draft_state.json). Raises DraftError if it isn't one."""
    try:
        payload = json.loads(data)
        if isinstance(payload, dict) and payload.get("kind") == "draft-assistant-backup":
            return from_dict(payload["draft"]), dict(payload.get("settings") or {})
        return from_dict(payload), {}
    except (ValueError, KeyError, TypeError) as exc:
        raise DraftError(f"not a draft backup file ({exc})") from exc


# ── Sleeper live sync ─────────────────────────────────────────────────────────

def sleeper_pick_player(sp: dict) -> dict:
    """Fallback player record built from a Sleeper pick's metadata (used when it can't be matched)."""
    md = sp.get("metadata") or {}
    name = " ".join(x for x in [md.get("first_name", ""), md.get("last_name", "")] if x).strip()
    pos = md.get("position") or ""
    return {
        "player_id": f"sl:{sp.get('player_id')}",
        "name": name or f"Sleeper player {sp.get('player_id')}",
        "positions": [p for p in str(pos).replace(",", "/").split("/") if p],
        "team": md.get("team") or "",
        "unmatched": True,
    }


def apply_sleeper_picks(state: DraftState, sleeper_picks: list[dict],
                        resolve: Callable[[str], dict | None]) -> tuple[list[Pick], list[str]]:
    """Apply picks from GET /v1/draft/{id}/picks. Sleeper is treated as authoritative.

    `resolve(sleeper_player_id)` returns a dataset player dict or None. Idempotent: picks that
    already match are skipped. Returns (new picks, human-readable messages).
    """
    applied: list[Pick] = []
    messages: list[str] = []
    for sp in sorted(sleeper_picks, key=lambda x: int(x.get("pick_no") or 0)):
        try:
            pick_no = int(sp["pick_no"])
            team = int(sp.get("draft_slot") or team_for_pick(pick_no, state.settings.n_teams))
        except (KeyError, TypeError, ValueError):
            messages.append(f"skipped malformed Sleeper pick: {sp!r}"[:200])
            continue
        if not sp.get("player_id") or pick_no > state.total_picks:
            continue
        player = resolve(str(sp["player_id"])) or sleeper_pick_player(sp)
        pid = str(player["player_id"])

        existing = state.filled().get(pick_no)
        if existing is not None:
            if existing.player_id == pid:
                continue
            messages.append(f"pick #{pick_no}: replaced {existing.name} with Sleeper's {player['name']}")
            state.picks.remove(existing)
        dup = next((p for p in state.picks if p.player_id == pid), None)
        if dup is not None:
            messages.append(f"{player['name']} moved from pick #{dup.pick_no} to Sleeper's pick #{pick_no}")
            state.picks.remove(dup)
        applied.append(make_pick(state, player, team=team, pick_no=pick_no, source="sleeper"))
        if player.get("unmatched"):
            messages.append(f"pick #{pick_no}: {player['name']} is not in the loaded dataset")
    return applied, messages
