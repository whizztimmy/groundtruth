/**
 * Wire types for the sync API in `docs/API.md`.
 *
 * This file is a transcription of a frozen contract, so it deliberately mirrors
 * the server's field names and units rather than reshaping them into something
 * more convenient. Durations are seconds; timestamps are ISO 8601 with an
 * explicit offset. Anything the UI finds more useful in another shape is derived
 * at the edge (see `lib/day.ts`) so the contract stays recognisable here.
 */

/** `GET /sessions` — a derived span. Sessions are never written by clients. */
export interface Session {
  id: string;
  start_ts: string;
  end_ts: string;
  device_id: string;
  category: string;
  label: string;
  /** 0..1. The agent accepts at >= 0.75 and retries below 0.45. */
  confidence: number;
  event_count: number;
  duration_s: number;
  evidence_summary: string;
}

export interface SessionsResponse {
  sessions: Session[];
}

/** One row of either decomposition in `GET /summary`. */
export interface SummaryGroup {
  key: string;
  duration_s: number;
  /** 0..1, as computed by the server against `total_tracked_s`. */
  share: number;
}

export interface Summary {
  from: string;
  to: string;
  total_tracked_s: number;
  /** The real decomposition: by activity category (or by label, with `group_by`). */
  groups: SummaryGroup[];
  /** What a conventional app-name tracker would have reported for the same window. */
  naive_by_process: SummaryGroup[];
}

/** `GET /uncertain` — a span the classifier declined to guess at. */
export interface UncertainSpan {
  id: string;
  start_ts: string;
  end_ts: string;
  process: string;
  evidence: string;
  suggestions: string[];
}

export interface UncertainResponse {
  spans: UncertainSpan[];
}

/** Body of `POST /uncertain/{id}/resolve`. */
export interface ResolveBody {
  category: string;
  label: string;
}

/** `POST /uncertain/{id}/resolve` responds with the re-segmented session. */
export type ResolveResponse = Session;

/** RFC 7807 problem+json, which the contract specifies for errors. */
export interface ProblemDetail {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
}
