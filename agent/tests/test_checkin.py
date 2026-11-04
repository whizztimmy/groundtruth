"""Tests for Tier 3, the voice check-in.

Two things carry the weight here. The first is that a check-in records only what somebody
said: a guessed duration becomes a number on their timeline that nobody uttered, and they
will plan around it. The second is that refusing is a normal outcome rather than a fault -
the person is standing at the desk and can say it again, so every unclear case should come
back with something to say rather than something recorded.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from groundtruth.backends import Backend, Placement
from groundtruth.checkin import (
    CADENCE_S,
    MAX_BACKDATE,
    OFFSCREEN_CATEGORIES,
    STATED_CONFIDENCE,
    CheckIn,
    CheckInRefused,
    checkin_categories,
    interpret,
    to_events,
)
from groundtruth.perceive.classify import DEFAULT_CATEGORIES
from groundtruth.types import Tier

NOW = datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc)


def backend() -> Backend:
    return Backend(
        base_url="https://api.invalid/v1", model="muse-spark", placement=Placement.MANAGED
    )


def reply(monkeypatch, payload) -> None:
    """Stand in for the model's reply. `payload` may be a dict or a raw string."""
    body = json.dumps(payload) if isinstance(payload, dict) else payload

    async def responder(*args, **kwargs):
        return body

    monkeypatch.setattr("groundtruth.checkin.chat", responder)


def checkin(minutes: int = 45, *, stated: bool = True) -> CheckIn:
    return CheckIn(
        category="fitness",
        label="lower body at the gym",
        start=NOW - timedelta(minutes=minutes),
        end=NOW,
        transcript="at the gym for 45 minutes, lower body",
        duration_stated=stated,
    )


class TestTheTaxonomyIsSplitByTier:
    def test_offscreen_categories_are_withheld_from_the_screen_classifier(self):
        """The reason they are a separate tuple.

        A frame can never truthfully be `fitness`, so offering that option to the screen
        classifier hands it a way to be confidently wrong about the one category it has no
        evidence for. Merging the lists would be the obvious tidy-up and would be a bug.
        """
        for category in OFFSCREEN_CATEGORIES:
            assert category not in DEFAULT_CATEGORIES

    def test_a_check_in_sees_both_sets(self):
        """Somebody reviewing a paper contract for an hour did deep work off-screen."""
        combined = checkin_categories(DEFAULT_CATEGORIES)
        assert "fitness" in combined
        assert "deep work" in combined

    def test_no_category_is_offered_twice(self):
        combined = checkin_categories(DEFAULT_CATEGORIES + ("fitness",))
        assert len(combined) == len(set(combined))

    def test_order_is_preserved(self):
        """The prompt lists these, and a stable order keeps prompts comparable between
        runs - which matters because run-to-run variance is already the largest source of
        noise in this system."""
        combined = checkin_categories(DEFAULT_CATEGORIES)
        assert combined[: len(DEFAULT_CATEGORIES)] == DEFAULT_CATEGORIES

    def test_no_category_is_named_after_a_venue(self):
        """`gym` was the first attempt and it narrowed the category to one building, so
        "I went for a run" and "I was on a walk" were refused outright. Naming the
        activity instead of the place is what made the tier usable."""
        assert "gym" not in OFFSCREEN_CATEGORIES
        assert "fitness" in OFFSCREEN_CATEGORIES


