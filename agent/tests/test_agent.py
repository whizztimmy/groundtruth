"""Tests for the agent loop's event continuity.

Both behaviours here were found by running the agent against a real desktop, not by
reading the code — each component was individually correct and the gap was between them.
"""

from datetime import datetime, timezone

import pytest

from groundtruth.config import Config
from groundtruth.main import Agent, StubClassifier
from groundtruth.types import Classification, Event, Observation, Tier


@pytest.fixture
def config(tmp_path):
    return Config(
        device_id="test-device",
        sample_interval_s=0.01,
        idle_threshold_s=99999,
        server_url="http://unused.invalid",
        sync_batch_size=10,
        sync_interval_s=999,
        buffer_path=tmp_path / "events.db",
        sync_titles="none",
        buffer_max_rows=1000,
    )


class Collect:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def __call__(self, event: Event) -> None:
        self.events.append(event)


@pytest.fixture
def desktop(monkeypatch):
    """A fake foreground window under test control."""
    state = {"process": "chrome.exe", "title": "a page", "idle": False}

    def foreground() -> Observation:
        return Observation(
            ts=datetime.now(timezone.utc),
            tier=Tier.WINDOW,
            process=state["process"],
            window_title=state["title"],
        )

    monkeypatch.setattr("groundtruth.main.window.foreground", foreground)
    monkeypatch.setattr(
        "groundtruth.main.window.is_idle", lambda threshold: state["idle"]
    )
    return state


async def test_known_process_emits_without_escalating(config, desktop):
    """A recognised process must produce events without ever calling the model."""
    desktop["process"] = "spotify.exe"
    sink = Collect()
    agent = Agent(config, StubClassifier(), sink=sink)

    await agent._tick()

    assert len(sink.events) == 1
    assert sink.events[0].tier == Tier.WINDOW
    assert sink.events[0].classification.category == "entertainment"


async def test_label_carries_forward_while_rate_limited(config, desktop):
    """The gap a live run exposed: rate limiting must not punch holes in the timeline.

    Tier 2 fires at most once every twenty seconds. Without carry-forward, an
    unrecognised process emits on the sample that classified it and nothing for the next
    ten, so the segmenter sees isolated points instead of a span.
    """
    desktop["process"] = "someapp.exe"
    sink = Collect()
    agent = Agent(config, StubClassifier(), sink=sink)

    for _ in range(5):
        await agent._tick()

    assert len(sink.events) == 5
    assert sum(1 for e in sink.events if e.tier == Tier.SCREEN) == 1, (
        "exactly one real classification should have happened"
    )
    assert all(e.classification.category == "uncertain" for e in sink.events)
    assert "carried forward" in sink.events[-1].classification.evidence


async def test_carry_forward_does_not_decay_confidence(config, desktop):
    """Decaying would show attention draining during the most focused stretches."""
    desktop["process"] = "someapp.exe"
    sink = Collect()
    agent = Agent(config, StubClassifier(), sink=sink)

    await agent._tick()
    await agent._tick()

    first, second = sink.events[0], sink.events[1]
    assert second.classification.confidence == first.classification.confidence


async def test_no_label_yet_emits_nothing(config, desktop):
    """Before any classification exists, a gap is the honest answer.

    Inventing a label here would be precisely the failure the product exists to correct.
    """
    desktop["process"] = "someapp.exe"
    sink = Collect()
    agent = Agent(config, StubClassifier(), sink=sink)
    # Pretend Tier 2 just ran for a different process, so this one is rate limited
    # before it has ever been classified.
    agent._state.record_tier2("other.exe", datetime.now(timezone.utc), confident=True)

    await agent._tick()

    assert sink.events == []


async def test_idle_is_emitted_as_a_category(config, desktop):
    desktop["idle"] = True
    sink = Collect()
    agent = Agent(config, StubClassifier(), sink=sink)

    await agent._tick()

    assert len(sink.events) == 1
    assert sink.events[0].classification.category == "idle"
    assert sink.events[0].classification.confidence == 1.0


async def test_a_failing_sample_does_not_kill_the_loop(config, desktop, monkeypatch):
    """An agent that silently stops observing is worse than one that logs and continues.

    The user would not notice until their day came back missing.
    """
    calls = {"n": 0}

    def exploding_foreground():
        calls["n"] += 1
        raise RuntimeError("window handle vanished")

    monkeypatch.setattr("groundtruth.main.window.foreground", exploding_foreground)
    sink = Collect()
    agent = Agent(config, StubClassifier(), sink=sink)

    import asyncio

    task = asyncio.create_task(agent.run())
    await asyncio.sleep(0.08)
    agent.stop()
    await task

    assert calls["n"] > 1, "loop should have kept sampling after the failure"
