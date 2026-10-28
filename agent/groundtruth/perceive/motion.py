"""What the screen is doing, and whether the person is doing it.

Churn alone was not enough, and a fixture day caught it: a long-running build scrolls its
output continuously with nobody touching the keyboard, which produces exactly the mask
instability that defines a social feed. Churn measures *that the screen changed*. It
cannot say *who changed it*.

Input rate answers that, and it is nearly free — `GetLastInputInfo` is already sampled for
idle detection. Pairing the two gives four quadrants rather than one axis:

                     │ little input          │ steady input
    ─────────────────┼───────────────────────┼──────────────────────────
    churning screen  │ PLAYING               │ SCROLLING
                     │ build log, video,     │ feed consumption, the
                     │ slideshow, animation  │ user pulling content past
    ─────────────────┼───────────────────────┼──────────────────────────
    static screen    │ READING               │ WORKING
                     │ held content, or away │ editing, typing, composing
                     │ (idle decides which)  │

The diagonal is the useful part. PLAYING and SCROLLING look identical to churn and are
completely different activities; READING and WORKING look identical to input rate and are
also different. Neither signal alone separates all four.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .churn import ACTIVE, STATIC


class Motion(str, Enum):
    """A coarse read of the screen-and-user pair.

    Deliberately coarse. This is a *prior* handed to the classifier, not a label shown to
    anyone — "SCROLLING" is evidence that an activity is feed-like, not a finding that it
    is doomscrolling. A terminal can scroll and still be work.
    """

    SCROLLING = "scrolling"
    PLAYING = "playing"
    READING = "reading"
    WORKING = "working"
    UNCLEAR = "unclear"
    """The middle band, or too few samples. Escalates rather than being guessed at."""

    @property
    def is_user_driven(self) -> bool:
        return self in (Motion.SCROLLING, Motion.WORKING)


@dataclass(frozen=True, slots=True)
class Sample:
    """One moment: how much the attended region changed, and whether input arrived."""

    churn: float
    input_active: bool


MIN_SAMPLES = 3
"""Below this, the answer is UNCLEAR.

A single reading means nothing: a tab switch, an advancing video frame or a notification
sliding in all spike churn exactly once. Activities are sustained; artefacts are not.
"""

INPUT_SHARE = 0.5
"""Fraction of recent samples carrying input before motion counts as user-driven.

Half rather than any-input, because an accidental mouse nudge during a video should not
reclassify watching as scrolling.
"""


def classify(samples: list[Sample], *, min_samples: int = MIN_SAMPLES) -> Motion:
    """Read a short run of samples into a motion quadrant.

    Returns UNCLEAR rather than guessing whenever the evidence is thin or the churn sits
    between the bands. That honesty is load-bearing: an UNCLEAR prior tells the
    classifier to look harder, while a wrong confident prior would steer it into exactly
    the mislabelling this project exists to prevent.
    """
    if len(samples) < min_samples:
        return Motion.UNCLEAR

    recent = samples[-min_samples:]
    driven = sum(1 for s in recent if s.input_active) > len(recent) * INPUT_SHARE

    if all(s.churn >= ACTIVE for s in recent):
        return Motion.SCROLLING if driven else Motion.PLAYING

    if all(s.churn <= STATIC for s in recent):
        return Motion.WORKING if driven else Motion.READING

    # Mixed or middling churn. Steady input with an unsettled screen is still recognisably
    # work — switching panes, scrubbing a timeline, moving between files.
    if driven and any(s.churn > STATIC for s in recent):
        return Motion.WORKING

    return Motion.UNCLEAR


def evidence(motion: Motion) -> str:
    """One phrase for the classifier's prompt and the user-facing evidence string.

    Phrased as an observation, never as a conclusion. The model is told what was seen so
    it can weigh it; it is not told what to decide.
    """
    return {
        Motion.SCROLLING: (
            "content replaced continuously while the user supplied input: "
            "the user is pulling content past"
        ),
        Motion.PLAYING: (
            "content replaced continuously with almost no input: "
            "the screen is advancing on its own"
        ),
        Motion.READING: (
            "content held still with almost no input: "
            "the same material stayed on screen"
        ),
        Motion.WORKING: (
            "content largely held with steady input: "
            "the user is acting on what is in front of them"
        ),
        Motion.UNCLEAR: "no consistent pattern of change across recent samples",
    }[motion]
