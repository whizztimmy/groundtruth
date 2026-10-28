"""Turning an event stream into sessions.

This is a pure function, and the purity is the point rather than an aesthetic preference.
Sessions are not stored: they are recomputed on every read. That is what makes a taxonomy
change cheap — the user splitting "research" into "research" and "learning" re-segments
three months of history instead of discarding it, because the events are still there and
nothing downstream of them was ever treated as the source of truth.

It also means this module touches no clock, no database and no configuration file. Given
the same events and the same config it returns the same spans, which is what the
re-segmentation test pins down.

## The rules

A span continues into the next event when all of these hold:

- the gap is no longer than `idle_gap_s` — a longer one means the agent stopped observing,
  and unobserved time is not evidence of anything;
- the device is the same — a phone pickup mid-desktop-session is a handoff, and stitching
  those back together is a read-time concern the contract puts in the query layer;
- the category is the same — this is the merge rule proper;
- and uncertainty is not being swallowed: an `uncertain` event joins only another
  `uncertain` event, from the same process.

## Why coverage is carried out of here

Every event is credited with a number of seconds. Inside a span that is simply the gap to
the next event, so a span's duration is exactly the sum of its events' coverage and
`end_ts - start_ts` agrees with it to the microsecond. At a span's final event there is no
next event inside the span, so coverage is capped at `sample_coverage_s`: one sample vouches
for about one sampling interval, and crediting it the whole idle threshold would invent time
the agent never observed.

That per-event number is also what `/summary` needs to compute `naive_by_process`, so it
leaves this module rather than being recomputed by a second code path that could disagree.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from collections.abc import Iterable, Sequence

from .ids import derive_id
from .models import UNCERTAIN, Coverage, Event, Resolution, Session

MAX_EVIDENCE_LINES = 6
"""How many distinct evidence strings a span keeps.

