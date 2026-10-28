"""The agent loop.

Ties the tiers together: sample cheaply, escalate only when the cheap signal is
ambiguous, classify, emit an event. Everything expensive is behind a decision made in
`escalate.should_escalate`, and everything that sees pixels is behind the placement guard
in `backends.vision`.

The classifier is injected rather than constructed here, so the loop runs end to end with
a stub before any model is reachable. That is not a testing convenience — it means the
sampling cadence, the escalation policy and the sync path can all be exercised and
demonstrated while partner inference is still being provisioned.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Protocol

from . import known
from .capture import window
from .config import Config
from .escalate import EscalationState, should_escalate
from .types import Classification, Event, Observation, Tier

log = logging.getLogger("groundtruth")

IDLE = Classification(
    label="idle",
    category="idle",
    confidence=1.0,
    evidence="No input for longer than the idle threshold.",
)
"""Idle is asserted, not inferred — the OS tells us directly, so confidence is 1.0.

Treating it as a real classification rather than a gap keeps the segmenter simple: it
closes a session on category change, and idle is just another category.
"""


class SupportsClassify(Protocol):
    """What the loop needs from a classifier. Deliberately narrow.

    Narrow because the loop should not care whether the model is local, self-hosted, a
    stub, or a replay of a recorded day — all four are useful, and all four satisfy this.
    """

    async def classify_verified(
        self, obs: Observation, frame: object
    ) -> Classification: ...


class StubClassifier:
    """Deterministic labels from the process name, for running without a model.

    This is honest about what it is: a placeholder that reproduces exactly the app-name
    heuristic the product exists to replace. Running the agent against it and seeing
    "chrome.exe -> browsing" is a useful demonstration of the problem, not of the
    solution.
    """

    MAPPING = {
        "code.exe": ("deep work", "Editor in foreground."),
        "windowsterminal.exe": ("deep work", "Terminal in foreground."),
        "chrome.exe": ("shallow work", "Browser in foreground; contents unexamined."),
        "msedge.exe": ("shallow work", "Browser in foreground; contents unexamined."),
        "discord.exe": ("communication", "Chat client in foreground."),
        "slack.exe": ("communication", "Chat client in foreground."),
    }

    async def classify_verified(
        self, obs: Observation, frame: object
    ) -> Classification:
        category, evidence = self.MAPPING.get(
            (obs.process or "").lower(),
            ("uncertain", "No model available; process not in the stub mapping."),
        )
        return Classification(
            label=category,
            category=category,
            confidence=0.5,
            evidence=f"{evidence} (stub classifier — not a real observation)",
        )


class Agent:
    """Samples, escalates, classifies, emits.

    Holds no frames beyond the instant of classification and writes none to disk.
    """

    def __init__(
        self,
        config: Config,
        classifier: SupportsClassify,
        *,
        sink: Callable[[Event], Awaitable[None]],
        grab_frame: Callable[[], object] | None = None,
    ) -> None:
        self._config = config
        self._classifier = classifier
        self._sink = sink
        self._grab_frame = grab_frame
        self._state = EscalationState()
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Sample until stopped.

        A failure in one sample must never kill the loop: an agent that silently stops
        observing is worse than one that logs an error and keeps going, because the user
        will not notice until their day is missing.
        """
        log.info("agent started (device=%s)", self._config.device_id)
        while not self._stopping.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("sample failed; continuing")

            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._config.sample_interval_s
                )
            except asyncio.TimeoutError:
                pass
        log.info("agent stopped")

    async def _tick(self) -> None:
        if window.is_idle(self._config.idle_threshold_s):
            await self._emit(
                Observation(ts=_now(), tier=Tier.WINDOW), IDLE, Tier.WINDOW
            )
            return

        obs = window.foreground()
        decision = should_escalate(obs, self._state)
        log.debug("tier1 %s -> %s (%s)", obs.process, decision.escalate, decision.reason)

        if not decision.escalate:
            # Tier 1 resolved it. Emitting here is what makes the cheap tier actually
            # cover a day rather than leave gaps wherever the model was not consulted.
            resolved = known.lookup(obs.process)
            if resolved is None:
                # The policy should have escalated; treating this as a hole rather than
                # guessing keeps the two components honest about disagreeing.
                log.warning("no tier 1 label for %s and no escalation", obs.process)
                return
            category, label = resolved
            await self._emit(
                obs,
                Classification(
                    label=label,
                    category=category,
                    confidence=0.9,
                    evidence=f"{obs.process} in foreground; its name describes the activity.",
                ),
                Tier.WINDOW,
            )
            return

        frame = self._grab_frame() if self._grab_frame else None
        classification = await self._classifier.classify_verified(obs, frame)
        # Dropped immediately; nothing downstream receives the frame.
        del frame

        self._state.record_tier2(
            (obs.process or "").lower(), obs.ts, confident=classification.is_acceptable
        )
        await self._emit(obs, classification, Tier.SCREEN)

    async def _emit(
        self, obs: Observation, classification: Classification, tier: Tier
    ) -> None:
        await self._sink(
            Event(
                ts=obs.ts,
                device_id=self._config.device_id,
                tier=tier,
                classification=classification,
                process=obs.process,
                window_title=obs.window_title,
            )
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = Config.from_env()

    async def print_sink(event: Event) -> None:
        c = event.classification
        log.info(
            "%s | %-22s | %-14s | %.2f | %s",
            event.ts.strftime("%H:%M:%S"),
            event.process or "-",
            c.category,
            c.confidence,
            c.evidence,
        )

    agent = Agent(config, StubClassifier(), sink=print_sink)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, agent.stop)
        except NotImplementedError:
            # Windows does not support add_signal_handler for these; KeyboardInterrupt
            # propagates instead, which asyncio.run handles.
            pass

    await agent.run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
