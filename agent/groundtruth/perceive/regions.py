"""Tier 2 perception: screen regions as a video scene, via SAM 3.1.

The interesting claim of this module is that the desktop is a *video*, not a sequence of
screenshots. SAM 3.1's object multiplexing tracks up to 16 objects in one forward pass
with stable track ids across frames, and that stability is what makes churn measurable.

Churn is the payoff, and it only exists in motion. A feed scrolling replaces content
inside a fixed region; reading holds it. Neither is visible in a single frame, which is
why every app-name tracker gets this wrong.

Frames live in memory only. Nothing here writes an image to disk.

## On the wire format

`_parse_detections` is the one function in this project written against documentation
rather than against a running endpoint, because SAM 3.1 serving was not yet provisioned
when it was written. It therefore accepts several plausible shapes — RLE, polygon, raw
mask, bare box — rather than committing to one.

That is deliberate containment, not indecision: when a real endpoint lands, exactly one
function needs to change, and every test above it still holds. Everything else in this
module — tracking, grid reduction, churn, attention ranking — is verified against
synthetic detections and does not depend on the wire format at all.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
from typing import Any

from ..backends import Backend, BackendError, chat
from ..types import Region
from . import grid
from .churn import churn as churn_between

log = logging.getLogger("groundtruth.regions")

MAX_TRACKED = 16
"""SAM 3.1 multiplex width. Regions beyond this are dropped by descending area, on the
assumption that a region too small to rank holds too little attention to matter."""

REGION_PROMPTS: tuple[str, ...] = (
    "text editor pane",
    "terminal window",
    "web page content",
    "video player",
    "chat panel",
    "image or design canvas",
    "notification popup",
)
"""Text prompts for SAM 3.1's concept segmentation.

