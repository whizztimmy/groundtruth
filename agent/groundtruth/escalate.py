"""When to climb from Tier 1 to Tier 2.

This is the most consequential rule in the system. Tier 1 is free and resolves most of a
day; Tier 2 costs GPU-seconds and is the only tier that sees pixels. Escalating too
eagerly makes always-on operation unaffordable and needlessly widens what the agent
looks at; escalating too rarely leaves hours labelled by application name, which is the
failure mode the whole project exists to fix.

The policy is deliberately pure and dependency-free so it can be reasoned about and
tested without a screen, a model, or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .known import is_known
from .types import Observation

AMBIGUOUS_PROCESSES: frozenset[str] = frozenset(
    {
        # Anything that can host arbitrary activity. A browser is never self-describing.
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
        "arc.exe",
        "opera.exe",
        # Chat and social clients host both work and scrolling.
        "discord.exe",
        "slack.exe",
        "telegram.exe",
        "whatsapp.exe",
        # Terminals and editors can hold focus while nothing happens in them.
        "windowsterminal.exe",
        "wt.exe",
        "powershell.exe",
        "cmd.exe",
        "code.exe",
        # File managers and generic viewers.
        "explorer.exe",
        "notepad.exe",
    }
)
"""Processes whose window title cannot be trusted to describe the activity.

Not a blocklist — membership only means "ask Tier 2", never "this is bad". Deliberately
conservative: a process absent from this set is resolved for free, so adding one is a
real cost decision.
"""

STALE_AFTER = timedelta(minutes=8)
"""How long a Tier 2 label stays trustworthy for an unchanged process.

Long sessions drift: an editor open for two hours is not two hours of editing. Re-asking
on this interval bounds how stale a label can be without making long focused work
expensive.
"""

MIN_INTERVAL = timedelta(seconds=20)
"""Floor between Tier 2 calls regardless of other signals.

Without this, rapid window switching (alt-tabbing through a handful of apps) would fire a
burst of classifications describing a transition rather than an activity.
"""


@dataclass
class EscalationState:
    """Per-process memory the policy needs. Owned by the agent loop, passed in.

    Kept outside the policy function so the decision stays pure and testable.
    """

    last_tier2_at: dict[str, datetime] = field(default_factory=dict)
    seen_titles: dict[str, set[str]] = field(default_factory=dict)
    low_confidence_streak: dict[str, int] = field(default_factory=dict)
    last_any_tier2_at: datetime | None = None

    def record_tier2(self, process: str, ts: datetime, confident: bool) -> None:
        self.last_tier2_at[process] = ts
        self.last_any_tier2_at = ts
        if confident:
            self.low_confidence_streak.pop(process, None)
        else:
            self.low_confidence_streak[process] = (
                self.low_confidence_streak.get(process, 0) + 1
            )

    def record_title(self, process: str, title: str | None) -> bool:
        """Remember a title. Returns True when it had not been seen for this process."""
        if not title:
            return False
        titles = self.seen_titles.setdefault(process, set())
        if title in titles:
            return False
        titles.add(title)
        return True


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether to climb, and why.

    The reason is carried rather than discarded so escalation behaviour can be audited
    against a real day's trace — the cheapest way to tune the policy is to look at what
    actually triggered it.
    """

    escalate: bool
    reason: str

    def __bool__(self) -> bool:
        return self.escalate


def should_escalate(
    obs: Observation,
    state: EscalationState,
    *,
    now: datetime | None = None,
) -> Decision:
    """Decide whether this observation needs Tier 2.

    Order matters: the rate limit is checked first so no other rule can override it, and
    the cheap structural checks run before the stateful ones.
    """
    now = now or obs.ts
    process = (obs.process or "").lower()

    if not process:
        return Decision(False, "no foreground process")

    if state.last_any_tier2_at is not None:
        since_any = now - state.last_any_tier2_at
        if since_any < MIN_INTERVAL:
            return Decision(False, f"rate limited ({since_any.total_seconds():.0f}s)")

    if process in AMBIGUOUS_PROCESSES:
        last = state.last_tier2_at.get(process)
        if last is None:
            return Decision(True, "ambiguous process, never classified")
        if now - last >= STALE_AFTER:
            return Decision(True, f"ambiguous process, label stale ({now - last})")
        if state.record_title(process, obs.window_title):
            return Decision(True, "ambiguous process, unseen window title")
        return Decision(False, "ambiguous process, recent label still fresh")

    if obs.is_ambiguous_by_title:
        return Decision(True, "unusable window title")

    if not is_known(process):
        # Tier 1 can only resolve a process whose name describes the activity. An
        # unrecognised process has no Tier 1 label at all, so declining to escalate here
        # would drop the sample silently rather than resolve it cheaply.
        return Decision(True, "process not recognised by the tier 1 labeller")

    if state.low_confidence_streak.get(process, 0) >= 2:
        return Decision(True, "process has a history of low-confidence labels")

    last = state.last_tier2_at.get(process)
    if last is not None and now - last >= STALE_AFTER * 3:
        # Unambiguous processes still drift, just far more slowly.
        return Decision(True, "periodic re-verification")

    return Decision(False, "tier 1 sufficient")
