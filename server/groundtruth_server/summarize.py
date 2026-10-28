"""Totals for a window, including what a conventional tracker would have said.

`naive_by_process` is the product's whole argument rendered as a second list: the same
seconds, grouped by application name instead of by activity. It is computed here, on the
server, for a reason the contract states and this module has to honour — if the dashboard
derived it from the sessions it was given, the comparison would be between real data and
something the dashboard made up, and the one number the user is being asked to distrust
would be the one number nobody checked.

Both lists come off the same `Coverage` records, so they sum to the same `total_tracked_s`
by construction. A disagreement between them is therefore a bug here and not a rounding
artefact somewhere downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from collections.abc import Iterable

from .models import OFF_SCREEN_PROCESS
from .segment import Span

GroupBy = Literal["category", "label"]

SHARE_PRECISION = 4
"""Shares are rounded for the wire, not for arithmetic.

Four places survives a dashboard rendering a percentage to one decimal, and keeps the
response from carrying sixteen digits of float noise that imply a precision the underlying
sampling does not have.
"""


@dataclass(frozen=True, slots=True)
class Group:
    """One row of a total."""

    key: str
    duration_s: float
    share: float


@dataclass(frozen=True, slots=True)
class Summary:
    """Both sides of the comparison for one window."""

    total_tracked_s: float
    groups: tuple[Group, ...]
    naive_by_process: tuple[Group, ...]


def _rank(weights: dict[str, float], total_s: float) -> tuple[Group, ...]:
    """Longest first, then alphabetical.

    The secondary sort is not cosmetic: two categories with identical durations would
    otherwise swap places between identical requests, and a dashboard that animates its rows
    would flicker for no reason.
    """
    rows = [
        Group(
            key=key,
            duration_s=seconds,
            share=round(seconds / total_s, SHARE_PRECISION) if total_s > 0 else 0.0,
        )
        for key, seconds in weights.items()
    ]
    rows.sort(key=lambda row: (-row.duration_s, row.key))
    return tuple(rows)


def summarize(spans: Iterable[Span], *, group_by: GroupBy = "category") -> Summary:
    """Total a window's spans by activity, and the same seconds by process.

    Both groupings are accumulated per *event*, not per session, and for `group_by="label"`
    that is the difference between a right answer and a plausible one. A session is
    homogeneous in category but not in label — "reading API documentation" and "comparing
    inference providers" are both research and can sit in one span — so totalling by the
    span's headline label would quietly credit the whole session to whichever label held the
    longer stretch. Per event, the minority label keeps its minutes.

    For `group_by="category"` the two are identical, because the segmenter closes a span on
    any category change. Doing it the same way for both is one code path instead of two that
    have to be kept in agreement.
    """
    real: dict[str, float] = {}
    naive: dict[str, float] = {}
    total_s = 0.0

    for span in spans:
        total_s += span.duration_s
        for item in span.coverage:
            key = item.event.category if group_by == "category" else item.event.label
            real[key] = real.get(key, 0.0) + item.seconds
            # Tier 3 has no foreground process because there was no foreground: a voice
            # check-in from the gym is exactly the time an app-name tracker cannot see. It is
            # bucketed rather than dropped so the blind spot is visible in the comparison
            # instead of quietly shrinking the naive denominator.
            process = item.event.process or OFF_SCREEN_PROCESS
            naive[process] = naive.get(process, 0.0) + item.seconds

    return Summary(
        total_tracked_s=total_s,
        groups=_rank(real, total_s),
        naive_by_process=_rank(naive, total_s),
    )
