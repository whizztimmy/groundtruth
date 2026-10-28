"""Shared contracts.

Every tier, the segmenter and the sync client all speak in these types. They are the
boundary between components that are built independently, so changes here ripple — treat
them as the stable surface and keep behaviour in the modules that produce them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum


class Tier(IntEnum):
    """Which rung of the escalation ladder produced an observation.

    Ordered so comparisons work: a Tier 2 label supersedes a Tier 1 label for the same
    instant, and a Tier 3 (user-stated) label supersedes both.
    """

    WINDOW = 1
    SCREEN = 2
    VOICE = 3


class Confidence(float, Enum):
    """Thresholds, not a closed set — classifiers return arbitrary floats in [0, 1].

    ACCEPT and below-ACCEPT are the only distinctions the pipeline makes. Everything
    under RETRY is re-asked with escalated reasoning effort before being stored.
    """

    ACCEPT = 0.75
    RETRY = 0.45


UNCERTAIN = "uncertain"
"""Reserved label. Stored verbatim rather than guessed, and surfaced to the user as a
question. Never merged into an adjacent session by the segmenter."""


@dataclass(frozen=True, slots=True)
class Region:
    """One tracked screen region from SAM 3.1.

    `track_id` is stable across frames within a multiplex run, which is what makes churn
    measurable. `churn` is mask instability within a fixed bounding area between the
    current frame and the previous one: high while a feed scrolls, low while reading.
    """

    track_id: int
    label: str
    bbox: tuple[int, int, int, int]
    area_frac: float
    churn: float
    has_focus: bool = False


@dataclass(frozen=True, slots=True)
class Observation:
    """What a capture tier saw. Pre-classification, so it carries no label."""

    ts: datetime
    tier: Tier
    process: str | None = None
    window_title: str | None = None
    regions: tuple[Region, ...] = ()
    transcript: str | None = None

    @property
    def is_ambiguous_by_title(self) -> bool:
        """True when the title carries no usable signal on its own."""
        return not self.window_title or len(self.window_title.strip()) < 3


@dataclass(frozen=True, slots=True)
class Classification:
    """A label a classifier is willing to be held to.

    `evidence` is required, not decorative: it is what the user sees when they are asked
    to confirm an uncertain span, and what makes a wrong label debuggable rather than
    mysterious.
    """

    label: str
    category: str
    confidence: float
    evidence: str
    reasoning_effort: str = "low"

    @property
    def is_acceptable(self) -> bool:
        return self.confidence >= Confidence.ACCEPT and self.label != UNCERTAIN

    @property
    def should_retry(self) -> bool:
        return Confidence.RETRY <= self.confidence < Confidence.ACCEPT


@dataclass(frozen=True, slots=True)
class Event:
    """An observation that has been classified. The unit that is persisted and synced.

    Events are immutable and append-only; a device never edits another device's events,
    which is what makes sync a conflict-free merge.
    """

    ts: datetime
    device_id: str
    tier: Tier
    classification: Classification
    process: str | None = None
    window_title: str | None = None


@dataclass(slots=True)
class Session:
    """A derived span of same-labelled events.

    Mutable because the segmenter extends an open session in place as events arrive.
    Recomputable from events, so a taxonomy change never requires re-observing the day.
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
