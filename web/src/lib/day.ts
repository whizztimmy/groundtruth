/**
 * Derivations the views share.
 *
 * The contract is kept verbatim in `api/types.ts`; the shapes the UI actually
 * wants — a timeline extent, a category legend, a session positioned as a
 * percentage of the day — are computed here so no component re-derives them and
 * the three views cannot disagree about the same day.
 */

import type { Day } from "../api/client";
import type { Session, SummaryGroup, UncertainSpan } from "../api/types";
import { UNCERTAIN, byCanonicalOrder, categoryColor } from "./categories";

/** A block positioned on the day timeline, in percentages of the tracked extent. */
export interface PositionedBlock {
  leftPct: number;
  widthPct: number;
}

export interface DayExtent {
  startMs: number;
  endMs: number;
  /** Whole-hour ticks across the extent, for the timeline's gridlines. */
  ticks: { ms: number; iso: string }[];
  position: (startIso: string, endIso: string) => PositionedBlock;
}

/**
 * The timeline spans the tracked day, snapped out to whole hours so the gridlines
 * land on readable times. It is derived from the data rather than fixed at 00:00–24:00:
 * a tracker that renders sixteen empty hours to show eight tracked ones wastes the
 * screen the demo is recorded on.
 */
export function dayExtent(sessions: Session[], uncertain: UncertainSpan[]): DayExtent {
  const stamps = [
    ...sessions.flatMap((s) => [Date.parse(s.start_ts), Date.parse(s.end_ts)]),
    ...uncertain.flatMap((u) => [Date.parse(u.start_ts), Date.parse(u.end_ts)]),
  ];
  const HOUR = 3_600_000;
  const first = stamps.length ? Math.min(...stamps) : Date.now();
  const last = stamps.length ? Math.max(...stamps) : first + HOUR;

  // Hours are snapped on the day's own clock, not on UTC. Snapping in UTC would
  // put the gridlines on :00 of the wrong timezone, so a day lived at +01:00 would
  // be ruled at 07:00, 08:00 ... while its sessions started at 08:40 — the blocks
  // would be positioned correctly and every label beside them would be an hour out.
  const offsetSource = sessions[0]?.start_ts ?? uncertain[0]?.start_ts ?? "";
  const offsetMs = offsetMinutesOf(offsetSource) * 60_000;
  const snap = (ms: number, round: (n: number) => number) =>
    round((ms + offsetMs) / HOUR) * HOUR - offsetMs;

  const startMs = snap(first, Math.floor);
  const endMs = snap(last, Math.ceil);
  const span = Math.max(1, endMs - startMs);
  const suffix = offsetSuffix(offsetSource);

  const ticks: { ms: number; iso: string }[] = [];
  for (let ms = startMs; ms <= endMs; ms += HOUR) {
    // Rebuilt in the day's offset so the label formats on the clock it was lived on.
    ticks.push({ ms, iso: new Date(ms + offsetMs).toISOString().slice(0, 19) + suffix });
  }

  return {
    startMs,
    endMs,
    ticks,
    position: (startIso, endIso) => {
      const s = Date.parse(startIso);
      const e = Date.parse(endIso);
      return {
        leftPct: ((s - startMs) / span) * 100,
        widthPct: (Math.max(0, e - s) / span) * 100,
      };
    },
  };
}

/** Minutes east of UTC carried by an ISO timestamp's own offset. */
function offsetMinutesOf(iso: string): number {
  const match = /([+-])(\d{2}):?(\d{2})$/.exec(iso);
  if (match) return (match[1] === "-" ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3]));
  return 0;
}

/** The offset exactly as the data wrote it, so rebuilt stamps stay in that zone. */
function offsetSuffix(iso: string): string {
  return /([+-]\d{2}:?\d{2})$/.exec(iso)?.[1] ?? "Z";
}

export interface LegendEntry {
  key: string;
  color: string;
  duration_s: number;
  share: number;
}

/**
 * The legend, in canonical category order rather than by size — a legend that
 * reorders itself between days is a legend you have to re-read every time.
 */
export function legendFor(groups: SummaryGroup[]): LegendEntry[] {
  return [...groups]
    .sort((a, b) => byCanonicalOrder(a.key, b.key))
    .map((g) => ({ key: g.key, color: categoryColor(g.key), duration_s: g.duration_s, share: g.share }));
}

/** Groups ordered the way the contrast view reads them: biggest claim first. */
export function groupsByDuration(groups: SummaryGroup[]): SummaryGroup[] {
  return [...groups].sort((a, b) => b.duration_s - a.duration_s);
}

/**
 * How many distinct activities the day resolved into, excluding the reserved
 * `uncertain` key — the uncertain total is a question, not an activity, and the
 * contrast view counts it separately.
 */
export function activityCount(groups: SummaryGroup[]): number {
  return groups.filter((g) => g.key !== UNCERTAIN && g.duration_s > 0).length;
}

export function uncertainTotal(groups: SummaryGroup[]): number {
  return groups.find((g) => g.key === UNCERTAIN)?.duration_s ?? 0;
}

/** The single largest process row — the figure the contrast view argues with. */
export function headlineProcess(day: Day): SummaryGroup | undefined {
  return groupsByDuration(day.summary.naive_by_process)[0];
}
