"""Tests for Tier 2 frame capture.

The invariants here are privacy guarantees, not conveniences. `ARCHITECTURE.md` promises
users that no frame touches disk and that frames are reduced before going anywhere; these
tests are what make those promises checkable rather than aspirational.
"""

from pathlib import Path

import pytest
from PIL import Image

from groundtruth.capture.screen import (
    TARGET_WIDTH,
    Display,
    FrameGrabber,
    _downscale,
)


class TestDisplay:
    def test_contains_point_inside(self):
        assert Display(0, 0, 1920, 1080).contains(100, 100)

    def test_excludes_far_edge(self):
        """Half-open bounds, so adjacent monitors cannot both claim a pixel."""
        assert not Display(0, 0, 1920, 1080).contains(1920, 0)

    def test_handles_negative_origin(self):
        """A monitor left of the primary has negative coordinates on Windows."""
        secondary = Display(-1920, 0, 1920, 1080)
        assert secondary.contains(-100, 50)
        assert not secondary.contains(100, 50)


class TestDownscale:
    def test_reduces_to_target_width(self):
        out = _downscale(Image.new("RGB", (3840, 2160)), TARGET_WIDTH)
        assert out.width == TARGET_WIDTH

    def test_preserves_aspect_ratio(self):
        out = _downscale(Image.new("RGB", (1920, 1080)), 960)
        assert (out.width, out.height) == (960, 540)

    def test_never_upscales(self):
        """Upscaling invents detail a model might read as real, and costs tokens."""
        small = Image.new("RGB", (400, 300))
        assert _downscale(small, TARGET_WIDTH).size == (400, 300)

    def test_ultrawide_keeps_at_least_one_pixel_of_height(self):
        out = _downscale(Image.new("RGB", (10000, 20)), 100)
        assert out.height >= 1

    def test_target_width_is_not_quietly_large(self):
        """A regression guard on a privacy control.

        TARGET_WIDTH exists so fine body text does not survive capture. Raising it
        materially should be a deliberate decision with a reason, not a drive-by tweak.
        """
        assert TARGET_WIDTH <= 1280


class TestGrabber:
    def test_rejects_unsegmentable_width(self):
        with pytest.raises(ValueError, match="too small to segment"):
            FrameGrabber(target_width=64)

    def test_grab_returns_downscaled_rgb(self):
        with FrameGrabber() as grabber:
            frame = grabber.grab()
        assert frame.mode == "RGB"
        assert frame.width <= TARGET_WIDTH

    def test_grabs_are_independent_objects(self):
        """The caller hands a frame to an async classifier.

        A shared buffer could be overwritten mid-inference by the next sample, so each
        grab must be its own image.
        """
        with FrameGrabber() as grabber:
            first = grabber.grab()
            second = grabber.grab()
        assert first is not second

    def test_grab_active_does_not_fail_without_focus(self):
        with FrameGrabber() as grabber:
            assert grabber.grab_active().mode == "RGB"

    def test_reports_at_least_one_display(self):
        with FrameGrabber() as grabber:
            assert len(grabber.displays()) >= 1

    def test_display_at_falls_back_rather_than_raising(self):
        """A window restoring from minimised sits at absurd coordinates.

        That must not crash a capture loop.
        """
        with FrameGrabber() as grabber:
            assert grabber.display_at(-99999, -99999) is not None

    def test_capture_writes_nothing_to_disk(self, tmp_path, monkeypatch):
        """The promise in ARCHITECTURE.md, made checkable.

        Runs a grab with the working directory moved to an empty tree and asserts it
        stayed empty. Not exhaustive — it cannot see writes to absolute paths — but it
        catches the realistic regression of someone adding a debug dump.
        """
        monkeypatch.chdir(tmp_path)
        with FrameGrabber() as grabber:
            grabber.grab()
            grabber.grab_active()
        assert list(Path(tmp_path).iterdir()) == []
