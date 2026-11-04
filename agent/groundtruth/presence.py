"""Is anybody there, and are they doing anything?

Tier 1 used to answer "what is focused?" and stop. A focused editor became `deep work` at
0.9 confidence whether somebody was typing in it or had left for lunch, because the input
signal was measured on every sample and then not consulted when the label was written.
Five minutes of invented work per idle stretch, asserted confidently, which is the exact
failure this project exists to correct.

The fix is not a shorter idle timeout. `config.idle_threshold_s` is five minutes for a
good reason: input idleness alone conflates "away from the machine" with "reading", and a
tracker that files twenty minutes of reading documentation as idle is wrong in a way the
user notices immediately.

So this separates the two questions that were being answered as one:

- **Is the machine attended?** Input answers that, and nothing else does.
- **Is anything happening on screen?** Churn answers that, locally and for free.

Neither alone is enough. Reading has no input and little churn. A video playing has churn
and no input. Typing has both. What the pair rules out, and what no single signal can, is
the case the user actually complained about: an editor focused, nothing typed, nothing
moving, for minutes.

## Confidence has to follow the evidence

Where the pair cannot settle it, the honest output is a weaker claim rather than a
different one. A Tier 1 label with no recent input is still probably right; it is just not
0.9-right. So confidence decays toward `FLOOR` as time since input grows, which does real
work downstream: below `Confidence.ACCEPT` the escalation policy's low-confidence rule
fires and Tier 2 looks at the screen, and session confidence is duration-weighted so a
long unattended stretch pulls the session down instead of riding on one good sample.

That is the difference between a tracker you believe and one you stop checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .perceive.churn import STATIC

FRESH_S = 30.0
"""Input this recently means the person is here. No decay applies.

Generous on purpose. Reading a paragraph, thinking about a line of code, or listening to
somebody on a call all produce half-minute input gaps inside genuine work, and a window
that punished them would make the confidence number noise.
"""

STALE_S = 150.0
"""No input for this long, with a screen that is not moving, means nobody is working.

Below `config.idle_threshold_s` (300s) on purpose: this is the weaker, earlier claim that
*this activity* is not happening, not the stronger claim that the person has left. Idle
still owns the second one.
"""

FLOOR = 0.45
"""Where decayed Tier 1 confidence bottoms out.

Deliberately at `Confidence.RETRY`, not lower. A focused window is real evidence of
something even with nobody at the keyboard, so the claim weakens rather than inverts.
Landing exactly on the retry boundary is what hands the sample to Tier 2 to settle.
"""


class Attention(str, Enum):
    """What the input and churn pair supports saying."""

    ACTIVE = "active"
    """Input recently. Whatever the window says it is, it is happening."""

    PASSIVE = "passive"
    """No recent input, but the screen is changing. Watching, or a build running, or a
    page being scrolled by something other than the person. The activity is real; who is
    driving it is not established."""

    UNATTENDED = "unattended"
    """No input and nothing moving. An application is open and nothing is being done with
    it. This is the state Tier 1 used to record as confident work."""

    UNKNOWN = "unknown"
    """No churn measurement available, so the pair cannot be formed. Only input is known,
    which cannot separate reading from absence, so no strong claim is made either way."""


@dataclass(frozen=True, slots=True)
class Presence:
    attention: Attention
    confidence: float
    """What a Tier 1 label may claim, given the evidence."""

    @property
    def credits_activity(self) -> bool:
        """Whether the focused window's own label should be recorded at all."""
        return self.attention is not Attention.UNATTENDED


def assess(
    idle_s: float,
    churn: float | None,
    *,
    base_confidence: float = 0.9,
    fresh_s: float = FRESH_S,
    stale_s: float = STALE_S,
) -> Presence:
    """Read the input and churn pair into what Tier 1 is entitled to say.

    `churn` is None when no frame was captured this sample, which is the normal case with
    Tier 2 disabled. That is treated as missing evidence rather than as absence of motion:
    without it, `UNATTENDED` is never claimed, because input alone cannot tell reading
    from an empty chair. Confidence still decays, since the absence of input is a fact
    whether or not a frame was taken.
    """
    if idle_s <= fresh_s:
        return Presence(Attention.ACTIVE, base_confidence)

    if churn is None:
        return Presence(Attention.UNKNOWN, _decay(idle_s, base_confidence, fresh_s, stale_s))

    if churn > STATIC:
        # Something is happening even though nobody touched the keyboard. A video, a
        # build, a feed advancing on its own. Not full confidence, because what is moving
        # and who is watching it are different questions.
        return Presence(Attention.PASSIVE, _decay(idle_s, base_confidence, fresh_s, stale_s))

    if idle_s >= stale_s:
        return Presence(Attention.UNATTENDED, 0.0)

    return Presence(Attention.UNKNOWN, _decay(idle_s, base_confidence, fresh_s, stale_s))


def _decay(idle_s: float, base: float, fresh_s: float, stale_s: float) -> float:
    """Straight line from `base` at `fresh_s` down to `FLOOR` at `stale_s`.

    Linear rather than exponential because the number is read by people as well as by
    thresholds, and "it halves every minute" is harder to reason about than "it slides to
    the retry band over two and a half minutes". Nothing in the data argues for a curve.
    """
    if stale_s <= fresh_s:
        return FLOOR
    span = (idle_s - fresh_s) / (stale_s - fresh_s)
    return max(FLOOR, base - (base - FLOOR) * min(1.0, span))


def evidence(presence: Presence, idle_s: float) -> str:
    """One phrase, stating what was observed rather than what it means.

    Same rule as `perceive/motion.evidence`: the user reading this should be able to
    disagree with the conclusion while accepting the observation.
    """
    seconds = int(idle_s)
    if presence.attention is Attention.ACTIVE:
        return "input within the last few seconds"
    if presence.attention is Attention.PASSIVE:
        return f"no input for {seconds}s, but screen content is still changing"
    if presence.attention is Attention.UNATTENDED:
        return f"no input for {seconds}s and the screen is static"
    return f"no input for {seconds}s; screen motion not measured"
