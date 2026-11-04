"""The agent loop.

Ties the tiers together: sample cheaply, escalate only when the cheap signal is
ambiguous, classify, emit an event. Everything expensive is behind a decision made in
`escalate.should_escalate`, and everything that sees pixels is behind the placement guard
in `backends.vision`.

The classifier is injected rather than constructed here, so the loop runs end to end with
a stub before any model is reachable. That is not a testing convenience - it means the
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
from typing import Any, Protocol

from . import known
from .buffer import EventBuffer
from .capture import window
from .capture.screen import TARGET_WIDTH, FrameGrabber
from .capture.window import CaptureUnavailable
from .backends import Backend, BackendError
from .perceive.classify import Classifier
from .perceive.regions import RegionTracker
from .config import Config, load_env_file
from .escalate import EscalationState, should_escalate
from .sync import SyncClient
from . import presence
from .activity import ActivityMeter, disagreement
from .patterns import FOCUS_CATEGORIES
from .perceive import framediff
from .types import Classification, Event, Observation, Tier

log = logging.getLogger("groundtruth")

_INPUT_HISTORY = 16
"""Samples of input history to retain - roughly half a minute at the default cadence.

Long enough for `motion.classify` to see a run, short enough that a burst of typing ten
minutes ago does not colour what is on screen now.
"""

IDLE = Classification(
    label="idle",
    category="idle",
    confidence=1.0,
    evidence="No input for longer than the idle threshold.",
)
"""Idle is asserted, not inferred - the OS tells us directly, so confidence is 1.0.

