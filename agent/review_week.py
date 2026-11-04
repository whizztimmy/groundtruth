"""Run Tier 4 over a week with a known pattern, so its answers can be judged.

A review of real data cannot be marked. If the model says deep work is fragmented, the
only way to know whether that is insight or noise is to already know the answer - so
this builds a week whose patterns were put there on purpose and checks which ones come
back.

    cd agent && ./.venv/Scripts/python.exe review_week.py           # Meta, managed
    cd agent && ./.venv/Scripts/python.exe review_week.py GLIMMER   # open weights

Taking a backend prefix on the command line is the point: the same input through two
models is the only comparison that isolates the model from the prompt. Results are
recorded in `docs/validation.md`.

Three patterns are planted:

1. **Deep work and doomscrolling trade off** across days - Thursday is the extreme
   (2h doomscrolling, 28m deep work) and Monday the opposite (1h 50m, 30m).
2. **Deep work is fragmented** - 6h 26m spread over 8 stretches, averaging 48m, with
   the longest at 1h 10m.
3. **Weekdays open with deep work, weekends with devotion.**

Nothing here is real data, so this file is safe to read and safe to commit. It also
sends no screen content: Tier 4 receives category names and durations, which is why a
managed placement is correct for it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_env() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        sys.exit(f"no .env at {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


MONDAY = datetime(2026, 10, 26, tzinfo=timezone.utc)


def build_week():
    from groundtruth.patterns import Span

    def at(day: int, hour: int, minute: int = 0) -> datetime:
        return MONDAY + timedelta(days=day, hours=hour, minutes=minute)

    def span(start: datetime, minutes: int, category: str, label: str) -> Span:
        return Span(
            start=start,
            end=start + timedelta(minutes=minutes),
            category=category,
            label=label,
            confidence=0.9,
        )

    return [
        span(at(0, 8), 55, "deep work", "implementing the sync client"),
        span(at(0, 10), 30, "doomscrolling", "scrolling a social feed"),
        span(at(0, 13), 55, "deep work", "debugging the buffer"),
        span(at(0, 16), 45, "admin", "filling in a credits form"),
        span(at(1, 8, 30), 40, "deep work", "writing the classifier"),
        span(at(1, 11), 45, "doomscrolling", "scrolling a social feed"),
        span(at(1, 14), 40, "deep work", "wiring the escalation policy"),
        span(at(2, 9), 65, "deep work", "region tracking"),
        span(at(2, 12), 60, "doomscrolling", "scrolling a social feed"),
        span(at(2, 15), 50, "research", "reading the model API docs"),
        span(at(3, 9, 15), 28, "deep work", "chasing a transport bug"),
        span(at(3, 10), 120, "doomscrolling", "scrolling a social feed"),
        span(at(3, 14), 40, "admin", "hackathon paperwork"),
        span(at(4, 8, 45), 70, "deep work", "calibration probe"),
        span(at(4, 11), 50, "doomscrolling", "scrolling a social feed"),
        span(at(5, 7), 40, "devotion", "morning devotion"),
        span(at(5, 10), 90, "doomscrolling", "scrolling a social feed"),
        span(at(5, 17), 60, "fitness", "gym session"),
        span(at(6, 7, 30), 50, "devotion", "morning devotion"),
        span(at(6, 11), 75, "doomscrolling", "scrolling a social feed"),
        span(at(6, 15), 33, "deep work", "tidying tests"),
    ]


async def main() -> None:
    load_env()
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from groundtruth.backends import Backend
    from groundtruth.patterns import compute, render
    from groundtruth.review import Reviewer

    prefix = sys.argv[1] if len(sys.argv) > 1 else "SPARK"
    patterns = compute(build_week())

    # Printed in full because this is the entire input the model receives. A reader
    # should be able to check every figure in the review against it by hand.
    print(render(patterns))
    print("=" * 70)

    backend = Backend.from_env(prefix)
    print(f"model: {backend.model}  ({backend.placement.value})\n")
    print((await Reviewer(backend).review(patterns, period="26 Oct to 01 Nov")).render())


if __name__ == "__main__":
    asyncio.run(main())
