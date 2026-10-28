"""Tests for the event store.

Mostly about idempotency, because that is the property the agent's offline-first design
depends on and the one whose absence would only show up as quietly doubled totals.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import DEVICE, T0, ev, run

from groundtruth_server.models import (
    Classification,
    Event,
    FewShotExample,
    Resolution,
    Tier,
)
from groundtruth_server.timeutil import from_storage, to_iso, to_storage


def test_a_fresh_batch_is_all_accepted(store):
    assert store.append(run(range(0, 301, 60), "research")) == (6, 0)
    assert store.count_events() == 6


def test_a_retried_batch_reports_duplicates_instead_of_failing(store):
    """The agent retries whole batches after a flaky upload, by design.

    A server that raised here would make the agent's correct behaviour look like a fault and
    would stall a device that had no way to tell which half of its batch landed.
    """
    batch = run(range(0, 301, 60), "research")
    assert store.append(batch) == (6, 0)
    assert store.append(batch) == (0, 6)
    assert store.count_events() == 6


def test_a_partially_overlapping_retry_accepts_only_what_is_new(store):
    store.append(run(range(0, 181, 60), "research"))
    assert store.append(run(range(120, 301, 60), "research")) == (2, 2)
    assert store.count_events() == 6


def test_duplicates_within_a_single_batch_are_counted_too(store):
    """A client that retried inside one request gets the same answer as one that retried across two."""
    batch = [ev(0, "research"), ev(0, "research"), ev(60, "research")]
    assert store.append(batch) == (2, 1)
    assert store.count_events() == 2


def test_accepted_and_duplicates_always_account_for_the_whole_batch(store):
    batch = run(range(0, 301, 60), "research") + [ev(0, "research")]
    accepted, duplicates = store.append(batch)
    assert accepted + duplicates == len(batch)


def test_the_same_instant_on_two_devices_is_not_a_duplicate(store):
    """Idempotency is on `(device_id, ts)`, so two machines sampling together both land."""
    batch = [ev(0, "research", device="desk"), ev(0, "research", device="phone")]
    assert store.append(batch) == (2, 0)


def test_an_escalated_label_supersedes_the_tier_1_one_it_replaces(store):
    """Tier 2 looked at the screen; Tier 1 read a window title. The better answer wins.

    It is still counted as a duplicate — the contract has nowhere else to report it — but the
    stored row improves.
    """
    store.append([ev(0, "doomscrolling", tier=Tier.WINDOW, confidence=0.5)])
    assert store.append([ev(0, "research", tier=Tier.SCREEN, confidence=0.9)]) == (0, 1)

    stored = store.events_between(T0, T0)[0]
    assert stored.category == "research"
    assert stored.tier == Tier.SCREEN


def test_a_stale_lower_tier_resend_does_not_overwrite_a_better_label(store):
    """An offline device flushing an old Tier 1 queue must not undo an escalation."""
    store.append([ev(0, "research", tier=Tier.SCREEN, confidence=0.9)])
    store.append([ev(0, "doomscrolling", tier=Tier.WINDOW, confidence=0.5)])

    stored = store.events_between(T0, T0)[0]
    assert stored.category == "research"


def test_a_batch_arriving_out_of_order_is_read_back_in_order(store):
    store.append(list(reversed(run(range(0, 301, 60), "research"))))
    timestamps = [event.ts for event in store.events_between(T0, T0 + timedelta(hours=1))]
    assert timestamps == sorted(timestamps)


def test_range_queries_include_both_bounds(store):
    store.append(run(range(0, 301, 60), "research"))
    window = store.events_between(T0 + timedelta(seconds=60), T0 + timedelta(seconds=180))
    assert len(window) == 3


def test_an_empty_batch_is_a_no_op(store):
    assert store.append([]) == (0, 0)
    assert store.count_events() == 0


def test_bounds_are_none_until_something_is_stored(store):
    assert store.event_bounds() is None
    store.append(run(range(0, 301, 60), "research"))
    assert store.event_bounds() == (T0, T0 + timedelta(seconds=300))


def test_a_batch_larger_than_the_sqlite_parameter_limit_still_round_trips(store):
    """The existence check is chunked; this is the test that the chunking is real."""
    batch = run(range(0, 2000 * 30, 30), "research")
    assert len(batch) == 2000
    assert store.append(batch) == (2000, 0)
    assert store.append(batch) == (0, 2000)


def test_a_naive_timestamp_is_refused(store):
    """Two devices' streams cannot be interleaved if one of them has no offset."""
    naive = Event(
        ts=datetime(2026, 10, 28, 9, 0),
        device_id=DEVICE,
        tier=Tier.SCREEN,
        classification=Classification(
            label="research", category="research", confidence=0.9, evidence="e"
        ),
    )
    with pytest.raises(ValueError, match="explicit UTC offset"):
        store.append([naive])


