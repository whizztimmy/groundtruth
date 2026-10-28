"""Tests for the segmenter.

Weighted hard towards the rules that are easy to get subtly wrong and impossible to notice
afterwards: a day of plausible-looking sessions gives no hint that an idle gap was absorbed
or that an uncertain minute was quietly rounded into the hour around it.

Everything here runs without a database. The segmenter is pure, so a test that needed one
would be testing the wrong thing.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from conftest import DEVICE, T0, ev, run

from groundtruth_server.models import UNCERTAIN, Resolution
from groundtruth_server.segment import (
    SegmentConfig,
    apply_resolutions,
    segment,
    segment_spans,
)
from groundtruth_server.timeutil import to_utc


def categories(sessions) -> list[str]:
    return [session.category for session in sessions]


# --------------------------------------------------------------------- merging


def test_empty_stream_yields_nothing(config):
    assert segment([], config) == []


def test_adjacent_same_category_events_merge(config):
    sessions = segment(run(range(0, 301, 60), "deep work"), config)
    assert len(sessions) == 1
    assert sessions[0].event_count == 6
    assert sessions[0].start_ts == T0


def test_category_change_closes_the_session(config):
    events = run(range(0, 121, 60), "deep work") + run(range(180, 301, 60), "research")
    sessions = segment(events, config)
    assert categories(sessions) == ["deep work", "research"]


def test_a_session_survives_a_process_change_within_one_category(config):
    """The editor and then the terminal is one stretch of work, not two.

    This is the case an app-name tracker cannot represent at all, so it is the case the
    segmenter most needs to get right.
    """
    events = run(range(0, 121, 60), "deep work", process="code.exe") + run(
        range(180, 301, 60), "deep work", process="windowsterminal.exe"
    )
    sessions = segment(events, config)
    assert len(sessions) == 1
    assert sessions[0].event_count == 6


def test_same_category_after_an_idle_gap_stays_two_sessions(config):
    """Doomscrolling before and after lunch is two sessions that total to one number.

    Merging them would be a lie about continuity; the summary is where they add up.
    """
    events = run(range(0, 121, 60), "doomscrolling") + run(range(3600, 3721, 60), "doomscrolling")
    sessions = segment(events, config)
    assert categories(sessions) == ["doomscrolling", "doomscrolling"]


# ------------------------------------------------------------------ idle gaps


def test_gap_exactly_at_the_threshold_does_not_close(config):
    """The rule is *longer than* the threshold, so the boundary itself still merges."""
    events = [ev(0, "research"), ev(config.idle_gap_s, "research")]
    assert len(segment(events, config)) == 1


def test_gap_one_second_over_the_threshold_closes(config):
    events = [ev(0, "research"), ev(config.idle_gap_s + 1, "research")]
    assert len(segment(events, config)) == 2


def test_idle_threshold_is_configurable(config):
    events = [ev(0, "research"), ev(300, "research")]
    assert len(segment(events, SegmentConfig(idle_gap_s=600))) == 1
    assert len(segment(events, SegmentConfig(idle_gap_s=120))) == 2


def test_unobserved_time_is_not_credited_to_anyone(config):
    """A sample before a long silence vouches for one interval, not for the silence.

    Two samples twenty minutes apart are two minutes of evidence. Crediting the gap would
    invent eighteen minutes of activity nobody observed, which is the failure mode the whole
    project is a reaction to.
    """
    sessions = segment([ev(0, "research"), ev(1200, "research")], config)
    assert len(sessions) == 2
    assert [session.duration_s for session in sessions] == [
        config.sample_coverage_s,
        config.sample_coverage_s,
    ]


# -------------------------------------------------------------- device handoff


def test_device_handoff_closes_the_session(config):
    events = [
        ev(0, "research", device="desk-win-01"),
        ev(30, "research", device="desk-win-01"),
        ev(60, "research", device="phone-android-01"),
        ev(90, "research", device="desk-win-01"),
    ]
    sessions = segment(events, config)
    assert [session.device_id for session in sessions] == [
        "desk-win-01",
        "phone-android-01",
        "desk-win-01",
    ]


def test_handoff_splits_even_when_everything_else_matches(config):
    """Same category, same second-level cadence, different machine: still a handoff."""
    events = [
        ev(offset, "deep work", device="a" if offset < 120 else "b") for offset in range(0, 241, 60)
    ]
    sessions = segment(events, config)
    assert len(sessions) == 2
    assert {session.device_id for session in sessions} == {"a", "b"}


# ------------------------------------------------------------------ uncertainty


def test_uncertain_does_not_merge_into_a_confident_neighbour(config):
    """The whole point of storing `uncertain` is that it stays askable."""
    events = (
        run(range(0, 121, 60), "research")
        + run(range(180, 301, 60), UNCERTAIN, confidence=0.4)
        + run(range(360, 481, 60), "research")
    )
    sessions = segment(events, config)
    assert categories(sessions) == ["research", UNCERTAIN, "research"]


def test_consecutive_uncertain_events_form_one_span(config):
    """Uncertainty merges with itself, or `/uncertain` would list one span per sample."""
    sessions = segment(run(range(0, 361, 60), UNCERTAIN, confidence=0.4), config)
    assert len(sessions) == 1
    assert sessions[0].event_count == 7


def test_an_uncertain_span_does_not_cross_a_process_change(config):
    """Resolving stores a few-shot example keyed by process, so a span needs exactly one."""
    events = run(range(0, 121, 60), UNCERTAIN, process="chrome.exe") + run(
        range(180, 301, 60), UNCERTAIN, process="msedge.exe"
    )
    spans = segment_spans(events, config)
    assert [span.process for span in spans] == ["chrome.exe", "msedge.exe"]


def test_a_confident_span_may_span_processes_but_an_uncertain_one_may_not(config):
    """The asymmetry is deliberate, so it is pinned down rather than left to be rediscovered."""
    confident = run(range(0, 61, 60), "deep work", process="code.exe") + run(
        range(120, 181, 60), "deep work", process="wt.exe"
    )
    uncertain = run(range(0, 61, 60), UNCERTAIN, process="code.exe") + run(
        range(120, 181, 60), UNCERTAIN, process="wt.exe"
    )
    assert len(segment(confident, config)) == 1
    assert len(segment(uncertain, config)) == 2


def test_a_single_uncertain_sample_survives_between_two_halves_of_one_activity(config):
    """One ambiguous minute splits a session rather than vanishing into it.

    The result is three sessions where a naive merge would report one, and that is the
    correct answer: the user is owed the question.
    """
    events = (
        run(range(0, 121, 60), "research")
        + [ev(180, UNCERTAIN, confidence=0.3)]
        + run(range(240, 361, 60), "research")
    )
    sessions = segment(events, config)
    assert categories(sessions) == ["research", UNCERTAIN, "research"]
    assert sessions[1].event_count == 1


# -------------------------------------------------- duration-weighted confidence


def test_confidence_is_weighted_by_duration_not_by_sample_count(config):
    """A long confident stretch is not dragged down by one brief doubtful sample.

    Forty minutes at 0.95 followed by a five-second sample at 0.1. By sample count the mean
    is 0.93; weighted by the time each sample actually accounts for it is 0.948. The
    difference is the point: a tracker that averaged per sample would let a blink of doubt
    rewrite forty minutes of confident work.
    """
    events = run(range(0, 2341, 60), "deep work", confidence=0.95)
    events.append(ev(2400, "deep work", confidence=0.1))
    events.append(ev(2405, "research"))

    sessions = segment(events, config)
    deep_work = sessions[0]

    assert deep_work.event_count == 41
    assert deep_work.duration_s == pytest.approx(2405)

    simple_mean = (0.95 * 40 + 0.1) / 41
    assert simple_mean == pytest.approx(0.9293, abs=1e-4)
    assert deep_work.confidence == pytest.approx(0.94823, abs=1e-4)
    assert deep_work.confidence > simple_mean


def test_a_long_doubtful_stretch_does_pull_confidence_down(config):
    """Weighting is not a way of ignoring doubt — sustained doubt still counts."""
    events = run(range(0, 1201, 60), "research", confidence=0.95) + run(
        range(1260, 2401, 60), "research", confidence=0.5
    )
    session = segment(events, config)[0]
    assert 0.7 < session.confidence < 0.76


def test_confidence_falls_back_to_a_plain_mean_when_there_is_no_duration(config):
    """Degenerate, but division by zero is not an acceptable way to find out."""
    zero = SegmentConfig(sample_coverage_s=0.0)
    session = segment([ev(0, "research", confidence=0.4)], zero)[0]
    assert session.duration_s == 0.0
    assert session.confidence == pytest.approx(0.4)


# ------------------------------------------------------- durations and coverage


def test_duration_equals_the_sum_of_its_events_coverage(config):
    """Two numbers that must agree, made to agree by construction rather than by rounding."""
    events = run(range(0, 601, 60), "research") + run(range(900, 1201, 60), "deep work")
    for span in segment_spans(events, config):
        assert span.duration_s == pytest.approx(sum(c.seconds for c in span.coverage))
        assert span.duration_s == pytest.approx((span.end_ts - span.start_ts).total_seconds())


def test_a_single_event_session_is_credited_one_sample_interval(config):
    session = segment([ev(0, "research")], config)[0]
    assert session.duration_s == config.sample_coverage_s
    assert session.end_ts == T0 + timedelta(seconds=config.sample_coverage_s)


def test_sessions_are_contiguous_when_the_stream_is(config):
    """A category change mid-cadence loses no time and double-counts none.

    The closing sample is credited exactly up to the next one, so the day's sessions tile
    the day instead of leaving slivers between them.
    """
    events = run(range(0, 121, 60), "research") + run(range(180, 301, 60), "deep work")
    first, second = segment(events, config)
    assert first.end_ts == second.start_ts
    assert first.duration_s + second.duration_s == pytest.approx(360)


def test_within_session_coverage_follows_the_real_cadence(config):
    """A 90s cadence is inside the idle threshold, so all 90 seconds belong to the session."""
    events = run(range(0, 271, 90), "research")
    session = segment(events, config)[0]
    assert session.duration_s == pytest.approx(270 + config.sample_coverage_s)


# ------------------------------------------------------------- recomputability


def test_segmentation_is_identical_when_run_twice(config):
    """Sessions are derived on every read, so two reads of unchanged events must agree.

    If this ever fails, the dashboard's list of sessions changes under it while nothing
    happened, and ids stop meaning anything.
    """
    events = (
        run(range(0, 601, 60), "deep work")
        + run(range(900, 1021, 60), UNCERTAIN, confidence=0.4)
        + run(range(1080, 1501, 60), "research", device="phone-android-01")
    )
    first = segment_spans(events, config)
    second = segment_spans(events, config)

    assert [span.id for span in first] == [span.id for span in second]
    assert [span.session for span in first] == [span.session for span in second]
    assert [span.coverage for span in first] == [span.coverage for span in second]


def test_segmentation_does_not_depend_on_input_order(config):
    """The stream is sorted here, not trusted, so a shuffled merge of two devices is safe."""
    events = (
        run(range(0, 601, 60), "deep work")
        + run(range(660, 781, 60), "research", device="phone-android-01")
        + run(range(840, 1201, 60), "doomscrolling")
    )
    expected = segment(events, config)

    shuffled = list(events)
    random.Random(1312).shuffle(shuffled)
    assert segment(shuffled, config) == expected


def test_a_higher_tier_supersedes_a_lower_one_for_the_same_instant(config):
    """Escalation produces a better answer for an instant, not a second activity.

    Left in, the stale Tier 1 sample would open a zero-second session on the category change
    between the two.
    """
    from groundtruth_server.models import Tier

    events = [
        ev(0, "doomscrolling", tier=Tier.WINDOW, confidence=0.5),
        ev(0, "research", tier=Tier.SCREEN, confidence=0.9),
        ev(60, "research", tier=Tier.SCREEN, confidence=0.9),
    ]
    sessions = segment(events, config)
    assert categories(sessions) == ["research"]
    assert sessions[0].event_count == 2


def test_ids_are_stable_across_derivations_and_distinct_across_devices(config):
    desk = segment_spans(run(range(0, 301, 60), "research", device="desk"), config)
    phone = segment_spans(run(range(0, 301, 60), "research", device="phone"), config)

    assert (
        desk[0].id == segment_spans(run(range(0, 301, 60), "research", device="desk"), config)[0].id
    )
    assert desk[0].id != phone[0].id
    assert desk[0].id.startswith("s_")


def test_uncertain_spans_are_prefixed_so_a_client_can_tell_them_apart(config):
    spans = segment_spans(run(range(0, 121, 60), UNCERTAIN, confidence=0.4), config)
    assert spans[0].id.startswith("u_")


# ------------------------------------------------------------------- labelling


def test_the_headline_label_is_the_one_with_the_most_time(config):
    """Several labels can share a category; the session is titled after the longer stretch."""
    events = run(range(0, 1801, 60), "research", label="reading API documentation") + run(
        range(1860, 2401, 60), "research", label="comparing inference providers"
    )
    session = segment(events, config)[0]
    assert session.label == "reading API documentation"


def test_evidence_is_deduplicated_and_capped(config):
    events = run(range(0, 1201, 60), "research", evidence="the same sentence every time")
    session = segment(events, config)[0]
    assert session.evidence == ["the same sentence every time"]


# ------------------------------------------------------- the resolution overlay


def test_a_resolution_relabels_events_without_editing_them(config):
    """Events stay append-only; the overlay is applied on the way to the segmenter."""
    events = tuple(run(range(0, 301, 60), UNCERTAIN, confidence=0.4))
    resolution = Resolution(
        span_id="u_test",
        device_id=DEVICE,
        start_ts=T0,
        end_ts=T0 + timedelta(seconds=360),
        category="research",
        label="comparing inference providers",
        resolved_at=to_utc(T0),
    )

    overlaid = apply_resolutions(events, [resolution])

    assert [e.category for e in overlaid] == ["research"] * 6
    assert [e.label for e in overlaid] == ["comparing inference providers"] * 6
    assert [e.category for e in events] == [UNCERTAIN] * 6


def test_a_resolved_span_is_certain_because_the_user_said_so(config):
    """A user-stated label is not a guess, and `Tier` already says user-stated wins."""
    events = run(range(0, 121, 60), UNCERTAIN, confidence=0.4)
    resolution = Resolution(
        span_id="u_test",
        device_id=DEVICE,
        start_ts=T0,
        end_ts=T0 + timedelta(seconds=180),
        category="research",
        label="docs",
        resolved_at=to_utc(T0),
    )
    session = segment(apply_resolutions(events, [resolution]), config)[0]
    assert session.confidence == pytest.approx(1.0)


def test_a_resolution_does_not_reach_past_the_span_it_answers(config):
    """`end_ts` is the first instant *after* the span, so the neighbour on it is untouched."""
    events = run(range(0, 121, 60), UNCERTAIN, confidence=0.4) + [ev(180, "doomscrolling")]
    resolution = Resolution(
        span_id="u_test",
        device_id=DEVICE,
        start_ts=T0,
        end_ts=T0 + timedelta(seconds=180),
        category="research",
        label="docs",
        resolved_at=to_utc(T0),
    )
    overlaid = apply_resolutions(events, [resolution])
    assert [e.category for e in overlaid] == ["research", "research", "research", "doomscrolling"]


def test_a_resolution_only_touches_its_own_device(config):
    events = run(range(0, 121, 60), UNCERTAIN, device="desk", confidence=0.4) + run(
        range(0, 121, 60), UNCERTAIN, device="phone", confidence=0.4
    )
    resolution = Resolution(
        span_id="u_test",
        device_id="desk",
        start_ts=T0,
        end_ts=T0 + timedelta(seconds=180),
        category="research",
        label="docs",
        resolved_at=to_utc(T0),
    )
    overlaid = apply_resolutions(events, [resolution])
    by_device = {(e.device_id, e.category) for e in overlaid}
    assert by_device == {("desk", "research"), ("phone", UNCERTAIN)}


def test_resolving_lets_a_span_merge_with_its_neighbours(config):
    """This is why segmentation is re-run instead of patched.

    The user answers one question and three sessions become one, because that is what the
    day actually was.
    """
    events = (
        run(range(0, 121, 60), "research")
        + run(range(180, 301, 60), UNCERTAIN, confidence=0.4)
        + run(range(360, 481, 60), "research")
    )
    assert len(segment(events, config)) == 3

    resolution = Resolution(
        span_id="u_test",
        device_id=DEVICE,
        start_ts=T0 + timedelta(seconds=180),
        end_ts=T0 + timedelta(seconds=360),
        category="research",
        label="research",
        resolved_at=to_utc(T0),
    )
    merged = segment(apply_resolutions(events, [resolution]), config)
    assert len(merged) == 1
    assert merged[0].event_count == 9


def test_a_later_answer_supersedes_an_earlier_one(config):
    """A user correcting themselves is not an error path."""
    events = run(range(0, 121, 60), UNCERTAIN, confidence=0.4)
    early = Resolution(
        span_id="u_test",
        device_id=DEVICE,
        start_ts=T0,
        end_ts=T0 + timedelta(seconds=180),
        category="research",
        label="docs",
        resolved_at=to_utc(T0),
    )
    late = Resolution(
        span_id="u_test",
        device_id=DEVICE,
        start_ts=T0,
        end_ts=T0 + timedelta(seconds=180),
        category="learning",
        label="a course",
        resolved_at=to_utc(T0 + timedelta(hours=1)),
    )
    session = segment(apply_resolutions(events, [early, late]), config)[0]
    assert session.category == "learning"


def test_no_resolutions_leaves_the_stream_untouched(config):
    events = tuple(run(range(0, 121, 60), "research"))
    assert apply_resolutions(events, []) == events
