"""Tests for churn — the measurement the product's central claim rests on.

If reading and scrolling are not separable here, the project is an app-name tracker with
extra steps. These tests encode what that separation has to look like.
"""

import pytest

from groundtruth.perceive.churn import (
    ACTIVE,
    STATIC,
    as_mask,
    churn,
    describe,
    is_scrolling,
    sustained_focus,
)


def block(x0: int, y0: int, x1: int, y1: int):
    """A filled rectangle of pixels, standing in for a segmented region."""
    return as_mask((x, y) for x in range(x0, x1) for y in range(y0, y1))


class TestChurn:
    def test_identical_masks_do_not_churn(self):
        mask = block(0, 0, 10, 10)
        assert churn(mask, mask) == 0.0

    def test_disjoint_masks_fully_churn(self):
        assert churn(block(0, 0, 10, 10), block(50, 50, 60, 60)) == 1.0

    def test_both_empty_is_zero_not_undefined(self):
        """A region that was empty and stayed empty did not change."""
        assert churn(as_mask([]), as_mask([])) == 0.0

    def test_appearing_region_fully_churns(self):
        assert churn(as_mask([]), block(0, 0, 4, 4)) == 1.0

    def test_half_overlap(self):
        a = block(0, 0, 10, 10)
        b = block(5, 0, 15, 10)
        # 50 shared of 150 union.
        assert churn(a, b) == pytest.approx(1 - 50 / 150)

    def test_is_scale_invariant(self):
        """A small panel and a big viewport must produce comparable numbers.

        Without this, thresholds would have to be tuned per region size, which is
        exactly the kind of hidden coupling that makes a heuristic unmaintainable.
        """
        small_before, small_after = block(0, 0, 10, 10), block(5, 0, 15, 10)
        big_before, big_after = block(0, 0, 100, 100), block(50, 0, 150, 100)
        assert churn(small_before, small_after) == pytest.approx(
            churn(big_before, big_after)
        )

    def test_is_symmetric(self):
        a, b = block(0, 0, 10, 10), block(5, 0, 15, 10)
        assert churn(a, b) == churn(b, a)


class TestBands:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.0, "static"),
            (STATIC, "static"),
            (0.35, "shifting"),
            (ACTIVE, "churning"),
            (1.0, "churning"),
        ],
    )
    def test_describe(self, value, expected):
        assert describe(value) == expected

    def test_bands_do_not_overlap(self):
        assert STATIC < ACTIVE


class TestScrollDetection:
    def test_single_spike_is_not_scrolling(self):
        """Tab switches and notifications spike churn once. That is not scrolling.

        This is the test that stops the product accusing someone of doomscrolling
        because they scrolled once to reach a paragraph.
        """
        assert not is_scrolling([0.0, 0.0, 0.95])

    def test_sustained_churn_is_scrolling(self):
        assert is_scrolling([0.8, 0.9, 0.85])

    def test_too_few_samples_refuses_to_decide(self):
        assert not is_scrolling([0.9, 0.9])

    def test_majority_rules(self):
        assert is_scrolling([0.9, 0.1, 0.9])
        assert not is_scrolling([0.1, 0.9, 0.1])

    def test_reading_is_sustained_focus(self):
        assert sustained_focus([0.02, 0.05, 0.01])

    def test_one_interruption_breaks_focus(self):
        assert not sustained_focus([0.02, 0.9, 0.01])

    def test_middle_band_is_neither(self):
        """The ambiguous band must escalate to the model, not be guessed at.

        If this ever passes as one or the other, a threshold has quietly become a
        classifier and the honesty of the uncertain label is gone.
        """
        middling = [0.35, 0.4, 0.38]
        assert not is_scrolling(middling)
        assert not sustained_focus(middling)
