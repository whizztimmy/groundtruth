"""Tests for churn — the measurement the product's central claim rests on.

If reading and scrolling are not separable here, the project is an app-name tracker with
extra steps. These tests encode what that separation has to look like.
"""

import pytest

from groundtruth.perceive.churn import ACTIVE, STATIC, as_mask, churn, describe


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


# Interpreting runs of churn values lives in test_motion.py: it needs an input signal
# alongside, because churn alone cannot tell a feed from a build log scrolling itself.
