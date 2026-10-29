"""HTTP transport shared by the Tier 2 clients.

One deliberate decision shapes this module: **where a model runs is configuration, not a
code path.** Muse Glimmer is Apache-2.0 open weights, so the same model answers whether it
is served by llama.cpp on a desktop GPU, by Baseten or Modal on hardware you rent, or by a
managed endpoint. Keeping one transport behind one interface is what makes the privacy
posture a deployment decision rather than a rewrite.

The endpoints speak OpenAI-compatible Chat Completions, which every target above exposes.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx


class Placement(str, Enum):
    """Where a backend physically runs, and therefore what it may be shown.

    This is checked at the call site rather than documented and hoped for: `vision()`
    refuses to send pixels to a backend not marked as operator-controlled. A privacy
    guarantee that depends on every future caller remembering it is not a guarantee.
    """

    LOCAL = "local"
    """On this machine. Nothing leaves the device."""

    SELF_HOSTED = "self_hosted"
    """Open weights on infrastructure the operator controls and can tear down —
    Baseten, Modal, a rented GPU. Not the user's machine, but not a third party's
    training corpus either."""

    MANAGED = "managed"
    """A vendor-managed endpoint. Appropriate for text that carries no screen content;
    never for frames."""

    @property
    def may_see_pixels(self) -> bool:
        return self in (Placement.LOCAL, Placement.SELF_HOSTED)


class BackendError(RuntimeError):
    """A call failed in a way retrying will not fix."""


class TruncatedBeforeAnswer(BackendError):
    """A reasoning model spent its whole budget thinking and emitted nothing.

    Distinct from other failures because it is mechanically fixable: the model did not
    refuse or error, it simply ran out of room. `chat` retries once with a doubled
    budget rather than making every caller guess a ceiling that depends on how much
    input they happened to send.
    """


class TrainingTierRefused(BackendError):
    """Raised when a configured model is a tier the vendor may train on.

    Meta prices `-contributor` checkpoints below the standard ones precisely because
    inputs may enter a training corpus. That is a reasonable trade for a throwaway
    script and the wrong one here: Tier 4 is handed a person's categorised week, and the
    README promises that stays theirs.

    It is refused rather than warned about because the two model ids differ by one
    suffix, the cheaper one appears in the vendor's own examples, and a warning in a log
    nobody reads is not a control. `ALLOW_TRAINING_TIER=1` overrides it for anyone who
    genuinely wants the discount on their own data.
    """


class PlacementViolation(BackendError):
    """Raised when screen content would reach a backend not allowed to see it.

    Deliberately not catchable-by-accident: it means a privacy invariant was about to be
    broken, so it should crash the agent rather than degrade quietly.
    """


@dataclass(frozen=True, slots=True)
class Backend:
    """One model endpoint.

    `placement` is not cosmetic — it gates what this backend may be shown.
    """

    base_url: str
    model: str
    placement: Placement
    api_key: str | None = None
    timeout_s: float = 60.0

    @classmethod
    def from_env(cls, prefix: str) -> Backend:
        """Build from `{PREFIX}_ENDPOINT`, `{PREFIX}_MODEL`, `{PREFIX}_PLACEMENT`.

        Falls back to `META_API_KEY` when no `{PREFIX}_API_KEY` is set, since most
        deployments use one key across models.

        Fails on a missing key rather than building a backend that will 401 on first use.
        A remote endpoint with no credential is always a misconfiguration, and finding
        out at startup beats finding out inside a retry loop at the end of a week.
        """
        endpoint = os.environ.get(f"{prefix}_ENDPOINT")
        model = os.environ.get(f"{prefix}_MODEL")
        if not endpoint or not model:
            raise BackendError(f"{prefix}_ENDPOINT and {prefix}_MODEL must both be set")

        raw_placement = os.environ.get(f"{prefix}_PLACEMENT", "managed")
        try:
            placement = Placement(raw_placement)
        except ValueError as exc:
            valid = ", ".join(p.value for p in Placement)
            raise BackendError(
                f"{prefix}_PLACEMENT={raw_placement!r} is not one of: {valid}"
            ) from exc

        if TRAINING_TIER.search(model) and not _flag("ALLOW_TRAINING_TIER"):
            raise TrainingTierRefused(
                f"{prefix}_MODEL={model!r} is a contributor-tier checkpoint, which the "
                "vendor may train on. Use the standard model id, or set "
                "ALLOW_TRAINING_TIER=1 if that trade is deliberate."
            )

        api_key = (
            os.environ.get(f"{prefix}_API_KEY") or os.environ.get("META_API_KEY") or ""
        ).strip()
        if not api_key and placement is not Placement.LOCAL:
            raise BackendError(
                f"{prefix}_API_KEY is empty and so is META_API_KEY, but "
                f"{prefix}_ENDPOINT is {placement.value}, not local. Put the key in .env."
            )

        return cls(
            base_url=endpoint.rstrip("/"),
            model=model,
            placement=placement,
            api_key=api_key or None,
        )

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


# Vendor suffixes that mark a discounted tier trained on its inputs. Matched on the
# model id because that is the only place the distinction appears — the endpoint, the
# auth and the wire format are identical to the standard tier.
TRAINING_TIER = re.compile(r"-(?:contributor|training|community)(?![a-z])")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


async def chat(
    backend: Backend,
    messages: list[dict[str, Any]],
    *,
    reasoning_effort: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    max_attempts: int = 3,
    max_token_ceiling: int = 16000,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Call an OpenAI-compatible chat endpoint and return the assistant's text.

    Temperature defaults to 0: classification wants the same answer for the same screen,
    and the playground's default of 1 is wrong for this use.

    Retries on transient status codes with jittered exponential backoff. Jitter matters
    because the agent samples on a fixed cadence — without it, a blip would resynchronise
    every pending retry into a thundering herd.
    """
    payload: dict[str, Any] = {
        "model": backend.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=backend.timeout_s)

    try:
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = await client.post(
                    f"{backend.base_url}/chat/completions",
                    json=payload,
                    headers=backend.headers,
                )
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    try:
                        return _extract_text(response.json())
                    except TruncatedBeforeAnswer:
                        # How much room a reasoning model needs depends on how much
                        # input it was given, which the caller cannot know in advance.
                        # Doubling once covers the realistic case without letting a
                        # pathological prompt spend without limit.
                        if payload["max_tokens"] >= max_token_ceiling:
                            raise
                        payload["max_tokens"] *= 2
                        continue
                if response.status_code not in RETRYABLE_STATUS:
                    raise BackendError(
                        f"{backend.model} returned {response.status_code}: "
                        f"{response.text[:300]}"
                    )
                last_error = BackendError(
                    f"{backend.model} returned {response.status_code}"
                )

            if attempt < max_attempts - 1:
                backoff = (2**attempt) * 0.5
                await asyncio.sleep(backoff + random.uniform(0, backoff))

        raise BackendError(
            f"{backend.model} failed after {max_attempts} attempts"
        ) from last_error
    finally:
        if owns_client:
            await client.aclose()


