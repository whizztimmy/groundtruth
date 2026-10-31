"""Tests for Tier 3 superseding observations over a span.

`order_events` already prefers a higher tier when two events share an instant. This covers
the other case, which the first live check-in exposed: a check-in claims a *stretch*, and
without this rule a backdated gym session overlapping a few minutes of desktop sampling
derived as `deep work, uncertain, fitness, uncertain, deep work` — five sessions, three of
them zero seconds, thrashing between two contradictory claims about the same minutes.

The rule is physical, not statistical: you cannot be at the gym and at this desk. The
boundary cases are where it earns its keep, because suppressing one event too many
silently deletes real observed time and suppressing one too few leaves the fragmentation
it exists to remove.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import DEVICE, T0, ev, run

from groundtruth_server.models import Tier
from groundtruth_server.segment import (
    SegmentConfig,
    apply_stated_spans,
    segment_spans,
    stated_intervals,
)

PHONE = "phone-android-01"


def stated(offsets, category="fitness", **kwargs):
    """A check-in run: Tier 3, no process, no window."""
    return run(
        offsets, category, tier=Tier.VOICE, process=None, window_title=None, **kwargs
    )


class TestTheLiveBug:
    def test_an_overlapping_check_in_does_not_fragment_the_timeline(self, config):
        """The exact shape of the first live failure.

        A check-in covering 0-600s, with desktop sampling from 480s. Before the rule this
        derived five spans; the observed minutes inside the stated span must not be
        derived at all.
        """
        events = stated(range(0, 601, 120)) + run(range(480, 721, 60), "deep work")
        spans = segment_spans(apply_stated_spans(events, config), config)
        assert [s.category for s in spans] == ["fitness", "deep work"]

    def test_no_zero_length_spans_survive(self, config):
        events = stated(range(0, 601, 120)) + run(range(480, 721, 60), "deep work")
        spans = segment_spans(apply_stated_spans(events, config), config)
        assert all(s.duration_s > 0 for s in spans)

    def test_the_observed_time_after_the_span_is_kept(self, config):
        """Suppression must stop at the stated interval's end, or a check-in would erase
        the rest of the day's work."""
        events = stated(range(0, 601, 120)) + run(range(480, 1201, 60), "deep work")
        kept = apply_stated_spans(events, config)
        observed = [e for e in kept if e.tier is Tier.SCREEN]
        assert observed
        assert min(e.ts for e in observed) > T0 + timedelta(seconds=600)


class TestAcrossDevices:
    def test_a_phone_check_in_does_not_suppress_the_desktop(self, config):
        """An overlap across devices is not a contradiction.

        A desktop left rendering something while you are out is a real record from another
        machine. Suppressing it would be this rule inventing a fact rather than preferring
        one, and it is how the fixture's gym block coexists with the desktop day.
        """
        events = stated(range(0, 601, 120), device=PHONE) + run(
            range(0, 601, 60), "deep work", device=DEVICE
        )
        kept = apply_stated_spans(events, config)
        assert any(e.device_id == DEVICE and e.tier is Tier.SCREEN for e in kept)
        assert any(e.device_id == PHONE for e in kept)

    def test_both_devices_still_derive_spans(self, config):
        events = stated(range(0, 601, 120), device=PHONE) + run(
            range(0, 601, 60), "deep work", device=DEVICE
        )
        spans = segment_spans(apply_stated_spans(events, config), config)
        assert {s.device_id for s in spans} == {PHONE, DEVICE}


class TestBoundaries:
    @pytest.mark.parametrize("offset", [0, 600])
    def test_an_event_exactly_on_a_boundary_is_suppressed(self, config, offset):
        """Inclusive at both ends. A sample taken at the instant a check-in starts is
        inside the claim, and leaving it in reproduces the fragmentation at one end."""
        events = stated(range(0, 601, 120)) + [ev(offset, "deep work")]
        kept = apply_stated_spans(events, config)
        assert all(e.tier is Tier.VOICE for e in kept)

    @pytest.mark.parametrize("offset", [-1, 601])
    def test_an_event_just_outside_is_kept(self, config, offset):
        events = stated(range(0, 601, 120)) + [ev(offset, "deep work")]
        kept = apply_stated_spans(events, config)
        assert any(e.tier is Tier.SCREEN for e in kept)


class TestIntervalConstruction:
    def test_two_check_ins_apart_do_not_merge_into_one_interval(self, config):
        """Otherwise an hour of work between two check-ins would be swallowed whole.

        The run breaks on a gap wider than `idle_gap_s`, which is the same question the
        segmenter asks of observations.
        """
        events = stated(range(0, 241, 120)) + stated(range(3600, 3841, 120))
        intervals = stated_intervals(events, config)
        assert len(intervals[DEVICE]) == 2

    def test_work_between_two_check_ins_survives(self, config):
        events = (
            stated(range(0, 241, 120))
            + run(range(1200, 1441, 60), "deep work")
            + stated(range(3600, 3841, 120))
        )
        spans = segment_spans(apply_stated_spans(events, config), config)
        assert [s.category for s in spans] == ["fitness", "deep work", "fitness"]

    def test_a_different_activity_starts_a_new_interval(self, config):
        events = stated(range(0, 241, 120)) + stated(
            range(360, 601, 120), category="commute"
        )
        intervals = stated_intervals(events, config)
        assert len(intervals[DEVICE]) == 2

    def test_a_cadence_equal_to_the_idle_gap_stays_one_interval(self, config):
        """The agent's check-in cadence is exactly `idle_gap_s`, so this sits on the
        boundary by construction. If the comparison were strict rather than `<=`, every
        check-in would shatter into one interval per event."""
        assert config.idle_gap_s == 120.0
        events = stated(range(0, 1201, 120))
        assert len(stated_intervals(events, config)[DEVICE]) == 1


class TestItChangesNothingElse:
    def test_a_stream_with_no_check_in_is_untouched(self, config):
        events = run(range(0, 601, 60), "deep work")
        assert apply_stated_spans(events, config) == tuple(events)

    def test_a_check_in_does_not_suppress_another_check_in(self, config):
        events = stated(range(0, 601, 120))
        kept = apply_stated_spans(events, config)
        assert len(kept) == len(events)

    def test_the_input_is_not_mutated(self, config):
        """This is a read-time overlay. Events stay append-only, so a deleted check-in has
        to re-derive the observations that were set aside."""
        events = stated(range(0, 601, 120)) + run(range(480, 721, 60), "deep work")
        before = list(events)
        apply_stated_spans(events, config)
        assert events == before

    def test_no_check_in_means_no_intervals(self, config):
        assert stated_intervals(run(range(0, 601, 60), "deep work"), config) == {}


class TestWithoutConfig:
    def test_the_rule_works_with_default_thresholds(self):
        """Callers in `derive.py` pass a config, but the default must behave, since a
        signature that only works when fully specified invites a caller to guess."""
        events = stated(range(0, 601, 120)) + run(range(480, 721, 60), "deep work")
        spans = segment_spans(apply_stated_spans(events), SegmentConfig())
        assert [s.category for s in spans] == ["fitness", "deep work"]
