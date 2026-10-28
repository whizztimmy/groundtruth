"""Tier 2 classification via Muse Glimmer.

Glimmer runs on a backend the operator controls — that is the whole privacy argument —
and is asked for a label it can be held to: a category, a confidence, and the evidence it
used. Evidence is not decoration. It is what the user reads when confirming an uncertain
span, and what makes a wrong label debuggable instead of mysterious.

The model is not shown a bare screenshot. It is shown a frame *plus* the measurements the
agent already has — region churn, how long each region held, whether input was arriving —
because those answer questions a still frame cannot. A screenshot of a feed and a
screenshot of an article look alike; one of them is being pulled past.

The retry path leans on a documented Glimmer behaviour: it diagnoses and retries on
failure rather than halting, and exposes controllable reasoning effort. A low-confidence
answer is re-asked at higher effort before being accepted, so GPU-seconds are spent only
on the samples that need them.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..backends import Backend, BackendError, vision
from ..types import UNCERTAIN, Classification, Observation, Region
from .motion import Motion, Sample, classify as classify_motion, evidence as motion_evidence

log = logging.getLogger("groundtruth.classify")

SYSTEM_PROMPT = """\
You observe a computer screen and report what the person is doing. You do not judge, \
score, or advise.

Report the ACTIVITY, not the application. "Chrome" is never an answer; "reading API \
documentation" and "scrolling a social feed" are.

You are given measurements taken across several seconds that a single screenshot cannot \
show: how much each region's content was replaced between frames, and whether the person \
was supplying input. Use them. Content replaced continuously while the person types or \
scrolls means they are pulling content past. Content replaced with no input means the \
screen is advancing on its own, which is a video or a build log, not browsing.

Respond with JSON only, no prose and no code fence:
{"category": "...", "label": "...", "confidence": 0.0, "evidence": "..."}

- category: one of the categories listed, or "uncertain"
- label: a short specific phrase, e.g. "reviewing a pull request"
- confidence: 0.0 to 1.0, your actual belief, not a formality
- evidence: one sentence citing what you actually saw

If the evidence does not support a confident answer, return "uncertain" rather than \
guessing. An honest "uncertain" is more useful than a plausible label, because the \
person will be asked and their answer teaches the system. A wrong confident label is \
never corrected."""


DEFAULT_CATEGORIES: tuple[str, ...] = (
    "deep work",
    "shallow work",
    "research",
    "communication",
    "doomscrolling",
    "entertainment",
    "devotion",
    "learning",
    "admin",
    "idle",
)
"""Seed taxonomy, not a fixed one.

The user owns their categories; these exist so a first run produces something useful
before any personalisation. User corrections become few-shot examples per process, so the
taxonomy converges on the individual rather than on an average.
"""

EFFORT_LADDER = ("low", "medium", "high")
"""Reasoning effort to escalate through on retry.

