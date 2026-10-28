/**
 * Generates `src/data/day.json` — one realistic tracked day, shaped exactly like
 * the responses in `docs/API.md`.
 *
 * The day is authored once, as an ordered run of contiguous segments, and every
 * published figure is then *computed* from it: category totals, process totals and
 * shares all derive from the same source, so the fixture cannot drift out of
 * internal consistency. Nothing in the dashboard invents a number.
 *
 * The totals are tuned to land on the worked example in the frozen contract and in
 * the README, so the fixture demonstrates the documented numbers rather than a
 * second, contradictory set:
 *
 *   total_tracked_s 28800 (8h) · deep work 6000 · doomscrolling 9060 · chrome.exe 15120
 *
 * Run with `npm run fixture`. Replace this file's output with the server's
 * `server/fixtures/day.json` once that lands; the loader in `src/api/client.ts`
 * accepts either.
 */

import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

const DEVICE = "desk-win-01";
/** The day is rendered in the offset it was lived in, not the viewer's. */
const OFFSET = "+01:00";
const DAY = "2026-10-27"; // Tuesday
const START_LOCAL = "08:40:00";

/**
 * The authored day. `kind: "uncertain"` segments are spans the classifier declined
 * to label: they are tracked time, so they count toward the day's total and appear
 * as a group, but they are deliberately absent from /sessions — the segmenter never
 * merges an uncertain span into a neighbour. The holes they leave in the timeline
 * are the point: the system shows what it does not know.
 *
 * `process` is the foreground application during the segment. Sessions do not carry
 * it (the contract's Session has no process field), but a conventional tracker sees
 * nothing else, so it is what `naive_by_process` is computed from.
 */
const SEGMENTS = [
  { s: 1500, kind: "session", category: "devotion", label: "morning scripture reading", process: "chrome.exe", confidence: 0.91,
    evidence: "A single passage held in one content region for the whole span; near-zero churn and no scroll velocity." },
  { s: 660, kind: "session", category: "communication", label: "triaging overnight mail", process: "outlook.exe", confidence: 0.94,
    evidence: "Message list and reading pane alternating focus; short dwell per item, consistent with triage rather than composition." },
  { s: 2460, kind: "session", category: "research", label: "reading Muse Glimmer API documentation", process: "chrome.exe", confidence: 0.89,
    evidence: "Static documentation pages across three tabs, code samples visible, low region churn throughout." },
  { s: 900, kind: "session", category: "shallow work", label: "tidying branches and stale files", process: "Code.exe", confidence: 0.81,
    evidence: "Editor focused but no sustained edit region; file tree and terminal taking turns in short bursts." },
  { s: 3600, kind: "session", category: "deep work", label: "auth refactor — token rotation", process: "Code.exe", confidence: 0.95,
    evidence: "One editor region held focus for the full hour with steady keystroke cadence and no feed region on screen." },
  { s: 660, kind: "session", category: "idle", label: "machine awake, no input", process: "Code.exe", confidence: 0.97,
    evidence: "Frame unchanged between samples and no input events; foreground window never lost focus." },
  { s: 1080, kind: "session", category: "communication", label: "stand-up call", process: "Teams.exe", confidence: 0.93,
    evidence: "Video tiles tracked as persistent regions with a shared-screen region appearing midway; no text entry." },
  { s: 900, kind: "uncertain", process: "explorer.exe",
    evidence: "File manager held over a downloads folder; no content region kept focus long enough to read.",
    suggestions: ["admin", "research"] },
  { s: 2460, kind: "session", category: "doomscrolling", label: "scrolling a social feed", process: "chrome.exe", confidence: 0.92,
    evidence: "High churn in a fixed-width content column for 41 minutes; view replaced continuously with no dwell." },
  { s: 720, kind: "session", category: "idle", label: "away from desk", process: "chrome.exe", confidence: 0.96,
    evidence: "No input and no region change across twelve consecutive samples while a browser window held focus." },
  { s: 420, kind: "session", category: "admin", label: "expense receipts", process: "chrome.exe", confidence: 0.84,
    evidence: "Form regions with short-lived input focus and a repeated upload dialog; no reading dwell." },
  { s: 2400, kind: "session", category: "deep work", label: "auth refactor — second pass", process: "Code.exe", confidence: 0.93,
    evidence: "Same editor region as the morning span, diff view opened twice, terminal used for targeted test runs." },
  { s: 420, kind: "uncertain", process: "WindowsTerminal.exe",
    evidence: "A long-running build scrolled output continuously while no input arrived — churn reads like a feed.",
    suggestions: ["deep work", "shallow work"] },
  { s: 1680, kind: "session", category: "research", label: "comparing inference providers", process: "chrome.exe", confidence: 0.86,
    evidence: "Two pricing pages and a benchmark table held side by side; repeated back-and-forth between fixed regions." },
  { s: 4740, kind: "session", category: "doomscrolling", label: "scrolling a video feed", process: "chrome.exe", confidence: 0.9,
    evidence: "Autoplaying player region replaced every few seconds for 79 minutes; churn sustained, no pause or seek." },
  { s: 1860, kind: "session", category: "doomscrolling", label: "scrolling a photo feed", process: "Instagram.exe", confidence: 0.88,
    evidence: "Fixed-aspect media regions cycling under continuous scroll; no comment entry and no profile dwell." },
  { s: 1200, kind: "session", category: "learning", label: "working through a SAM 3.1 notebook", process: "Code.exe", confidence: 0.87,
    evidence: "Notebook cells executed top to bottom with output regions read between runs; progression, not revision." },
  { s: 780, kind: "session", category: "entertainment", label: "watching a series episode", process: "chrome.exe", confidence: 0.9,
    evidence: "One full-width player region held for the whole span with no scrolling and a single deliberate seek." },
  { s: 360, kind: "uncertain", process: "chrome.exe",
    evidence: "Mixed content: a documentation tab and a feed tab alternated faster than the sampler could attribute.",
    suggestions: ["research", "doomscrolling"] },
];

