"""Tests for which uncertain spans earn a question.

The queue is the only place this product spends the user's attention, so it has to be the
scarcest thing in it. A queue full of questions that would have answered themselves on the
next poll teaches people to ignore it, and then the honest abstention that mattered goes
unread with the rest.

The number behind the rule is in `docs/validation.md`: repeat runs of one identical
ambiguous screenshot span a 0.53 confidence range and change category. A single
`uncertain` is therefore as likely to be the dice as a genuinely unreadable screen.
"""

from __future__ import annotations

from conftest import DEVICE, ev, run

from groundtruth_server.derive import ASK_ANYWAY_S, MIN_ABSTENTIONS, worth_asking
from groundtruth_server.models import UNCERTAIN, Tier
from groundtruth_server.segment import SegmentConfig, segment_spans


def spans_for(events, config=None):
    return segment_spans(events, config or SegmentConfig())


def uncertain_run(offsets, *, tier=Tier.SCREEN, **kw):
    return run(offsets, UNCERTAIN, tier=tier, **kw)


class TestOneShrugIsNotAQuestion:
    def test_a_single_abstention_is_not_asked_about(self):
        events = [ev(0, UNCERTAIN, tier=Tier.SCREEN)] + uncertain_run(
            range(2, 60, 2), tier=Tier.WINDOW
        )
        [span] = spans_for(events)
        assert span.is_uncertain
        assert not worth_asking(span)

    def test_carry_forward_does_not_inflate_the_count(self):
        """A span is mostly tier 1 carry-forward, which repeats one abstention rather than
        making new ones. Counting those would let a single shrug become a dozen, which is
        exactly how 92 real abstentions became 1,023 events in a day of real data.
        """
        events = [ev(0, UNCERTAIN, tier=Tier.SCREEN)] + uncertain_run(
            range(2, 120, 2), tier=Tier.WINDOW
        )
        [span] = spans_for(events)
        assert len(span.coverage) > 10
        assert not worth_asking(span)


class TestTwiceIsASignal:
    def test_two_abstentions_earn_a_question(self):
        events = [
            ev(0, UNCERTAIN, tier=Tier.SCREEN),
            ev(30, UNCERTAIN, tier=Tier.SCREEN),
        ]
        [span] = spans_for(events)
        assert worth_asking(span)

    def test_the_threshold_is_the_documented_one(self):
        assert MIN_ABSTENTIONS == 2


class TestLongEnoughAsksRegardless:
    def test_five_unexplained_minutes_is_worth_a_question(self):
        """The rule exists to stop noise reaching the user, not to hide real gaps. One
        abstention covering a long stretch is still a long stretch nobody can account for.
        """
        events = [ev(0, UNCERTAIN, tier=Tier.SCREEN)] + uncertain_run(
            range(60, int(ASK_ANYWAY_S) + 120, 60), tier=Tier.WINDOW
        )
        [span] = spans_for(events)
        assert span.duration_s >= ASK_ANYWAY_S
        assert worth_asking(span)

    def test_a_short_span_does_not_qualify_on_duration(self):
        events = [ev(0, UNCERTAIN, tier=Tier.SCREEN)] + uncertain_run(
            range(2, 30, 2), tier=Tier.WINDOW
        )
        [span] = spans_for(events)
        assert span.duration_s < ASK_ANYWAY_S
        assert not worth_asking(span)


class TestItDoesNotTouchAnythingElse:
    def test_a_confident_span_is_unaffected(self):
        """`worth_asking` only ever gates the uncertain queue. A deep work span is not a
        question and never reaches this code."""
        [span] = spans_for(run(range(0, 120, 2), "deep work"))
        assert not span.is_uncertain

    def test_two_abstentions_across_a_gap_are_two_spans(self):
        """Each judged on its own, which is the point: an abstention twenty minutes ago is
        not corroboration for one now."""
        events = [ev(0, UNCERTAIN, tier=Tier.SCREEN), ev(3600, UNCERTAIN, tier=Tier.SCREEN)]
        spans = spans_for(events)
        assert len(spans) == 2
        assert not any(worth_asking(s) for s in spans)
