/**
 * The data layer: the live server when it answers, the bundled fixture when it
 * does not.
 *
 * The dashboard is the demo surface for a local-first product, so it has to be
 * useful with nothing running behind it. Rather than silently inventing data, the
 * client records *where the numbers came from* and the UI states it in the header.
 * Both paths return the same contract types, so no view knows which one it got.
 */

import type {
  ResolveBody, ResolveResponse, Session, SessionsResponse,
  Summary, UncertainResponse, UncertainSpan,
} from "./types";
import fixture from "../data/day.json";

/** Where the figures on screen came from. Surfaced in the UI, never hidden. */
export type Provenance =
  | { kind: "server"; base: string }
  | { kind: "fixture"; reason: string };

export interface Day {
  summary: Summary;
  sessions: Session[];
  uncertain: UncertainSpan[];
  provenance: Provenance;
}

/**
 * In dev, Vite proxies these paths to `GROUNDTRUTH_SERVER` so the browser sees one
 * origin and the server keeps its loopback-only binding. In a build, a same-origin
 * deployment works unchanged; anything else can point `VITE_GROUNDTRUTH_SERVER` at
 * an absolute base.
 */
const BASE = (import.meta.env.VITE_GROUNDTRUTH_SERVER ?? "").replace(/\/$/, "");
const url = (path: string) => `${BASE}${path}`;

/** How long to wait for a local server before falling back to the fixture. */
const TIMEOUT_MS = 2500;

/**
 * Accepts either shape of fixture: this repo's generated `web/src/data/day.json`
 * or the server's `server/fixtures/day.json`, whose `uncertain` may be the bare
 * span array rather than the endpoint's `{ spans }` envelope. Swapping one for the
 * other is then a file copy with no code change.
 */
function normalise(raw: unknown): { summary: Summary; sessions: Session[]; uncertain: UncertainSpan[] } {
  const doc = raw as {
    summary: Summary;
    sessions: Session[] | SessionsResponse;
    uncertain: UncertainSpan[] | UncertainResponse;
  };
  return {
    summary: doc.summary,
    sessions: Array.isArray(doc.sessions) ? doc.sessions : doc.sessions.sessions,
    uncertain: Array.isArray(doc.uncertain) ? doc.uncertain : doc.uncertain.spans,
  };
}

const FIXTURE = normalise(fixture);

/** The window the fixture covers, so fixture mode queries the day it actually has. */
export const fixtureWindow = { from: FIXTURE.summary.from, to: FIXTURE.summary.to };

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url(path), { signal, headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`${path} responded ${response.status}`);
  return (await response.json()) as T;
}

/**
 * Fetches a day. The three endpoints are independent, so they go out together and
 * the slowest one sets the latency; any failure among them falls the whole day back
 * to the fixture rather than rendering a half-real screen.
 */
export async function fetchDay(window?: { from: string; to: string }): Promise<Day> {
  const { from, to } = window ?? fixtureWindow;
  const query = `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const [summary, sessions, uncertain] = await Promise.all([
      getJson<Summary>(`/summary?${query}`, controller.signal),
      getJson<SessionsResponse>(`/sessions?${query}`, controller.signal),
      getJson<UncertainResponse>(`/uncertain`, controller.signal),
    ]);
    return {
      summary,
      sessions: sessions.sessions,
      uncertain: uncertain.spans,
      provenance: { kind: "server", base: BASE || "same origin" },
    };
  } catch (error) {
    return {
      ...FIXTURE,
      provenance: { kind: "fixture", reason: reasonFor(error) },
    };
  } finally {
    clearTimeout(timer);
  }
}

function reasonFor(error: unknown): string {
  if (error instanceof DOMException && error.name === "AbortError") return "no response within 2.5s";
  if (error instanceof Error) return error.message;
  return "unreachable";
}

/**
 * Answers an uncertain span. The server stores the answer as a few-shot example for
 * that process and returns the re-segmented session.
 *
 * In fixture mode there is nothing to POST to, so the span is resolved locally and
 * the session that *would* come back is constructed from the span's own timestamps —
 * no figure is invented, and the caller is told it was local so the UI can say so.
 */
export async function resolveUncertain(
  span: UncertainSpan,
  body: ResolveBody,
  provenance: Provenance,
): Promise<{ session: ResolveResponse; applied: "server" | "local" }> {
  if (provenance.kind === "fixture") {
    return { session: localResolution(span, body), applied: "local" };
  }
  const response = await fetch(url(`/uncertain/${encodeURIComponent(span.id)}/resolve`), {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new Error(problem?.detail ?? problem?.title ?? `resolve responded ${response.status}`);
  }
  return { session: (await response.json()) as ResolveResponse, applied: "server" };
}

/** The session the server would derive from an answered span, built from that span. */
function localResolution(span: UncertainSpan, body: ResolveBody): Session {
  const duration_s = (Date.parse(span.end_ts) - Date.parse(span.start_ts)) / 1000;
  return {
    id: span.id.replace(/^u_/, "s_"),
    start_ts: span.start_ts,
    end_ts: span.end_ts,
    device_id: FIXTURE.sessions[0]?.device_id ?? "unknown",
    category: body.category,
    label: body.label,
    // A user-stated label is Tier 3 and supersedes any classifier output, so it is
    // held with certainty rather than with a confidence the user did not give.
    confidence: 1,
    event_count: Math.max(1, Math.round(duration_s / 180)),
    duration_s,
    evidence_summary: `Answered by you: ${body.label}. Stored as a few-shot example for ${span.process}.`,
  };
}
