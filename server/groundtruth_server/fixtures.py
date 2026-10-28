"""Load `fixtures/day.json` into a store, or into a running server.

The fixture is the dashboard's development data, so it has to arrive by the same route a
real agent's events would: each entry of `batches` is literally a `POST /events` body, and
the loader validates it through `EventBatchIn` before writing. A fixture that loads is
therefore a fixture the API would have accepted, which is the only version of "loads" worth
having — one that bypassed validation could drift out of contract without anything failing.

    python -m groundtruth_server.fixtures --db demo.db
    python -m groundtruth_server.fixtures --server http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from collections.abc import Sequence

from .config import FIXTURE_DAY
from .models import Classification, Event, Tier
from .schemas import EventBatchIn
from .store import EventStore

HTTP_TIMEOUT_S = 30.0


def read_batches(path: Path) -> list[EventBatchIn]:
    """Parse and validate a fixture file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [EventBatchIn.model_validate(batch) for batch in payload["batches"]]


def to_events(batch: EventBatchIn) -> list[Event]:
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


def load_into_store(store: EventStore, batches: Sequence[EventBatchIn]) -> tuple[int, int]:
    """Write batches straight to a store. Returns `(accepted, duplicates)`."""
    accepted = duplicates = 0
    for batch in batches:
        added, dupes = store.append(to_events(batch))
        accepted += added
        duplicates += dupes
    return (accepted, duplicates)


def load_over_http(base_url: str, batches: Sequence[EventBatchIn]) -> tuple[int, int]:
    """POST batches to a running server.

    Uses `urllib` rather than a client library so seeding a demo needs nothing installed
    beyond the server's own runtime dependencies.
    """
    accepted = duplicates = 0
    for batch in batches:
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/events",
            data=batch.model_dump_json().encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            body = json.loads(response.read())
        accepted += body["accepted"]
        duplicates += body["duplicates"]
    return (accepted, duplicates)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE_DAY)
    parser.add_argument(
        "--db",
        default="groundtruth.db",
        help="SQLite path to write directly. Ignored when --server is given.",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Base URL of a running server to POST to instead of writing a database.",
    )
    args = parser.parse_args(argv)

    batches = read_batches(args.fixture)
    total = sum(len(batch.events) for batch in batches)

    if args.server:
        try:
            accepted, duplicates = load_over_http(args.server, batches)
        except urllib.error.URLError as exc:
            parser.error(f"could not reach {args.server}: {exc}")
        target = args.server
    else:
        store = EventStore(args.db)
        try:
            accepted, duplicates = load_into_store(store, batches)
        finally:
            store.close()
        target = args.db

    print(
        f"{args.fixture.name}: {total} events across {len(batches)} device(s) -> {target} "
        f"(accepted {accepted}, duplicates {duplicates})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