class TestInterpret:
    @pytest.mark.asyncio
    async def test_a_stated_duration_is_recorded(self, monkeypatch):
        reply(monkeypatch, {
            "category": "fitness", "label": "lower body at the gym",
            "minutes": 45, "duration_stated": True,
        })
        result = await interpret(
            backend(), "at the gym for 45 minutes", categories=("fitness",), now=NOW
        )
        assert result.category == "fitness"
        assert result.minutes == pytest.approx(45)
        assert result.confidence == STATED_CONFIDENCE

    @pytest.mark.asyncio
    async def test_the_span_ends_now_and_runs_backwards(self, monkeypatch):
        """A check-in is reported on the way back to the desk, so 45 minutes at 19:00
        means 18:15 to 19:00. Asking the person to confirm that is friction for no gain."""
        reply(monkeypatch, {
            "category": "fitness", "label": "run", "minutes": 45, "duration_stated": True,
        })
        result = await interpret(
            backend(), "a 45 minute run", categories=("fitness",), now=NOW
        )
        assert result.end == NOW
        assert result.start == NOW - timedelta(minutes=45)

    @pytest.mark.asyncio
    async def test_an_unstated_duration_is_refused_rather_than_estimated(self, monkeypatch):
        """The model returns 0 when nothing was said, and that must not become a number.

        This is the behaviour the live runs settled: across five phrasings the model never
        estimated, so there is no inferred-duration path to fall back on. Refusing sends
        the question back to the only person who knows.
        """
        reply(monkeypatch, {
            "category": "fitness", "label": "gym", "minutes": 0, "duration_stated": False,
        })
        with pytest.raises(CheckInRefused, match="how long"):
            await interpret(
                backend(), "I just got back from the gym",
                categories=("fitness",), now=NOW,
            )

    @pytest.mark.asyncio
    async def test_an_activity_outside_the_taxonomy_is_refused(self, monkeypatch):
        reply(monkeypatch, {"category": None})
        with pytest.raises(CheckInRefused, match="off-screen"):
            await interpret(backend(), "ugh whatever", categories=("fitness",), now=NOW)

    @pytest.mark.asyncio
    async def test_a_span_beyond_the_backdate_limit_is_refused(self, monkeypatch):
        """Refused, not clamped. Silently moving somebody's session to a time they did not
        say is worse than declining to record it."""
        minutes = MAX_BACKDATE.total_seconds() / 60 + 60
        reply(monkeypatch, {
            "category": "rest", "label": "asleep",
            "minutes": minutes, "duration_stated": True,
        })
        with pytest.raises(CheckInRefused):
            await interpret(backend(), "I slept", categories=("rest",), now=NOW)

    @pytest.mark.asyncio
    async def test_a_negative_duration_is_refused(self, monkeypatch):
        reply(monkeypatch, {
            "category": "fitness", "label": "x", "minutes": -30, "duration_stated": True,
        })
        with pytest.raises(CheckInRefused):
            await interpret(backend(), "whatever", categories=("fitness",), now=NOW)

    @pytest.mark.asyncio
    async def test_unparseable_output_refuses_rather_than_raising_json_errors(
        self, monkeypatch
    ):
        reply(monkeypatch, "I think you went to the gym?")
        with pytest.raises(CheckInRefused, match="try again"):
            await interpret(backend(), "gym", categories=("fitness",), now=NOW)

    @pytest.mark.asyncio
    async def test_fenced_json_is_accepted(self, monkeypatch):
        """Models wrap JSON in code fences unprompted; the classifier tolerates it and so
        must this, or a correct answer is thrown away over formatting."""
        reply(monkeypatch, '```json\n{"category": "meal", "label": "lunch", '
                           '"minutes": 30, "duration_stated": true}\n```')
        result = await interpret(
            backend(), "lunch, half an hour", categories=("meal",), now=NOW
        )
        assert result.category == "meal"


class TestToEvents:
    def test_events_carry_no_process_or_window(self):
        """Not missing data - the absence is the entire point of this tier, and it is what
        the dashboard keys on to show a span as reported rather than observed."""
        for event in to_events(checkin(), device_id="phone-1"):
            assert event.process is None
            assert event.window_title is None

    def test_every_event_is_tier_three(self):
        for event in to_events(checkin(), device_id="phone-1"):
            assert event.tier is Tier.VOICE

    def test_the_span_is_covered_at_the_cadence(self):
        """Asserts the spacing and the coverage rather than recomputing the count.

        The first version of this test hard-coded `int(45 * 60 / CADENCE_S)` and failed by
        one: the loop walks a half-open interval, so a 45-minute span at two-minute
        spacing holds 23 points, not 22. Duplicating the arithmetic under test only moves
        the fencepost into the test.
        """
        c = checkin(45)
        events = to_events(c, device_id="phone-1")
        gaps = {
            (b.ts - a.ts).total_seconds() for a, b in zip(events, events[1:])
        }
        assert gaps == {CADENCE_S}
        assert events[0].ts == c.start
        assert (c.end - events[-1].ts).total_seconds() <= CADENCE_S

    def test_events_stay_inside_the_span(self):
        c = checkin(45)
        for event in to_events(c, device_id="phone-1"):
            assert c.start <= event.ts < c.end

    def test_a_span_shorter_than_one_step_still_produces_an_event(self):
        """One minute at the door is still a minute that happened. Rounding it to nothing
        would make short check-ins silently vanish, which teaches people not to bother."""
        events = to_events(checkin(1), device_id="phone-1")
        assert len(events) == 1

    def test_the_transcript_is_quoted_in_the_evidence(self):
        """What the person said is the whole evidence for this label, so it has to be
        checkable later rather than summarised away."""
        [event, *_] = to_events(checkin(), device_id="phone-1")
        assert "at the gym for 45 minutes" in event.classification.evidence

    def test_the_evidence_records_that_the_duration_was_stated(self):
        [event, *_] = to_events(checkin(), device_id="phone-1")
        assert "stated" in event.classification.evidence

    def test_timestamps_are_unique(self):
        """The server is idempotent on `(device_id, ts)`, so a collision inside one
        check-in would silently drop its own events as duplicates."""
        events = to_events(checkin(45), device_id="phone-1")
        assert len({e.ts for e in events}) == len(events)