def test_timestamps_survive_storage_exactly(store):
    moment = datetime(2026, 10, 28, 9, 14, 2, 123456, tzinfo=timezone.utc)
    assert from_storage(to_storage(moment)) == moment


def test_storage_text_sorts_chronologically():
    """Why the format is fixed-width: SQLite's `<` has to be time's `<`."""
    earlier = to_storage(datetime(2026, 10, 28, 9, 0, 0, 0, tzinfo=timezone.utc))
    later = to_storage(datetime(2026, 10, 28, 9, 0, 0, 500, tzinfo=timezone.utc))
    assert earlier < later
    assert len(earlier) == len(later)


def test_offsets_are_normalised_to_utc_on_the_way_in(store):
    """A device in another zone must interleave with one in UTC."""
    shifted = timezone(timedelta(hours=2))
    store.append(
        [
            Event(
                ts=datetime(2026, 10, 28, 11, 0, tzinfo=shifted),
                device_id=DEVICE,
                tier=Tier.SCREEN,
                classification=Classification(
                    label="research", category="research", confidence=0.9, evidence="e"
                ),
            )
        ]
    )
    assert store.events_between(T0, T0)[0].ts == T0


def test_wire_timestamps_drop_microseconds_when_there_are_none():
    """The contract's examples have no fractional part; neither do whole-second samples."""
    assert to_iso(T0) == "2026-10-28T09:00:00Z"
    assert to_iso(T0.replace(microsecond=123000)) == "2026-10-28T09:00:00.123Z"


# ------------------------------------------------------------------ resolutions


def _resolution(span_id: str = "u_test", **overrides) -> Resolution:
    fields = {
        "span_id": span_id,
        "device_id": DEVICE,
        "start_ts": T0,
        "end_ts": T0 + timedelta(minutes=7),
        "category": "research",
        "label": "docs",
        "resolved_at": T0,
    }
    fields.update(overrides)
    return Resolution(**fields)


def test_a_resolution_round_trips(store):
    store.save_resolution(_resolution())
    assert store.resolution("u_test") == _resolution()


def test_an_unknown_span_has_no_resolution(store):
    assert store.resolution("u_nope") is None


def test_a_second_answer_replaces_the_first(store):
    store.save_resolution(_resolution())
    store.save_resolution(_resolution(category="learning", label="a course"))

    stored = store.resolution("u_test")
    assert (stored.category, stored.label) == ("learning", "a course")


def test_resolutions_are_fetched_by_overlap_not_containment(store):
    """A span straddling the window's edge still has to be overlaid, or its events revert."""
    store.save_resolution(_resolution())
    overlapping = store.resolutions_between(T0 + timedelta(minutes=3), T0 + timedelta(hours=1))
    assert len(overlapping) == 1
    assert store.resolutions_between(T0 + timedelta(hours=2), T0 + timedelta(hours=3)) == ()


# ------------------------------------------------------------ few-shot examples


def test_few_shot_examples_accumulate_per_process(store):
    for index, category in enumerate(("research", "doomscrolling")):
        store.save_few_shot(
            FewShotExample(
                process="chrome.exe",
                category=category,
                label=category,
                evidence="e",
                window_title="t",
                source_span_id=f"u_{index}",
                created_at=T0 + timedelta(minutes=index),
            )
        )
    store.save_few_shot(
        FewShotExample(
            process="msedge.exe",
            category="admin",
            label="admin",
            evidence="e",
            window_title="t",
            source_span_id="u_9",
            created_at=T0,
        )
    )

    chrome = store.few_shot_for("chrome.exe")
    assert [example.category for example in chrome] == ["doomscrolling", "research"]
    assert len(store.few_shot_for("msedge.exe")) == 1
    assert store.few_shot_for("unused.exe") == ()
