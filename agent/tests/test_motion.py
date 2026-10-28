"""Tests for the churn-by-input motion model.

The case that motivated this module is `test_build_log_is_not_scrolling`. It came from a
fixture day, not from the code: a long-running build scrolls its output continuously with
nobody at the keyboard, and churn alone called that a feed.
"""

import pytest

from groundtruth.perceive.motion import Motion, Sample, classify, evidence


def run(churn: float, *, input_active: bool, n: int = 3) -> list[Sample]:
    return [Sample(churn=churn, input_active=input_active) for _ in range(n)]


class TestQuadrants:
    def test_feed_is_scrolling(self):
        assert classify(run(0.85, input_active=True)) is Motion.SCROLLING

    def test_build_log_is_not_scrolling(self):
        """The bug this module exists for.

        A build scrolls its own output continuously. Churn is indistinguishable from a
        social feed; the difference is that nobody is touching the keyboard.
        """
        assert classify(run(0.85, input_active=False)) is Motion.PLAYING

    def test_reading_is_static_without_input(self):
        assert classify(run(0.03, input_active=False)) is Motion.READING

    def test_editing_is_static_with_input(self):
        assert classify(run(0.05, input_active=True)) is Motion.WORKING

    def test_the_two_axes_are_both_required(self):
        """Neither signal alone separates all four quadrants.

        Same churn, different input, different answer — twice, on both rows. If this
        ever collapses, one of the signals has stopped contributing.
        """
        assert classify(run(0.9, input_active=True)) != classify(
            run(0.9, input_active=False)
        )
        assert classify(run(0.05, input_active=True)) != classify(
            run(0.05, input_active=False)
        )


class TestHonesty:
    def test_too_few_samples_is_unclear(self):
        assert classify(run(0.9, input_active=True, n=2)) is Motion.UNCLEAR

    def test_middling_churn_without_input_is_unclear(self):
        """The ambiguous band must escalate, not be assigned by a threshold."""
        assert classify(run(0.35, input_active=False)) is Motion.UNCLEAR

    def test_single_spike_does_not_decide(self):
        """A tab switch spikes churn once. That is an artefact, not an activity."""
        samples = [
            Sample(0.02, True),
            Sample(0.02, True),
            Sample(0.95, True),
        ]
        assert classify(samples) is not Motion.SCROLLING

    def test_occasional_nudge_does_not_make_watching_into_scrolling(self):
        """An accidental mouse move during a video must not reclassify it."""
        samples = [
            Sample(0.9, False),
            Sample(0.9, True),
            Sample(0.9, False),
        ]
        assert classify(samples) is Motion.PLAYING


class TestUserDriven:
    @pytest.mark.parametrize(
        "motion,driven",
        [
            (Motion.SCROLLING, True),
            (Motion.WORKING, True),
            (Motion.PLAYING, False),
            (Motion.READING, False),
            (Motion.UNCLEAR, False),
        ],
    )
    def test_is_user_driven(self, motion, driven):
        assert motion.is_user_driven is driven


class TestEvidence:
    @pytest.mark.parametrize("motion", list(Motion))
    def test_every_motion_has_evidence(self, motion):
        assert evidence(motion)

    def test_evidence_describes_rather_than_concludes(self):
        """The model is told what was seen, not what to decide.

        An evidence string containing a verdict would bias the classifier toward the
        heuristic instead of letting it weigh the observation.
        """
        for motion in Motion:
            text = evidence(motion).lower()
            assert "doomscroll" not in text
            assert "productive" not in text
