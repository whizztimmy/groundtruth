"""Coarse occupancy grids for masks.

Churn is a Jaccard distance between consecutive masks, and the naive representation — the
set of occupied pixels — does not survive contact with a real screen. A 1024x576 frame has
590,000 pixels, and SAM 3.1 multiplexes up to 16 regions, so a single comparison could
touch nine million coordinates every couple of seconds. On a machine that is supposed to
be running quietly in the background, that is not acceptable.

So masks are reduced to a coarse grid before comparison. This is sound rather than merely
cheap: Jaccard is scale-invariant, so the ratio a coarse grid produces closely tracks the
one full resolution would give. What is lost is sub-cell detail — a caret blinking, one
word changing — and losing that is a feature. Those are not the changes that distinguish
reading from scrolling, and treating them as churn would make every text editor look like
a feed.
"""

from __future__ import annotations

from collections.abc import Iterable

Cell = tuple[int, int]
Grid = frozenset[Cell]

COLS = 64
ROWS = 36
"""Grid resolution, 16:9 to match typical displays.

2,304 cells. Fine enough that a scrolling viewport lights up a visibly different set of
cells between frames, coarse enough that a comparison is microseconds. Raising it buys
sensitivity to changes too small to indicate an activity.
"""


def from_pixels(
    points: Iterable[tuple[int, int]],
    *,
    width: int,
    height: int,
    cols: int = COLS,
    rows: int = ROWS,
) -> Grid:
    """Reduce pixel coordinates to occupied grid cells.

    A cell counts as occupied if any pixel falls in it. Coverage fraction is deliberately
    not tracked: it would make churn sensitive to antialiasing at region edges, which
    changes constantly and means nothing.
    """
    if width <= 0 or height <= 0:
        return frozenset()

    cell_w = width / cols
    cell_h = height / rows
    return frozenset(
        (
            min(cols - 1, max(0, int(x / cell_w))),
            min(rows - 1, max(0, int(y / cell_h))),
        )
        for x, y in points
    )


def from_bbox(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    cols: int = COLS,
    rows: int = ROWS,
) -> Grid:
    """Every cell a bounding box touches.

    The fallback for a backend that returns boxes rather than masks. Churn computed from
    boxes is far weaker — a feed scrolling inside a fixed box shows no change at all — so
    a caller relying on this should expect `motion.classify` to return `unclear` often,
    which is the honest outcome rather than a degraded guess.
    """
    left, top, right, bottom = bbox
    if right <= left or bottom <= top or width <= 0 or height <= 0:
        return frozenset()

    cell_w = width / cols
    cell_h = height / rows
    c0 = max(0, min(cols - 1, int(left / cell_w)))
    c1 = max(0, min(cols - 1, int((right - 1) / cell_w)))
    r0 = max(0, min(rows - 1, int(top / cell_h)))
    r1 = max(0, min(rows - 1, int((bottom - 1) / cell_h)))
    return frozenset(
        (c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)
    )


def coverage(grid: Grid, *, cols: int = COLS, rows: int = ROWS) -> float:
    """Fraction of the screen a region occupies, for ranking attention."""
    total = cols * rows
    return len(grid) / total if total else 0.0
