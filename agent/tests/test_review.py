"""Tests for pattern computation and the weekly review.

The property under test throughout is that the review can only say things the data
supports. A review that invents a pattern is worse than a dull one, because the person
will act on it.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from groundtruth.backends import Backend, BackendError, Placement
from groundtruth.patterns import Span, compute, render
from groundtruth.review import Reviewer, _observations, detail_for

MON = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def span(category="deep work", *, start=MON, minutes=60, label=None) -> Span:
    return Span(
        start=start,
        end=start + timedelta(minutes=minutes),
        category=category,
        label=label or f"{category} thing",
        confidence=0.9,
    )


def backend() -> Backend:
    # Managed is correct here: this tier sees category names and durations, never pixels.
    return Backend(
        base_url="https://api.invalid/v1", model="muse-spark", placement=Placement.MANAGED
    )


class TestCompute:
    def test_empty_is_empty(self):
        assert compute([]).span_count == 0

    def test_totals_by_category(self):
        patterns = compute([span(minutes=60), span("research", minutes=30)])
        assert patterns.by_category["deep work"] == 3600
        assert patterns.tracked_s == 5400

    def test_share_is_a_fraction(self):
        patterns = compute([span(minutes=60), span("research", minutes=60)])
        assert patterns.share("deep work") == pytest.approx(0.5)

    def test_share_of_nothing_is_zero_not_an_error(self):
        assert compute([]).share("deep work") == 0.0

    def test_groups_by_day(self):
        tue = MON + timedelta(days=1)
        patterns = compute([span(start=MON), span(start=tue)])
        assert set(patterns.by_day) == {"Monday", "Tuesday"}

    def test_fragmentation_is_mean_stretch(self):
        """Short mean with a large total is the signature of a fragmented day."""
        patterns = compute([span(minutes=10), span(minutes=10), span(minutes=40)])
        assert patterns.fragmentation["deep work"] == pytest.approx(20 * 60)

    def test_longest_focus_ignores_non_focus_categories(self):
        patterns = compute(
            [span("communication", minutes=300), span("deep work", minutes=45)]
        )
        assert patterns.longest_focus.category == "deep work"

    def test_no_focus_spans_leaves_it_unset(self):
        assert compute([span("communication")]).longest_focus is None

    def test_first_activity_skips_idle(self):
        """What a day actually started with is often not what someone believes."""
        patterns = compute(
            [
                span("idle", start=MON, minutes=5),
                span("devotion", start=MON + timedelta(minutes=5), label="scripture"),
            ]
        )
        assert patterns.first_activity["Monday"] == "scripture"

    def test_uncertain_is_tracked_separately(self):
        patterns = compute([span("uncertain", minutes=20)])
        assert patterns.uncertain_s == 1200


class TestRender:
    def test_empty_period_says_so(self):
        assert "No tracked activity" in render(compute([]))

    def test_durations_are_preformatted(self):
        """The model must never see seconds, or it will try to convert them."""
        text = render(compute([span(minutes=90)]))
        assert "1h 30m" in text
        assert "5400" not in text

    def test_shares_are_precomputed(self):
        text = render(compute([span(minutes=60), span("research", minutes=60)]))
        assert "50%" in text

    def test_uncertain_is_marked_as_not_evidence(self):
        text = render(compute([span(minutes=60), span("uncertain", minutes=30)]))
        assert "excluded from any claim" in text


class TestDetailFor:
    def test_answers_about_a_day(self):
        patterns = compute([span(start=MON, minutes=60)])
        assert "Monday" in detail_for(patterns, "What happened on Monday?")

    def test_answers_about_a_category(self):
        patterns = compute([span("research", minutes=60)])
        assert "research by day" in detail_for(patterns, "Tell me about research")

    def test_refuses_what_it_does_not_have(self):
        assert "not available" in detail_for(compute([span()]), "what about my email")

    def test_cannot_widen_what_is_visible(self):
        """The follow-up looks harder at existing facts; it never fetches more."""
        patterns = compute([span(start=MON)])
        assert "Tuesday" not in detail_for(patterns, "Tuesday please")


class TestObservations:
    def test_drops_unevidenced_findings(self):
        """An assertion the person cannot check is what this tier must not produce."""
        parsed = {
            "observations": [
                {"finding": "You focus best early", "evidence": ""},
                {"finding": "Deep work averaged 24m", "evidence": "average stretch 24m"},
            ]
        }
        assert len(_observations(parsed)) == 1

    def test_clamps_confidence(self):
        parsed = {"observations": [{"finding": "a", "evidence": "b", "confidence": 9}]}
        assert _observations(parsed)[0].confidence == 1.0

    def test_missing_observations_key_is_empty(self):
        assert _observations({}) == ()


class TestReviewer:
    def respond(self, payloads):
        """Returns successive canned responses."""
        remaining = list(payloads)

        async def responder(backend_, messages, **kw):
            return json.dumps(remaining.pop(0))

        return responder

    async def test_empty_period_skips_the_model(self, monkeypatch):
        """No data means no call, and no invented observations."""
        called = []

        async def boom(*a, **k):
            called.append(1)
            return "{}"

        monkeypatch.setattr("groundtruth.review.chat", boom)
        review = await Reviewer(backend()).review(compute([]), period="this week")
        assert review.observations == ()
        assert called == []

    async def test_returns_observations(self, monkeypatch):
        monkeypatch.setattr(
            "groundtruth.review.chat",
            self.respond(
                [
                    {
                        "observations": [
                            {
                                "finding": "Deep work came in short stretches",
                                "evidence": "average stretch 20m",
                                "confidence": 0.8,
                            }
                        ],
                        "question": None,
                    }
                ]
            ),
        )
        review = await Reviewer(backend()).review(
            compute([span(minutes=20), span(minutes=20)]), period="this week"
        )
        assert len(review.observations) == 1
        assert "short stretches" in review.render()

    async def test_follow_up_is_answered_once(self, monkeypatch):
        monkeypatch.setattr(
            "groundtruth.review.chat",
            self.respond(
                [
                    {"observations": [], "question": "What happened on Monday?"},
                    {
                        "observations": [
                            {"finding": "Monday was fragmented", "evidence": "deep work 20m"}
                        ],
                        "question": None,
                    },
                ]
            ),
        )
        review = await Reviewer(backend()).review(
            compute([span(start=MON, minutes=20)]), period="this week"
        )
        assert review.asked == "What happened on Monday?"
        assert len(review.observations) == 1

    async def test_a_second_question_is_not_entertained(self, monkeypatch):
        """An unbounded loop on a per-token endpoint is how a budget disappears."""
        calls = []

        async def responder(backend_, messages, **kw):
            calls.append(1)
            return json.dumps({"observations": [], "question": "and Tuesday?"})

        monkeypatch.setattr("groundtruth.review.chat", responder)
        await Reviewer(backend()).review(compute([span()]), period="w")
        assert len(calls) == 2

    async def test_unparseable_response_raises_clearly(self, monkeypatch):
        async def responder(*a, **k):
            return "Here's your week!"

        monkeypatch.setattr("groundtruth.review.chat", responder)
        with pytest.raises(BackendError, match="not JSON"):
            await Reviewer(backend()).review(compute([span()]), period="w")
