"""Persistence. Events, the user's resolutions, and the few-shot examples they become.

Three things live here and only one of them is authoritative. Events are the ground the
rest stands on: append-only, device-owned, never edited. Resolutions are an overlay the
read path applies on the way to the segmenter. Sessions are not here at all, by design — a
`sessions` table would become the thing clients trusted, and then a taxonomy change would
have to migrate it instead of simply re-deriving it.

SQLite is used synchronously through the stdlib driver rather than through `aiosqlite`. The
work per request is a bounded index range scan and some arithmetic; wrapping that in an
event loop buys nothing and `async def` endpoints that then block the loop on `sqlite3`
would be worse than honest threadpool execution. FastAPI runs plain `def` endpoints in a
threadpool, so one connection guarded by a lock is both correct and the simplest thing that
is correct.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from collections.abc import Iterable, Sequence

from .models import (
    Classification,
    Event,
    FewShotExample,
    Resolution,
    Tier,
)
from .timeutil import from_storage, to_storage, to_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    device_id    TEXT    NOT NULL,
    ts           TEXT    NOT NULL,
    tier         INTEGER NOT NULL,
    process      TEXT,
    window_title TEXT,
    label        TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    confidence   REAL    NOT NULL,
    evidence     TEXT    NOT NULL,
    PRIMARY KEY (device_id, ts)
);

-- The primary key already indexes (device_id, ts), which serves per-device history and the
-- idempotency check. This covers the other access pattern: every read in the API is a time
-- range across all devices, because segmentation needs the interleaved stream to see a
-- handoff at all.
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);

CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resolutions (
    span_id     TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL,
    start_ts    TEXT NOT NULL,
    end_ts      TEXT NOT NULL,
    category    TEXT NOT NULL,
    label       TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resolutions_range
    ON resolutions (device_id, start_ts, end_ts);

-- Written by the server, read by the agent's Tier 2 classifier. Keyed by process because
-- that is the unit the user's answer generalises over: being told once what a particular
-- kind of browser tab is should stop Tier 2 asking about that browser again.
CREATE TABLE IF NOT EXISTS few_shot_examples (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    process        TEXT NOT NULL,
    category       TEXT NOT NULL,
    label          TEXT NOT NULL,
    evidence       TEXT NOT NULL,
    window_title   TEXT,
    source_span_id TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_few_shot_process ON few_shot_examples (process);
"""


def _event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        ts=from_storage(row["ts"]),
        device_id=row["device_id"],
        tier=Tier(row["tier"]),
        classification=Classification(
            label=row["label"],
            category=row["category"],
            confidence=row["confidence"],
            evidence=row["evidence"],
        ),
        process=row["process"],
        window_title=row["window_title"],
    )


