"""Tests for the durable buffer and the sync client.

The property that matters most is that events survive every failure mode short of losing
the disk. A tracker that drops an hour because the server was restarting is one the user
stops trusting, and trust is the whole product.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from groundtruth.buffer import EventBuffer
from groundtruth.sync import SyncClient, TitlePolicy, serialise
from groundtruth.types import Classification, Event, Tier

T0 = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


def event(n: int = 0, device: str = "dev-1") -> Event:
    return Event(
        ts=T0 + timedelta(seconds=n),
        device_id=device,
        tier=Tier.SCREEN,
        classification=Classification(
            label="reading documentation",
            category="research",
            confidence=0.88,
            evidence="Static page, low churn.",
        ),
        process="chrome.exe",
        window_title="Meta Model API docs - Q3 budget.xlsx",
    )


@pytest.fixture
def buffer(tmp_path):
    with EventBuffer(tmp_path / "events.db") as buf:
        yield buf


class TestBuffer:
    def test_append_and_take(self, buffer):
        assert buffer.append(event(0))
        assert buffer.count() == 1
        [(row_id, restored)] = buffer.take(10)
        assert isinstance(row_id, int)
        assert restored.classification.category == "research"
        assert restored.ts == T0

    def test_roundtrip_preserves_everything(self, buffer):
        buffer.append(event(0))
        [(_, restored)] = buffer.take(1)
        assert restored == event(0)

    def test_duplicate_is_ignored_not_raised(self, buffer):
        """A restart mid-sample can re-observe an instant. That is not fatal."""
        assert buffer.append(event(0))
        assert not buffer.append(event(0))
        assert buffer.count() == 1

    def test_same_ts_different_device_both_kept(self, buffer):
        buffer.append(event(0, device="dev-1"))
        buffer.append(event(0, device="dev-2"))
        assert buffer.count() == 2

    def test_take_is_fifo(self, buffer):
        for n in range(5):
            buffer.append(event(n))
        taken = [e.ts for _, e in buffer.take(3)]
        assert taken == [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)]

    def test_take_does_not_remove(self, buffer):
        """Removal happens on confirm. This gap is what makes delivery at-least-once."""
        buffer.append(event(0))
        buffer.take(10)
        assert buffer.count() == 1

    def test_confirm_removes(self, buffer):
        for n in range(3):
            buffer.append(event(n))
        ids = [row_id for row_id, _ in buffer.take(2)]
        assert buffer.confirm(ids) == 2
        assert buffer.count() == 1

    def test_confirm_empty_is_safe(self, buffer):
        assert buffer.confirm([]) == 0

    def test_survives_reopen(self, tmp_path):
        """The point of the whole module: a crash must not cost the day."""
        path = tmp_path / "events.db"
        with EventBuffer(path) as buf:
            buf.append(event(0))
            buf.append(event(1))
        with EventBuffer(path) as reopened:
            assert reopened.count() == 2

    def test_prune_keeps_newest(self, buffer):
        for n in range(10):
            buffer.append(event(n))
        buffer.prune(keep_newest=3)
        assert buffer.count() == 3
        remaining = [e.ts for _, e in buffer.take(10)]
        assert remaining[0] == T0 + timedelta(seconds=7)


class TestSerialise:
    def test_default_omits_titles_and_process(self):
        """A default that leaks is a bug, however convenient."""
        body = serialise(event(0))
        assert "window_title" not in body
        assert "process" not in body
        assert body["category"] == "research"

    def test_process_only(self):
        body = serialise(event(0), titles=TitlePolicy.PROCESS_ONLY)
        assert body["process"] == "chrome.exe"
        assert "window_title" not in body

    def test_full_includes_title(self):
        body = serialise(event(0), titles=TitlePolicy.FULL)
        assert "Q3 budget.xlsx" in body["window_title"]

    def test_timestamp_carries_offset(self):
        assert serialise(event(0))["ts"].endswith("+00:00")


class TestSyncClient:
    def make(self, buffer, handler, **kwargs):
        transport = httpx.MockTransport(handler)
        return SyncClient(
            server_url="http://server.invalid",
            device_id="dev-1",
            buffer=buffer,
            client=httpx.AsyncClient(transport=transport),
            **kwargs,
        )

    async def test_empty_buffer_is_success_not_failure(self, buffer):
        client = self.make(buffer, lambda r: httpx.Response(202, json={}))
        result = await client.flush_once()
        assert result.sent == 0

    async def test_successful_flush_clears_buffer(self, buffer):
        for n in range(3):
            buffer.append(event(n))

        def handler(request):
            return httpx.Response(202, json={"accepted": 3, "duplicates": 0})

        result = await self.make(buffer, handler).flush_once()
        assert result.accepted == 3
        assert buffer.count() == 0

    async def test_network_failure_keeps_everything(self, buffer):
        """The property the whole module exists for."""
        buffer.append(event(0))

        def handler(request):
            raise httpx.ConnectError("offline")

        result = await self.make(buffer, handler).flush_once()
        assert not result.ok
        assert buffer.count() == 1

    async def test_server_error_keeps_everything(self, buffer):
        buffer.append(event(0))
        handler = lambda r: httpx.Response(500, text="boom")  # noqa: E731
        result = await self.make(buffer, handler).flush_once()
        assert not result.ok
        assert buffer.count() == 1

    async def test_duplicates_count_as_delivered(self, buffer):
        """Re-sending after a crash must not strand events forever."""
        buffer.append(event(0))
        handler = lambda r: httpx.Response(  # noqa: E731
            202, json={"accepted": 0, "duplicates": 1}
        )
        result = await self.make(buffer, handler).flush_once()
        assert result.ok
        assert buffer.count() == 0

    async def test_respects_batch_size(self, buffer):
        for n in range(10):
            buffer.append(event(n))
        seen = []

        def handler(request):
            import json

            seen.append(len(json.loads(request.content)["events"]))
            return httpx.Response(202, json={"accepted": 4, "duplicates": 0})

        await self.make(buffer, handler, batch_size=4).flush_once()
        assert seen == [4]
        assert buffer.count() == 6

    async def test_flush_all_drains(self, buffer):
        for n in range(10):
            buffer.append(event(n))
        handler = lambda r: httpx.Response(  # noqa: E731
            202, json={"accepted": 4, "duplicates": 0}
        )
        result = await self.make(buffer, handler, batch_size=4).flush_all()
        assert result.sent == 10
        assert buffer.count() == 0

    async def test_flush_all_stops_on_failure(self, buffer):
        """A dead server must not spin through twenty batches every interval."""
        for n in range(10):
            buffer.append(event(n))
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(503)

        await self.make(buffer, handler, batch_size=4).flush_all()
        assert len(calls) == 1
        assert buffer.count() == 10

    def test_rejects_unknown_title_policy(self, buffer):
        with pytest.raises(ValueError, match="not one of"):
            SyncClient(
                server_url="http://x.invalid",
                device_id="d",
                buffer=buffer,
                titles="everything",
            )
