"""Pushes buffered events to the sync server.

Batched, because a flaky network should cost one retry rather than one per sample, and
because the agent appends every two seconds while the server has no reason to hear from
it that often.

Never blocks capture. A failed flush leaves the buffer untouched and returns; the next
interval tries again. The one thing this module must not do is lose events, so nothing is
deleted until the server has confirmed receipt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .buffer import EventBuffer
from .types import Event

log = logging.getLogger("groundtruth.sync")


class TitlePolicy:
    """How much of a window title to send.

    Window titles are the most revealing thing the agent collects. "Chrome" is harmless;
    "Q3 layoffs - final list.xlsx" is not, and it is the kind of string that ends up in a
    title bar constantly. The privacy model in `ARCHITECTURE.md` promises that only
    derived labels are synced, and a raw title is not a derived label.

    So this is explicit rather than incidental. The default sends nothing, because a
    default that leaks is a bug no matter how convenient it is.
    """

    NONE = "none"
    """Drop titles entirely. The default."""

    PROCESS_ONLY = "process_only"
    """Send the process name but never the title. Enough to group by application."""

    FULL = "full"
    """Send titles verbatim. Useful while debugging the classifier; appropriate only on a
    server the user operates, and worth a deliberate decision either way."""

    ALL = (NONE, PROCESS_ONLY, FULL)


@dataclass(frozen=True, slots=True)
class SyncResult:
    sent: int
    accepted: int
    duplicates: int

    @property
    def ok(self) -> bool:
        return self.sent > 0 and self.accepted + self.duplicates >= self.sent


def serialise(event: Event, *, titles: str = TitlePolicy.NONE) -> dict:
    """Event to the wire shape in `docs/API.md`.

    Timestamps carry an explicit offset; the contract requires it, and a naive timestamp
    from a laptop that crossed a timezone is unrecoverable once stored.
    """
    c = event.classification
    body = {
        "ts": event.ts.isoformat(),
        "tier": int(event.tier),
        "label": c.label,
        "category": c.category,
        "confidence": c.confidence,
        "evidence": c.evidence,
    }
    if titles in (TitlePolicy.PROCESS_ONLY, TitlePolicy.FULL):
        body["process"] = event.process
    if titles == TitlePolicy.FULL:
        body["window_title"] = event.window_title
    return body


class SyncClient:
    """Drains an `EventBuffer` to the server in batches."""

    def __init__(
        self,
        *,
        server_url: str,
        device_id: str,
        buffer: EventBuffer,
        batch_size: int = 50,
        titles: str = TitlePolicy.NONE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if titles not in TitlePolicy.ALL:
            raise ValueError(
                f"titles={titles!r} is not one of {', '.join(TitlePolicy.ALL)}"
            )
        self._server_url = server_url.rstrip("/")
        self._device_id = device_id
        self._buffer = buffer
        self._batch_size = batch_size
        self._titles = titles
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def flush_once(self) -> SyncResult:
        """Send at most one batch.

        Returns a zero result when there is nothing pending — callers treat that as
        success, not as a failed send.
        """
        batch = self._buffer.take(self._batch_size)
        if not batch:
            return SyncResult(0, 0, 0)

        ids = [row_id for row_id, _ in batch]
        payload = {
            "device_id": self._device_id,
            "events": [
                serialise(event, titles=self._titles) for _, event in batch
            ],
        }

        client = await self._http()
        try:
            response = await client.post(f"{self._server_url}/events", json=payload)
        except httpx.TransportError as exc:
            # Offline is the expected case, not an exception worth escalating. The
            # buffer is untouched and the next interval retries.
            log.debug("sync unreachable (%s); %d events still buffered", exc, len(ids))
            return SyncResult(len(ids), 0, 0)

        if response.status_code >= 400:
            log.warning(
                "sync rejected batch (%d): %s", response.status_code, response.text[:200]
            )
            return SyncResult(len(ids), 0, 0)

        body = response.json() if response.content else {}
        accepted = int(body.get("accepted", len(ids)))
        duplicates = int(body.get("duplicates", 0))

        # Confirm only after the server has it. A crash before this line resends the
        # batch, which the idempotent endpoint absorbs.
        self._buffer.confirm(ids)
        log.info(
            "synced %d events (%d new, %d duplicate), %d remaining",
            len(ids),
            accepted,
            duplicates,
            self._buffer.count(),
        )
        return SyncResult(len(ids), accepted, duplicates)

    async def flush_all(self, *, max_batches: int = 20) -> SyncResult:
        """Drain the buffer, bounded.

        Bounded because a device returning from a long offline stretch could otherwise
        monopolise the loop; the remainder goes out on the next interval.
        """
        sent = accepted = duplicates = 0
        for _ in range(max_batches):
            result = await self.flush_once()
            if result.sent == 0:
                break
            sent += result.sent
            accepted += result.accepted
            duplicates += result.duplicates
            if not result.ok:
                break
        return SyncResult(sent, accepted, duplicates)
