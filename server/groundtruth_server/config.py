"""Runtime configuration.

The segmenter's thresholds are configuration rather than constants because they are claims
about a person's habits, not facts about software: two minutes of silence means something
different for someone who reads on paper at their desk than for someone who does not. They
are read here, at the edge, and passed down — `segment.py` never reaches for the
environment, which is what keeps it testable without one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .segment import SegmentConfig

DEFAULT_DB = "groundtruth.db"
FIXTURE_DAY = Path(__file__).resolve().parent.parent / "fixtures" / "day.json"


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the app needs to start."""

    db_path: str = DEFAULT_DB
    segment: SegmentConfig = field(default_factory=SegmentConfig)


def _float_env(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        # Starting with a silently defaulted threshold would mean every number the server
        # reports afterwards is subtly not the one that was asked for.
        raise ValueError(f"{key} must be a number, got {raw!r}") from exc


def settings_from_env(env: dict[str, str] | None = None) -> Settings:
    """Build settings from the process environment.

    `GROUNDTRUTH_DB` is a path, or `:memory:` for a store that lives as long as the process
    — useful for a demo against the fixture day, and useless for anything else.
    """
    env = dict(os.environ if env is None else env)
    # Defaults are read off an instance, not off the class. `SegmentConfig` is declared with
    # `slots=True`, and on a slotted dataclass the class attribute is the slot descriptor
    # rather than the default value — reading `SegmentConfig.idle_gap_s` here would hand a
    # descriptor to the comparison in the segmenter.
    fallback = SegmentConfig()
    return Settings(
        db_path=env.get("GROUNDTRUTH_DB", DEFAULT_DB),
        segment=SegmentConfig(
            idle_gap_s=_float_env(env, "GROUNDTRUTH_IDLE_GAP_S", fallback.idle_gap_s),
            sample_coverage_s=_float_env(
                env, "GROUNDTRUTH_SAMPLE_COVERAGE_S", fallback.sample_coverage_s
            ),
        ),
    )
