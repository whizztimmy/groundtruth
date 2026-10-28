"""The read path: events out of the store, through the overlay, into the segmenter.

Everything the API returns is derived here, and in this order, every time a request
arrives. Nothing is cached. That is affordable because the work is one index range scan and
a linear pass, and it is necessary because the alternative — a `sessions` table — is the
design mistake the architecture is built to avoid: the moment sessions are stored, renaming
a category becomes a migration instead of a re-read.

Filters are applied *after* derivation rather than pushed into SQL, and that is a
correctness requirement rather than laziness. Dropping events before segmentation would
splice their neighbours together: filter out a phone's events and a desktop session either
side of a handoff merges into one, reporting a continuous stretch of work that never
happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import (
    UNCERTAIN,
    FewShotExample,
    Resolution,
    evidence_summary,
)
from .segment import SegmentConfig, Span, apply_resolutions, segment_spans
from .store import EventStore

MAX_SUGGESTIONS = 3
"""How many categories to offer alongside an uncertain span.

The point of the question is that it is answerable in one tap. A list long enough to need
reading is a list long enough to be dismissed.
"""

RESOLVE_CONTEXT = timedelta(hours=6)
"""How far either side of a resolved span to re-segment.

Resolving a span can make it merge with its neighbours, so the answer has to be derived from
a window wider than the span itself. Six hours bounds the work while comfortably exceeding
any session an idle gap of two minutes could hold together — a longer unbroken span would
mean the agent sampled continuously for six hours without a single two-minute pause.
"""


@dataclass(frozen=True, slots=True)
class UncertainView:
    """An uncertain span, dressed for `GET /uncertain`."""

    id: str
    start_ts: datetime
    end_ts: datetime
    process: str | None
    evidence: str
    suggestions: tuple[str, ...]


def derive_spans(
    store: EventStore,
    start: datetime,
    end: datetime,
    config: SegmentConfig,
) -> list[Span]:
    """Derive every span overlapping `[start, end]`.

    The window bounds which *events* are read, so a session running across `start` is
    reported from `start` rather than from its true beginning. That is the right answer for a
    windowed query — the alternative is a response whose first row reaches outside the range
    the caller asked about — but it does mean two adjacent windows do not simply concatenate.
    """
    events = store.events_between(start, end)
    if not events:
        return []
    overlay = store.resolutions_between(start, end)
    return segment_spans(apply_resolutions(events, overlay), config)


def select_spans(
    spans: list[Span],
    *,
    device_id: str | None = None,
    category: str | None = None,
) -> list[Span]:
    """Narrow derived spans to what the caller asked for."""
    selected = spans
    if device_id is not None:
        selected = [span for span in selected if span.device_id == device_id]
    if category is not None:
        selected = [span for span in selected if span.category == category]
    return selected


def _history_categories(spans: list[Span], process: str | None) -> list[str]:
    """Categories this process has been seen as, longest total first."""
    if process is None:
        return []
    weights: dict[str, float] = {}
    for span in spans:
        if span.category == UNCERTAIN:
            continue
        for item in span.coverage:
            if item.event.process == process:
                weights[span.category] = weights.get(span.category, 0.0) + item.seconds
    return sorted(weights, key=lambda key: -weights[key])


def _neighbour_categories(spans: list[Span], index: int) -> list[str]:
    """Categories of the spans either side, nearest first."""
    out: list[str] = []
    for offset in (-1, 1):
        probe = index + offset
        if 0 <= probe < len(spans) and spans[probe].category != UNCERTAIN:
            out.append(spans[probe].category)
    return out


def suggest_categories(
    store: EventStore,
    spans: list[Span],
    index: int,
) -> tuple[str, ...]:
    """Guess what an uncertain span might be, best guess first.

    Three sources in descending order of how much they know about this particular user:
    what they have already confirmed for this process, what this process has usually turned
    out to be, and what they were doing either side of the gap. Personal history outranks
    statistics outranks context, which is the same ordering the taxonomy itself converges
    along.
    """
    span = spans[index]
    ranked: list[str] = []

    if span.process is not None:
        ranked.extend(example.category for example in store.few_shot_for(span.process))
    ranked.extend(_history_categories(spans, span.process))
    ranked.extend(_neighbour_categories(spans, index))

    out: list[str] = []
    for category in ranked:
        if category != UNCERTAIN and category not in out:
            out.append(category)
        if len(out) == MAX_SUGGESTIONS:
            break
    return tuple(out)


def full_range(store: EventStore) -> tuple[datetime, datetime] | None:
    """Every event in the store, as a window.

    `GET /uncertain` takes no range in the contract, so "all of it" is the only reading. A
    deployment holding years of events would want a bound here; a deployment holding years of
    events would also want the contract to grow a `from`/`to`, and inventing those
    unilaterally would break the dashboard built against the frozen version.
    """
    bounds = store.event_bounds()
    if bounds is None:
        return None
    lo, hi = bounds
    return (lo, hi)


def derive_uncertain(store: EventStore, config: SegmentConfig) -> list[UncertainView]:
    """Every unresolved uncertain span, newest first."""
    window = full_range(store)
    if window is None:
        return []
    spans = derive_spans(store, window[0], window[1], config)

    views = [
        UncertainView(
            id=span.id,
            start_ts=span.start_ts,
            end_ts=span.end_ts,
            process=span.process,
            evidence=evidence_summary(span.evidence),
            suggestions=suggest_categories(store, spans, index),
        )
        for index, span in enumerate(spans)
        if span.is_uncertain
    ]
    views.sort(key=lambda view: view.start_ts, reverse=True)
    return views


def find_uncertain_span(store: EventStore, span_id: str, config: SegmentConfig) -> Span | None:
    """Locate an uncertain span by the id a previous derivation handed out.

    Ids are derived rather than stored, so this re-derives and looks for a match instead of
    reversing the id. The id is a pure function of the span's identity, so a span that has
    not changed is found again; one that has changed is genuinely no longer the span the
    client was told about, and a 404 is the truthful answer.
    """
    window = full_range(store)
    if window is None:
        return None
    for span in derive_spans(store, window[0], window[1], config):
        if span.id == span_id and span.is_uncertain:
            return span
    return None


def resolve_span(
    store: EventStore,
    span_id: str,
    category: str,
    label: str,
    config: SegmentConfig,
    *,
    now: datetime | None = None,
) -> Span | None:
    """Record the user's answer and return the session it re-segments into.

    Returns None when the id matches no uncertain span and no previous answer — the caller
    turns that into a 404.

    A repeat POST for the same span is not an error. The span is no longer uncertain after
    the first one, so re-deriving would not find it; the stored resolution is used instead,
    which makes the endpoint safe for a client that retried a request whose response it never
    saw.
    """
    now = now or datetime.now(timezone.utc)
    existing = store.resolution(span_id)

    if existing is not None:
        resolution = Resolution(
            span_id=span_id,
            device_id=existing.device_id,
            start_ts=existing.start_ts,
            end_ts=existing.end_ts,
            category=category,
            label=label,
            resolved_at=now,
        )
        window_title = None
        evidence = ""
        process = None
    else:
        span = find_uncertain_span(store, span_id, config)
        if span is None:
            return None
        resolution = Resolution(
            span_id=span_id,
            device_id=span.device_id,
            start_ts=span.start_ts,
            end_ts=span.end_ts,
            category=category,
            label=label,
            resolved_at=now,
        )
        process = span.process
        window_title = span.coverage[0].event.window_title
        evidence = evidence_summary(span.evidence)

    store.save_resolution(resolution)

    if process is not None:
        # The answer is only generalisable if there is a process to generalise it over. A
        # Tier 3 voice check-in has none, and there is nothing a classifier could key on.
        store.save_few_shot(
            FewShotExample(
                process=process,
                category=category,
                label=label,
                evidence=evidence,
                window_title=window_title,
                source_span_id=span_id,
                created_at=now,
            )
        )

    spans = derive_spans(
        store,
        resolution.start_ts - RESOLVE_CONTEXT,
        resolution.end_ts + RESOLVE_CONTEXT,
        config,
    )
    for span in spans:
        if span.device_id == resolution.device_id and (
            span.start_ts <= resolution.start_ts < span.end_ts
        ):
            return span
    return None
