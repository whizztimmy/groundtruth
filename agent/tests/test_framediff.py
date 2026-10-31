"""Tests for whole-screen churn.

The signal has to separate three things that all look like "the screen changed": content
being replaced (a feed scrolling), content being edited (a caret, a repaint), and nothing
happening. Every test below is one of those three, because a churn number that cannot
tell them apart would make a text editor read as a feed and sink the whole motion model.
"""

from __future__ import annotations

import pytest

from groundtruth.perceive import framediff, grid
from groundtruth.perceive.churn import ACTIVE, STATIC

PIL = pytest.importorskip("PIL.Image")


def frame(fill=(0, 0, 0), size=(1024, 576)):
    from PIL import Image

    return Image.new("RGB", size, fill)


def striped(offset: int, size=(1024, 576), band: int = 32):
    """Horizontal bands, shifted by `offset` pixels: a page of text scrolled."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, (20, 20, 20))
    draw = ImageDraw.Draw(img)
    for y in range(-band, size[1] + band, band * 2):
        draw.rectangle([0, y + offset, size[0], y + offset + band], fill=(230, 230, 230))
    return img


class TestDigest:
    def test_one_value_per_grid_cell(self):
        assert len(framediff.digest(frame())) == grid.COLS * grid.ROWS

    def test_size_is_independent_of_resolution(self):
        """A digest from a 4K screen and a laptop screen must stay comparable."""
        small = framediff.digest(frame(size=(1024, 576)))
        large = framediff.digest(frame(size=(3840, 2160)))
        assert len(small) == len(large)

    def test_it_is_small_enough_not_to_be_a_screenshot(self):
        """2,304 bytes. The privacy argument in framediff.py rests on this number."""
        assert len(framediff.digest(frame())) == 2304


class TestNothingHappening:
    def test_identical_frames_have_no_churn(self):
        d = framediff.digest(frame())
        assert framediff.churn_of(d, d) == 0.0

    def test_a_static_screen_reads_below_the_static_threshold(self):
        a = framediff.digest(striped(0))
        b = framediff.digest(striped(0))
        assert framediff.churn_of(a, b) <= STATIC


class TestContentReplaced:
    def test_a_full_repaint_churns_everything(self):
        a = framediff.digest(frame((0, 0, 0)))
        b = framediff.digest(frame((255, 255, 255)))
        assert framediff.churn_of(a, b) == 1.0

    def test_scrolled_content_reads_as_active(self):
        """The case the whole motion model exists for.

        Bands shifted by half their height replace what sits in most cells, which is what
        a feed being pulled past does. It must clear ACTIVE, or `motion.classify` can
        never return SCROLLING and the feed-versus-article distinction is lost.
        """
        a = framediff.digest(striped(0))
        b = framediff.digest(striped(32))
        assert framediff.churn_of(a, b) >= ACTIVE

    def test_a_partial_change_lands_between_the_bands(self):
        """Half the screen replaced is neither reading nor scrolling, and must not
        masquerade as either: `motion.classify` returns UNCLEAR for the middle band and
        escalation handles it."""
        from PIL import Image

        a = frame((0, 0, 0))
        b = frame((0, 0, 0))
        b.paste(Image.new("RGB", (1024, 288), (255, 255, 255)), (0, 0))
        value = framediff.churn_of(framediff.digest(a), framediff.digest(b))
        assert STATIC < value < ACTIVE


class TestContentEdited:
    def test_a_caret_sized_change_stays_far_below_static(self):
        """A blinking caret changes a handful of pixels every half second forever.

        It does light its own cell — CELL_DELTA is too low to suppress a 2x18 block of
        white, and raising it that far would start discarding real text replacement. The
        protection comes from the ratio instead: one or two cells out of 2,304 is three
        orders of magnitude below STATIC, so an idle editor cannot read as a live feed.

        Written this way deliberately. Asserting == 0.0 here would be asserting that
        CELL_DELTA does a job it does not do, and the test would be pinning a comment
        rather than a behaviour.
        """
        from PIL import Image

        a = frame((40, 40, 40))
        b = frame((40, 40, 40))
        b.paste(Image.new("RGB", (2, 18), (255, 255, 255)), (300, 200))
        value = framediff.churn_of(framediff.digest(a), framediff.digest(b))
        assert 0.0 < value < STATIC / 100

    def test_a_uniform_dimming_is_not_churn(self):
        """Backlight or theme-animation drift moves every cell a little and replaces
        nothing. Counting it would report 100% churn for an untouched screen."""
        a = framediff.digest(frame((100, 100, 100)))
        b = framediff.digest(frame((104, 104, 104)))
        assert framediff.churn_of(a, b) == 0.0


class TestIncomparableDigests:
    def test_a_resolution_change_reports_no_churn_rather_than_raising(self):
        """Digests are fixed-length, so this needs a hand-made mismatch.

        A monitor switch mid-session must cost one sample, not a crash and not a spurious
        100% reading that looks exactly like scrolling.
        """
        assert framediff.churn_of((1, 2, 3), (1, 2, 3, 4)) == 0.0

    def test_empty_digests_report_no_churn(self):
        assert framediff.churn_of((), ()) == 0.0


class TestChangedCells:
    def test_cells_are_reported_in_grid_coordinates(self):
        from PIL import Image

        a = frame((0, 0, 0))
        b = frame((0, 0, 0))
        b.paste(Image.new("RGB", (16, 16), (255, 255, 255)), (0, 0))
        cells = framediff.changed_cells(framediff.digest(a), framediff.digest(b))
        assert cells == frozenset({(0, 0)})
