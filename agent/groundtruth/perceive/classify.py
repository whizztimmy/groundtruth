"""Tier 2 classification via Muse Glimmer.

Glimmer runs locally — that is the whole privacy argument — and is asked for a label it
can be held to: a category, a confidence, and the evidence it used. Evidence is not
decoration. It is what the user reads when confirming an uncertain span, and what makes a
wrong label debuggable instead of mysterious.

The retry path leans on a documented Glimmer behaviour: it is trained to diagnose and
retry on failure rather than halt, and exposes controllable reasoning effort. A
low-confidence answer is therefore re-asked at higher effort before being accepted, which
costs GPU-seconds only on the samples that actually need them.
"""

from __future__ import annotations

from ..types import UNCERTAIN, Classification, Observation

SYSTEM_PROMPT = """\
You observe a computer screen and report what the person is doing. You do not judge, \
score, or advise.

Report the ACTIVITY, not the application. "Chrome" is never an answer; "reading API \
documentation" and "scrolling a social feed" are.

Pay attention to region churn. High churn in a content region means the view is being \
replaced continuously — scrolling. Low churn means the same content is being held — \
reading.

Return the category, a confidence in [0,1], and one sentence of evidence citing what you \
actually saw. If the evidence does not support a confident answer, return the category \
"uncertain" rather than guessing."""


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

The user owns their categories; these exist so the first run produces something useful
before any personalisation. User corrections become few-shot examples per process, so the
taxonomy converges on the individual rather than on an average.
"""


class Classifier:
    """Wraps a Glimmer endpoint, local or partner-served.

    The backend is a config change, not a code path: local llama.cpp/vLLM and partner
    inference expose the same interface, so the privacy posture is a deployment decision
    the architecture already supports.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model: str = "muse-glimmer-30b",
        categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._categories = categories

    async def classify(
        self,
        obs: Observation,
        frame: object,
        *,
        effort: str = "low",
    ) -> Classification:
        """Classify one observation.

        `frame` is passed to the perception encoder and never retained or written to disk.
        Starts at low reasoning effort; `classify_verified` handles escalation.
        """
        raise NotImplementedError("Glimmer inference — see docs/tier2.md")

    async def classify_verified(
        self,
        obs: Observation,
        frame: object,
        *,
        max_retries: int = 1,
    ) -> Classification:
        """Classify, retrying at higher effort when the first answer is not acceptable.

        Returns an `UNCERTAIN` classification rather than a low-confidence guess when the
        retries are exhausted. Storing uncertainty honestly is what lets the UI ask the
        user a question instead of quietly showing them a wrong number.
        """
        result = await self.classify(obs, frame, effort="low")
        if result.is_acceptable:
            return result

        efforts = ("medium", "high")
        for attempt in range(min(max_retries, len(efforts))):
            if not result.should_retry:
                break
            result = await self.classify(obs, frame, effort=efforts[attempt])
            if result.is_acceptable:
                return result

        if result.is_acceptable:
            return result

        return Classification(
            label=UNCERTAIN,
            category=UNCERTAIN,
            confidence=result.confidence,
            evidence=result.evidence or "insufficient evidence after retry",
            reasoning_effort=result.reasoning_effort,
        )
