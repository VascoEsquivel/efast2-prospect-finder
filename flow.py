"""The app's state machines, made explicit (pure: no Streamlit, no I/O).

Each machine has an enum of states, a function that derives the current state from the data the
app already keeps (nothing here is stored separately, so it can't drift), and its transitions
documented next to it. app.py reads these instead of re-deriving them inline; tests/test_flow.py
covers every transition; AGENTS.md has the one-page map.

  DraftPhase   setup -> drafting -> complete, with "reopened" after a removed pick
  Clock        who is on the clock, my next pick, the one-line note under the clock
  SyncState    Sleeper live sync: off / invalid link / ready / live / failing
  SourceStatus which player data is showing: projections, actuals, test data; fallback; cached
  BootAction   what a fresh browser session does with the saved draft
  ListOrder    which list orders exist right now, and which one is in effect
  SnapshotState freshness of a fetched source on disk
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

import draft as d


# ── Draft phase ───────────────────────────────────────────────────────────────

class DraftPhase(str, Enum):
    """Where the draft is. Derived from the picks; never stored.

    SETUP     --pick-->             DRAFTING (COMPLETE if that was the only pick)
    DRAFTING  --pick-->             DRAFTING | COMPLETE
    DRAFTING  --remove an earlier pick--> REOPENED   (the next entry fills that hole)
    COMPLETE  --remove an earlier pick--> REOPENED
    REOPENED  --pick (fills the hole)--> DRAFTING | COMPLETE | REOPENED (more holes)
    any       --undo-->             recomputed (SETUP once no picks are left)
    any       --reset / start fresh--> SETUP
    League size (teams, rounds) can change only in SETUP.
    """
    SETUP = "setup"
    DRAFTING = "drafting"
    REOPENED = "reopened"
    COMPLETE = "complete"


def draft_phase(state: d.DraftState) -> DraftPhase:
    if not state.picks:
        return DraftPhase.SETUP
    if state.current_pick() is None:
        return DraftPhase.COMPLETE
    return DraftPhase.REOPENED if state.reopened_slot() else DraftPhase.DRAFTING


@dataclass(frozen=True)
class Clock:
    """Everything the score bug, search box and recommendation need about the current pick."""
    phase: DraftPhase
    pick: int | None            # current pick number (None when complete)
    round: int | None
    pick_in_round: int | None
    team: int | None            # team on the clock
    me: int                     # my draft slot
    mine: bool                  # I'm on the clock
    next_mine: int | None       # my next pick; when I'm on the clock, the one after this
    up_next: int | None         # team that picks right after the current pick
    is_last_pick: bool

    @property
    def league_locked(self) -> bool:
        """Teams and rounds can't change once any pick exists."""
        return self.phase != DraftPhase.SETUP

    @property
    def can_draft(self) -> bool:
        return self.phase != DraftPhase.COMPLETE

    @property
    def picks_until_mine(self) -> int | None:
        return None if self.next_mine is None or self.pick is None else self.next_mine - self.pick

    @property
    def mine_soon(self) -> bool:
        """My next pick is within 3 picks (the score bug highlights it)."""
        return self.picks_until_mine is not None and self.picks_until_mine <= 3

    def note(self, team_name: Callable[[int], str]) -> str:
        """The line under ON THE CLOCK."""
        if self.phase == DraftPhase.COMPLETE:
            return "draft complete"
        if self.phase == DraftPhase.REOPENED:
            return "reopened pick"
        if self.mine:
            return "make your pick"
        if self.is_last_pick or self.up_next is None:
            return "last pick"
        return "you're up next" if self.up_next == self.me else f"up next: {team_name(self.up_next)}"


