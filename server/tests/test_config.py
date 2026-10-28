"""Tests for environment configuration.

Thin module, real failure mode: a threshold that arrives as something other than a number
is not caught by anything downstream — it surfaces as a `TypeError` deep inside the
segmenter on the first request that compares a gap against it, long after startup looked
fine. So the defaults are asserted to *be numbers*, not merely to be present.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from groundtruth_server.app import create_app
from groundtruth_server.config import DEFAULT_DB, settings_from_env
from groundtruth_server.segment import SegmentConfig


def test_an_empty_environment_yields_working_defaults():
    """Not just equal to the defaults — usable as numbers.

    `SegmentConfig` is a slotted dataclass, so reading a default off the class rather than
    off an instance returns the slot descriptor instead of the value. It compares equal to
    nothing and raises on `>`, which is why this asserts the type.
    """
    settings = settings_from_env({})
    defaults = SegmentConfig()

    assert settings.db_path == DEFAULT_DB
    assert settings.segment == defaults
    assert isinstance(settings.segment.idle_gap_s, float)
    assert isinstance(settings.segment.sample_coverage_s, float)
    assert settings.segment.idle_gap_s > 0


def test_thresholds_can_be_overridden():
    settings = settings_from_env(
        {
            "GROUNDTRUTH_DB": "/tmp/somewhere.db",
            "GROUNDTRUTH_IDLE_GAP_S": "300",
            "GROUNDTRUTH_SAMPLE_COVERAGE_S": "15",
        }
    )
    assert settings.db_path == "/tmp/somewhere.db"
    assert settings.segment == SegmentConfig(idle_gap_s=300.0, sample_coverage_s=15.0)


def test_a_blank_value_falls_back_rather_than_failing():
    """An exported-but-empty variable is a shell artefact, not an instruction."""
    assert settings_from_env({"GROUNDTRUTH_IDLE_GAP_S": "  "}).segment == SegmentConfig()


def test_a_nonsense_threshold_fails_at_startup():
    """Better than defaulting silently: every number reported afterwards would be the wrong one."""
    with pytest.raises(ValueError, match="GROUNDTRUTH_IDLE_GAP_S must be a number"):
        settings_from_env({"GROUNDTRUTH_IDLE_GAP_S": "two minutes"})


def test_the_configured_threshold_reaches_the_segmenter(store, tmp_path):
    """An end-to-end check that configuration is wired through, not merely parsed.

    Two events five minutes apart are two sessions at the default threshold and one at ten
    minutes. Asserting it through the API is the only way to catch a `Settings` that never
    reaches `segment_spans`.
    """
    from conftest import ev

    store.append([ev(0, "research"), ev(300, "research")])
    params = {"from": "2026-10-28T00:00:00Z", "to": "2026-10-28T23:59:59Z"}

    tight = settings_from_env({"GROUNDTRUTH_DB": str(tmp_path / "unused.db")})
    with TestClient(create_app(store=store, settings=tight)) as client:
        assert len(client.get("/sessions", params=params).json()["sessions"]) == 2

    loose = settings_from_env(
        {"GROUNDTRUTH_DB": str(tmp_path / "unused.db"), "GROUNDTRUTH_IDLE_GAP_S": "600"}
    )
    with TestClient(create_app(store=store, settings=loose)) as client:
        assert len(client.get("/sessions", params=params).json()["sessions"]) == 1