// --- derive ------------------------------------------------------------------

const offsetMinutes = (() => {
  const [, sign, h, m] = /^([+-])(\d{2}):(\d{2})$/.exec(OFFSET);
  return (sign === "-" ? -1 : 1) * (Number(h) * 60 + Number(m));
})();

/** Format an instant as ISO 8601 in the day's own offset, as the contract requires. */
function iso(epochMs) {
  const shifted = new Date(epochMs + offsetMinutes * 60_000);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return (
    `${shifted.getUTCFullYear()}-${p(shifted.getUTCMonth() + 1)}-${p(shifted.getUTCDate())}` +
    `T${p(shifted.getUTCHours())}:${p(shifted.getUTCMinutes())}:${p(shifted.getUTCSeconds())}${OFFSET}`
  );
}

const dayStartMs = Date.parse(`${DAY}T00:00:00${OFFSET}`);
let cursorMs = Date.parse(`${DAY}T${START_LOCAL}${OFFSET}`);

/** Stable, readable ids. ULID-shaped like the contract's examples, but deterministic. */
const idFor = (prefix, n) => `${prefix}_01JB${String(n).padStart(3, "0")}${prefix === "s" ? "SESS" : "UNCR"}`;

const sessions = [];
const uncertain = [];
const byCategory = new Map();
const byProcess = new Map();

const add = (map, key, seconds) => map.set(key, (map.get(key) ?? 0) + seconds);

