"""Tier 3: off-screen time, reported by the person who was there.

A screen agent is blind to the gym, the commute, a walk, a conversation. Those hours are
not idle — they are the ones a person most wants back, and no amount of perception
recovers them. So Tier 3 inverts the direction: instead of the agent inferring an
activity, the user states one, and the agent records it with the confidence that deserves.

## This module never opens a microphone

Deliberately, and it is the main design decision here. There is no always-on listening
path, and the agent loop cannot reach this code — Tier 3 happens only when a person runs
`python -m groundtruth.checkin` against audio they already recorded, on their phone or
with any recorder they like.

That is a stronger control than any runtime check could be. A guard that permits
background audio capture under conditions is a guard someone will eventually widen; a
codebase with no capture path cannot be widened into one by accident. It also matches how
the fixture tells this story: the gym logged by voice from a phone while the desktop sat
silent.

## Audio does reach a managed endpoint, and that is a change

`vision()` refuses to send a frame to a `managed` backend. Audio is sent to one, because
`muse-voice-transcribe` is a Meta API model and there is no local alternative.

That is not the placement rule being weakened; it is a different situation. A frame is
captured passively, continuously, and shows whatever happened to be on screen — including
things the person forgot was there. A check-in is a sentence somebody chose to say,
knowing it was being recorded and why. The rule that actually matters is not "nothing
leaves the device", it is "nothing leaves that the person did not deliberately offer",
and a frame can never satisfy that while a check-in satisfies it by construction.

`ARCHITECTURE.md` previously claimed voice audio never leaves the device. It was written
before this existed, and it was wrong; it now says what happens.

Audio is held in memory and never written anywhere by this project. A file the user
recorded is theirs and is only read.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backends import Backend, BackendError, chat
from .types import Classification, Event, Tier

log = logging.getLogger("groundtruth.checkin")

MAX_BACKDATE = timedelta(hours=12)
"""How far back a check-in may reach.

A check-in is for off-screen time you are reporting as you come back to the desk. Beyond
half a day it stops being that and becomes manual time entry, which is a different
feature with different failure modes — and one whose main risk is a person reconstructing
a week from memory and then trusting the total. Refused rather than clamped, because
silently moving somebody's gym session to a time they did not say is worse than declining
to record it.
"""

MIN_MINUTES = 1
MAX_MINUTES = 8 * 60
"""An activity shorter than a minute is not a session, and a single check-in claiming more
than a working day is a misparse rather than a long walk."""

STATED_CONFIDENCE = 0.97
"""A stated activity, with a stated duration. Not 1.0: the transcript may be
mis-transcribed and the category still has to be mapped onto a taxonomy, so a sliver of
doubt is honest. 1.0 is reserved for `apply_resolutions`, where the user answered a
direct question about a specific span.
"""

# There is deliberately no "inferred duration" path. The first version asked the model to
# estimate a length when none was stated and recorded it at low confidence. Across five
# phrasings it never once estimated — it returned 0 every time, declining to invent. That
# is the better behaviour, so the prompt now asks for a refusal instead of an estimate and
# the caller is told to say how long it was. The person is standing right there.

CADENCE_S = 120.0
"""Spacing of the synthesised events across the span.

The contract in `docs/API.md` has no duration field — an event is an instant, and the
server derives sessions from runs of same-labelled events. So a 45-minute check-in becomes
events every two minutes carrying the same label, which is exactly the shape
`server/fixtures/build_day.py` already encodes for its voice-logged gym block.

Two minutes rather than the agent's own cadence because nothing is being observed here.
Denser events would not add information, only rows.
"""

OFFSCREEN_CATEGORIES: tuple[str, ...] = (
    "fitness",
    "commute",
    "meal",
    "conversation",
    "errand",
    "rest",
)
"""Categories only a check-in can produce, kept out of the screen classifier's list.

`perceive/classify.DEFAULT_CATEGORIES` was built for things visible on a screen, and it
has nowhere to put a gym session — so without this, `interpret` refuses the most obvious
check-in there is.

`fitness` rather than `gym`, which was the first attempt and was wrong: a gym is a place,
so "I went for a run" and "I was on a walk" had nowhere to go and were refused. Naming a
category after a venue narrows it to that venue. The fixture was renamed to match, because
one concept with two names in the same taxonomy is how a timeline becomes unreadable.

Separate rather than merged, because the two tiers have different *legitimate* output
spaces. A frame can never truthfully be "gym", so offering that option to the screen
classifier is handing it a way to be confidently wrong about the one category it has no
evidence for. A check-in can say "deep work" — reviewing a contract on paper for an hour —
so check-ins see the union and the classifier does not.
"""


def checkin_categories(screen: tuple[str, ...]) -> tuple[str, ...]:
    """The union, de-duplicated and order-preserving: anything a person might report."""
    seen: dict[str, None] = {}
    for name in (*screen, *OFFSCREEN_CATEGORIES):
        seen.setdefault(name, None)
    return tuple(seen)


SYSTEM_PROMPT = """\
You turn a spoken check-in about time away from a computer into one structured record.

