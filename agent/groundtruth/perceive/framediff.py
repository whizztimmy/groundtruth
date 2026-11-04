"""Whole-screen churn measured locally, with no model and no network.

`regions.py` measures churn per tracked region using SAM 3.1, which is the better signal
because it can say *which* part of the screen was replaced. It needs a SAM endpoint, and
as of this writing none is reachable with the credentials this project has - Baseten's
Model APIs serve text models only, and there is no GPU here to self-host on.

So this computes the same underlying quantity the cheap way: how much of the screen's
content was replaced between two consecutive frames. It loses region identity, so it
cannot separate a video playing in a corner from a feed filling the window. It keeps the
thing the classifier actually asked for. Both abstentions in the 2026-10-29 calibration
run cited the same absence, unprompted:

    no motion or input data to distinguish browsing, reading, or task context

That is this measurement, named by the model that wanted it.

## It holds a digest, never a second frame

Comparing consecutive frames naively means keeping the previous frame, which would break
the capture invariant: one in-memory buffer, overwritten, nothing retained. So only a
digest survives a sample - the frame reduced to `COLS x ROWS` mean luminances, 2,304
bytes. A screen cannot be reconstructed from it, it never touches disk, and it is never
sent anywhere. The signal is strictly cheaper *and* strictly less revealing than the SAM
path it stands in for, which is the unusual case where the fallback is better on privacy
than the design it replaces.
"""

from __future__ import annotations

from typing import Any

from . import grid

CELL_DELTA = 8
"""Mean-luminance change, out of 255, before a cell counts as replaced.

This exists for *global* drift, not small local edits. A backlight step, a theme
animation or a gradient repaint moves every cell slightly at once; with no threshold that
reads as 100% churn on an untouched screen, which is indistinguishable from a feed being
scrolled. Set this to 0 and `test_a_uniform_dimming_is_not_churn` fails.

Small local changes need no threshold to be harmless. A blinking caret lights one or two
cells out of 2,304, so the ratio alone puts it three orders of magnitude below `STATIC` -
the grid does that work, not this number. Raising this to chase such changes would start
discarding real text replacement instead.
"""

Digest = tuple[int, ...]


def digest(frame: Any) -> Digest:
    """Reduce a frame to one mean luminance per grid cell.

    `Image.BOX` resampling is an exact area average, so each output pixel *is* the mean of
    the cell it covers. A sharper filter would weight cell centres and make the reading
    depend on where content happened to land within a cell.
    """
    from PIL import Image

    small = frame.convert("L").resize((grid.COLS, grid.ROWS), Image.BOX)
    # tobytes rather than getdata: one byte per pixel in mode "L", and getdata is
    # deprecated from Pillow 14.
    return tuple(small.tobytes())


def changed_cells(previous: Digest, current: Digest, *, delta: int = CELL_DELTA) -> grid.Grid:
    """Cells whose content was replaced between the two frames.

    Returns an empty grid on a length mismatch rather than raising. A resolution change
    mid-session - a monitor switch, a window moving to a different display - makes the two
    digests incomparable, and reporting "no churn" for one sample is the right cost. The
    alternative, a spurious 100% reading, would look exactly like a feed being scrolled.
    """
    if len(previous) != len(current) or not previous:
        return frozenset()

    return frozenset(
        (i % grid.COLS, i // grid.COLS)
        for i, (a, b) in enumerate(zip(previous, current))
        if abs(a - b) >= delta
    )


def churn_of(previous: Digest, current: Digest, *, delta: int = CELL_DELTA) -> float:
    """Fraction of the screen replaced, in [0, 1].

    Comparable with `churn.STATIC` and `churn.ACTIVE` because both are ratios over cells,
    which is what makes this substitutable for a region's churn in `motion.classify`
    without retuning the thresholds.
    """
    return grid.coverage(changed_cells(previous, current, delta=delta))
