"""Tests for the escalation policy.

The policy decides when the agent is allowed to look at pixels, so its edge cases are
worth pinning down: an over-eager rule costs GPU-seconds every few seconds for sixteen
hours a day, and an under-eager one silently reverts the product to an app-name tracker.
"""

from datetime import datetime, timedelta, timezone

import pytest

from groundtruth.escalate import (
    MIN_INTERVAL,
    STALE_AFTER,
    EscalationState,
    should_escalate,
)
from groundtruth.types import Observation, Tier

T0 = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


def obs(process: str | None, title: str | None = "a window", at: datetime = T0) -> Observation:
    return Observation(ts=at, tier=Tier.WINDOW, process=process, window_title=title)


def test_no_process_never_escalates():
    assert not should_escalate(obs(None), EscalationState())


def test_unseen_ambiguous_process_escalates():
    d = should_escalate(obs("chrome.exe"), EscalationState())
    assert d.escalate
    assert "never classified" in d.reason


def test_unambiguous_process_resolves_at_tier1():
    assert not should_escalate(obs("photoshop.exe"), EscalationState())


def test_fresh_label_suppresses_repeat():
    state = EscalationState()
    state.record_tier2("chrome.exe", T0, confident=True)
    state.record_title("chrome.exe", "a window")

    later = T0 + MIN_INTERVAL + timedelta(seconds=1)
    assert not should_escalate(obs("chrome.exe", at=later), state, now=later)


def test_stale_label_reescalates():
    state = EscalationState()
    state.record_tier2("chrome.exe", T0, confident=True)

    later = T0 + STALE_AFTER + timedelta(seconds=1)
    d = should_escalate(obs("chrome.exe", at=later), state, now=later)
    assert d.escalate
    assert "stale" in d.reason


def test_new_title_on_ambiguous_process_escalates():
    state = EscalationState()
    state.record_tier2("chrome.exe", T0, confident=True)
    state.record_title("chrome.exe", "docs")

    later = T0 + MIN_INTERVAL + timedelta(seconds=1)
    d = should_escalate(obs("chrome.exe", title="a feed", at=later), state, now=later)
    assert d.escalate
    assert "unseen window title" in d.reason


def test_rate_limit_overrides_everything():
    """A burst of alt-tabs must not produce a burst of classifications."""
    state = EscalationState()
    state.record_tier2("code.exe", T0, confident=True)

    soon = T0 + MIN_INTERVAL - timedelta(seconds=1)
    d = should_escalate(obs("chrome.exe", at=soon), state, now=soon)
    assert not d.escalate
    assert "rate limited" in d.reason


@pytest.mark.parametrize("title", ["", "  ", "ab", None])
def test_unusable_title_escalates(title):
    state = EscalationState()
    state.last_any_tier2_at = T0 - timedelta(minutes=5)
    d = should_escalate(obs("someapp.exe", title=title), state)
    assert d.escalate


def test_low_confidence_history_escalates():
    state = EscalationState()
    state.last_any_tier2_at = T0 - timedelta(minutes=5)
    state.record_tier2("someapp.exe", T0 - timedelta(minutes=5), confident=False)
    state.record_tier2("someapp.exe", T0 - timedelta(minutes=4), confident=False)

    d = should_escalate(obs("someapp.exe"), state)
    assert d.escalate
    assert "low-confidence" in d.reason


def test_decision_is_truthy():
    """Callers write `if should_escalate(...)`, so the bool protocol must hold."""
    assert bool(should_escalate(obs("chrome.exe"), EscalationState())) is True
    assert bool(should_escalate(obs(None), EscalationState())) is False
