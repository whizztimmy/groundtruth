"""Wire models. These are `docs/API.md`, expressed as types.

Kept apart from `models.py` on purpose: the domain dataclasses mirror the agent's contract
and are free to grow, while these mirror a *frozen* document that a separately-built
dashboard is already coded against. When the two need to differ, they differ here, in one
translation step, rather than by the domain being bent to match the wire.

Timestamps are serialised through `to_iso` rather than left to the default encoder, so the
exact shape the contract shows — `2026-10-28T09:14:02Z` — is a property of this file and not
of whichever pydantic version is installed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

from .models import evidence_summary
from .segment import Span
from .summarize import Group, Summary
from .timeutil import to_iso

IsoDateTime = Annotated[AwareDatetime, PlainSerializer(to_iso, return_type=str)]
"""ISO 8601 in, ISO 8601 with a `Z` out. Naive datetimes are rejected at the boundary,
because a timestamp without an offset cannot be ordered against another device's."""

CONFIDENCE_PRECISION = 3
"""Rounding for confidences on the wire.

Three places is finer than the sampling justifies and coarse enough to stay readable; the
unrounded value is never persisted anyway, since it is re-derived on every read.
"""


class EventIn(BaseModel):
    """One classified observation, as `POST /events` carries it.

    `device_id` is absent here and present on the batch: a device writes only its own
    events, so putting it on each one would invite a batch that disagreed with itself.
    """

    model_config = ConfigDict(extra="forbid")

    ts: IsoDateTime
    tier: Annotated[int, Field(ge=1, le=3)]
    process: str | None = None
    window_title: str | None = None
    label: str
    category: str
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence: str


class EventBatchIn(BaseModel):
    """A sync batch."""

    model_config = ConfigDict(extra="forbid")

    device_id: Annotated[str, Field(min_length=1)]
    events: list[EventIn]


class IngestAck(BaseModel):
    """`202` for `POST /events`.

    `duplicates` is a success value. The agent retries whole batches after a flaky upload by
    design, so the overlap is expected traffic rather than a fault to report as one.
    """

    accepted: int
    duplicates: int


class SessionOut(BaseModel):
    """A derived span, as the dashboard renders it."""

    id: str
    start_ts: IsoDateTime
    end_ts: IsoDateTime
    device_id: str
    category: str
    label: str
    confidence: float
    event_count: int
    duration_s: float
    evidence_summary: str

    @classmethod
    def of(cls, span: Span) -> SessionOut:
        return cls(
            id=span.id,
            start_ts=span.start_ts,
            end_ts=span.end_ts,
            device_id=span.device_id,
            category=span.category,
            label=span.session.label,
            confidence=round(span.session.confidence, CONFIDENCE_PRECISION),
            event_count=span.session.event_count,
            duration_s=span.duration_s,
            evidence_summary=evidence_summary(span.evidence),
        )


class SessionsOut(BaseModel):
    """`200` for `GET /sessions`."""

    sessions: list[SessionOut]


class GroupOut(BaseModel):
    """One row of a total."""

    key: str
    duration_s: float
    share: float

    @classmethod
    def of(cls, group: Group) -> GroupOut:
        return cls(key=group.key, duration_s=group.duration_s, share=group.share)


class SummaryOut(BaseModel):
    """`200` for `GET /summary`.

    `from` is a Python keyword, so the field is `from_` and the alias carries the contract's
    spelling. Nothing outside this class ever sees the underscore.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: IsoDateTime = Field(alias="from")
    to: IsoDateTime
    total_tracked_s: float
    groups: list[GroupOut]
    naive_by_process: list[GroupOut]

    @classmethod
    def of(cls, start: datetime, end: datetime, summary: Summary) -> SummaryOut:
        return cls(
            from_=start,
            to=end,
            total_tracked_s=summary.total_tracked_s,
            groups=[GroupOut.of(group) for group in summary.groups],
            naive_by_process=[GroupOut.of(group) for group in summary.naive_by_process],
        )


class UncertainOut(BaseModel):
    """One span the classifier declined to guess at."""

    id: str
    start_ts: IsoDateTime
    end_ts: IsoDateTime
    process: str | None
    evidence: str
    suggestions: list[str]


class UncertainListOut(BaseModel):
    """`200` for `GET /uncertain`."""

    spans: list[UncertainOut]


class ResolveIn(BaseModel):
    """The user's answer to an uncertain span."""

    model_config = ConfigDict(extra="forbid")

    category: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]


class Problem(BaseModel):
    """RFC 9457 problem detail, as the contract's error section asks for."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None


GroupByParam = Literal["category", "label"]
