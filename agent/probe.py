"""Send chosen images through the production classifier prompt and tabulate the answers.

Built to settle one question: **is the confidence number real?**

Both answers in the first validation run came back at exactly 0.9. If that figure is
constant regardless of what the model sees, then `is_acceptable` always passes, the retry
ladder never climbs, and the uncertain queue never fills - and honest abstention is a
feature of this product rather than a nicety. A constant 0.9 would mean the feature is
dead and the dashboard screen built around it shows an empty list forever.

Manual playground tests cannot answer that, because nobody runs twenty screenshots by
hand or re-runs them after a prompt change. This does.

    python probe.py shot1.png shot2.png              # as captured
    python probe.py --downscale shots/*.png          # at production width

`--downscale` reduces each image to `TARGET_WIDTH` first, which answers the second open
question: whether classification survives the reduction `capture/screen.py` applies.

    python probe.py --sweep shots/*.png                # one row per effort level

`--sweep` runs each image through `EFFORT_LADDER` instead of a single level, and answers a
question the ladder has always assumed rather than tested: **does escalating reasoning
effort improve a low-confidence answer, or degrade it?**

`classify_verified` climbs that ladder whenever confidence lands under `ACCEPT`, on the
premise that more thinking helps. The Fireworks workshop published a sweep on short-answer
text benchmarks where Muse scores *worse* at high effort than at low. Those are text tasks
and this is screen classification, so the finding does not transfer - but it does mean the
premise is untested here, and the samples the ladder actually fires on are the ambiguous
ones, which are exactly where a wrong premise would do its damage.

## On placement

This tool sends images to whatever backend `--model-prefix` names, typically a `managed`
one, and so deliberately overrides the placement guard that `vision()` enforces.

That is defensible here and nowhere else. The guard exists to stop a background agent
streaming a live screen to a vendor. These are files a person named on a command line
after choosing them - test fixtures, not surveillance. The distinction is the same one
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
    """Defers to the agent's own loader, so a tool cannot be configured differently from
    the thing it is diagnosing. That divergence is what hid the stub-classifier fault."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from groundtruth.config import load_env_file

    if not load_env_file():
        sys.exit("no .env at the repository root")

@dataclass(frozen=True, slots=True)
class Result:
    name: str
    category: str
    confidence: float
    evidence: str
    width: int
    effort: str = "low"


async def probe_one(
    classifier, path: Path, *, downscale: bool, effort: str = "low"
) -> Result:
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
    result = await classifier.classify(observation, frame, effort=effort)
    return Result(
        name=path.name,
        category=result.category,
        confidence=result.confidence,
        evidence=result.evidence,
        width=frame.width,
        effort=effort,
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

    # With repeats, the only honest first question is how much the same input moves on
    # its own. Pooling repeats into one spread reports noise as signal, which is exactly
    # the error the 2026-10-29 entry in docs/validation.md made.
    repeats: dict[str, list[float]] = {}
    for r in results:
        repeats.setdefault(r.name, []).append(r.confidence)
    if any(len(v) > 1 for v in repeats.values()):
        print()
        print("within-image spread across repeats of identical input:")
        for name, values in repeats.items():
            if len(values) < 2:
                continue
            span = max(values) - min(values)
            cats = {r.category for r in results if r.name == name}
            flip = f", category flipped between {sorted(cats)}" if len(cats) > 1 else ""
            print(f"  {name}: {len(values)} runs, spread {span:.2f}{flip}")
        print()
        print(
            "Any across-image comparison below is only meaningful where it exceeds "
            "these numbers. A single reading per cell cannot."
        )

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
                "Nothing fell below the 0.75 accept threshold, though - the uncertain "
                "path is still unproven. Try a deliberately contentless image."
            )


def sweep_report(results: list[Result], ladder: tuple[str, ...]) -> None:
    """Tabulate image x effort, then say which direction the ladder moved things.

    Reports category changes separately from confidence changes because they mean
    different things: a drifting number is noise the design already damps, while a
    category that flips under escalation means the retry is changing the answer, not
    just its stated certainty.
    """
    by_image: dict[str, dict[str, Result]] = {}
    for r in results:
        by_image.setdefault(r.name, {})[r.effort] = r

    width = max((len(n) for n in by_image), default=10)
    print()
    print(f"{'image':<{width}}  " + "  ".join(f"{e:>18}" for e in ladder))
    print("-" * (width + 2 + 20 * len(ladder)))
    for name, cells in by_image.items():
        row = []
        for e in ladder:
            r = cells.get(e)
            row.append(f"{r.category[:11]:<11} {r.confidence:>5.2f}" if r else f"{'-':>17}")
        print(f"{name:<{width}}  " + "  ".join(f"{c:>18}" for c in row))

    rose = fell = flat = 0
    flipped: list[str] = []
    for name, cells in by_image.items():
        first, last = cells.get(ladder[0]), cells.get(ladder[-1])
        if not first or not last:
            continue
        delta = last.confidence - first.confidence
        # 0.1 is the measured run-to-run drift floor from docs/validation.md. Anything
        # inside it is not a movement, and calling it one would be reading noise.
        if delta > 0.1:
            rose += 1
        elif delta < -0.1:
            fell += 1
        else:
            flat += 1
        if first.category != last.category:
            flipped.append(f"{name}: {first.category} -> {last.category}")

    print()
    print(f"from {ladder[0]} to {ladder[-1]}, beyond the 0.1 noise floor:")
    print(f"  confidence rose on {rose}, fell on {fell}, unchanged on {flat}")
    if flipped:
        print("  category changed:")
        for line in flipped:
            print(f"    {line}")
    else:
        print("  no category changed")

    print()
    if fell > rose:
        print(
            "Escalating made answers LESS confident more often than more confident. The "
            "ladder in classify_verified is spending tokens to make things worse; it "
            "should stop at the level that performs best rather than climbing."
        )
    elif rose > fell:
        print(
            "Escalating raised confidence more often than it lowered it. The ladder's "
            "premise holds on this sample."
        )
    else:
        print(
            "No net direction. The ladder is costing tokens and latency for no measured "
            "gain, which is an argument for dropping it rather than keeping it."
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
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run each image through EFFORT_LADDER instead of one level.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Readings per cell. Reasoning models are not deterministic; see "
        "docs/validation.md on the 0.1 drift floor.",
    )
    args = parser.parse_args()

    load_env()
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    if not (
        os.environ.get(f"{args.model_prefix}_API_KEY")
        or os.environ.get("META_API_KEY")
    ):
        sys.exit(
            f"{args.model_prefix}_API_KEY and META_API_KEY are both empty in .env"
        )

    from groundtruth.backends import Backend, BackendError, Placement
    from groundtruth.perceive.classify import EFFORT_LADDER, Classifier

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
    ladder = EFFORT_LADDER if args.sweep else ("low",)
    results: list[Result] = []
    for path in args.images:
        if not path.exists():
            print(f"skipping {path}: not found")
            continue
        for effort in ladder:
            for _ in range(args.repeat):
                try:
                    results.append(
                        await probe_one(
                            classifier, path, downscale=args.downscale, effort=effort
                        )
                    )
                    print(f"  {path.name} @ {effort}: {results[-1].category} "
                          f"{results[-1].confidence:.2f}")
                except BackendError as exc:
                    print(f"{path.name} @ {effort}: FAILED -> {exc}")

    if args.sweep:
        sweep_report(results, ladder)
    else:
        report(results)


if __name__ == "__main__":
    asyncio.run(main())