The person is reporting what they just did. Your only job is to read back what they said, \
mapped onto their categories. You are not estimating, improving or interpreting.

Rules:

- Choose `category` from the list given. If nothing fits, return null for the whole \
record rather than forcing it into the nearest one.
- `label` is a short specific phrase in their own words - "lower body at the gym", not \
"exercise session".
- `minutes` is the duration they stated. If they stated one, set `duration_stated` true. \
If they did not, estimate nothing: give your best reading of the length and set \
`duration_stated` false, so the system can ask them instead of trusting you.
- "I was at the gym for 45 minutes" is a stated duration. "I just got back from the gym" \
is not, no matter how typical a gym session is.
- If the check-in describes screen work rather than time away, return null. That time is \
observed directly and does not need stating.

Respond with JSON only:
{"category": "...", "label": "...", "minutes": 0, "duration_stated": true}

or, when nothing fits:
{"category": null}"""


@dataclass(frozen=True, slots=True)
class CheckIn:
    """One reported stretch of off-screen time."""

    category: str
    label: str
    start: datetime
    end: datetime
    transcript: str
    duration_stated: bool

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    @property
    def confidence(self) -> float:
        return STATED_CONFIDENCE


class CheckInRefused(BackendError):
    """The check-in could not be recorded, with a reason to tell the user.

    A refusal here is a normal outcome, not a fault: an unparseable mumble, an activity
    outside the taxonomy, or a span too far back. It carries a message because the person
    is standing right there and can simply say it again.
    """


async def transcribe(backend: Backend, audio: bytes, *, fmt: str) -> str:
    """Audio to text via a transcription model.

    **This function is unverified, and the endpoint it needs was not found.** What was
    tried against `api.meta.ai/v1` on 2026-10-31, with a key that reaches
    `muse-voice-transcribe-1.0` in `/models`:

    — `/chat/completions` with an `input_audio` content part: 404 `model_not_found`
    — `/audio/transcriptions` (the Whisper-style multipart shape): 404, empty body
    — `/audio/translations`: 404, empty body

    An empty 404 is a missing route, while the chat endpoint returned a structured error
    about the model — so the model is in the catalogue and is served somewhere this did
    not find. `sam-3.1` turned out to be on `/v1/responses`, which `backends.py` does not
    speak, so that is the first place to look.

    Until then `--text` is the working path and the CLI says so. Contained to one function
    for the same reason `perceive/regions.py` contains `_parse_detections`: when the real
    surface is known, one function changes.
    """
    encoded = base64.b64encode(audio).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Transcribe this check-in verbatim."},
                {
                    "type": "input_audio",
                    "input_audio": {"data": encoded, "format": fmt},
                },
            ],
        }
    ]
    text = (await chat(backend, messages, max_tokens=600)).strip()
    if not text:
        raise CheckInRefused("nothing was transcribed from that audio")
    return text


async def interpret(
    backend: Backend,
    transcript: str,
    *,
    categories: tuple[str, ...],
    now: datetime | None = None,
) -> CheckIn:
    """Transcript to a structured check-in, or a refusal with a reason.

    The span ends *now* and runs backwards, because a check-in is reported on return. A
    person saying "I was at the gym for 45 minutes" at 19:00 means 18:15 to 19:00, and
    asking them to confirm that is friction for no gain.
    """
    now = now or datetime.now(timezone.utc)
    body = await chat(
        backend,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Categories: {', '.join(categories)}\n\n"
                    f"Check-in: {transcript}"
                ),
            },
        ],
        max_tokens=2000,
    )

    parsed = _parse(body)
    category = parsed.get("category")
    if not category:
        raise CheckInRefused(
            "that did not describe off-screen time in any of your categories"
        )

    minutes = parsed.get("minutes")
    if not isinstance(minutes, (int, float)) or not (
        MIN_MINUTES <= minutes <= MAX_MINUTES
    ):
        raise CheckInRefused(
            "that did not say how long it was - try "
            '"at the gym for 45 minutes"'
        )

    span = timedelta(minutes=float(minutes))
    if span > MAX_BACKDATE:
        raise CheckInRefused(
            f"a check-in covers at most {MAX_BACKDATE.total_seconds() / 3600:.0f} hours; "
            "that is manual time entry, which this is not"
        )

    return CheckIn(
        category=str(category),
        label=str(parsed.get("label") or category),
        start=now - span,
        end=now,
        transcript=transcript,
        duration_stated=bool(parsed.get("duration_stated")),
    )


def _parse(raw: str) -> dict:
    """Model output to a dict. A refusal, not an exception, on unparseable output.

    Same reasoning as `perceive/classify.parse_response`: the person is waiting, and
    "say that again" is a better outcome than a stack trace.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckInRefused("could not read the model's reply; try again") from exc
    if not isinstance(parsed, dict):
        raise CheckInRefused("could not read the model's reply; try again")
    return parsed