async def vision(
    backend: Backend,
    system: str,
    user_text: str,
    frame: Any,
    *,
    reasoning_effort: str | None = None,
    **kwargs: Any,
) -> str:
    """Call a multimodal endpoint with one frame.

    Refuses outright when `backend.placement` is not allowed to see pixels. This is the
    single enforcement point for the project's central privacy claim, which is why it
    raises rather than warns.

    `frame` is encoded in memory and never written to disk.
    """
    if not backend.placement.may_see_pixels:
        raise PlacementViolation(
            f"refusing to send screen content to a {backend.placement.value} backend "
            f"({backend.model}). Set {backend.model}'s placement to 'local' or "
            f"'self_hosted', or route this call to a backend that is."
        )

    data_url = f"data:image/png;base64,{_encode_png(frame)}"
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    return await chat(
        backend, messages, reasoning_effort=reasoning_effort, **kwargs
    )


def _encode_png(frame: Any) -> str:
    """Encode a PIL Image to base64 PNG, in memory only."""
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_text(body: dict[str, Any]) -> str:
    """Pull assistant text out of a Chat Completions response.

    Tolerates the shape differences between providers: some return `content` as a string,
    others as a list of typed parts.
    """
    try:
        choice = body["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise BackendError(f"unexpected response shape: {str(body)[:300]}") from exc

    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        joined = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
        if joined.strip():
            return joined

    # Reasoning models spend budget thinking before they emit anything, so a tight
    # max_tokens yields a null content and a finish_reason of "length" — a silent,
    # confusing failure that looks like a broken endpoint rather than a cramped one.
    # Muse Spark Open does exactly this. Say so plainly instead of reporting a type.
    if choice.get("finish_reason") == "length":
        raise TruncatedBeforeAnswer(
            f"{backend_name(body)} ran out of tokens before emitting content. "
            "Reasoning models consume the budget thinking first — raise max_tokens, "
            "or lower reasoning effort."
        )

    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        raise BackendError(
            "model returned reasoning but no answer; raise max_tokens so it can "
            f"finish. Reasoning began: {reasoning.strip()[:120]}"
        )

    raise BackendError(
        f"model returned empty content (finish_reason="
        f"{choice.get('finish_reason')!r}); full response: {str(body)[:200]}"
    )


def backend_name(body: dict[str, Any]) -> str:
    return str(body.get("model") or "model")
