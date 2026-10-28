"""Agent configuration, read once at startup.

Everything here is environment-driven so that the same binary runs on a developer laptop
with no GPU, on a workstation serving Glimmer locally, and against partner inference —
without a code change. See `backends.Placement` for why that matters.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a number") from exc


def _device_id() -> str:
    """Stable id for this machine.

    Persisted to a dotfile rather than derived from hostname or MAC: a hostname can
    change, and a hardware address is an identifier the user did not consent to sharing
    with a sync server. A random id per install leaks nothing and is equally useful for
    attributing events to a device.
    """
    configured = os.environ.get("DEVICE_ID", "").strip()
    if configured:
        return configured

    path = Path.home() / ".groundtruth" / "device_id"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    generated = f"{os.name}-{uuid.uuid4().hex[:12]}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generated, encoding="utf-8")
    return generated


@dataclass(frozen=True, slots=True)
class Config:
    device_id: str
    sample_interval_s: float
    idle_threshold_s: float
    server_url: str
    sync_batch_size: int
    sync_interval_s: float

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            device_id=_device_id(),
            # Two seconds is a compromise: fast enough that a short span is not missed
            # entirely, slow enough that Tier 1 costs nothing noticeable. Tier 2 has its
            # own much slower rate limit in escalate.MIN_INTERVAL.
            sample_interval_s=_env_float("SAMPLE_INTERVAL_S", 2.0),
            # Five minutes, not two. Input idleness conflates "away from the machine"
            # with "reading something long", and reading is a category this product
            # exists to measure — so a short timeout would systematically mislabel the
            # thing it most needs to get right. Observed during a live run: a user
            # reading for two minutes was reported idle.
            #
            # The real fix is for Tier 2 to arbitrate, since a static screen with no
            # input looks different from a lock screen. Until then, erring long costs a
            # few idle minutes misfiled as activity; erring short costs the reading
            # category entirely.
            idle_threshold_s=_env_float("IDLE_THRESHOLD_S", 300.0),
            server_url=os.environ.get(
                "GROUNDTRUTH_SERVER", "http://127.0.0.1:8000"
            ).rstrip("/"),
            # Batched so a flaky network costs one retry rather than one per sample,
            # and so the agent works offline and catches up.
            sync_batch_size=int(_env_float("SYNC_BATCH_SIZE", 50)),
            sync_interval_s=_env_float("SYNC_INTERVAL_S", 60.0),
        )