Treating it as a real classification rather than a gap keeps the segmenter simple: it
closes a session on category change, and idle is just another category.
"""


def _unattended(obs: Observation, idle_s: float) -> Classification:
    """An application is focused and nothing is being done with it.

    Distinct from idle, which claims the person is away. This makes the narrower claim
    that *this activity* is not happening: nothing typed, nothing moving on screen, while
    a window still holds focus. The two can be told apart and conflating them is what a
    five-minute idle timeout does.

    Confidence 0.9 rather than idle's 1.0. Both halves are measurements, so the claim is
    strong, but it is read off two signals rather than asserted by the OS.

    The process is kept in the evidence because "which app was sitting open doing
    nothing" is a question people actually want answered.
    """
    return Classification(
        label="open, not in use",
        category="unattended",
        confidence=0.9,
        evidence=(
            f"{obs.process or 'a window'} in foreground; no input for {int(idle_s)}s "
            "and the screen is static."
        ),
    )


class SupportsClassify(Protocol):
    """What the loop needs from a classifier. Deliberately narrow.

    Narrow because the loop should not care whether the model is local, self-hosted, a
    stub, or a replay of a recorded day - all four are useful, and all four satisfy this.
    """

    async def classify_verified(
        self,
        obs: Observation,
        frame: object,
        *,
        input_history: list[bool] | None = None,
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
        self,
        obs: Observation,
        frame: object,
        *,
        input_history: list[bool] | None = None,
    ) -> Classification:
        category, evidence = self.MAPPING.get(
            (obs.process or "").lower(),
            ("uncertain", "No model available; process not in the stub mapping."),
        )
        return Classification(
            label=category,
            category=category,
            confidence=0.5,
            evidence=f"{evidence} (stub classifier - not a real observation)",
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
        tracker: Any | None = None,
    ) -> None:
        self._config = config
        self._classifier = classifier
        self._sink = sink
        self._grab_frame = grab_frame
        self._tracker = tracker
        self._state = EscalationState()
        self._stopping = asyncio.Event()
        self._input_history: list[bool] = []
        self._activity = ActivityMeter()
        """The industry activity percentage, computed alongside our own label.

        Not used to decide anything. It is carried on every event so the two numbers can
        be compared on real data instead of in a pitch deck. See activity.py.
        """
        self._last_digest: framediff.Digest | None = None
        """Previous frame reduced to grid-cell luminances, for whole-screen churn.

        The one thing a sample leaves behind. 2,304 bytes, from which no screen can be
        reconstructed, and the reason churn is measurable without keeping a second frame.
        """
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

        Exposed so sibling tasks - the syncer, chiefly - can wind down on the same
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
        self._activity.record(had_input)

        obs = replace(window.foreground(), input_active=had_input)

        # Grabbed once per tick rather than only when escalating, because tier 1 needs
        # the motion signal too: a focused editor with a static screen and no input is
        # the case this whole gate exists for, and tier 1 is what labels it. Costs a
        # screenshot and a 2,304-cell diff, no inference and no network. Only the digest
        # outlives the sample, never the frame. See framediff.py.
        frame = self._grab_frame() if self._grab_frame else None
        churn: float | None = None
        if frame is not None:
            current = framediff.digest(frame)
            if self._last_digest is not None:
                churn = framediff.churn_of(self._last_digest, current)
                obs = replace(obs, screen_churn=churn)
                log.debug("screen churn %.3f", churn)
            self._last_digest = current

        attention = presence.assess(idle_s, churn)

        decision = should_escalate(obs, self._state)
        log.debug("tier1 %s -> %s (%s)", obs.process, decision.escalate, decision.reason)

        if not decision.escalate:
            if not attention.credits_activity:
                # An application is focused and nothing is being done with it. Recording
                # the window name here is what used to manufacture five minutes of
                # confident "deep work" out of an editor somebody walked away from.
                del frame
                await self._emit(obs, _unattended(obs, idle_s), Tier.WINDOW)
                return

            # Tier 1 resolved it. Emitting here is what makes the cheap tier actually
            # cover a day rather than leave gaps wherever the model was not consulted.
            resolved = known.lookup(obs.process)
            if resolved is None:
                carried = self._last.get((obs.process or "").lower())
                if carried is not None:
                    await self._emit(
                        obs, _carry(carried, attention.confidence), Tier.WINDOW
                    )
                    return
                # No Tier 1 label and nothing to carry: the very first samples of an
                # unrecognised process while Tier 2 is rate limited. A gap is the honest
                # answer - inventing a label here would be the exact failure the product
                # exists to correct.
                log.debug("no label yet for %s; awaiting first classification", obs.process)
                return
            category, label = resolved
            await self._emit(
                obs,
                Classification(
                    label=label,
                    category=category,
                    confidence=attention.confidence,
                    evidence=(
                        f"{obs.process} in foreground; its name describes the activity. "
                        f"{presence.evidence(attention, idle_s)}."
                    ),
                ),
                Tier.WINDOW,
            )
            return

        if frame is not None and self._tracker is not None:
            try:
                obs = replace(obs, regions=await self._tracker.track(frame))
            except Exception:
                # Segmentation is an enrichment, not a prerequisite. Losing it costs the
                # classifier its churn evidence, which is worse than having it and far
                # better than dropping the sample.
                log.warning("region tracking failed; classifying without regions")

        classification = await self._classifier.classify_verified(
            obs, frame, input_history=list(self._input_history)
        )
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
                classification=self._with_activity(classification),
                process=obs.process,
                window_title=obs.window_title,
            )
        )


    def _with_activity(self, classification: Classification) -> Classification:
        """Attach the input-activity score, and name it where it contradicts the screen.

        Every event carries it, at every tier, because the comparison is the product's
        argument and an argument that only appears in the cases that flatter it is not
        one. Most of the time the two agree and the line is just a number.

        Appended to evidence rather than added to the event: `docs/API.md` is frozen, and
        a new field would be a silent divergence from a contract three components were
        built against.
        """
        level = self._activity.level()
        note = f"Input activity {level.percent}% over the last {level.elapsed_minutes}m"
        if level.is_partial:
            note += " (partial segment)"
        clash = disagreement(
            level, classification.category, focus_categories=FOCUS_CATEGORIES
        )
        if clash:
            note += f". For comparison, {clash}"
        return replace(
            classification, evidence=f"{classification.evidence} [{note}.]"
        )

def _carry(previous: Classification, ceiling: float = 1.0) -> Classification:
    """Re-assert a previous label for a sample that could not be classified afresh.

    Confidence is not reduced by the passage of time. The label is as good as it was a
    few seconds ago, and decaying it on a clock would make the dashboard show attention
    quietly draining away during exactly the stretches where someone was most focused.

    It is capped by `ceiling`, which is a different thing: what the presence evidence
    supports right now. A carried label cannot be more confident than the fact that
    somebody is there, and without that cap this path quietly reinstated the bug the
    presence gate exists to fix. Most labels come through here rather than from the
    process table, so capping only the table would have fixed almost nothing.

    The evidence string says it was carried, so a user asking "why did it say that?"
    gets an honest answer rather than the impression of a fresh observation.
    """
    return Classification(
        label=previous.label,
        category=previous.category,
        confidence=min(previous.confidence, ceiling),
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
    # Before anything reads os.environ. See config.load_env_file for why this is
    # here rather than in a wrapper script.
    load_env_file()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = Config.from_env()

    buffer = EventBuffer(config.buffer_path)
    buffer.prune(config.buffer_max_rows)

    # Only constructed when a backend is actually allowed to see pixels. Without one,
    # the agent runs Tier 1 only rather than grabbing frames nothing will look at -
    # capturing a screen for no reason is the thing this project exists not to do.
    grabber: FrameGrabber | None = None
    if config.tier2_enabled:
        try:
            grabber = FrameGrabber()
            log.info("tier 2 enabled; frames reduced to %dpx wide", TARGET_WIDTH)
        except CaptureUnavailable as exc:
            log.warning("tier 2 unavailable (%s); running tier 1 only", exc)
    else:
        log.info("tier 2 disabled; no frames will be captured")
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

    classifier: SupportsClassify = StubClassifier()
    tracker = None
    if grabber is not None:
        try:
            classifier = Classifier(Backend.from_env("GLIMMER"))
            log.info("classifier ready: %s", getattr(classifier, "_backend").model)
        except BackendError as exc:
            # Falling back to the stub keeps the loop observable while inference is
            # being provisioned. It is logged loudly because the stub reproduces the
            # app-name heuristic this project exists to replace - useful for a demo of
            # the problem, never to be mistaken for the product working.
            log.warning("classifier unavailable (%s); using the stub", exc)

        # Constructed separately, because segmentation is an enrichment everywhere else
        # in this code and the startup path should not be the one place that treats it
        # as a prerequisite. Coupling them meant an unset SAM endpoint silently
        # discarded a working classifier and reverted the agent to app-name labelling.
        try:
            tracker = RegionTracker(Backend.from_env("SAM"))
            log.info("region tracking ready")
        except BackendError as exc:
            log.warning(
                "region tracking unavailable (%s); falling back to whole-screen churn, "
                "which cannot attribute movement to a region",
                exc,
            )

    agent = Agent(
        config,
        classifier,
        sink=buffering_sink,
        grab_frame=grabber.grab_active if grabber else None,
        tracker=tracker,
    )

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
        if grabber is not None:
            grabber.close()
        log.info("%d events buffered at exit", buffer.count())
        buffer.close()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
