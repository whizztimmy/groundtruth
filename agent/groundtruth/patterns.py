"""Deterministic facts about a stretch of sessions.

Everything a weekly review asserts numerically is computed here, in ordinary code, before
any model sees it. The model's job is to interpret — to notice that deep work collapses
on the days a certain meeting lands — never to add up minutes.

That split is deliberate. A language model doing arithmetic over forty sessions will
occasionally be wrong, and a review that misstates someone's own week by twenty minutes
is one they stop believing. Worse, the errors are invisible: a plausible wrong number
reads exactly like a right one. So the numbers are computed and handed over as given
facts, and the model is asked only for the thing it is actually good at.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class Span:
    """One classified stretch of time. Mirrors the server's session shape."""

    start: datetime
    end: datetime
    category: str
    label: str
    confidence: float

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def day(self) -> str:
        return self.start.strftime("%A")


@dataclass
class Patterns:
    """Facts about a period. Every figure here is measured, never inferred."""

    span_count: int = 0
    tracked_s: float = 0.0
    by_category: dict[str, float] = field(default_factory=dict)
    by_day: dict[str, dict[str, float]] = field(default_factory=dict)
    longest_focus: Span | None = None
    fragmentation: dict[str, float] = field(default_factory=dict)
    uncertain_s: float = 0.0
    first_activity: dict[str, str] = field(default_factory=dict)

    def share(self, category: str) -> float:
        return self.by_category.get(category, 0.0) / self.tracked_s if self.tracked_s else 0.0


FOCUS_CATEGORIES = frozenset({"deep work", "research", "learning", "devotion"})
"""Categories where an uninterrupted stretch is the thing being measured.

Fragmenting communication into six pieces is normal and says nothing. Fragmenting deep
work into six pieces is the finding.
"""


def compute(spans: list[Span]) -> Patterns:
    """Reduce spans to the facts a review is allowed to assert."""
    patterns = Patterns(span_count=len(spans))
    if not spans:
        return patterns

    ordered = sorted(spans, key=lambda s: s.start)
    by_category: dict[str, float] = defaultdict(float)
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, int] = defaultdict(int)

    for span in ordered:
        patterns.tracked_s += span.seconds
        by_category[span.category] += span.seconds
        by_day[span.day][span.category] += span.seconds
        counts[span.category] += 1
        if span.category == "uncertain":
            patterns.uncertain_s += span.seconds

    patterns.by_category = dict(by_category)
    patterns.by_day = {day: dict(cats) for day, cats in by_day.items()}

    focus_spans = [s for s in ordered if s.category in FOCUS_CATEGORIES]
    if focus_spans:
        patterns.longest_focus = max(focus_spans, key=lambda s: s.seconds)

    # Mean span length per category. Short mean with large total is the signature of a
    # day that was busy but never continuous, which is the thing people feel and cannot
    # otherwise name.
    patterns.fragmentation = {
        category: by_category[category] / counts[category]
        for category in by_category
        if counts[category]
    }

    # What each day actually started with, which is often not what the person believes.
    seen_days: set[str] = set()
    for span in ordered:
        if span.day not in seen_days and span.category != "idle":
            patterns.first_activity[span.day] = span.label
            seen_days.add(span.day)

    return patterns


def render(patterns: Patterns) -> str:
    """Patterns as plain text for a prompt.

    Durations are pre-formatted as hours and minutes so the model never converts seconds,
    and shares are pre-computed as percentages so it never divides. Removing every
    opportunity for arithmetic is the cheapest way to remove arithmetic errors.
    """
    if not patterns.span_count:
        return "No tracked activity in this period."

    lines = [
        f"Period total: {_hm(patterns.tracked_s)} across {patterns.span_count} sessions.",
        "",
        "Time by category:",
    ]
    for category, seconds in sorted(
        patterns.by_category.items(), key=lambda kv: -kv[1]
    ):
        share = seconds / patterns.tracked_s * 100 if patterns.tracked_s else 0
        mean = patterns.fragmentation.get(category, 0.0)
        lines.append(
            f"  {category}: {_hm(seconds)} ({share:.0f}%), "
            f"average stretch {_hm(mean)}"
        )

    if patterns.by_day:
        lines.append("")
        lines.append("By day:")
        for day, categories in patterns.by_day.items():
            top = sorted(categories.items(), key=lambda kv: -kv[1])[:3]
            summary = ", ".join(f"{c} {_hm(s)}" for c, s in top)
            lines.append(f"  {day}: {summary}")

    if patterns.first_activity:
        lines.append("")
        lines.append("First activity each day:")
        for day, label in patterns.first_activity.items():
            lines.append(f"  {day}: {label}")

    if patterns.longest_focus:
        focus = patterns.longest_focus
        lines.append("")
        lines.append(
            f"Longest unbroken focus: {_hm(focus.seconds)} on {focus.day}, "
            f"{focus.label}."
        )

    if patterns.uncertain_s:
        lines.append("")
        lines.append(
            f"{_hm(patterns.uncertain_s)} could not be classified and is excluded from "
            "any claim about what happened."
        )

    return "\n".join(lines)


def _hm(seconds: float) -> str:
    delta = timedelta(seconds=round(seconds))
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes:02d}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
