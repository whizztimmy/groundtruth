# Sync API contract

Frozen before parallel work started, so the agent, the server and the dashboard can be
built independently. Changes here need to land before the components that depend on them.

Base: `GROUNDTRUTH_SERVER`, default `http://127.0.0.1:8000`.

All timestamps are ISO 8601 with an explicit UTC offset. All durations are seconds.

## Principles

- **Events are append-only and device-owned.** A device writes only its own events, so
  there are no write conflicts to resolve.
- **Sessions are derived, never written by clients.** The server segments events into
  sessions, which means a taxonomy change re-segments history instead of losing it.
- **The server never sees pixels.** No endpoint accepts an image.

## `POST /events`

Append a batch of events. Idempotent on `(device_id, ts)` — a retried batch after a
flaky upload is safe, which matters because the agent is offline-first.

```json
{
  "device_id": "desk-win-01",
  "events": [
    {
      "ts": "2026-10-28T09:14:02Z",
      "tier": 2,
      "process": "chrome.exe",
      "window_title": "Meta Model API docs",
      "label": "reading API documentation",
      "category": "research",
      "confidence": 0.88,
      "evidence": "Static documentation page, low region churn, code samples visible."
    }
  ]
}
```

Response `202`:

```json
{ "accepted": 1, "duplicates": 0 }
```

## `GET /sessions`

Derived spans. This is what the dashboard renders.

Query: `from`, `to` (ISO 8601), optional `device_id`, optional `category`.

```json
{
  "sessions": [
    {
      "id": "s_01JB...",
      "start_ts": "2026-10-28T09:12:00Z",
      "end_ts": "2026-10-28T09:51:30Z",
      "device_id": "desk-win-01",
      "category": "research",
      "label": "reading API documentation",
      "confidence": 0.86,
      "event_count": 14,
      "duration_s": 2370,
      "evidence_summary": "Documentation pages across three tabs, low churn throughout."
    }
  ]
}
```

## `GET /summary`

Totals for a window, for the headline comparison view.

Query: `from`, `to`, optional `group_by` of `category` (default) or `label`.

```json
{
  "from": "2026-10-28T00:00:00Z",
  "to": "2026-10-28T23:59:59Z",
  "total_tracked_s": 28800,
  "groups": [
    { "key": "deep work", "duration_s": 6000, "share": 0.21 },
    { "key": "doomscrolling", "duration_s": 9060, "share": 0.31 }
  ],
  "naive_by_process": [
    { "key": "chrome.exe", "duration_s": 15120, "share": 0.52 }
  ]
}
```

`naive_by_process` is what a conventional tracker would have reported for the same window.
It exists so the dashboard can show both side by side — that contrast is the product's
entire argument, so the server computes it rather than leaving it to the client.

## `GET /uncertain`

Spans the classifier declined to guess at, newest first.

```json
{
  "spans": [
    {
      "id": "u_01JB...",
      "start_ts": "2026-10-28T14:02:00Z",
      "end_ts": "2026-10-28T14:09:00Z",
      "process": "chrome.exe",
      "evidence": "Mixed content; could be research or browsing.",
      "suggestions": ["research", "doomscrolling"]
    }
  ]
}
```

## `POST /uncertain/{id}/resolve`

The user answers. The answer is stored as a few-shot example for that process, which is
how the taxonomy converges on the individual.

```json
{ "category": "research", "label": "comparing inference providers" }
```

Response `200`: the re-segmented session.

## Errors

Conventional problem+json. `409` only on a genuine device-id collision; event duplicates
are counted in the success response rather than raised, because the agent retries by
design.
