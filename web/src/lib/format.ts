/**
 * Formatting helpers. Every figure the UI shows passes through here.
 *
 * Times are rendered in *the day's own UTC offset*, not the viewer's timezone. A
 * day of someone's activity belongs to the clock they lived it on: a span logged
 * at 08:40+01:00 should read 08:40 whether it is opened in Lagos or Seattle, and a
 * demo recording should not shift its hours with the machine it plays on. The
 * contract guarantees an explicit offset on every timestamp, which is what makes
 * this possible.
 */

/** Minutes east of UTC carried by an ISO 8601 timestamp's own offset. */
function offsetMinutes(iso: string): number {
  const match = /([+-])(\d{2}):?(\d{2})$/.exec(iso);
  if (match) {
    const sign = match[1] === "-" ? -1 : 1;
    return sign * (Number(match[2]) * 60 + Number(match[3]));
  }
  if (/[zZ]$/.test(iso)) return 0;
  // The contract requires an explicit offset. Fall back to the viewer's zone
  // rather than throwing, so one malformed row cannot blank the dashboard.
  return -new Date(iso).getTimezoneOffset();
}

/** The instant, shifted so UTC getters read the wall clock of its own offset. */
function wallClock(iso: string): Date {
  return new Date(Date.parse(iso) + offsetMinutes(iso) * 60_000);
}

const TIME = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC",
});
const DAY = new Intl.DateTimeFormat("en-GB", {
  weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
});

export function clockTime(iso: string): string {
  return TIME.format(wallClock(iso));
}

export function dayLabel(iso: string): string {
  return DAY.format(wallClock(iso));
}

export function timeRange(startIso: string, endIso: string): string {
  return `${clockTime(startIso)}–${clockTime(endIso)}`;
}

/** The day's offset, shown once in the header so the clock is never ambiguous. */
export function offsetLabel(iso: string): string {
  const minutes = offsetMinutes(iso);
  const sign = minutes < 0 ? "-" : "+";
  const abs = Math.abs(minutes);
  return `UTC${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

/**
 * Durations read as "4h 12m" — the unit the product argues about. Below a minute
 * seconds are shown, because rounding to "0m" would read as nothing at all.
 */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (m === 60) return `${h + 1}h 00m`;
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** Compact form for dense places — tooltips, tick labels, inline counts. */
export function durationCompact(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  if (m === 60) return `${h + 1}h`;
  return `${h}h ${m}m`;
}

/** Shares arrive as 0..1 from the server and are never recomputed here. */
export function percent(share: number, digits = 0): string {
  return `${(share * 100).toFixed(digits)}%`;
}

export function confidencePercent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export { wallClock };
