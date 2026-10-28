"""Tier 4: the weekly review.

The only part of this project that may legitimately use a vendor-managed endpoint, and
the reason is structural rather than a concession. Every other tier handles screen
content; this one never sees a pixel, a window title, or a transcript. It is handed
pre-computed durations and category names — "deep work, 6h 20m, average stretch 24m" —
and asked what they mean.

That is why `Reviewer` accepts a `managed` backend where `Classifier` and `RegionTracker`
refuse one, and why Meta's free-credit terms are acceptable here: "your content may be
used for product improvement" applies to the string "deep work", not to anyone's screen.

The model does no arithmetic. `patterns.py` computes every number before the prompt is
built — see that module for why.

It is agentic in a bounded way: the review may ask one follow-up question about a
specific day or category before committing to its observations. One round, because the
value is in looking twice at something surprising, and an unbounded loop on an endpoint
billed per token is how a hackathon budget disappears overnight.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .backends import Backend, BackendError, chat
from .patterns import Patterns, Span, compute, render

log = logging.getLogger("groundtruth.review")

SYSTEM_PROMPT = """\
You review a person's own record of how they spent their time, and report what you \
notice. You are describing their week back to them, not grading it.

Rules you must not break:

- Every number you state is given to you. Never compute, estimate, or round one.
- Never moralise. "You spent 2h 31m on feeds" is a fact; "you wasted 2h 31m" is a \
judgement you were not asked for. No praise either.
- An observation must cite the specific figure it rests on, so the person can check it.
- If the data does not support an observation, say there was nothing notable. An invented \
pattern is worse than a dull review, because they will act on it.
- Time that could not be classified is not evidence of anything. Do not reason about it.

Respond with JSON only:
{"observations": [{"finding": "...", "evidence": "...", "confidence": 0.0}],
 "question": "..." or null}

Set "question" only if one specific detail would materially change an observation. Ask \
for a named day or category, nothing broader. Otherwise set it to null."""


@dataclass(frozen=True, slots=True)
class Observation:
    finding: str
    evidence: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Review:
    period: str
    observations: tuple[Observation, ...]
    asked: str | None = None

    def render(self) -> str:
        if not self.observations:
            return f"{self.period}: nothing notable in this period."
        lines = [self.period, ""]
        for obs in self.observations:
            lines.append(f"- {obs.finding}")
            lines.append(f"  ({obs.evidence})")
        if self.asked:
            lines.append("")
            lines.append(f"[looked closer at: {self.asked}]")
        return "\n".join(lines)


async def fetch_spans(
    server_url: str,
    *,
    since: datetime,
    until: datetime,
    client: httpx.AsyncClient | None = None,
) -> list[Span]:
    """Pull sessions from the sync server.

    Reads `GET /sessions`, which returns derived spans rather than raw events — the
    review has no business seeing individual observations.
    """
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.get(
            f"{server_url.rstrip('/')}/sessions",
            params={"from": since.isoformat(), "to": until.isoformat()},
        )
        response.raise_for_status()
        body = response.json()
    finally:
        if owns:
            await client.aclose()

    return [
        Span(
            start=datetime.fromisoformat(item["start_ts"]),
            end=datetime.fromisoformat(item["end_ts"]),
            category=item["category"],
            label=item.get("label", item["category"]),
            confidence=float(item.get("confidence", 0.0)),
        )
        for item in body.get("sessions", [])
    ]


def detail_for(patterns: Patterns, question: str) -> str:
    """Answer the model's one follow-up from already-computed facts.

    Deliberately cannot fetch anything new. The follow-up exists to let the review look
    harder at what it already has, not to widen what it is allowed to see.
    """
    needle = question.lower()

    for day, categories in patterns.by_day.items():
        if day.lower() in needle:
            rows = ", ".join(
                f"{c} {s / 60:.0f}m"
                for c, s in sorted(categories.items(), key=lambda kv: -kv[1])
            )
            return f"{day}: {rows}"

    for category in patterns.by_category:
        if category.lower() in needle:
            per_day = ", ".join(
                f"{day} {cats.get(category, 0) / 60:.0f}m"
                for day, cats in patterns.by_day.items()
            )
            mean = patterns.fragmentation.get(category, 0.0)
            return (
                f"{category} by day: {per_day}. "
                f"Average unbroken stretch {mean / 60:.0f}m."
            )

    return "That detail is not available in this period's data."


class Reviewer:
    """Interprets a period's patterns.

    Accepts a `managed` backend by design: this tier handles category names and
    durations, never screen content. See the module docstring.
    """

    def __init__(self, backend: Backend, *, max_followups: int = 1) -> None:
        self._backend = backend
        self._max_followups = max_followups

    async def review(self, patterns: Patterns, *, period: str) -> Review:
        if not patterns.span_count:
            return Review(period=period, observations=())

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": render(patterns)},
        ]

        asked: str | None = None
        for _ in range(self._max_followups + 1):
            raw = await chat(
                self._backend, messages, max_tokens=1200, temperature=0.2
            )
            parsed = _parse(raw)
            question = parsed.get("question")

            if not question or asked is not None:
                return Review(
                    period=period,
                    observations=_observations(parsed),
                    asked=asked,
                )

            asked = str(question)
            log.info("review asked a follow-up: %s", asked)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": detail_for(patterns, asked)})

        return Review(period=period, observations=(), asked=asked)


def _parse(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackendError(f"review response was not JSON: {text[:200]}") from exc
    return body if isinstance(body, dict) else {}


def _observations(parsed: dict[str, Any]) -> tuple[Observation, ...]:
    """Build observations, dropping any that cite no evidence.

    An observation without evidence is an assertion the person cannot check, which is
    exactly what this tier is supposed not to produce.
    """
    out: list[Observation] = []
    for item in parsed.get("observations") or []:
        finding = str(item.get("finding") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if not finding or not evidence:
            log.debug("dropping unevidenced observation: %s", finding[:80])
            continue
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        out.append(Observation(finding, evidence, confidence))
    return tuple(out)


async def run(server_url: str, backend: Backend, *, days: int = 7) -> Review:
    """Fetch, compute, interpret."""
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    spans = await fetch_spans(server_url, since=since, until=until)
    patterns = compute(spans)
    period = f"{since:%d %b} to {until:%d %b}"
    return await Reviewer(backend).review(patterns, period=period)


def _main() -> None:
    """`python -m groundtruth.review` — print a review of the last N days.

    Reads SPARK_* or WONDER_* from the environment. Managed placement is expected here
    and not a misconfiguration: see the module docstring.
    """
    import argparse
    import asyncio
    import os

    parser = argparse.ArgumentParser(description="Review a period of tracked activity.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--server", default=os.environ.get("GROUNDTRUTH_SERVER", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--model-prefix",
        default="SPARK",
        help="Environment prefix for the backend, e.g. SPARK or WONDER.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        backend = Backend.from_env(args.model_prefix)
    except BackendError as exc:
        raise SystemExit(f"{exc}\nSet {args.model_prefix}_ENDPOINT and _MODEL in .env")

    review = asyncio.run(run(args.server, backend, days=args.days))
    print()
    print(review.render())
    print()


if __name__ == "__main__":
    _main()