Deliberately generic: these describe *kinds of surface*, not applications, so the same set
works across a browser, a native app and a remote desktop. Tuning these is the cheapest
lever on Tier 2 quality.
"""


class RegionTracker:
    """Stateful tracker over consecutive frames.

    Holds the previous frame's grids so churn can be computed as a delta. One instance per
    display; sharing one across monitors would collide track ids.
    """

    def __init__(self, backend: Backend, *, prompts: tuple[str, ...] = REGION_PROMPTS):
        if not backend.placement.may_see_pixels:
            # Checked at construction as well as per call, so a misconfigured tracker
            # fails at startup rather than silently at the first frame.
            raise BackendError(
                f"SAM backend placement is {backend.placement.value}; region tracking "
                "sees screen content and requires 'local' or 'self_hosted'"
            )
        self._backend = backend
        self._prompts = prompts
        self._previous: dict[int, grid.Grid] = {}

    def reset(self) -> None:
        """Drop tracking state.

        Called on display change or after an idle gap, where track ids from before no
        longer refer to the same on-screen things. Without this, the first frame after a
        gap would report enormous churn for regions that merely stopped existing.
        """
        self._previous.clear()

    async def track(self, frame: Any) -> tuple[Region, ...]:
        """Segment and track regions in `frame`, with churn filled in.

        `frame` is a PIL Image; it is consumed and not retained.

        A track id seen for the first time reports churn 0.0 rather than 1.0. It has no
        predecessor, and scoring a newly appeared region as maximally churning would make
        every window switch look like scrolling.
        """
        detections = await self._detect(frame)
        width, height = frame.size

        current: dict[int, grid.Grid] = {}
        regions: list[Region] = []

        for det in detections[:MAX_TRACKED]:
            cells = _to_grid(det, width=width, height=height)
            if not cells:
                continue
            track_id = det["track_id"]
            current[track_id] = cells

            previous = self._previous.get(track_id)
            value = churn_between(previous, cells) if previous is not None else 0.0

            regions.append(
                Region(
                    track_id=track_id,
                    label=det.get("label", "region"),
                    bbox=det["bbox"],
                    area_frac=grid.coverage(cells),
                    churn=value,
                    has_focus=bool(det.get("has_focus", False)),
                )
            )

        # Replaced wholesale rather than merged: a track id absent from this frame is
        # gone, and keeping its stale grid would produce a spurious churn reading if the
        # id were later reused.
        self._previous = current
        return tuple(regions)

    async def _detect(self, frame: Any) -> list[dict[str, Any]]:
        from ..backends import vision

        prompt = (
            "Segment and track these regions: "
            + ", ".join(self._prompts)
            + ". Return JSON only."
        )
        try:
            raw = await vision(
                self._backend,
                system="You segment screen regions and return JSON.",
                user_text=prompt,
                frame=frame,
                max_tokens=3000,
            )
        except BackendError:
            raise
        return _parse_detections(raw)


def _to_grid(det: dict[str, Any], *, width: int, height: int) -> grid.Grid:
    """Best available occupancy for one detection.

    Prefers a real mask; falls back to the bounding box. The fallback is much weaker —
    a feed scrolling inside a fixed box registers no churn at all — so it is logged.
    """
    points = det.get("points")
    if points:
        return grid.from_pixels(points, width=width, height=height)
    log.debug("detection %s has no mask; falling back to bbox", det.get("track_id"))
    return grid.from_bbox(det["bbox"], width=width, height=height)


def _parse_detections(raw: str) -> list[dict[str, Any]]:
    """Normalise a segmentation response into detections.

    **This is the function to change against a live endpoint.** It accepts the shapes SAM
    3.1 and comparable services are documented to emit; everything downstream is tested
    against the normalised form and is unaffected by which one arrives.

    A detection is `{track_id, label, bbox, points?, has_focus?}`, where `points` is pixel
    coordinates when a mask was available.
    """
    import json

    text = raw.strip()
    if text.startswith("```"):
        # Models wrap JSON in fences regardless of instructions.
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text

    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackendError(f"segmentation response was not JSON: {text[:200]}") from exc

    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = body.get("objects") or body.get("detections") or body.get("masks") or []
    else:
        raise BackendError(f"segmentation response was not a list or object: {text[:200]}")

    detections: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        bbox = _parse_bbox(item)
        if bbox is None:
            continue
        detection: dict[str, Any] = {
            "track_id": int(item.get("track_id", item.get("id", index))),
            "label": str(item.get("label") or item.get("concept") or "region"),
            "bbox": bbox,
            "has_focus": bool(item.get("has_focus", False)),
        }
        points = _parse_mask(item, bbox)
        if points:
            detection["points"] = points
        detections.append(detection)

    return detections


def _parse_bbox(item: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """Bounding box as (left, top, right, bottom), from any of the usual spellings."""
    box = item.get("bbox") or item.get("box") or item.get("bounding_box")
    if not box or len(box) != 4:
        return None
    a, b, c, d = (int(v) for v in box)
    # [x, y, w, h] is as common as [x0, y0, x1, y1]. Width/height form is identifiable
    # because the last two values would otherwise sit left of or above the first two.
    if item.get("format") == "xywh" or (c <= a and d <= b):
        return (a, b, a + c, b + d)
    return (a, b, c, d)


def _parse_mask(
    item: dict[str, Any], bbox: tuple[int, int, int, int]
) -> list[tuple[int, int]] | None:
    """Pixel coordinates from whichever mask encoding is present, or None."""
    rle = item.get("segmentation") or item.get("rle")
    if isinstance(rle, dict) and "counts" in rle and "size" in rle:
        return _decode_rle(rle)

    polygon = item.get("polygon") or item.get("contour")
    if polygon:
        return _decode_polygon(polygon, bbox)

    encoded = item.get("mask")
    if isinstance(encoded, str):
        return _decode_png_mask(encoded, bbox)

    return None


def _decode_rle(rle: dict[str, Any]) -> list[tuple[int, int]]:
    """COCO-style run-length encoding, column-major as the format specifies."""
    counts = rle["counts"]
    height, width = rle["size"]
    if isinstance(counts, str):
        counts = [int(c) for c in counts.split()]

    points: list[tuple[int, int]] = []
    position = 0
    occupied = False
    for run in counts:
        if occupied:
            for offset in range(position, position + run):
                points.append((offset // height, offset % height))
        position += run
        occupied = not occupied
    return points


def _decode_polygon(
    polygon: list, bbox: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Polygon vertices to the cells its bounding extent covers.

    Deliberately not a scanline fill. Churn is computed on a 64x36 grid, so filling the
    exact interior would be discarded almost entirely by the reduction — the vertices
    already determine which cells light up.
    """
    flat = polygon[0] if polygon and isinstance(polygon[0], list) else polygon
    points = [
        (int(flat[i]), int(flat[i + 1])) for i in range(0, len(flat) - 1, 2)
    ]
    return points or [(bbox[0], bbox[1])]


def _decode_png_mask(
    encoded: str, bbox: tuple[int, int, int, int]
) -> list[tuple[int, int]] | None:
    """Base64 PNG mask to pixel coordinates, offset into frame space.

    Subsampled every other pixel: the result is reduced to a coarse grid immediately
    afterwards, so full resolution would be discarded work.
    """
    try:
        from PIL import Image

        payload = encoded.split(",", 1)[-1]
        image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("L")
    except (binascii.Error, ValueError, OSError) as exc:
        log.debug("could not decode mask png: %s", exc)
        return None

    left, top = bbox[0], bbox[1]
    return [
        (left + x, top + y)
        for y in range(0, image.height, 2)
        for x in range(0, image.width, 2)
        if image.getpixel((x, y)) > 127
    ]


def attention_locus(regions: tuple[Region, ...]) -> Region | None:
    """The region most likely holding attention.

    Focus alone is insufficient — the focused window is often not where the eyes are,
    which is the two-monitor case every existing tracker gets wrong. Ranks by focus
    first, then area, then inverse churn, so a large stable region beats a small
    flickering one.
    """
    if not regions:
        return None
    return max(regions, key=lambda r: (r.has_focus, r.area_frac, -r.churn))