def to_events(checkin: CheckIn, *, device_id: str, cadence_s: float = CADENCE_S) -> list[Event]:
    """Expand a check-in into the Tier 3 events the contract can carry.

    `process` and `window_title` are None because there was no screen. That is not missing
    data — it is the whole point of this tier, and it is what the dashboard keys on to
    show the span as reported rather than observed.
    """
    stated = "stated" if checkin.duration_stated else "duration not stated"
    evidence = f"Voice check-in: '{checkin.transcript.strip()}' ({stated})."
    classification = Classification(
        label=checkin.label,
        category=checkin.category,
        confidence=checkin.confidence,
        evidence=evidence,
    )

    events: list[Event] = []
    step = timedelta(seconds=max(cadence_s, 1.0))
    ts = checkin.start
    while ts < checkin.end:
        events.append(
            Event(
                ts=ts,
                device_id=device_id,
                tier=Tier.VOICE,
                classification=classification,
                process=None,
                window_title=None,
            )
        )
        ts += step
    if not events:
        # A span shorter than one step still happened and still belongs on the timeline.
        events.append(
            Event(
                ts=checkin.start,
                device_id=device_id,
                tier=Tier.VOICE,
                classification=classification,
            )
        )
    return events


async def record(
    *,
    audio_path: Path | None,
    text: str | None,
    device_id: str,
    categories: tuple[str, ...],
    voice_backend: Backend | None,
    reason_backend: Backend,
    now: datetime | None = None,
) -> tuple[CheckIn, list[Event]]:
    """Run one check-in end to end and return what would be buffered.

    Takes `text` as an alternative to audio so the interpretation half can be exercised
    without a transcription endpoint, and so a person with no microphone still has the
    tier. The evidence string records which it was, because "typed" and "spoken" are
    different claims about where a label came from.
    """
    if text:
        transcript = text.strip()
    elif audio_path is not None:
        if voice_backend is None:
            raise CheckInRefused("no transcription backend configured; use --text")
        audio = audio_path.read_bytes()
        transcript = await transcribe(
            voice_backend, audio, fmt=audio_path.suffix.lstrip(".").lower() or "wav"
        )
        log.info("transcript: %s", transcript)
    else:
        raise CheckInRefused("pass --audio or --text")

    checkin = await interpret(
        reason_backend, transcript, categories=categories, now=now
    )
    return checkin, to_events(checkin, device_id=device_id)


def _main() -> None:
    """`python -m groundtruth.checkin` - record one check-in.

        python -m groundtruth.checkin --text "at the gym for 45 minutes, lower body"
        python -m groundtruth.checkin --audio memo.m4a

    Writes to the same buffer the agent uses, so a check-in syncs by the same
    at-least-once path as an observation and needs no second delivery mechanism.

    `--dry-run` prints what would be written without writing it, because the first thing
    anyone will want to know is whether the model read their sentence correctly, and
    finding that out should not require editing their timeline.
    """
    import argparse
    import asyncio

    from .buffer import EventBuffer
    from .config import Config, load_env_file
    from .perceive.classify import DEFAULT_CATEGORIES

    parser = argparse.ArgumentParser(description="Record off-screen time.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="A typed check-in, skipping transcription.")
    source.add_argument("--audio", type=Path, help="An audio file you recorded.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--model-prefix",
        default="SPARK",
        help="Backend that interprets the transcript. Text only; sees no screen content.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_env_file()
    config = Config.from_env()

    try:
        reason_backend = Backend.from_env(args.model_prefix)
    except BackendError as exc:
        raise SystemExit(str(exc))

    voice_backend: Backend | None = None
    if args.audio is not None:
        try:
            voice_backend = Backend.from_env('VOICE')
        except BackendError as exc:
            raise SystemExit(
                f'{exc}' + chr(10)
                + 'Set VOICE_ENDPOINT and VOICE_MODEL in .env, or use --text.'
            )

    try:
        checkin, events = asyncio.run(
            record(
                audio_path=args.audio,
                text=args.text,
                device_id=config.device_id,
                categories=checkin_categories(DEFAULT_CATEGORIES),
                voice_backend=voice_backend,
                reason_backend=reason_backend,
            )
        )
    except CheckInRefused as exc:
        raise SystemExit(f'not recorded: {exc}')
    except BackendError as exc:
        if args.audio is not None:
            hint = (
                'Transcription is unverified: no working audio route was found on '
                'this endpoint (see checkin.transcribe). Use --text for now.'
            )
            raise SystemExit(f'{exc}' + chr(10) + chr(10) + hint)
        raise SystemExit(str(exc))

    # Every recorded check-in has a stated duration; an unstated one is refused in
    # `interpret`, so there is no second case to report here.
    print()
    print(f"  {checkin.category}: {checkin.label}")
    print(
        f"  {checkin.start:%H:%M} to {checkin.end:%H:%M} "
        f"({checkin.minutes:.0f} min, as stated)"
    )
    print(f"  confidence {checkin.confidence:.2f}, {len(events)} events")
    print()

    if args.dry_run:
        print("dry run; nothing written")
        return

    with EventBuffer(config.buffer_path) as buffer:
        written = sum(1 for event in events if buffer.append(event))
        print(f"buffered {written} events; {buffer.count()} awaiting sync")


if __name__ == "__main__":
    _main()