class EventStore:
    """The server's only stateful component."""

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if path != ":memory:":
                # WAL lets the dashboard's reads run while an agent is mid-batch, which is
                # the normal case: one writer, several pollers.
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- events

    def append(self, events: Sequence[Event], *, now: datetime | None = None) -> tuple[int, int]:
        """Append a batch. Returns `(accepted, duplicates)`.

        Idempotent on `(device_id, ts)`, and the duplicate count is a success value rather
        than an error because the agent is offline-first: a flaky upload that half-succeeded
        is retried wholesale by design, and a server that treated the overlap as a fault
        would make the agent's own correct behaviour look like a bug.

        A conflict is still allowed to improve the stored row. A device that emitted a
        Tier 1 sample and then escalated to Tier 2 for the same instant has a better answer
        for that instant, and `Tier`'s ordering says so; it is counted as a duplicate — the
        contract has nowhere else to put it — but the better label wins.
        """
        if not events:
            return (0, 0)

        now = to_utc(now or datetime.now(timezone.utc))
        submitted = len(events)

        # Collapse within the batch first, on the same rule the table uses, so an agent that
        # retried inside a single request gets the same answer as one that retried across two.
        best: dict[tuple[str, str], Event] = {}
        for event in events:
            key = (event.device_id, to_storage(event.ts))
            incumbent = best.get(key)
            if incumbent is None or int(event.tier) > int(incumbent.tier):
                best[key] = event

        with self._lock:
            cursor = self._conn.cursor()
            existing: set[tuple[str, str]] = set()
            keys = list(best)
            # Chunked so a large batch cannot blow past SQLITE_MAX_VARIABLE_NUMBER.
            for start in range(0, len(keys), 400):
                chunk = keys[start : start + 400]
                placeholders = ",".join("(?,?)" for _ in chunk)
                flat = [part for key in chunk for part in key]
                rows = cursor.execute(
                    f"SELECT device_id, ts FROM events WHERE (device_id, ts) IN ({placeholders})",
                    flat,
                ).fetchall()
                existing.update((row["device_id"], row["ts"]) for row in rows)

            accepted = sum(1 for key in best if key not in existing)

            cursor.executemany(
                """
                INSERT INTO events
                    (device_id, ts, tier, process, window_title,
                     label, category, confidence, evidence)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT (device_id, ts) DO UPDATE SET
                    tier         = excluded.tier,
                    process      = excluded.process,
                    window_title = excluded.window_title,
                    label        = excluded.label,
                    category     = excluded.category,
                    confidence   = excluded.confidence,
                    evidence     = excluded.evidence
                WHERE excluded.tier > events.tier
                """,
                [
                    (
                        event.device_id,
                        to_storage(event.ts),
                        int(event.tier),
                        event.process,
                        event.window_title,
                        event.classification.label,
                        event.classification.category,
                        event.classification.confidence,
                        event.classification.evidence,
                    )
                    for event in best.values()
                ],
            )

            for device_id in {event.device_id for event in best.values()}:
                cursor.execute(
                    """
                    INSERT INTO devices (device_id, first_seen, last_seen) VALUES (?,?,?)
                    ON CONFLICT (device_id) DO UPDATE SET last_seen = excluded.last_seen
                    """,
                    (device_id, to_storage(now), to_storage(now)),
                )
            self._conn.commit()

        return (accepted, submitted - accepted)

    def events_between(self, start: datetime, end: datetime) -> tuple[Event, ...]:
        """Every device's events in `[start, end]`, in stream order.

        Deliberately not filtered by device. Segmentation has to see the interleaved stream
        to notice a handoff at all, so narrowing by device belongs after derivation, not
        before it.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE ts >= ? AND ts <= ? ORDER BY ts, device_id",
                (to_storage(start), to_storage(end)),
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def event_bounds(self) -> tuple[datetime, datetime] | None:
        """Earliest and latest event, or None when the store is empty."""
        with self._lock:
            row = self._conn.execute("SELECT MIN(ts) AS lo, MAX(ts) AS hi FROM events").fetchone()
        if row is None or row["lo"] is None:
            return None
        return (from_storage(row["lo"]), from_storage(row["hi"]))

    def count_events(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    # ----------------------------------------------------------- resolutions

    def resolutions_between(self, start: datetime, end: datetime) -> tuple[Resolution, ...]:
        """Resolutions overlapping `[start, end]`."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM resolutions
                WHERE start_ts <= ? AND end_ts >= ?
                ORDER BY resolved_at
                """,
                (to_storage(end), to_storage(start)),
            ).fetchall()
        return tuple(_resolution_from_row(row) for row in rows)

    def resolution(self, span_id: str) -> Resolution | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM resolutions WHERE span_id = ?", (span_id,)
            ).fetchone()
        return _resolution_from_row(row) if row is not None else None

    def save_resolution(self, resolution: Resolution) -> None:
        """Record an answer, superseding any earlier answer for the same span.

        Upsert rather than insert: a user who corrects themselves is not an error case, and
        the second answer has to win without the first one having to be found and deleted.
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO resolutions
                    (span_id, device_id, start_ts, end_ts, category, label, resolved_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT (span_id) DO UPDATE SET
                    category    = excluded.category,
                    label       = excluded.label,
                    resolved_at = excluded.resolved_at
                """,
                (
                    resolution.span_id,
                    resolution.device_id,
                    to_storage(resolution.start_ts),
                    to_storage(resolution.end_ts),
                    resolution.category,
                    resolution.label,
                    to_storage(resolution.resolved_at),
                ),
            )
            self._conn.commit()

    # ------------------------------------------------------ few-shot examples

    def save_few_shot(self, example: FewShotExample) -> None:
        """Append a confirmed label for a process.

        One row per resolution rather than one row per process: the classifier wants several
        examples, and the history of how a user has answered is more useful as a set of
        examples than as a single overwritten latest answer.
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO few_shot_examples
                    (process, category, label, evidence, window_title,
                     source_span_id, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    example.process,
                    example.category,
                    example.label,
                    example.evidence,
                    example.window_title,
                    example.source_span_id,
                    to_storage(example.created_at),
                ),
            )
            self._conn.commit()

    def few_shot_for(self, process: str, *, limit: int = 8) -> tuple[FewShotExample, ...]:
        """Most recent confirmed labels for a process, newest first."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM few_shot_examples
                WHERE process = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (process, limit),
            ).fetchall()
        return tuple(_few_shot_from_row(row) for row in rows)

    def few_shot_all(self) -> tuple[FewShotExample, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM few_shot_examples ORDER BY created_at, id"
            ).fetchall()
        return tuple(_few_shot_from_row(row) for row in rows)


def _resolution_from_row(row: sqlite3.Row) -> Resolution:
    return Resolution(
        span_id=row["span_id"],
        device_id=row["device_id"],
        start_ts=from_storage(row["start_ts"]),
        end_ts=from_storage(row["end_ts"]),
        category=row["category"],
        label=row["label"],
        resolved_at=from_storage(row["resolved_at"]),
    )


def _few_shot_from_row(row: sqlite3.Row) -> FewShotExample:
    return FewShotExample(
        process=row["process"],
        category=row["category"],
        label=row["label"],
        evidence=row["evidence"],
        window_title=row["window_title"],
        source_span_id=row["source_span_id"],
        created_at=from_storage(row["created_at"]),
    )


def events_from(device_id: str, raw: Iterable[dict]) -> tuple[Event, ...]:
    """Build domain events from already-validated wire dicts.

    Used by the fixture loader, which goes through the same construction the API uses so a
    fixture that loads is a fixture the API would have accepted.
    """
    return tuple(
        Event(
            ts=to_utc(item["ts"]),
            device_id=device_id,
            tier=Tier(item["tier"]),
            classification=Classification(
                label=item["label"],
                category=item["category"],
                confidence=item["confidence"],
                evidence=item.get("evidence", ""),
            ),
            process=item.get("process"),
            window_title=item.get("window_title"),
        )
        for item in raw
    )
