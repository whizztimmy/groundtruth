"""The activity percentage, as time-tracking tools have measured it for a decade.

Hubstaff, the Upwork desktop app, Time Doctor and the rest all report a number built the
same way: split the clock into fixed segments, count how many minutes inside a segment saw
any keyboard or mouse input, and divide. Ten minutes with input in three of them is 30%.

This implements that metric, deliberately and faithfully, because GroundTruth's whole
argument is a comparison and an argument you cannot check is worth nothing. A claim that
"activity percentage measures the wrong thing" is a pitch. The same number, computed
honestly from the same signal and shown next to what the screen actually contained, is
evidence.

## What the number can and cannot know

It is a real signal. Input is cheap to collect, impossible to fake convincingly at scale
without deliberate effort, and genuinely correlates with doing things.

What it cannot do is distinguish *what* you were doing, and that is not a gap in the
implementation. The signal carries no information about content:

- Reading documentation carefully for forty minutes scores near zero. The metric calls it
  idle. It was research.
- Pulling a feed for forty minutes scores near one hundred. The metric calls it maximum
  productivity. It was doomscrolling.
- A design review on a call, a long debugging stare, reading a contract: all near zero.
- A mouse jiggler: a perfect score, forever. An entire product category exists to sell
  this, which tells you what the metric is actually measuring.

So the number is not wrong. It is answering "were your hands moving?" while being read as
though it answered "were you working?", and those diverge exactly where it matters.

GroundTruth reports both, side by side, and lets the disagreement speak.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

SEGMENT_MINUTES = 10
"""Length of a scoring window, matching the convention these tools use.

Ten minutes is short enough that a score tracks the current stretch of work rather than
the whole day, and long enough that standing up for a glass of water does not read as
absence.
"""

SAMPLES_PER_MINUTE = 30
"""At the agent's two-second cadence. Only used to size the ring buffer."""


@dataclass(frozen=True, slots=True)
class ActivityLevel:
    """One segment's score, and the counts it was computed from."""

    active_minutes: int
    elapsed_minutes: int

    @property
    def percent(self) -> int:
        """Whole percent, as these tools display it. Zero elapsed scores zero."""
        if self.elapsed_minutes <= 0:
            return 0
        return round(100 * self.active_minutes / self.elapsed_minutes)

    @property
    def is_partial(self) -> bool:
        """True before a full segment has elapsed.

        Worth surfacing: a 100% score from forty seconds of data is not the same claim as
        a 100% score from ten minutes, and tools that hide the distinction invite people
        to read noise as a verdict.
        """
        return self.elapsed_minutes < SEGMENT_MINUTES


class ActivityMeter:
    """Rolling activity percentage over the trailing segment.

    A minute counts as active if *any* input landed in it. That is the rule these tools
    use, and it is worth seeing how low the bar is: one keystroke in sixty seconds scores
    that minute exactly the same as sixty seconds of continuous typing. The metric is far
    coarser than its two-decimal presentation in most dashboards suggests.

    Rolling rather than aligned to wall-clock boundaries, so the score means "the last ten
    minutes" at every sample instead of resetting to zero on the hour and climbing back.
    """

    def __init__(
        self,
        *,
        segment_minutes: int = SEGMENT_MINUTES,
        samples_per_minute: int = SAMPLES_PER_MINUTE,
    ) -> None:
        self._segment_minutes = segment_minutes
        self._samples_per_minute = max(1, samples_per_minute)
        self._minutes: deque[bool] = deque(maxlen=segment_minutes)
        self._current_any = False
        self._current_samples = 0

    def record(self, had_input: bool) -> None:
        """Fold one sample in, closing the minute when enough samples have accrued.

        Counted in samples rather than from a clock so the meter is deterministic and
        testable, and so a paused or descheduled agent does not silently gain minutes it
        never observed.
        """
        self._current_any = self._current_any or had_input
        self._current_samples += 1
        if self._current_samples >= self._samples_per_minute:
            self._minutes.append(self._current_any)
            self._current_any = False
            self._current_samples = 0

    def level(self) -> ActivityLevel:
        """The score over completed minutes, plus the minute in progress if it is active.

        The partial minute is counted only when it already has input. Counting it as
        inactive would drag a genuinely busy stretch down for up to a minute at a time,
        and counting it as active when nothing has happened would invent a score.
        """
        active = sum(self._minutes)
        elapsed = len(self._minutes)
        if self._current_samples and self._current_any:
            active += 1
            elapsed += 1
        return ActivityLevel(active_minutes=active, elapsed_minutes=elapsed)


def disagreement(level: ActivityLevel, category: str, *, focus_categories: frozenset[str]) -> str | None:
    """Where the input metric and the screen disagree, named in one phrase.

    Returns None when they agree, which is most of the time and is worth saying: this is
    not an argument that the old metric is always wrong. It is an argument that it fails
    in two specific, predictable directions, and those are the two this reports.
    """
    if level.is_partial:
        return None

    if level.percent <= 25 and category in focus_categories:
        return (
            f"an input-activity tracker would score this {level.percent}% and flag it as "
            f"idle; the screen shows {category}"
        )

    if level.percent >= 75 and category == "doomscrolling":
        return (
            f"an input-activity tracker would score this {level.percent}% and rank it "
            "among your most productive time; the screen shows a feed"
        )

    return None
