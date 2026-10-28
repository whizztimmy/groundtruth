"""Identifiers for derived objects.

Sessions are derived, never stored, so their identifiers cannot come from a database
sequence: the same day re-segmented tomorrow has to produce the same ids it produced
today, or every poll from the dashboard looks like a fresh set of sessions and the client
loses track of what it was already showing.

So ids are *derived* too — a ULID-shaped string whose timestamp half comes from the span's
start and whose random half is a hash of the span's identity. That keeps the two properties
the contract's `s_01JB...` examples imply (sortable by time, opaque to the client) while
being a pure function of the events underneath.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
"""Crockford base32, as ULID uses: no I, L, O or U, so ids survive being read aloud."""

_TIME_CHARS = 10
"""Characters for the 48-bit millisecond timestamp, matching ULID's layout."""

_TAIL_CHARS = 16
"""Characters for the 80-bit identity hash, matching ULID's random half."""


def _encode(value: int, length: int) -> str:
    """Render `value` as fixed-width Crockford base32, most significant digit first."""
    out = ["0"] * length
    for i in range(length - 1, -1, -1):
        out[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(out)


def derive_id(prefix: str, start: datetime, *identity: str) -> str:
    """Build a stable id for a derived span.

    `identity` is everything that makes this span the span it is. Two derivations that
    disagree about any of it should produce different ids — a re-labelled span really is a
    different session, and pretending otherwise would let a stale client cache a label the
    server no longer holds.

    The prefix is folded into the hash as well, so an uncertain span and the session it
    later becomes never collide on their random half.
    """
    millis = int(start.timestamp() * 1000) & ((1 << 48) - 1)
    digest = hashlib.blake2b(
        "\x1f".join((prefix, *identity)).encode("utf-8"), digest_size=10
    ).digest()
    tail = int.from_bytes(digest, "big")
    return f"{prefix}_{_encode(millis, _TIME_CHARS)}{_encode(tail, _TAIL_CHARS)}"
