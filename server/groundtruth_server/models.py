"""Domain types, mirroring `agent/groundtruth/types.py` field for field.

These are re-declared rather than imported. The agent and the server are separate
distributions with separate dependency sets — the agent pulls in `mss`, `pillow` and
`pywin32`, none of which belong on a sync host — so an import would couple a deployment to
a platform it does not run on.

The cost of that decision is drift, so it is paid down with a test: `tests/test_parity.py`
loads the agent's module from the repo and asserts the field names still line up. If this
file and that one disagree, the test fails rather than the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from collections.abc import Sequence

UNCERTAIN = "uncertain"
"""Reserved category. The segmenter refuses to merge it into a neighbour, so uncertainty
survives derivation and can be put to the user as a question."""

OFF_SCREEN_PROCESS = "(off-screen)"
"""Bucket for coverage with no foreground process — Tier 3 voice check-ins.

Only used in `naive_by_process`. An app-name tracker has no row for the gym, so the honest
rendering of its blind spot is a named bucket rather than silently dropped time: dropping it
would make the naive shares sum to less than one for a reason the dashboard could not
explain to the user.
"""


class Tier(IntEnum):
    """Which rung of the escalation ladder produced an observation.

    Ordered, and the server depends on the ordering: when two events arrive for the same
    `(device_id, ts)` the higher tier wins, because a device that escalated to Tier 2 after
    emitting a Tier 1 sample for the same instant has produced a better answer, not a
    duplicate observation of a different thing.
    """

    WINDOW = 1
    SCREEN = 2
    VOICE = 3


@dataclass(frozen=True, slots=True)
class Classification:
    """A label a classifier is willing to be held to."""

    label: str
    category: str
    confidence: float
    evidence: str
    reasoning_effort: str = "low"


@dataclass(frozen=True, slots=True)
class Event:
    """An observation that has been classified. The unit the agent syncs and the store holds.

    Immutable and append-only. A resolution from the user is applied as an overlay at read
    time rather than as an edit here, which is what lets the server answer "what did the
    device actually report?" after the taxonomy has moved on.
    """

    ts: datetime
    device_id: str
    tier: Tier
    classification: Classification
    process: str | None = None
    window_title: str | None = None

    @property
    def category(self) -> str:
        return self.classification.category

    @property
    def label(self) -> str:
        return self.classification.label

    @property
    def confidence(self) -> float:
        return self.classification.confidence

    @property
    def is_uncertain(self) -> bool:
        return self.classification.category == UNCERTAIN


@dataclass(slots=True)
class Session:
    """A derived span of same-category events.

    Mirrors the agent's `Session`. Mutable because the segmenter extends an open span in
    place as it walks the stream; immutable by the time anything else sees it.
    """

    start_ts: datetime
    end_ts: datetime
    device_id: str
    category: str
    label: str
    confidence: float
    event_count: int = 1
    evidence: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return (self.end_ts - self.start_ts).total_seconds()


@dataclass(frozen=True, slots=True)
class Coverage:
    """One event and the seconds of wall clock it is credited with.

    The segmenter has to decide this anyway to close sessions, and two very different
    questions need the answer: how long a session was, and how long an *application* was in
    front of the user. Carrying coverage out of the segmenter means `/summary` computes both
    the real and the naive totals from one derivation, so they reconcile to the same second
    by construction instead of by luck.
    """

    event: Event
    seconds: float


EVIDENCE_SUMMARY_CHARS = 240
"""Length budget for a span's `evidence_summary`.

The dashboard renders it as a caption under a bar, so the budget is a design constraint
more than a storage one: past a couple of lines it stops being read at all.
"""


def evidence_summary(lines: Sequence[str]) -> str:
    """Join a span's distinct evidence into one caption, truncated on a word boundary.

    Truncating mid-word reads as corruption rather than as brevity, and this string is the
    user's only window into why a label was chosen — it has to look deliberate.
    """
    joined = " ".join(line.strip() for line in lines if line.strip())
    if len(joined) <= EVIDENCE_SUMMARY_CHARS:
        return joined
    clipped = joined[:EVIDENCE_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return f"{clipped}\u2026"


@dataclass(frozen=True, slots=True)
class Resolution:
    """A user's answer to an uncertain span.

    Stored as an overlay keyed by the span's id and time range rather than as an edit to the
    events, so the raw observations stay append-only and a later answer can supersede an
    earlier one without losing what the device originally reported.
    """

    span_id: str
    device_id: str
    start_ts: datetime
    end_ts: datetime
    category: str
    label: str
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class FewShotExample:
    """A confirmed label for a process, for the classifier to read back as a prompt example.

    This is the mechanism by which the taxonomy converges on the individual: the user is
    asked once what a particular kind of browser tab is, and Tier 2 stops asking.
    """

    process: str
    category: str
    label: str
    evidence: str
    window_title: str | None
    source_span_id: str
    created_at: datetime