Starts low because most samples are easy — a full-screen editor with no feed in sight
needs no deliberation — and the cost of always reasoning hard on an always-on loop is the
reason people turn trackers off.
"""


def build_prompt(
    obs: Observation,
    *,
    categories: tuple[str, ...],
    input_history: list[bool] | None = None,
    examples: list[tuple[str, str]] | None = None,
) -> str:
    """Assemble the observation the model reasons over.

    Measurements are stated as observations, never as conclusions. The motion prior says
    "content replaced continuously while the user supplied input", not "this is
    doomscrolling" — the model must weigh what was seen rather than ratify a heuristic.
    That distinction is what keeps the model able to overrule the heuristic when a
    terminal scrolls during real work.
    """
    lines = [f"Categories: {', '.join(categories)}, uncertain", ""]

    if obs.process:
        lines.append(f"Foreground application: {obs.process}")
    if obs.window_title:
        lines.append(f"Window title: {obs.window_title}")

    if obs.regions:
        lines.append("")
        lines.append("Tracked regions, measured across recent frames:")
        for region in sorted(obs.regions, key=lambda r: -r.area_frac):
            lines.append(f"  - {_describe_region(region)}")

    motion = _motion_for(obs.regions, input_history)
    if motion is not Motion.UNCLEAR:
        lines.append("")
        lines.append(f"Across recent samples: {motion_evidence(motion)}.")

    if examples:
        lines.append("")
        lines.append("This person previously corrected similar observations:")
        for label, category in examples[-3:]:
            lines.append(f"  - {label} -> {category}")

    lines.append("")
    lines.append("What is this person doing?")
    return "\n".join(lines)


def _describe_region(region: Region) -> str:
    from .churn import describe

    share = f"{region.area_frac * 100:.0f}% of screen"
    focus = ", holds focus" if region.has_focus else ""
    return f"{region.label}: {share}, content {describe(region.churn)}{focus}"


def _motion_for(
    regions: tuple[Region, ...], input_history: list[bool] | None
) -> Motion:
    """Motion for the region most likely holding attention.

    Computed over the attended region rather than the screen as a whole: a video playing
    in a corner while someone writes should not make the whole sample look like playback.
    """
    if not regions or not input_history:
        return Motion.UNCLEAR

    from .regions import attention_locus

    locus = attention_locus(regions)
    if locus is None:
        return Motion.UNCLEAR

    # The attended region's churn paired with each recent input flag. Churn is a single
    # reading per sample, so the run is reconstructed from the input history's length.
    recent = input_history[-3:]
    return classify_motion([Sample(locus.churn, active) for active in recent])


def parse_response(raw: str, *, effort: str) -> Classification:
    """Model output to a Classification.

    A response that cannot be parsed becomes `uncertain` rather than raising. A classifier
    that crashes the capture loop over a malformed reply would cost the user their day;
    an uncertain span costs them one question.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text

    try:
        body: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        log.debug("unparseable classification: %s", text[:200])
        return Classification(
            label=UNCERTAIN,
            category=UNCERTAIN,
            confidence=0.0,
            evidence="Classifier response could not be parsed.",
            reasoning_effort=effort,
        )

    category = str(body.get("category") or UNCERTAIN).strip().lower()
    label = str(body.get("label") or category).strip()
    evidence = str(body.get("evidence") or "").strip() or "No evidence given."

    try:
        confidence = float(body.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    # A model that returns 1.0 for everything is not more certain, it is miscalibrated;
    # clamping keeps one bad response from dominating a session's weighted confidence.
    confidence = min(1.0, max(0.0, confidence))

    if category == UNCERTAIN:
        # Honour the refusal. Overriding it with the label's confidence would turn an
        # honest abstention into a guess.
        return Classification(
            label=UNCERTAIN,
            category=UNCERTAIN,
            confidence=confidence,
            evidence=evidence,
            reasoning_effort=effort,
        )

    return Classification(
        label=label,
        category=category,
        confidence=confidence,
        evidence=evidence,
        reasoning_effort=effort,
    )


class Classifier:
    """Wraps a Glimmer endpoint, local or partner-served.

    The backend is a config change, not a code path: local llama.cpp/vLLM and partner
    inference expose the same interface, so the privacy posture is a deployment decision
    the architecture already supports.
    """

    def __init__(
        self,
        backend: Backend,
        *,
        categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    ) -> None:
        if not backend.placement.may_see_pixels:
            raise BackendError(
                f"classifier placement is {backend.placement.value}; classification sees "
                "screen content and requires 'local' or 'self_hosted'"
            )
        self._backend = backend
        self._categories = categories
        self._examples: dict[str, list[tuple[str, str]]] = {}

    def learn(self, process: str, label: str, category: str) -> None:
        """Record a user correction as a few-shot example for that application.

        Per-process rather than global: "uncertain in a terminal" and "uncertain in a
        browser" are different problems, and mixing their corrections would blur both.
        """
        key = (process or "").lower()
        self._examples.setdefault(key, []).append((label, category))

    async def classify(
        self,
        obs: Observation,
        frame: Any,
        *,
        effort: str = "low",
        input_history: list[bool] | None = None,
    ) -> Classification:
        """Classify one observation.

        `frame` goes to the perception encoder and is never retained or written to disk.
        `vision` refuses outright if this backend is not allowed to see pixels.
        """
        prompt = build_prompt(
            obs,
            categories=self._categories,
            input_history=input_history,
            examples=self._examples.get((obs.process or "").lower()),
        )
        raw = await vision(
            self._backend,
            system=SYSTEM_PROMPT,
            user_text=prompt,
            frame=frame,
            reasoning_effort=effort,
            max_tokens=400,
        )
        return parse_response(raw, effort=effort)

    async def classify_verified(
        self,
        obs: Observation,
        frame: Any,
        *,
        max_retries: int = 1,
        input_history: list[bool] | None = None,
    ) -> Classification:
        """Classify, retrying at higher effort when the first answer is not acceptable.

        Returns `uncertain` rather than a low-confidence guess once retries are spent.
        Storing uncertainty honestly is what lets the UI ask a question instead of
        quietly showing a wrong number.
        """
        result = await self._attempt(obs, frame, EFFORT_LADDER[0], input_history)
        if result.is_acceptable:
            return result

        for effort in EFFORT_LADDER[1 : 1 + max_retries]:
            if not result.should_retry:
                # Either confidently wrong-shaped or far below threshold. More compute
                # will not rescue an answer the model has already abandoned.
                break
            log.debug("retrying %s at effort=%s", obs.process, effort)
            result = await self._attempt(obs, frame, effort, input_history)
            if result.is_acceptable:
                return result

        if result.is_acceptable:
            return result

        return Classification(
            label=UNCERTAIN,
            category=UNCERTAIN,
            confidence=result.confidence,
            evidence=result.evidence or "Insufficient evidence after retry.",
            reasoning_effort=result.reasoning_effort,
        )

    async def _attempt(
        self,
        obs: Observation,
        frame: Any,
        effort: str,
        input_history: list[bool] | None,
    ) -> Classification:
        """One call, with transport failure folded into an uncertain result.

        A backend hiccup must not kill the capture loop. It costs one uncertain span,
        which the user can answer, rather than a gap they cannot.
        """
        try:
            return await self.classify(
                obs, frame, effort=effort, input_history=input_history
            )
        except BackendError as exc:
            log.warning("classification failed (%s)", exc)
            return Classification(
                label=UNCERTAIN,
                category=UNCERTAIN,
                confidence=0.0,
                evidence="Classifier unreachable for this sample.",
                reasoning_effort=effort,
            )
