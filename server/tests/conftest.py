"""Shared test fixtures.

The server package is imported from the repo rather than from an installed wheel, so the
tests run against the working tree without a build step — which matters during a hackathon,
where an install you forgot to redo is a bug you spend an hour not finding.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from groundtruth_server.models import Classification, Event, Tier  # noqa: E402
from groundtruth_server.segment import SegmentConfig  # noqa: E402
from groundtruth_server.store import EventStore  # noqa: E402

T0 = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
"""Arbitrary but fixed. Tests speak in offsets from it so the arithmetic is visible."""

DEVICE = "desk-win-01"


def ev(
    offset_s: float,
    category: str,
    *,
    device: str = DEVICE,
    process: str | None = "chrome.exe",
    confidence: float = 0.9,
    label: str | None = None,
    tier: Tier = Tier.SCREEN,
    evidence: str = "evidence",
    window_title: str | None = "a window",
) -> Event:
    """Build one event `offset_s` seconds after `T0`.

    `label` defaults to the category because most tests do not care about the distinction,
    and the ones that do say so explicitly.
    """
    return Event(
        ts=T0 + timedelta(seconds=offset_s),
        device_id=device,
        tier=tier,
        classification=Classification(
            label=label if label is not None else category,
            category=category,
            confidence=confidence,
            evidence=evidence,
        ),
        process=process,
        window_title=window_title,
    )


def run(offsets: range | list[float], category: str, **kwargs) -> list[Event]:
    """A stretch of same-category events."""
    return [ev(offset, category, **kwargs) for offset in offsets]


@pytest.fixture
def config() -> SegmentConfig:
    return SegmentConfig()


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    """A real SQLite store on disk.

    On disk rather than `:memory:` so the tests exercise the same driver configuration,
    pragmas and index plans the server runs with.
    """
    instance = EventStore(str(tmp_path / "test.db"))
    yield instance
    instance.close()
