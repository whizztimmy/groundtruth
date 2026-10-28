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
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol

from . import known
from .buffer import EventBuffer
from .capture import window
from .config import Config
from .escalate import EscalationState, should_escalate
from .sync import SyncClient
from .types import Classification, Event, Observation, Tier

log = logging.getLogger("groundtruth")

_INPUT_HISTORY = 16
"""Samples of input history to retain — roughly half a minute at the default cadence.

Long enough for `motion.classify` to see a run, short enough that a burst of typing ten
minutes ago does not colour what is on screen now.
"""

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
        self._input_history: list[bool] = []
        """Recent per-sample input flags, newest last.

        Half of the motion model in `perceive/motion.py`. The churn half arrives with
        Tier 2 region tracking; collecting this continuously means that when a frame is
        finally classified, the run of input leading up to it is already known rather
        than having to be reconstructed.
        """
        self._last: dict[str, Classification] = {}
        """Most recent Tier 2 label per process, carried forward between classifications.

        Tier 2 is rate limited to roughly one call per twenty seconds, so without this a
        process that only Tier 2 can label produces an event on the sample that
        classified it and nothing for the next ten samples. The timeline would be a
        scatter of points rather than a span, and the segmenter would have nothing to
        merge into a session.
        """

    def stop(self) -> None:
        self._stopping.set()

    @property
    def stopping(self) -> asyncio.Event:
        """Set when the agent is shutting down.

        Exposed so sibling tasks — the syncer, chiefly — can wind down on the same
        signal instead of being cancelled mid-request.
        """
        return self._stopping

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
        idle_s = window.idle_seconds()
        if idle_s >= self._config.idle_threshold_s:
            await self._emit(
                Observation(ts=_now(), tier=Tier.WINDOW), IDLE, Tier.WINDOW
            )
            return

        # Recorded on every sample, including Tier 1 ones that never reach a model: the
        # signal is only meaningful as a run, so it has to be collected continuously
        # rather than fetched at classification time.
        had_input = idle_s < self._config.sample_interval_s
        self._input_history.append(had_input)
        if len(self._input_history) > _INPUT_HISTORY:
            self._input_history.pop(0)

        obs = replace(window.foreground(), input_active=had_input)
        decision = should_escalate(obs, self._state)
        log.debug("tier1 %s -> %s (%s)", obs.process, decision.escalate, decision.reason)

        if not decision.escalate:
            # Tier 1 resolved it. Emitting here is what makes the cheap tier actually
            # cover a day rather than leave gaps wherever the model was not consulted.
            resolved = known.lookup(obs.process)
            if resolved is None:
                carried = self._last.get((obs.process or "").lower())
                if carried is not None:
                    await self._emit(obs, _carry(carried), Tier.WINDOW)
                    return
                # No Tier 1 label and nothing to carry: the very first samples of an
                # unrecognised process while Tier 2 is rate limited. A gap is the honest
                # answer — inventing a label here would be the exact failure the product
                # exists to correct.
                log.debug("no label yet for %s; awaiting first classification", obs.process)
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
        self._last[(obs.process or "").lower()] = classification
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


def _carry(previous: Classification) -> Classification:
    """Re-assert a previous label for a sample that could not be classified afresh.

    Confidence is deliberately not reduced. The label is as good as it was a few seconds
    ago, and decaying it would make the dashboard show attention quietly draining away
    during exactly the stretches where someone was most focused.

    The evidence string says it was carried, so a user asking "why did it say that?"
    gets an honest answer rather than the impression of a fresh observation.
    """
    return Classification(
        label=previous.label,
        category=previous.category,
        confidence=previous.confidence,
        evidence=f"{previous.evidence} (carried forward; no new classification due)",
        reasoning_effort=previous.reasoning_effort,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _sync_forever(client: SyncClient, interval_s: float, stop: asyncio.Event) -> None:
    """Flush the buffer on an interval until stopped.

    Runs beside the capture loop rather than inside it so a slow or unreachable server
    can never delay a sample. Capture is the thing that cannot be retried later.
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
        try:
            await client.flush_all()
        except Exception:
            log.exception("sync failed; events remain buffered")


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = Config.from_env()

    buffer = EventBuffer(config.buffer_path)
    buffer.prune(config.buffer_max_rows)
    sync = SyncClient(
        server_url=config.server_url,
        device_id=config.device_id,
        buffer=buffer,
        batch_size=config.sync_batch_size,
        titles=config.sync_titles,
    )

    async def buffering_sink(event: Event) -> None:
        # Durable before anything else. Everything downstream can be retried; an
        # unobserved minute cannot.
        buffer.append(event)
        c = event.classification
        log.info(
            "%s | tier %d | %-20s | %-14s | %.2f",
            event.ts.strftime("%H:%M:%S"),
            int(event.tier),
            event.process or "-",
            c.category,
            c.confidence,
        )

    agent = Agent(config, StubClassifier(), sink=buffering_sink)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, agent.stop)
        except NotImplementedError:
            # Windows does not support add_signal_handler for these; KeyboardInterrupt
            # propagates instead, which asyncio.run handles.
            pass

    syncer = asyncio.create_task(
        _sync_forever(sync, config.sync_interval_s, agent.stopping)
    )
    try:
        await agent.run()
    finally:
        syncer.cancel()
        # One last drain so a clean shutdown does not strand a partial batch until the
        # next launch.
        try:
            await sync.flush_all()
        except Exception:
            log.debug("final flush failed; events remain buffered for next start")
        await sync.aclose()
        log.info("%d events buffered at exit", buffer.count())
        buffer.close()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