def clock(state: d.DraftState, me: int | None = None) -> Clock:
    me = state.settings.my_slot if me is None else me
    n = state.settings.n_teams
    cur = state.current_pick()
    phase = draft_phase(state)
    if cur is None:
        return Clock(phase, None, None, None, None, me, False, None, None, False)
    rnd = d.round_of(cur, n)
    team = d.team_for_pick(cur, n)
    last = cur >= state.total_picks
    return Clock(phase, cur, rnd, cur - (rnd - 1) * n, team, me, team == me, state.next_pick_for_team(me),
                 None if last else d.team_for_pick(cur + 1, n), last)


# ── Sleeper live sync ─────────────────────────────────────────────────────────

class SyncState(str, Enum):
    """Sleeper live sync, from the sidebar's point of view.

    OFF      --paste a link-->            READY | INVALID_LINK
    INVALID  --fix the link-->            READY
    READY    --Connect / toggle on-->     LIVE  (first poll pending)
    LIVE     --a poll fails-->            FAILING
    FAILING  --a poll succeeds-->         LIVE
    LIVE / FAILING --toggle off-->        READY
    any      --clear the link-->          OFF
    Picks from Sleeper are applied only in LIVE; manual entry works in every state.
    """
    OFF = "off"
    INVALID_LINK = "invalid_link"
    READY = "ready"
    LIVE = "live"
    FAILING = "failing"


@dataclass(frozen=True)
class Poll:
    """Result of the latest Sleeper picks request (kept in the browser session)."""
    ok: bool
    at: str                 # HH:MM:SS
    picks_seen: int = 0
    error: str = ""


@dataclass(frozen=True)
class SyncStatus:
    state: SyncState
    label: str              # short, for a pill
    detail: str             # one sentence for the sidebar
    tone: str               # "", "ok", "live", "warn"


def sync_status(link_text: str, draft_id: str | None, enabled: bool, last: Poll | None) -> SyncStatus:
    if not (link_text or "").strip():
        return SyncStatus(SyncState.OFF, "Not connected", "Paste your Sleeper draft link to sync picks.", "")
    if not draft_id:
        return SyncStatus(SyncState.INVALID_LINK, "Link not recognized",
                          "It should contain /draft/nba/ followed by a long number.", "warn")
    if not enabled:
        return SyncStatus(SyncState.READY, "Ready", "Click Connect (or turn Live sync on) to pull picks.", "")
    if last is not None and not last.ok:
        return SyncStatus(SyncState.FAILING, "Sync failing",
                          f"Last check {last.at} failed: {last.error[:90]}. Manual entry still works.", "warn")
    if last is None:
        return SyncStatus(SyncState.LIVE, "Live", "Checking Sleeper every 5 seconds.", "live")
    return SyncStatus(SyncState.LIVE, "Live", f"Last check {last.at}: {last.picks_seen} picks seen.", "live")


# ── Player data source ────────────────────────────────────────────────────────

class SourceKind(str, Enum):
    PROJECTIONS = "projections"     # 2026-27 projections (bundled model, ESPN, a CSV)
    ACTUALS = "actuals"             # last season's real stats, not projections
    SYNTHETIC = "synthetic"         # fake test players
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceStatus:
    """Which data is showing. `fallback_from` is set when the chosen source failed to load."""
    kind: SourceKind
    label: str
    fallback_from: str = ""
    cached: bool = False

    @property
    def tags(self) -> list[tuple[str, str]]:
        """Header pills: (text, tone)."""
        out = []
        if self.kind == SourceKind.ACTUALS:
            out.append(("2025-26 actuals, not projections", "warn"))
        elif self.kind == SourceKind.PROJECTIONS:
            out.append(("2026-27 projections", "ok"))
        elif self.kind == SourceKind.SYNTHETIC:
            out.append(("Test data", "warn"))
        if self.cached:
            out.append(("Cached snapshot", "warn"))
        return out


_KINDS = {"projections": SourceKind.PROJECTIONS, "last_season_actuals": SourceKind.ACTUALS,
          "synthetic": SourceKind.SYNTHETIC}