A ninety-event session repeats itself; the dashboard shows a sentence, not a transcript.
The cap is on *distinct* strings, so it only bites on genuinely varied evidence.
"""


@dataclass(frozen=True, slots=True)
class SegmentConfig:
    """Thresholds the segmenter reads. Passed in, never read from the environment here."""

    idle_gap_s: float = 120.0
    """Longest gap between samples that still counts as the same continuous activity.

    Two minutes is long enough to survive a phone call or a bathroom break being sampled
    across, and short enough that a lunch break does not get absorbed into the morning's
    work.
    """

    sample_coverage_s: float = 60.0
    """Most seconds a single sample may be credited with.

    Only applies to the last event of a span, where there is no following sample to bound
    it. The alternative — crediting it up to `idle_gap_s` — would attribute two minutes of
    unobserved time to whatever happened to be on screen when the agent stopped looking,
    which is precisely the kind of made-up number this project exists to replace.
    """


@dataclass(frozen=True, slots=True)
class Span:
    """A session plus the bookkeeping that does not belong on the wire.

    `GET /sessions` serialises `session`. The query layer needs the rest: `process` to key a
    few-shot example when the user resolves an uncertain span, and `coverage` to compute what
    an app-name tracker would have reported for the same seconds.
    """

    id: str
    session: Session
    process: str | None
    coverage: tuple[Coverage, ...]

    @property
    def start_ts(self) -> datetime:
        return self.session.start_ts

    @property
    def end_ts(self) -> datetime:
        return self.session.end_ts

    @property
    def device_id(self) -> str:
        return self.session.device_id

    @property
    def category(self) -> str:
        return self.session.category

    @property
    def duration_s(self) -> float:
        return self.session.duration_s

    @property
    def is_uncertain(self) -> bool:
        return self.session.category == UNCERTAIN

    @property
    def evidence(self) -> list[str]:
        return self.session.evidence


def order_events(events: Iterable[Event]) -> tuple[Event, ...]:
    """Put a stream in segmentation order, keeping one event per `(device_id, ts)`.

    Sorting here rather than trusting the caller is what makes segmentation independent of
    how the events were fetched — the store returns them ordered, but a merge of two devices'
    batches in memory would not be, and a segmenter whose output depends on arrival order is
    not recomputable.

    The de-duplication mirrors the store's supersede rule: a device that emitted a Tier 1
    sample and then escalated to Tier 2 for the same instant produced a better answer, not a
    second activity. Left in, the stale one would contribute a zero-second span on the
    category change between them.
    """
    ordered = sorted(events, key=lambda e: (e.ts, e.device_id, -int(e.tier)))
    best: OrderedDict[tuple[str, datetime], Event] = OrderedDict()
    for event in ordered:
        best.setdefault((event.device_id, event.ts), event)
    return tuple(best.values())


def _continues(prev: Event, nxt: Event, gap_s: float, config: SegmentConfig) -> bool:
    """Whether `nxt` belongs to the span `prev` ends.

    Checked in cost order, cheapest first, but the order carries no precedence: every clause
    is a veto.
    """
    if gap_s > config.idle_gap_s:
        return False
    if nxt.device_id != prev.device_id:
        return False
    if nxt.category != prev.category:
        return False

    if prev.is_uncertain or nxt.is_uncertain:
        # An uncertain event is a question the user has not been asked yet. Absorbed into a
        # confident neighbour it would stop being a question and start being a wrong number,
        # which is the one failure this system is not allowed to have. Redundant with the
        # category check above today — `uncertain` *is* a category — and kept explicit so
        # that any future "merge adjacent similar categories" rule cannot quietly undo it.
        if not (prev.is_uncertain and nxt.is_uncertain):
            return False
        # Resolving a span stores a few-shot example keyed by process, so a span that spans
        # two processes has no single key to store under. Splitting on the process change
        # keeps every uncertain span answerable.
        if nxt.process != prev.process:
            return False

    return True


def _dominant(weights: dict[str, float]) -> str:
    """Highest-coverage key, earliest insertion winning a tie.

    Insertion order is stream order, so a tie resolves to whichever came first — stable
    across re-segmentation, which a `max()` over a plain dict would not be.
    """
    return max(weights, key=weights.__getitem__)


def _close(covered: Sequence[Coverage]) -> Span:
    """Build a span from its events and their coverage."""
    first = covered[0].event
    total_s = sum(c.seconds for c in covered)
    start_ts = first.ts
    end_ts = start_ts + timedelta(seconds=total_s)

    label_weights: dict[str, float] = {}
    process_weights: dict[str, float] = {}
    confidence_numerator = 0.0
    evidence: list[str] = []

    for item in covered:
        event = item.event
        label_weights[event.label] = label_weights.get(event.label, 0.0) + item.seconds
        if event.process is not None:
            process_weights[event.process] = process_weights.get(event.process, 0.0) + item.seconds
        confidence_numerator += event.confidence * item.seconds
        if (
            event.classification.evidence
            and event.classification.evidence not in evidence
            and len(evidence) < MAX_EVIDENCE_LINES
        ):
            evidence.append(event.classification.evidence)

    if total_s > 0:
        confidence = confidence_numerator / total_s
    else:
        # Degenerate only when coverage is configured to zero or every event shares one
        # instant. A plain mean is wrong in general — that is the whole point of weighting —
        # but it beats dividing by zero, and with no duration to weight by it is also the
        # only answer available.
        confidence = sum(c.event.confidence for c in covered) / len(covered)

    # The headline label is what the person mostly did. A session can hold several labels
    # under one category ("reading API documentation" then "comparing inference providers"
    # are both research), and the longer stretch is the honest title for the span.
    label = _dominant(label_weights)
    process = _dominant(process_weights) if process_weights else None

    session = Session(
        start_ts=start_ts,
        end_ts=end_ts,
        device_id=first.device_id,
        category=first.category,
        label=label,
        confidence=confidence,
        event_count=len(covered),
        evidence=evidence,
    )
    prefix = "u" if first.category == UNCERTAIN else "s"
    span_id = derive_id(prefix, start_ts, first.device_id, first.category, label)
    return Span(id=span_id, session=session, process=process, coverage=tuple(covered))


def segment_spans(
    events: Iterable[Event],
    config: SegmentConfig | None = None,
) -> list[Span]:
    """Derive spans from an event stream. Pure: same input, same output, always."""
    config = config or SegmentConfig()
    ordered = order_events(events)
    if not ordered:
        return []

    spans: list[Span] = []
    open_span: list[Coverage] = []

    for index, event in enumerate(ordered):
        nxt = ordered[index + 1] if index + 1 < len(ordered) else None
        if nxt is None:
            gap_s: float | None = None
            continues = False
        else:
            gap_s = (nxt.ts - event.ts).total_seconds()
            continues = _continues(event, nxt, gap_s, config)

        if continues:
            # Inside a span, coverage is the whole gap. Capping it here would make
            # `sum(coverage)` disagree with `end_ts - start_ts` for any cadence slower than
            # the cap, and two numbers that must be equal are better made equal by
            # construction than by a reconciliation step.
            seconds = float(gap_s or 0.0)
        elif gap_s is None:
            seconds = config.sample_coverage_s
        else:
            seconds = min(gap_s, config.sample_coverage_s)

        open_span.append(Coverage(event=event, seconds=seconds))
        if not continues:
            spans.append(_close(open_span))
            open_span = []

    return spans


def segment(
    events: Iterable[Event],
    config: SegmentConfig | None = None,
) -> list[Session]:
    """Derive sessions from an event stream — the contract's view of `segment_spans`."""
    return [span.session for span in segment_spans(events, config)]


def apply_resolutions(
    events: Iterable[Event],
    resolutions: Iterable[Resolution],
) -> tuple[Event, ...]:
    """Overlay the user's answers onto a stream, without editing the stream.

    Events stay append-only, so a resolution is applied here on the way to the segmenter
    rather than written back over the device's observation. The device's record of what it
    saw survives; only the derivation changes. That is also what makes a corrected answer
    cheap: the second resolution supersedes the first and history re-derives.

    Confidence becomes 1.0 because the label is no longer a guess — the user stated it, and
    `Tier` already encodes that a user-stated label supersedes a classified one. The tier
    itself is left alone: Tier 2 is still what *observed* this span, even once the user is
    what labelled it.
    """
    resolutions = list(resolutions)
    if not resolutions:
        return tuple(events)

    by_device: dict[str, list[Resolution]] = {}
    for resolution in resolutions:
        by_device.setdefault(resolution.device_id, []).append(resolution)
    for device_resolutions in by_device.values():
        device_resolutions.sort(key=lambda r: r.resolved_at)

    out: list[Event] = []
    for event in events:
        applied: Resolution | None = None
        for resolution in by_device.get(event.device_id, ()):
            # Half-open: a span's `end_ts` is the first instant after it, so an event sitting
            # exactly there is the neighbour's, not this span's.
            if resolution.start_ts <= event.ts < resolution.end_ts:
                applied = resolution
        if applied is None:
            out.append(event)
            continue
        out.append(
            replace(
                event,
                classification=replace(
                    event.classification,
                    category=applied.category,
                    label=applied.label,
                    confidence=1.0,
                ),
            )
        )
    return tuple(out)
