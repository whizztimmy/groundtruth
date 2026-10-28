"""The five endpoints of `docs/API.md`.

Thin on purpose. Every handler validates, calls into `derive.py`, and translates the result
to the wire shape; the decisions all live in `segment.py` and `summarize.py`, where they can
be tested without a client. If a rule about sessions ever needs reading from this file, it
has been put in the wrong place.

Note what is absent: there is no endpoint that accepts an image, and there is no code here
that could decode one. The agent's Tier 2 sees pixels on the device and discards them; the
server's job is to hold labels. An upload endpoint would not be a feature, it would be the
privacy model failing.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from http import HTTPStatus

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, settings_from_env
from .derive import derive_spans, derive_uncertain, resolve_span, select_spans
from .models import Classification, Event, Tier
from .schemas import (
    EventBatchIn,
    GroupByParam,
    IngestAck,
    Problem,
    ResolveIn,
    SessionOut,
    SessionsOut,
    SummaryOut,
    UncertainListOut,
    UncertainOut,
)
from .store import EventStore
from .summarize import summarize

PROBLEM_JSON = "application/problem+json"

UNPROCESSABLE = 422
"""Spelled out rather than taken from `starlette.status`.

Starlette is mid-rename on this constant — `HTTP_422_UNPROCESSABLE_ENTITY` is deprecated in
favour of `HTTP_422_UNPROCESSABLE_CONTENT` — and the number is not going to change.
"""


def _problem(status_code: int, detail: str | None = None, **extra: object) -> JSONResponse:
    """Render an error as problem+json.

    `about:blank` is the RFC's own answer for a problem whose type adds nothing beyond the
    status code, which is every error this API can raise: the contract reserves the one
    interesting code, `409`, for a device-id collision.
    """
    phrase = HTTPStatus(status_code).phrase
    body = Problem(title=phrase, status=status_code, detail=detail).model_dump()
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_JSON)


def _to_events(batch: EventBatchIn) -> list[Event]:
    """Translate a validated batch into domain events.

    `device_id` comes off the envelope, which is the only place it exists — a device writes
    its own stream and nothing else, so there is no per-event device to disagree with it.
    """
    return [
        Event(
            ts=item.ts,
            device_id=batch.device_id,
            tier=Tier(item.tier),
            classification=Classification(
                label=item.label,
                category=item.category,
                confidence=item.confidence,
                evidence=item.evidence,
            ),
            process=item.process,
            window_title=item.window_title,
        )
        for item in batch.events
    ]


def create_app(store: EventStore | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the app.

    Takes its store rather than opening one, so the tests drive the real handlers against a
    temporary database instead of a mock that could drift from the thing it stands in for.
    """
    settings = settings or settings_from_env()
    owned = store is None
    store = store or EventStore(settings.db_path)
    segment_config = settings.segment

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """Close the store on shutdown, but only the one this app opened.

        A store handed in by a caller — the tests, or an embedding process — belongs to
        whoever created it; closing it here would be reaching into someone else's resource.
        """
        yield
        if owned:
            store.close()

    app = FastAPI(
        title="GroundTruth sync",
        version="0.1.0",
        description=(
            "Append-only event sync and derived sessions. The server never sees pixels: no "
            "endpoint accepts an image."
        ),
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.settings = settings

    @app.exception_handler(StarletteHTTPException)
    def _http_problem(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(exc.status_code, exc.detail if isinstance(exc.detail, str) else None)

    @app.exception_handler(RequestValidationError)
    def _validation_problem(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            UNPROCESSABLE,
            "The request did not match the contract in docs/API.md.",
            errors=exc.errors(),
        )

    def get_store() -> EventStore:
        return store

    def window(
        from_: datetime = Query(alias="from", description="ISO 8601 with a UTC offset"),
        to: datetime = Query(description="ISO 8601 with a UTC offset"),
    ) -> tuple[datetime, datetime]:
        """Validate a query window.

        Both bounds are required because the contract lists them without marking them
        optional, unlike `device_id` and `category` beside them. An unbounded default would
        make an accidental full-history scan the easiest request to send.
        """
        if from_.tzinfo is None or to.tzinfo is None:
            raise HTTPException(
                UNPROCESSABLE,
                "from and to must carry an explicit UTC offset",
            )
        if to < from_:
            raise HTTPException(UNPROCESSABLE, "to precedes from")
        return (from_, to)

    @app.post(
        "/events",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=IngestAck,
        summary="Append a batch of events",
    )
    def post_events(
        batch: EventBatchIn,
        events_store: EventStore = Depends(get_store),
    ) -> IngestAck:
        accepted, duplicates = events_store.append(_to_events(batch))
        return IngestAck(accepted=accepted, duplicates=duplicates)

    @app.get("/sessions", response_model=SessionsOut, summary="Derived session spans")
    def get_sessions(
        bounds: tuple[datetime, datetime] = Depends(window),
        device_id: str | None = Query(default=None),
        category: str | None = Query(default=None),
        events_store: EventStore = Depends(get_store),
    ) -> SessionsOut:
        spans = derive_spans(events_store, bounds[0], bounds[1], segment_config)
        selected = select_spans(spans, device_id=device_id, category=category)
        return SessionsOut(sessions=[SessionOut.of(span) for span in selected])

    @app.get("/summary", response_model=SummaryOut, summary="Totals, real and naive")
    def get_summary(
        bounds: tuple[datetime, datetime] = Depends(window),
        group_by: GroupByParam = Query(default="category"),
        events_store: EventStore = Depends(get_store),
    ) -> SummaryOut:
        spans = derive_spans(events_store, bounds[0], bounds[1], segment_config)
        return SummaryOut.of(bounds[0], bounds[1], summarize(spans, group_by=group_by))

    @app.get("/uncertain", response_model=UncertainListOut, summary="Spans awaiting an answer")
    def get_uncertain(
        events_store: EventStore = Depends(get_store),
    ) -> UncertainListOut:
        return UncertainListOut(
            spans=[
                UncertainOut(
                    id=view.id,
                    start_ts=view.start_ts,
                    end_ts=view.end_ts,
                    process=view.process,
                    evidence=view.evidence,
                    suggestions=list(view.suggestions),
                )
                for view in derive_uncertain(events_store, segment_config)
            ]
        )

    @app.post(
        "/uncertain/{span_id}/resolve",
        response_model=SessionOut,
        summary="Answer an uncertain span",
    )
    def post_resolve(
        span_id: str,
        answer: ResolveIn,
        events_store: EventStore = Depends(get_store),
    ) -> SessionOut:
        span = resolve_span(events_store, span_id, answer.category, answer.label, segment_config)
        if span is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no uncertain span {span_id!r}")
        return SessionOut.of(span)

    @app.get("/health", include_in_schema=False)
    def health(events_store: EventStore = Depends(get_store)) -> dict[str, object]:
        """Not part of the contract. Here so a deployment can be checked without writing."""
        return {"status": "ok", "events": events_store.count_events()}

    return app


app = create_app()
"""Module-level app for `uvicorn groundtruth_server.app:app`."""
