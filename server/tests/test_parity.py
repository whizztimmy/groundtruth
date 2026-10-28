"""Does the server still agree with `agent/groundtruth/types.py`?

The two packages share contracts by convention rather than by import, for the reason stated
in `models.py`: the agent's dependencies do not belong on a sync host. Convention without a
check is drift, and drift here is expensive in a specific way — the agent would keep sending
events the server kept accepting, and the field that stopped lining up would surface as a
wrong number on a dashboard rather than as an error anywhere.

So the agent's module is loaded from the repository, by path, with no import of the agent
package itself. If the layout moves the tests skip rather than fail: a missing agent is a
different problem from a disagreeing one.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from groundtruth_server import models as server_models

AGENT_TYPES = Path(__file__).resolve().parents[2] / "agent" / "groundtruth" / "types.py"


def load_agent_types() -> ModuleType:
    """Load the agent's contract module directly from its file.

    Loaded by path because importing `groundtruth.types` would import the agent package,
    which pulls in `mss` and `pywin32` — neither of which installs on a sync host, and
    neither of which this test needs.
    """
    spec = importlib.util.spec_from_file_location("agent_types", AGENT_TYPES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because `@dataclass(slots=True)` rebuilds the class and
    # then looks itself up in `sys.modules` to repoint the name at the replacement. Skip
    # this and every slotted dataclass in the file fails to define itself.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agent() -> ModuleType:
    if not AGENT_TYPES.exists():
        pytest.skip(f"agent contracts not present at {AGENT_TYPES}")
    return load_agent_types()


def field_names(cls) -> list[str]:
    return [field.name for field in dataclasses.fields(cls)]


@pytest.mark.parametrize("name", ["Classification", "Event", "Session"])
def test_mirrored_dataclasses_have_the_same_fields(agent, name):
    """Field-for-field, in the same order, so positional construction agrees too."""
    assert field_names(getattr(server_models, name)) == field_names(getattr(agent, name))


@pytest.mark.parametrize("name", ["Classification", "Event", "Session"])
def test_mirrored_dataclasses_have_the_same_defaults(agent, name):
    """A default that differs is a value that appears on one side and not the other."""

    def defaults(cls) -> dict[str, object]:
        return {
            field.name: field.default
            for field in dataclasses.fields(cls)
            if field.default is not dataclasses.MISSING
        }

    assert defaults(getattr(server_models, name)) == defaults(getattr(agent, name))


def test_the_tier_ladder_is_the_same_ladder(agent):
    """The server relies on the ordering to decide which of two labels for one instant wins."""
    assert {tier.name: int(tier) for tier in server_models.Tier} == {
        tier.name: int(tier) for tier in agent.Tier
    }
    assert server_models.Tier.VOICE > server_models.Tier.SCREEN > server_models.Tier.WINDOW


def test_the_reserved_uncertain_category_is_spelled_identically(agent):
    """A mismatch here would make every uncertain span a normal session with an odd name."""
    assert server_models.UNCERTAIN == agent.UNCERTAIN


def test_an_event_round_trips_into_the_agents_dataclass(agent):
    """Construct the agent's types from the server's values, and compare field by field.

    Stronger than comparing signatures: it fails if a type that happens to share a field name
    cannot actually hold the value the server would put in it.
    """
    from conftest import ev

    server_event = ev(0, "research", label="reading API documentation", confidence=0.88)

    agent_event = agent.Event(
        ts=server_event.ts,
        device_id=server_event.device_id,
        tier=agent.Tier(int(server_event.tier)),
        classification=agent.Classification(
            label=server_event.classification.label,
            category=server_event.classification.category,
            confidence=server_event.classification.confidence,
            evidence=server_event.classification.evidence,
            reasoning_effort=server_event.classification.reasoning_effort,
        ),
        process=server_event.process,
        window_title=server_event.window_title,
    )

    for field in dataclasses.fields(agent.Event):
        if field.name == "classification":
            continue
        assert getattr(agent_event, field.name) == getattr(server_event, field.name)
    for field in dataclasses.fields(agent.Classification):
        assert getattr(agent_event.classification, field.name) == getattr(
            server_event.classification, field.name
        )


def test_a_derived_session_round_trips_into_the_agents_dataclass(agent, config):
    """The segmenter's output has to be a `Session` the agent would recognise.

    The agent segments locally while offline and the server re-segments on sync; if these two
    `Session` types diverged, the same day would describe itself differently depending on
    which component you asked.
    """
    from conftest import run

    from groundtruth_server.segment import segment

    derived = segment(run(range(0, 601, 60), "research"), config)[0]
    mirrored = agent.Session(
        **{field.name: getattr(derived, field.name) for field in dataclasses.fields(derived)}
    )

    assert mirrored.duration_s == derived.duration_s
    assert mirrored.start_ts == derived.start_ts
    assert mirrored.category == derived.category
    assert mirrored.event_count == derived.event_count
