"""Tests for region tracking and grid reduction.

Everything here runs against synthetic detections rather than a live endpoint, which is
the point of isolating `_parse_detections`: tracking, churn and attention ranking are
verified now and stay verified when the wire format is pinned down.
"""

import json

import pytest

from groundtruth.backends import Backend, BackendError, Placement
from groundtruth.perceive import grid
from groundtruth.perceive.regions import (
    _parse_bbox,
    _parse_detections,
    _to_grid,
    attention_locus,
    RegionTracker,
)
from groundtruth.types import Region


def backend(placement=Placement.SELF_HOSTED) -> Backend:
    return Backend(
        base_url="https://sam.invalid/v1", model="sam-3.1", placement=placement
    )


class TestGrid:
    def test_pixels_reduce_to_cells(self):
        cells = grid.from_pixels([(0, 0), (1, 1)], width=640, height=360)
        assert cells == {(0, 0)}

    def test_spans_map_across_the_grid(self):
        cells = grid.from_pixels(
            [(0, 0), (639, 359)], width=640, height=360, cols=64, rows=36
        )
        assert cells == {(0, 0), (63, 35)}

    def test_out_of_range_is_clamped_not_dropped(self):
        """A mask can exceed frame bounds by a pixel after rounding."""
        cells = grid.from_pixels([(99999, 99999)], width=640, height=360)
        assert cells == {(63, 35)}

    def test_empty_frame_is_empty(self):
        assert grid.from_pixels([(1, 1)], width=0, height=0) == frozenset()

    def test_bbox_fills_its_extent(self):
        cells = grid.from_bbox((0, 0, 640, 360), width=640, height=360)
        assert len(cells) == grid.COLS * grid.ROWS

    def test_degenerate_bbox_is_empty(self):
        assert grid.from_bbox((10, 10, 10, 10), width=640, height=360) == frozenset()

    def test_coverage_is_a_fraction(self):
        assert grid.coverage(grid.from_bbox((0, 0, 640, 360), width=640, height=360)) == 1.0

    def test_reduction_is_tractable(self):
        """The reason this module exists.

        A full-resolution pixel set for one region on a 1024x576 frame is ~590k
        coordinates; the grid caps any region at 2,304 cells.
        """
        full = [(x, y) for x in range(0, 1024, 2) for y in range(0, 576, 2)]
        cells = grid.from_pixels(full, width=1024, height=576)
        assert len(cells) <= grid.COLS * grid.ROWS


class TestParseBbox:
    def test_xyxy(self):
        assert _parse_bbox({"bbox": [10, 20, 110, 220]}) == (10, 20, 110, 220)

    def test_xywh_detected_by_geometry(self):
        """Width/height form is identifiable: the last two would sit left of the first."""
        assert _parse_bbox({"bbox": [100, 100, 50, 40]}) == (100, 100, 150, 140)

    def test_explicit_format_wins(self):
        assert _parse_bbox({"bbox": [10, 20, 30, 40], "format": "xywh"}) == (
            10,
            20,
            40,
            60,
        )

    def test_missing_box_is_none(self):
        assert _parse_bbox({}) is None

    def test_wrong_arity_is_none(self):
        assert _parse_bbox({"bbox": [1, 2, 3]}) is None


class TestParseDetections:
    def test_objects_key(self):
        raw = json.dumps({"objects": [{"id": 3, "label": "feed", "bbox": [0, 0, 10, 10]}]})
        [det] = _parse_detections(raw)
        assert det["track_id"] == 3
        assert det["label"] == "feed"

    def test_bare_list(self):
        raw = json.dumps([{"bbox": [0, 0, 10, 10]}])
        assert len(_parse_detections(raw)) == 1

    def test_fenced_json_is_tolerated(self):
        """Models fence JSON regardless of instructions."""
        raw = '```json\n{"objects": [{"bbox": [0, 0, 10, 10]}]}\n```'
        assert len(_parse_detections(raw)) == 1

    def test_index_is_the_fallback_track_id(self):
        raw = json.dumps({"objects": [{"bbox": [0, 0, 5, 5]}, {"bbox": [5, 5, 9, 9]}]})
        assert [d["track_id"] for d in _parse_detections(raw)] == [0, 1]

    def test_detections_without_a_box_are_skipped(self):
        raw = json.dumps({"objects": [{"label": "nothing"}, {"bbox": [0, 0, 9, 9]}]})
        assert len(_parse_detections(raw)) == 1

    def test_non_json_raises_clearly(self):
        with pytest.raises(BackendError, match="not JSON"):
            _parse_detections("I'm afraid I can't do that")

    def test_rle_becomes_points(self):
        raw = json.dumps(
            {
                "objects": [
                    {
                        "bbox": [0, 0, 4, 4],
                        "segmentation": {"size": [4, 4], "counts": [0, 4, 12]},
                    }
                ]
            }
        )
        [det] = _parse_detections(raw)
        assert len(det["points"]) == 4

    def test_polygon_becomes_points(self):
        raw = json.dumps(
            {"objects": [{"bbox": [0, 0, 10, 10], "polygon": [0, 0, 10, 0, 10, 10]}]}
        )
        [det] = _parse_detections(raw)
        assert (10, 10) in det["points"]


class TestToGrid:
    def test_prefers_mask_over_bbox(self):
        """A box covers its whole extent; a mask of one point covers one cell."""
        det = {"track_id": 1, "bbox": (0, 0, 640, 360), "points": [(0, 0)]}
        assert _to_grid(det, width=640, height=360) == {(0, 0)}

    def test_falls_back_to_bbox(self):
        det = {"track_id": 1, "bbox": (0, 0, 640, 360)}
        assert len(_to_grid(det, width=640, height=360)) == grid.COLS * grid.ROWS


class TestTracker:
    def test_refuses_a_managed_backend_at_construction(self):
        """Fail at startup, not silently at the first frame."""
        with pytest.raises(BackendError, match="requires 'local' or 'self_hosted'"):
            RegionTracker(backend(Placement.MANAGED))

    def test_accepts_self_hosted(self):
        assert RegionTracker(backend()) is not None

    def test_reset_clears_state(self):
        tracker = RegionTracker(backend())
        tracker._previous = {1: frozenset({(0, 0)})}
        tracker.reset()
        assert tracker._previous == {}


class TestAttentionLocus:
    def region(self, **kw) -> Region:
        base = dict(
            track_id=1, label="r", bbox=(0, 0, 10, 10), area_frac=0.1, churn=0.0
        )
        return Region(**{**base, "has_focus": False, **kw})

    def test_empty_is_none(self):
        assert attention_locus(()) is None

    def test_focus_wins(self):
        small_focused = self.region(track_id=1, area_frac=0.05, has_focus=True)
        big_unfocused = self.region(track_id=2, area_frac=0.9)
        assert attention_locus((big_unfocused, small_focused)).track_id == 1

    def test_area_breaks_ties(self):
        assert (
            attention_locus(
                (self.region(track_id=1, area_frac=0.2), self.region(track_id=2, area_frac=0.6))
            ).track_id
            == 2
        )

    def test_stable_beats_flickering_at_equal_size(self):
        calm = self.region(track_id=1, area_frac=0.4, churn=0.05)
        busy = self.region(track_id=2, area_frac=0.4, churn=0.9)
        assert attention_locus((busy, calm)).track_id == 1
