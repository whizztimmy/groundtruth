"""Churn: how much a tracked region's contents replaced themselves between frames.

This is the measurement the whole project rests on. Every existing activity tracker reads
a browser as one undifferentiated thing because a single frame genuinely cannot tell
reading from scrolling — the pixels look alike. The difference is in the motion:

- **Reading** holds content still. The region's mask is stable frame to frame.
- **Scrolling** replaces content continuously. The mask churns inside a fixed area.

So churn is a scalar in [0, 1] over a *pair* of frames for the same tracked region.

It is necessary but not sufficient. Churn says *that* the screen changed, never *who*
changed it — a build log scrolling its own output is indistinguishable here from a feed
being pulled past. `motion.py` pairs this with an input signal to separate them.

Deliberately pure and numpy-free at the boundary: masks come in as sets of coordinates,
so the policy can be tested without a GPU, a screen, or SAM.
"""

from __future__ import annotations

from collections.abc import Iterable

Mask = frozenset[tuple[int, int]]
"""A region's occupied pixels. Sparse on purpose — a scrolled viewport changes a modest
fraction of its area, so a set beats a dense array for both memory and clarity."""


def as_mask(points: Iterable[tuple[int, int]]) -> Mask:
    return frozenset(points)


def churn(previous: Mask, current: Mask) -> float:
    """Jaccard distance between consecutive masks for one tracked region.

    Returns 0.0 for an identical mask and approaches 1.0 as the contents fully replace
    themselves. Jaccard rather than raw pixel count because it is scale-invariant: a
    small chat panel and a full-screen feed must produce comparable numbers, or the
    thresholds below would have to be per-region.

    Two empty masks are 0.0, not undefined — a region that was empty and stayed empty
    did not change, which is the answer the segmenter wants.
    """
    if not previous and not current:
        return 0.0

    union = len(previous | current)
    if union == 0:
        return 0.0

    intersection = len(previous & current)
    return 1.0 - (intersection / union)


# Thresholds are starting points, not findings. They need calibration against a real
# screen recording before the demo — see docs/tier2.md. Encoded here so there is one
# place to change them rather than magic numbers scattered through the classifier.
STATIC = 0.15
"""At or below: content is being held. Reading, watching a paused frame, idling on a page."""

ACTIVE = 0.55
"""At or above: content is being replaced continuously. Scrolling, or video playback."""


def describe(value: float) -> str:
    """Human-readable band, for the evidence string shown to the user.

    The classifier is asked to justify its label, and "high region churn" is more
    legible in a tooltip than "0.73". The user is the audience here, not a log parser.
    """
    if value <= STATIC:
        return "static"
    if value >= ACTIVE:
        return "churning"
    return "shifting"


# Interpreting a run of churn values lives in `motion.py`, not here, and deliberately
# requires an input signal alongside it. Churn on its own cannot tell a social feed from
# a build log scrolling its own output — both churn hard, and only one of them has a
# person driving it. This module measures; motion.py interprets.
