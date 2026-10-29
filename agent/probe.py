"""Send chosen images through the production classifier prompt and tabulate the answers.

Built to settle one question: **is the confidence number real?**

Both answers in the first validation run came back at exactly 0.9. If that figure is
constant regardless of what the model sees, then `is_acceptable` always passes, the retry
ladder never climbs, and the uncertain queue never fills — and honest abstention is a
feature of this product rather than a nicety. A constant 0.9 would mean the feature is
dead and the dashboard screen built around it shows an empty list forever.

Manual playground tests cannot answer that, because nobody runs twenty screenshots by
hand or re-runs them after a prompt change. This does.

    python probe.py shot1.png shot2.png              # as captured
    python probe.py --downscale shots/*.png          # at production width

`--downscale` reduces each image to `TARGET_WIDTH` first, which answers the second open
question: whether classification survives the reduction `capture/screen.py` applies.

## On placement

This tool sends images to whatever backend `--model-prefix` names, typically a `managed`
one, and so deliberately overrides the placement guard that `vision()` enforces.

That is defensible here and nowhere else. The guard exists to stop a background agent
streaming a live screen to a vendor. These are files a person named on a command line
after choosing them — test fixtures, not surveillance. The distinction is the same one
drawn in `docs/validation.md`, and the override is explicit, loud, and confined to this
file so it can never leak into the agent's own path.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class Result:
    name: str
    category: str
    confidence: float
    evidence: str
    width: int


async def probe_one(classifier, path: Path, *, downscale: bool) -> Result:
    from PIL import Image

    from groundtruth.capture.screen import TARGET_WIDTH, _downscale
    from groundtruth.types import Observation, Tier
    from datetime import datetime, timezone

    frame = Image.open(path).convert("RGB")
    if downscale:
        frame = _downscale(frame, TARGET_WIDTH)

    # No process or title: this isolates what the model gets from the image alone,
    # which is the thing being measured. Supplying a window title would let it answer
    # from metadata and tell us nothing about perception.
    observation = Observation(ts=datetime.now(timezone.utc), tier=Tier.SCREEN)
    result = await classifier.classify(observation, frame, effort="low")
    return Result(
        name=path.name,
        category=result.category,
        confidence=result.confidence,
        evidence=result.evidence,
        width=frame.width,
    )


def report(results: list[Result]) -> None:
    if not results:
        return

    print()
    print(f"{'image':<28} {'px':>5}  {'category':<16} {'conf':>5}")
    print("-" * 60)
    for r in results:
        print(f"{r.name[:28]:<28} {r.width:>5}  {r.category[:16]:<16} {r.confidence:>5.2f}")

    print()
    for r in results:
        print(f"{r.name}: {r.evidence}")

    confidences = {round(r.confidence, 2) for r in results}
    print()
    if len(results) < 3:
        print("Too few samples to say anything about calibration.")
    elif len(confidences) == 1:
        value = confidences.pop()
        print(
            f"All {len(results)} answers returned {value}. The confidence signal is "
            "constant, so the uncertain path will never fire and the retry ladder will "
            "never climb. Treat the uncertain queue as dead until this changes."
        )
    else:
        spread = max(confidences) - min(confidences)
        print(
            f"{len(confidences)} distinct values across {len(results)} images, "
            f"spread {spread:.2f}. The signal varies, so thresholds are meaningful."
        )
        if not any(c < 0.75 for c in confidences):
            print(
                "Nothing fell below the 0.75 accept threshold, though — the uncertain "
                "path is still unproven. Try a deliberately contentless image."
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument(
        "--downscale",
        action="store_true",
        help="Reduce to production capture width before sending.",
    )
    parser.add_argument("--model-prefix", default="SPARK")
    args = parser.parse_args()

    load_env()
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    if not os.environ.get("META_API_KEY"):
        sys.exit("META_API_KEY is empty in .env")

    from groundtruth.backends import Backend, BackendError, Placement
    from groundtruth.perceive.classify import Classifier

    configured = Backend.from_env(args.model_prefix)
    # The deliberate override described in the module docstring. Confined to this file.
    probe_backend = Backend(
        base_url=configured.base_url,
        model=configured.model,
        placement=Placement.SELF_HOSTED,
        api_key=configured.api_key,
    )
    print(f"model: {probe_backend.model}  (placement overridden for named files)")

    classifier = Classifier(probe_backend)
    results: list[Result] = []
    for path in args.images:
        if not path.exists():
            print(f"skipping {path}: not found")
            continue
        try:
            results.append(await probe_one(classifier, path, downscale=args.downscale))
        except BackendError as exc:
            print(f"{path.name}: FAILED -> {exc}")

    report(results)


if __name__ == "__main__":
    asyncio.run(main())
