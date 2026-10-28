"""Does the real Meta Model API work through our transport?

Run once a key is in `.env`:

    cd agent && ./.venv/Scripts/python.exe check_api.py

Answers three questions the mock could not:

1. Does `/chat/completions` work, or must we use the Responses API at `/responses`?
   The playground generates `/responses`, but the docs list an OpenAI-compatible
   Chat Completions surface, and `backends.py` is built on the latter.
2. Does an inline base64 image work, or must images be uploaded for a `file_id` first?
   The playground uploads; Chat Completions conventionally accepts inline.
3. Does the Tier 4 review path actually return usable JSON?

Prints nothing sensitive. The key is read from the environment and never echoed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def load_env() -> None:
    """Minimal .env loader, so this runs without extra dependencies."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        sys.exit(f"no .env at {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


async def main() -> None:
    load_env()
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    if not os.environ.get("META_API_KEY"):
        sys.exit("META_API_KEY is empty in .env")

    from groundtruth.backends import Backend, BackendError, chat

    backend = Backend.from_env("SPARK")
    print(f"endpoint : {backend.base_url}")
    print(f"model    : {backend.model}")
    print(f"placement: {backend.placement.value}")
    print()

    print("1. text-only call via /chat/completions ...")
    try:
        reply = await chat(
            backend,
            [{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=16,
        )
        print(f"   OK -> {reply.strip()[:60]}")
    except BackendError as exc:
        print(f"   FAILED -> {exc}")
        print("   If this is a 404, the compat surface is elsewhere; the playground's")
        print("   'Get code' showed /responses, so backends.py needs that path instead.")
        return

    print()
    print("2. inline base64 image via /chat/completions ...")
    try:
        from PIL import Image

        from groundtruth.backends import vision

        # A trivial generated image: this asks whether the transport is accepted,
        # not whether the model is clever.
        swatch = Image.new("RGB", (64, 64), (30, 30, 40))
        probe = Backend(
            base_url=backend.base_url,
            model=backend.model,
            placement=__import__(
                "groundtruth.backends", fromlist=["Placement"]
            ).Placement.SELF_HOSTED,
            api_key=backend.api_key,
        )
        reply = await vision(
            probe,
            system="You describe images in one short phrase.",
            user_text="What colour is this?",
            frame=swatch,
            max_tokens=32,
        )
        print(f"   OK -> {reply.strip()[:60]}")
        print("   Inline images work; no file upload step needed.")
    except BackendError as exc:
        print(f"   FAILED -> {exc}")
        print("   Images likely need uploading to /v1/files for a file_id first.")

    print()
    print("3. tier 4 review prompt shape ...")
    from groundtruth.patterns import Span, compute
    from groundtruth.review import Reviewer
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
    spans = [
        Span(start, start + timedelta(minutes=95), "deep work", "auth refactor", 0.95),
        Span(
            start + timedelta(hours=2),
            start + timedelta(hours=4, minutes=31),
            "doomscrolling",
            "scrolling a feed",
            0.9,
        ),
        Span(
            start + timedelta(hours=5),
            start + timedelta(hours=5, minutes=25),
            "devotion",
            "scripture reading",
            0.91,
        ),
    ]
    try:
        review = await Reviewer(backend).review(compute(spans), period="a sample day")
        print("   OK")
        print()
        print(review.render())
    except BackendError as exc:
        print(f"   FAILED -> {exc}")


if __name__ == "__main__":
    asyncio.run(main())
