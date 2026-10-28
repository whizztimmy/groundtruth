"""Timestamp handling.

The contract says every timestamp is ISO 8601 with an explicit UTC offset, which leaves two
decisions to make once and then never revisit: what goes into SQLite, and what comes back
out over the wire.

Storage is fixed-width UTC text so that SQLite's lexicographic `<` is also chronological
`<`. That is what makes `WHERE ts BETWEEN ? AND ?` an index range scan instead of a table
scan with a conversion per row, and it is why the width is fixed even when the microseconds
are zero.
"""

from __future__ import annotations

from datetime import datetime, timezone

STORAGE_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"
"""Fixed-width UTC text. Never varies, so string order is time order."""


def to_utc(value: datetime) -> datetime:
    """Normalise to UTC, rejecting naive datetimes.

    A naive timestamp from one device cannot be compared to an aware one from another, and
    sync exists to interleave exactly those two streams. Guessing a zone here would make
    cross-device ordering silently wrong, so the caller has to be explicit.
    """
    if value.tzinfo is None:
        raise ValueError("timestamps must carry an explicit UTC offset")
    return value.astimezone(timezone.utc)


def to_storage(value: datetime) -> str:
    """Render for the `ts` column."""
    return to_utc(value).strftime(STORAGE_FORMAT)


def from_storage(value: str) -> datetime:
    """Parse the `ts` column back."""
    return datetime.strptime(value, STORAGE_FORMAT).replace(tzinfo=timezone.utc)


def to_iso(value: datetime) -> str:
    """Render for a JSON response.

    Trailing `Z` with the microseconds dropped when they are zero, which is the form the
    contract's examples use and the form a `datetime`-parsing client expects.
    """
    utc = to_utc(value)
    if utc.microsecond:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")
