"""Durable local queue of events awaiting sync.

The agent is offline-first because the alternative is unacceptable: a laptop that was on a
train for two hours must not come back with a two-hour hole in the day. So every event is
written to disk *before* any attempt to send it, and removed only once the server has
confirmed receipt.

That ordering gives at-least-once delivery — a crash between send and confirm resends the
batch. Which is fine, and deliberate: `POST /events` is idempotent on `(device_id, ts)`,
so duplicates are counted rather than rejected. Choosing at-least-once over at-most-once
is the right trade when the failure modes are "one row counted twice" versus "an hour of
someone's life silently lost".

Only derived labels are stored here. No frame, mask, or pixel-derived feature is ever
written to disk anywhere in this project.

Those labels are still text a model wrote while looking at a screen, so `append` is the
gate where credential-shaped substrings are scrubbed out — see `redact.py`. It is done
here rather than at the classifier because this is the single point every stored and
synced string passes through, and a control placed anywhere upstream can be bypassed by
the next caller that forgets it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from .redact import scrub_event
from .types import Classification, Event, Tier

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    rowid_alias   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    device_id     TEXT    NOT NULL,
    tier          INTEGER NOT NULL,
    process       TEXT,
    window_title  TEXT,
    label         TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    confidence    REAL    NOT NULL,
    evidence      TEXT    NOT NULL,
    UNIQUE(device_id, ts)
);
CREATE INDEX IF NOT EXISTS pending_ts ON pending(ts);
"""


class EventBuffer:
    """SQLite-backed FIFO of unsynced events.

    SQLite rather than a JSONL file because a half-written line after a hard power loss is
    a corrupt record that has to be hand-parsed, while SQLite's write-ahead log gives the
    same durability with recovery already solved. The dependency is free — it is stdlib.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        # WAL so a sync read never blocks a capture write. The agent appends on a
        # two-second cadence and must not stall behind a slow flush.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EventBuffer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def append(self, event: Event) -> bool:
        """Persist one event. Returns False when it was already buffered.

        Duplicate `(device_id, ts)` is ignored rather than raised: the agent can
        legitimately re-observe the same instant after a clock adjustment or a restart
        mid-sample, and that is not an error worth crashing a day's capture over.
        """
        event = scrub_event(event)
        c = event.classification
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO pending
                (ts, device_id, tier, process, window_title,
                 label, category, confidence, evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.ts.isoformat(),
                event.device_id,
                int(event.tier),
                event.process,
                event.window_title,
                c.label,
                c.category,
                c.confidence,
                c.evidence,
            ),
        )
        return cursor.rowcount > 0

    def take(self, limit: int) -> list[tuple[int, Event]]:
        """Oldest `limit` events, paired with the row ids needed to confirm them.

        Does not remove anything. Removal is `confirm`, and only after the server has
        acknowledged — that gap is what makes delivery at-least-once rather than
        at-most-once.
        """
        rows = self._conn.execute(
            "SELECT * FROM pending ORDER BY rowid_alias LIMIT ?", (limit,)
        ).fetchall()
        return [(row["rowid_alias"], _to_event(row)) for row in rows]

    def confirm(self, ids: Iterable[int]) -> int:
        """Drop rows the server has accepted."""
        ids = list(ids)
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cursor = self._conn.execute(
            f"DELETE FROM pending WHERE rowid_alias IN ({placeholders})", ids
        )
        return cursor.rowcount

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM pending").fetchone()
        return int(row["n"])

    def prune(self, keep_newest: int) -> int:
        """Drop the oldest rows beyond `keep_newest`.

        A safety valve for a device that has been offline for weeks: unbounded growth
        would eventually fill a disk, and losing the oldest unsynced events is a better
        failure than taking the machine down. Expected to do nothing in normal use.
        """
        cursor = self._conn.execute(
            """
            DELETE FROM pending WHERE rowid_alias NOT IN (
                SELECT rowid_alias FROM pending ORDER BY rowid_alias DESC LIMIT ?
            )
            """,
            (keep_newest,),
        )
        return cursor.rowcount


def _to_event(row: Sequence) -> Event:
    return Event(
        ts=datetime.fromisoformat(row["ts"]),
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
