"""Rebuild `day.json`.

Committed alongside the fixture because a hand-edited 480-event file is not reviewable and
not trustworthy: the interesting thing about the fixture is the *shape* of the day, and that
is legible here in thirteen rows of a table but not in the JSON it expands into.

The day is deliberately awkward in every way the segmenter has to cope with:

- a session that spans two processes (the editor then the terminal, both "deep work"),
  which a tracker keyed on application name splits in half;
- a device handoff, with the gym logged by voice from a phone while the desktop is silent;
- genuine idle gaps — commute, a walk, lunch — that must close sessions rather than be
  absorbed into them;
- two categories that recur in separate blocks (doomscrolling before and after lunch), so
  the summary has to total across sessions rather than per session;
- two uncertain spans on two different browsers, which must survive derivation;
- low-confidence samples inside a long confident block, which must *not* drag the session's
  confidence down, because the weighting is by duration.

Chrome accounts for 208 of the day's 480 tracked minutes while meaning five different
things, which is the comparison the product exists to make.

Run: `python build_day.py`
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DAY = datetime(2026, 10, 28, tzinfo=timezone.utc)
CADENCE = timedelta(minutes=1)
"""One sample per minute.

Comfortably inside the 120s idle threshold, so a block stays one session, and far enough
from it that a genuine gap is unmistakable.
"""

DESK = "desk-win-01"
PHONE = "phone-android-01"


def at(hour: int, minute: int) -> datetime:
    return DAY.replace(hour=hour, minute=minute)


class Block:
    """A stretch of one activity, expanded to one event per minute."""

    def __init__(
        self,
        *,
        device: str,
        start: datetime,
        minutes: int,
        tier: int,
        category: str,
        label: str,
        process: str | None,
        titles: tuple[str | None, ...],
        evidence: tuple[str, ...],
        confidence: float,
        dips: tuple[tuple[int, float, str], ...] = (),
        relabel: tuple[int, str, str] | None = None,
    ) -> None:
        self.device = device
        self.start = start
        self.minutes = minutes
        self.tier = tier
        self.category = category
        self.label = label
        self.process = process
        self.titles = titles
        self.evidence = evidence
        self.confidence = confidence
        self.dips = {index: (value, why) for index, value, why in dips}
        self.relabel = relabel

    def events(self) -> list[dict]:
        out = []
        for index in range(self.minutes):
            label, confidence = self.label, self.confidence
            evidence = self.evidence[index % len(self.evidence)]
            if self.relabel is not None and index >= self.relabel[0]:
                label = self.relabel[1]
                evidence = self.relabel[2]
            if index in self.dips:
                confidence, evidence = self.dips[index]
            ts = self.start + index * CADENCE
            out.append(
                {
                    "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "tier": self.tier,
                    "process": self.process,
                    "window_title": self.titles[index % len(self.titles)],
                    "label": label,
                    "category": self.category,
                    "confidence": round(confidence, 2),
                    "evidence": evidence,
                }
            )
        return out


BLOCKS: tuple[Block, ...] = (
    Block(
        device=DESK,
        start=at(6, 0),
        minutes=28,
        tier=2,
        category="devotion",
        label="scripture reading",
        process="chrome.exe",
        titles=(
            "Psalm 119 - Bible Gateway",
            "Psalm 119:97-112 - Bible Gateway",
            "Psalm 119:105 commentary - Bible Gateway",
        ),
        evidence=(
            "Single text region held in view, near-zero churn across frames.",
            "Scripture text with a commentary pane beside it; no scrolling between samples.",
            "Same passage on screen as the previous sample; reading, not browsing.",
        ),
        confidence=0.91,
    ),
    Block(
        device=PHONE,
        start=at(6, 34),
        minutes=55,
        tier=3,
        category="gym",
        label="strength training",
        process=None,
        titles=(None,),
        evidence=(
            "Voice check-in: 'at the gym, lower body today'.",
            "Voice check-in continuing; no screen activity on any device.",
        ),
        confidence=0.97,
    ),
    Block(
        device=DESK,
        start=at(7, 45),
        minutes=55,
        tier=2,
        category="deep work",
        label="auth refactor",
        process="code.exe",
        titles=(
            "session.py - groundtruth - Visual Studio Code",
            "tokens.py - groundtruth - Visual Studio Code",
            "test_session.py - groundtruth - Visual Studio Code",
        ),
        evidence=(
            "Editor pane dominant and low-churn; edits concentrated in one file.",
            "Two editor panes side by side, diff visible, no feed or video region present.",
            "Cursor activity co-located with the editor region across consecutive frames.",
        ),
        confidence=0.93,
        dips=(
            (22, 0.52, "A notification surface briefly overlaps the editor; attention unclear."),
            (23, 0.55, "Notification dismissed; editor regaining focus but churn still high."),
        ),
    ),
    Block(
        device=DESK,
        start=at(8, 40),
        minutes=35,
        tier=2,
        category="deep work",
        label="running the test suite",
        process="windowsterminal.exe",
        titles=(
            "pytest - Windows Terminal",
            "pytest -x server/tests - Windows Terminal",
        ),
        evidence=(
            "Terminal region dominant, output appended rather than replaced: a test run.",
            "Scrollback advancing in one direction only; reading results, not browsing.",
        ),
        confidence=0.9,
    ),
    Block(
        device=DESK,
        start=at(9, 15),
        minutes=7,
        tier=2,
        category="uncertain",
        label="uncertain",
        process="chrome.exe",
        titles=("New Tab", "(2) New Tab", "Search results"),
        evidence=(
            "Mixed content; could be research or browsing.",
            "Churn between reading and scrolling levels; no region holds long enough to tell.",
        ),
        confidence=0.38,
    ),
    Block(
        device=DESK,
        start=at(9, 22),
        minutes=55,
        tier=2,
        category="research",
        label="reading API documentation",
        process="chrome.exe",
        titles=(
            "Meta Model API docs",
            "SAM 3.1 - object multiplexing",
            "Muse Glimmer - reasoning effort",
        ),
        evidence=(
            "Static documentation page, low region churn, code samples visible.",
            "Documentation pages across three tabs; each held for several samples.",
            "Long-form prose region stable between frames; no feed geometry present.",
        ),
        confidence=0.88,
        relabel=(
            35,
            "comparing inference providers",
            "Two pricing pages held side by side; comparison rather than reading through.",
        ),
    ),
    Block(
        device=DESK,
        start=at(10, 26),
        minutes=25,
        tier=2,
        category="communication",
        label="team standup thread",
        process="slack.exe",
        titles=("#groundtruth - Slack", "#groundtruth - 3 new - Slack"),
        evidence=(
            "Message list with a compose region active; short bursts of typing.",
            "Thread pane open, same conversation across samples.",
        ),
        confidence=0.86,
    ),
    Block(
        device=DESK,
        start=at(10, 51),
        minutes=7,
        tier=2,
        category="uncertain",
        label="uncertain",
        process="msedge.exe",
        titles=("Microsoft Edge", "3 tabs - Microsoft Edge"),
        evidence=(
            "Two regions competing for focus; neither resolves to a known activity.",
            "Window held but no region churn and no input locus: possibly not being read.",
        ),
        confidence=0.41,
    ),
    Block(
        device=DESK,
        start=at(10, 58),
        minutes=83,
        tier=2,
        category="doomscrolling",
        label="scrolling a social feed",
        process="chrome.exe",
        titles=("Instagram", "Instagram - Explore", "X - Home"),
        evidence=(
            "Continuous high churn in the content region; viewport replaced every few frames.",
            "Feed geometry with fixed chrome and a scrolling content column.",
            "No region held longer than one sample; nothing is being read.",
        ),
        confidence=0.94,
    ),
    Block(
        device=DESK,
        start=at(12, 46),
        minutes=35,
        tier=2,
        category="doomscrolling",
        label="scrolling a social feed",
        process="chrome.exe",
        titles=("X - Home", "Instagram - Reels"),
        evidence=(
            "High churn resumed in the same feed geometry as before lunch.",
            "Short video regions replaced continuously; autoplay rather than selection.",
        ),
        confidence=0.95,
    ),
    Block(
        device=DESK,
        start=at(13, 21),
        minutes=30,
        tier=1,
        category="admin",
        label="inbox triage",
        process="outlook.exe",
        titles=("Inbox - Outlook", "Inbox (12) - Outlook"),
        evidence=("Unambiguous process; the window title names the activity without escalating.",),
        confidence=0.81,
    ),
    Block(
        device=DESK,
        start=at(13, 51),
        minutes=45,
        tier=2,
        category="deep work",
        label="segmenter implementation",
        process="code.exe",
        titles=(
            "segment.py - groundtruth - Visual Studio Code",
            "test_segment.py - groundtruth - Visual Studio Code",
        ),
        evidence=(
            "Editor and test file alternating; edits followed by a run, repeatedly.",
            "Single editor region dominant with low churn and sustained input.",
        ),
        confidence=0.92,
    ),
    Block(
        device=DESK,
        start=at(14, 36),
        minutes=20,
        tier=1,
        category="entertainment",
        label="watching a film",
        process="vlc.exe",
        titles=("VLC media player",),
        evidence=("Unambiguous process; a media player is self-describing.",),
        confidence=0.84,
    ),
)


def build() -> dict:
    by_device: dict[str, list[dict]] = {}
    for block in BLOCKS:
        by_device.setdefault(block.device, []).extend(block.events())
    for events in by_device.values():
        events.sort(key=lambda event: event["ts"])
    return {
        "generated_for": "2026-10-28",
        "note": (
            "One realistic eight-hour day. Each entry of 'batches' is exactly a POST /events "
            "request body as docs/API.md defines it, so loading the fixture exercises the "
            "same validation a real agent would hit. Regenerate with fixtures/build_day.py."
        ),
        "batches": [
            {"device_id": device, "events": events} for device, events in by_device.items()
        ],
    }


def render(payload: dict) -> str:
    """Serialise with one event per line.

    `json.dumps(indent=2)` would spread 480 events over four thousand lines, which makes the
    file unreadable in review and unhelpful in a diff. One line per event keeps a block of
    the day visible on one screen.
    """
    lines = [
        "{",
        f'  "generated_for": {json.dumps(payload["generated_for"])},',
        f'  "note": {json.dumps(payload["note"])},',
        '  "batches": [',
    ]
    for batch_index, batch in enumerate(payload["batches"]):
        lines.append("    {")
        lines.append(f'      "device_id": {json.dumps(batch["device_id"])},')
        lines.append('      "events": [')
        for event_index, event in enumerate(batch["events"]):
            comma = "," if event_index < len(batch["events"]) - 1 else ""
            lines.append(f"        {json.dumps(event, separators=(', ', ': '))}{comma}")
        lines.append("      ]")
        lines.append("    }" + ("," if batch_index < len(payload["batches"]) - 1 else ""))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    payload = build()
    target = Path(__file__).resolve().parent / "day.json"
    target.write_text(render(payload), encoding="utf-8")
    total = sum(len(batch["events"]) for batch in payload["batches"])
    print(f"wrote {target} ({total} events across {len(payload['batches'])} devices)")


if __name__ == "__main__":
    main()
