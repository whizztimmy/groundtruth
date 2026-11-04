"""Tests for the industry activity percentage.

This metric is implemented to be compared against, so it has to be implemented correctly.
A straw-man version that scored reading at zero because it was built to lose the argument
would make the comparison worthless, and a judge or a user would be right to discount
everything built on top of it.

So the tests below pin the real behaviour, including the parts that flatter it: input is a
genuine signal, and most of the time its score and the screen agree.
"""

from __future__ import annotations

import pytest

from groundtruth.activity import (
    SEGMENT_MINUTES,
    ActivityLevel,
    ActivityMeter,
    disagreement,
)
from groundtruth.patterns import FOCUS_CATEGORIES

PER_MIN = 30


def meter(**kw) -> ActivityMeter:
    return ActivityMeter(samples_per_minute=PER_MIN, **kw)


def feed(m: ActivityMeter, pattern: list[bool], *, minutes: int = 1) -> None:
    """Repeat `pattern` across whole minutes of samples."""
    for _ in range(minutes):
        for i in range(PER_MIN):
            m.record(pattern[i % len(pattern)])


class TestHowAMinuteIsScored:
    def test_a_minute_with_no_input_is_inactive(self):
        m = meter()
        feed(m, [False], minutes=1)
        assert m.level() == ActivityLevel(active_minutes=0, elapsed_minutes=1)

    def test_continuous_input_scores_the_minute(self):
        m = meter()
        feed(m, [True], minutes=1)
        assert m.level().percent == 100

    def test_one_sample_of_input_scores_the_whole_minute(self):
        """The bar is this low, and it is the single most important thing to understand
        about the metric. One keystroke in sixty seconds scores identically to sixty
        seconds of typing, which is why a mouse jiggler defeats it completely."""
        m = meter()
        m.record(True)
        for _ in range(PER_MIN - 1):
            m.record(False)
        assert m.level().percent == 100

    def test_a_jiggler_scores_a_perfect_day(self):
        """One twitch per minute, nothing else. The metric reports maximum productivity.

        An entire product category exists to sell this, which says what is really being
        measured.
        """
        m = meter()
        for _ in range(SEGMENT_MINUTES):
            m.record(True)
            for _ in range(PER_MIN - 1):
                m.record(False)
        assert m.level().percent == 100


class TestTheSegment:
    def test_three_active_minutes_in_ten_is_thirty_percent(self):
        m = meter()
        feed(m, [True], minutes=3)
        feed(m, [False], minutes=7)
        assert m.level().percent == 30

    def test_the_window_rolls_rather_than_resetting(self):
        """Aligned segments make a score collapse to zero on the boundary and climb back,
        which reads as a change in behaviour that did not happen."""
        m = meter()
        feed(m, [False], minutes=SEGMENT_MINUTES)
        assert m.level().percent == 0
        feed(m, [True], minutes=SEGMENT_MINUTES)
        assert m.level().percent == 100

    def test_it_never_counts_more_than_a_segment(self):
        m = meter()
        feed(m, [True], minutes=SEGMENT_MINUTES * 3)
        assert m.level().elapsed_minutes == SEGMENT_MINUTES

    def test_nothing_recorded_scores_zero_rather_than_dividing_by_zero(self):
        assert meter().level().percent == 0


class TestThePartialMinute:
    def test_a_fresh_segment_is_marked_partial(self):
        m = meter()
        feed(m, [True], minutes=2)
        assert m.level().is_partial

    def test_a_full_segment_is_not(self):
        m = meter()
        feed(m, [True], minutes=SEGMENT_MINUTES)
        assert not m.level().is_partial

    def test_an_active_partial_minute_counts_immediately(self):
        """Otherwise a genuinely busy stretch reads low for up to a minute at a time."""
        m = meter()
        m.record(True)
        assert m.level().active_minutes == 1

    def test_an_idle_partial_minute_invents_nothing(self):
        m = meter()
        m.record(False)
        assert m.level().elapsed_minutes == 0


class TestWhereItDisagreesWithTheScreen:
    def full(self, active: int) -> ActivityLevel:
        return ActivityLevel(active_minutes=active, elapsed_minutes=SEGMENT_MINUTES)

    def test_quiet_focus_is_named(self):
        """Reading documentation for ten minutes. The metric calls it idle."""
        note = disagreement(self.full(1), "research", focus_categories=FOCUS_CATEGORIES)
        assert note and "idle" in note and "research" in note

    def test_busy_doomscrolling_is_named(self):
        """A feed pulled for ten minutes. The metric ranks it top of the day."""
        note = disagreement(self.full(9), "doomscrolling", focus_categories=FOCUS_CATEGORIES)
        assert note and "feed" in note

    @pytest.mark.parametrize(
        "active,category",
        [(9, "deep work"), (1, "idle"), (5, "research"), (9, "communication")],
    )
    def test_agreement_says_nothing(self, active, category):
        """Most of the time the two agree, and the honest output is silence. A comparison
        that fired constantly would be an accusation, not a measurement."""
        assert disagreement(self.full(active), category, focus_categories=FOCUS_CATEGORIES) is None

    def test_a_partial_segment_makes_no_accusation(self):
        """Forty seconds of data is not grounds for contradicting anybody."""
        thin = ActivityLevel(active_minutes=0, elapsed_minutes=2)
        assert disagreement(thin, "research", focus_categories=FOCUS_CATEGORIES) is None

    def test_every_focus_category_is_covered(self):
        """Devotion and learning are as quiet as research and fail the metric the same
        way. Hard-coding one category would have left the others silently unprotected."""
        for category in FOCUS_CATEGORIES:
            assert disagreement(self.full(0), category, focus_categories=FOCUS_CATEGORIES)