def source_status(meta) -> SourceStatus:
    """From a data_fetcher.SourceMeta (kind, source, from_cache, fallback_from)."""
    return SourceStatus(_KINDS.get(getattr(meta, "kind", ""), SourceKind.UNKNOWN), getattr(meta, "source", ""),
                        getattr(meta, "fallback_from", ""), bool(getattr(meta, "from_cache", False)))


# ── Browser session boot ──────────────────────────────────────────────────────

class BootAction(str, Enum):
    """What a new browser session does with the draft file on disk.

    no file, or an unused draft with the old 12-team defaults during the v3 move -> NEW (league defaults)
    saved draft without picks                                                  -> USE_SAVED
    saved draft with picks                                                     -> ASK_RESUME
    ASK_RESUME --Resume--> the saved draft;  --Start fresh--> archived, new draft, same league
    """
    NEW = "new"
    USE_SAVED = "use_saved"
    ASK_RESUME = "ask_resume"


def boot_action(saved: d.DraftState | None, moving_to_my_league: bool,
                old_default_league: tuple[int, int, int]) -> BootAction:
    if saved is None:
        return BootAction.NEW
    if saved.picks:
        return BootAction.ASK_RESUME
    league = (saved.settings.n_teams, saved.settings.n_rounds, saved.settings.my_slot)
    if moving_to_my_league and league == old_default_league:
        return BootAction.NEW
    return BootAction.USE_SAVED


# ── Best-available list order ─────────────────────────────────────────────────

class ListOrder(str, Enum):
    """List orders. RECOMMENDED exists while I have a pick left; TEAM_FIT after my 3rd pick
    (category leagues). A preferred order that isn't available right now is kept, not lost:
    the list shows MY_RANK until it comes back."""
    MY_RANK = "My rank"
    RECOMMENDED = "Recommended"
    ADP = "ADP"
    TEAM_FIT = "Team fit"


def list_orders(has_recommendation: bool, has_team_fit: bool) -> list[str]:
    return ([ListOrder.MY_RANK.value] + ([ListOrder.RECOMMENDED.value] if has_recommendation else [])
            + [ListOrder.ADP.value] + ([ListOrder.TEAM_FIT.value] if has_team_fit else []))


def effective_order(preferred: str, available: list[str]) -> str:
    return preferred if preferred in available else ListOrder.MY_RANK.value


def next_preferred_order(preferred: str, shown: str, chosen: str) -> str:
    """Update the saved preference only when the user actually picked something different."""
    return chosen if chosen != shown else preferred


# ── Fetched sources on disk ───────────────────────────────────────────────────

class SnapshotState(str, Enum):
    """A fetched source (ESPN, Sleeper players, ...) saved under data/cache.

    MISSING --fetch ok--> FRESH --time passes--> STALE --fetch ok--> FRESH
    any     --fetch fails--> unchanged; the app keeps using the snapshot (marked "cached")
    """
    MISSING = "missing"
    FRESH = "fresh"
    STALE = "stale"


def snapshot_state(age_hours: float | None, max_age_hours: float) -> SnapshotState:
    if age_hours is None:
        return SnapshotState.MISSING
    return SnapshotState.FRESH if age_hours < max_age_hours else SnapshotState.STALE


# ── Machine-readable page state (for tests, automation and agents) ────────────

def page_state(c: Clock, sync: SyncStatus, source: SourceStatus, n_picks: int, total_picks: int) -> dict[str, str]:
    """Flat key/values rendered as data-* attributes on a hidden #app-state element."""
    return {
        "phase": c.phase.value, "pick": str(c.pick or ""), "round": str(c.round or ""),
        "clock-team": str(c.team or ""), "mine": "1" if c.mine else "0", "next-mine": str(c.next_mine or ""),
        "picks-made": str(n_picks), "picks-total": str(total_picks),
        "sync": sync.state.value, "source": source.kind.value, "fallback": "1" if source.fallback_from else "0",
        "cached": "1" if source.cached else "0",
    }