SEGMENTS.forEach((seg, i) => {
  const startMs = cursorMs;
  const endMs = startMs + seg.s * 1000;
  cursorMs = endMs;

  add(byProcess, seg.process, seg.s);

  if (seg.kind === "uncertain") {
    add(byCategory, "uncertain", seg.s);
    uncertain.push({
      id: idFor("u", uncertain.length + 1),
      start_ts: iso(startMs),
      end_ts: iso(endMs),
      process: seg.process,
      evidence: seg.evidence,
      suggestions: seg.suggestions,
    });
    return;
  }

  add(byCategory, seg.category, seg.s);
  sessions.push({
    id: idFor("s", sessions.length + 1),
    start_ts: iso(startMs),
    end_ts: iso(endMs),
    device_id: DEVICE,
    category: seg.category,
    label: seg.label,
    confidence: seg.confidence,
    // One sample every ~3 minutes, with the first sample opening the session.
    event_count: Math.max(1, Math.round(seg.s / 180) + (i % 3) - 1),
    duration_s: seg.s,
    evidence_summary: seg.evidence,
  });
});

const total = SEGMENTS.reduce((sum, seg) => sum + seg.s, 0);

/** Shares are computed once, here, against the same total the server publishes. */
const toGroups = (map) =>
  [...map.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([key, duration_s]) => ({ key, duration_s, share: Number((duration_s / total).toFixed(4)) }));

const summary = {
  from: iso(dayStartMs),
  to: iso(dayStartMs + 86_399_000),
  total_tracked_s: total,
  groups: toGroups(byCategory),
  naive_by_process: toGroups(byProcess),
};

// --- check -------------------------------------------------------------------
// The fixture is the dashboard's only data until the server lands, so it is
// verified rather than trusted: a fixture that does not add up would put a wrong
// number on screen, which is the one thing this UI must never do.

const problems = [];
const sum = (rows) => rows.reduce((a, r) => a + r.duration_s, 0);

if (sum(summary.groups) !== total) problems.push(`groups sum ${sum(summary.groups)} != total ${total}`);
if (sum(summary.naive_by_process) !== total) problems.push(`naive sum ${sum(summary.naive_by_process)} != total ${total}`);

const sessionsTotal = sessions.reduce((a, s) => a + s.duration_s, 0);
const uncertainTotal = uncertain.reduce((a, u) => a + (Date.parse(u.end_ts) - Date.parse(u.start_ts)) / 1000, 0);
if (sessionsTotal + uncertainTotal !== total)
  problems.push(`sessions ${sessionsTotal} + uncertain ${uncertainTotal} != total ${total}`);

// Contiguity: a gap or overlap would make the timeline lie about where time went.
const spans = [...sessions, ...uncertain]
  .map((x) => [Date.parse(x.start_ts), Date.parse(x.end_ts)])
  .sort((a, b) => a[0] - b[0]);
spans.forEach(([start], i) => {
  if (i > 0 && spans[i - 1][1] !== start)
    problems.push(`segment ${i} starts at ${new Date(start).toISOString()}, previous ended ${new Date(spans[i - 1][1]).toISOString()}`);
});

for (const s of sessions) {
  if ((Date.parse(s.end_ts) - Date.parse(s.start_ts)) / 1000 !== s.duration_s)
    problems.push(`${s.id}: duration_s disagrees with its timestamps`);
  // The agent accepts a classification at >= 0.75; anything below is retried or
  // stored as uncertain, so an accepted session cannot sit under the threshold.
  if (s.confidence < 0.75) problems.push(`${s.id}: confidence ${s.confidence} below the ACCEPT threshold`);
}

if (problems.length) {
  console.error("fixture is inconsistent:");
  for (const p of problems) console.error("  -", p);
  process.exit(1);
}

const out = join(HERE, "..", "src", "data", "day.json");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify({ summary, sessions, uncertain: { spans: uncertain } }, null, 2) + "\n");

const hm = (s) => `${Math.floor(s / 3600)}h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}m`;
console.log(`wrote ${out}`);
console.log(`  ${sessions.length} sessions, ${uncertain.length} uncertain spans, ${hm(total)} tracked`);
console.log(`  categories: ${summary.groups.map((g) => `${g.key} ${hm(g.duration_s)}`).join(" · ")}`);
console.log(`  processes:  ${summary.naive_by_process.map((g) => `${g.key} ${hm(g.duration_s)}`).join(" · ")}`);
