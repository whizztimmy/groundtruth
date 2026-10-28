import { useMemo, useRef, useState } from "react";

import type { Day } from "../api/client";
import type { Session, UncertainSpan } from "../api/types";
import { UNCERTAIN, categoryColor } from "../lib/categories";
import { dayExtent, legendFor } from "../lib/day";
import { clockTime, dayLabel, duration, durationCompact, offsetLabel, percent, timeRange } from "../lib/format";
import { ConfidenceMeter } from "./ConfidenceMeter";
import "./TimelineView.css";

interface Props {
  day: Day;
}

type Hovered =
  | { kind: "session"; session: Session; centerPct: number }
  | { kind: "uncertain"; span: UncertainSpan; centerPct: number };

/**
 * The day as a timeline: one lane of sessions over an hour ruler, with the
 * unanswered spans on their own track beneath.
 *
 * Sessions and uncertain spans are drawn on separate tracks rather than mixed into
 * one, because they are different kinds of claim. A session is a label the
 * classifier will stand behind; an uncertain span is an admission. The gaps the
 * admissions leave in the session lane are legible on purpose — you can see what
 * the system does not know, which is the opposite of how a tracker usually reads.
 */
export function TimelineView({ day }: Props) {
  const { summary, sessions, uncertain } = day;
  const extent = useMemo(() => dayExtent(sessions, uncertain), [sessions, uncertain]);
  const legend = useMemo(() => legendFor(summary.groups), [summary.groups]);
  const [hovered, setHovered] = useState<Hovered | null>(null);
  const laneRef = useRef<HTMLDivElement>(null);

  return (
    <section className="tl">
      <header className="tl__head">
        <div>
          <h1 className="tl__title">{dayLabel(summary.from)}</h1>
          <p className="tl__sub">
            {sessions.length} sessions over <span className="tnum">{duration(summary.total_tracked_s)}</span>
            {uncertain.length > 0 && (
              <>
                {" · "}
                <span className="tnum">{uncertain.length}</span> span{uncertain.length === 1 ? "" : "s"} unanswered
              </>
            )}
            {" · "}
            <span className="tl__offset">{offsetLabel(summary.from)}</span>
          </p>
        </div>
        <p className="tl__hint">Hover or focus a block for its evidence.</p>
      </header>

      <div className="tl__card card">
        <div className="tl__scroll">
          <div className="tl__inner">
            {/* Ruler first, so the lanes below are read against a scale. */}
            <div className="tl__ruler" aria-hidden="true">
              {extent.ticks.map((tick, index) => {
                const pct = ((tick.ms - extent.startMs) / (extent.endMs - extent.startMs)) * 100;
                return (
                  <div key={tick.ms} className="tl__tick" style={{ left: `${pct}%` }}>
                    <span className="tl__tickLine" />
                    {index < extent.ticks.length - 1 && (
                      <span className="tl__tickLabel tnum">{clockTime(tick.iso)}</span>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="tl__lane" ref={laneRef} onMouseLeave={() => setHovered(null)}>
              {extent.ticks.map((tick) => {
                const pct = ((tick.ms - extent.startMs) / (extent.endMs - extent.startMs)) * 100;
                return <span key={tick.ms} className="tl__grid" style={{ left: `${pct}%` }} aria-hidden="true" />;
              })}

              {sessions.map((session) => {
                const { leftPct, widthPct } = extent.position(session.start_ts, session.end_ts);
                const active = hovered?.kind === "session" && hovered.session.id === session.id;
                return (
                  <button
                    key={session.id}
                    type="button"
                    className={`tl__block${active ? " tl__block--active" : ""}`}
                    style={{
                      left: `${leftPct}%`,
                      width: `${widthPct}%`,
                      ["--block-color" as string]: categoryColor(session.category),
                    }}
                    onMouseEnter={() => setHovered({ kind: "session", session, centerPct: leftPct + widthPct / 2 })}
                    onFocus={() => setHovered({ kind: "session", session, centerPct: leftPct + widthPct / 2 })}
                    onBlur={() => setHovered(null)}
                  >
                    <span className="visually-hidden">
                      {session.label} — {session.category}, {timeRange(session.start_ts, session.end_ts)},{" "}
                      {duration(session.duration_s)}, confidence {percent(session.confidence)}. {session.evidence_summary}
                    </span>
                    {/* The label rides inside the block only where it fits; everything
                        else is named in the tooltip and the table below. */}
                    {widthPct > 11 && <span className="tl__blockLabel">{session.label}</span>}
                  </button>
                );
              })}
            </div>

            {uncertain.length > 0 && (
              <div className="tl__uncertainLane" onMouseLeave={() => setHovered(null)}>
                <span className="tl__laneTag">not guessed</span>
                {uncertain.map((span) => {
                  const { leftPct, widthPct } = extent.position(span.start_ts, span.end_ts);
                  const active = hovered?.kind === "uncertain" && hovered.span.id === span.id;
                  return (
                    <button
                      key={span.id}
                      type="button"
                      className={`tl__uncertain hatched${active ? " tl__uncertain--active" : ""}`}
                      style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                      onMouseEnter={() => setHovered({ kind: "uncertain", span, centerPct: leftPct + widthPct / 2 })}
                      onFocus={() => setHovered({ kind: "uncertain", span, centerPct: leftPct + widthPct / 2 })}
                      onBlur={() => setHovered(null)}
                    >
                      <span className="visually-hidden">
                        Unanswered span on {span.process}, {timeRange(span.start_ts, span.end_ts)}. {span.evidence}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {hovered && <Tooltip hovered={hovered} />}
          </div>
        </div>

        <Legend entries={legend} />
      </div>

      <SessionTable sessions={sessions} />
    </section>
  );
}

/** The hover card. Evidence is the reason it exists: a label you cannot interrogate
 *  is a label you have to take on faith, which is the thing this product is against. */
function Tooltip({ hovered }: { hovered: Hovered }) {
  const clamped = Math.min(88, Math.max(12, hovered.centerPct));
  const style = { left: `${clamped}%` };

  if (hovered.kind === "uncertain") {
    const { span } = hovered;
    return (
      <div className="tlTip tlTip--uncertain" style={style} role="tooltip">
        <div className="tlTip__top">
          <span className="swatch hatched" style={{ ["--swatch" as string]: "var(--cat-uncertain)" }} />
          <span className="tlTip__cat">not guessed</span>
          <span className="tlTip__dur tnum">
            {durationCompact((Date.parse(span.end_ts) - Date.parse(span.start_ts)) / 1000)}
          </span>
        </div>
        <p className="tlTip__label">{span.process}</p>
        <p className="tlTip__range tnum">{timeRange(span.start_ts, span.end_ts)}</p>
        <p className="tlTip__evidence">{span.evidence}</p>
        <p className="tlTip__foot">
          Suggested: {span.suggestions.join(" or ")} — answerable in the uncertain queue.
        </p>
      </div>
    );
  }

  const { session } = hovered;
  return (
    <div className="tlTip" style={style} role="tooltip">
      <div className="tlTip__top">
        <span className="swatch" style={{ ["--swatch" as string]: categoryColor(session.category) }} />
        <span className="tlTip__cat">{session.category}</span>
        <span className="tlTip__dur tnum">{durationCompact(session.duration_s)}</span>
      </div>
      <p className="tlTip__label">{session.label}</p>
      <p className="tlTip__range tnum">
        {timeRange(session.start_ts, session.end_ts)} · {session.event_count} events
      </p>
      <p className="tlTip__evidence">{session.evidence_summary}</p>
      <div className="tlTip__conf">
        <ConfidenceMeter confidence={session.confidence} />
      </div>
    </div>
  );
}

function Legend({ entries }: { entries: ReturnType<typeof legendFor> }) {
  return (
    <ul className="tlLegend" aria-label="Categories">
      {entries.map((entry) => (
        <li key={entry.key} className="tlLegend__item">
          <span
            className={`swatch${entry.key === UNCERTAIN ? " hatched" : ""}`}
            style={{ ["--swatch" as string]: entry.color }}
            aria-hidden="true"
          />
          <span className="tlLegend__key">{entry.key}</span>
          <span className="tlLegend__dur tnum">{durationCompact(entry.duration_s)}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Every session as rows. The timeline is the picture; this is the same data in a
 * form you can read, search and tab through without hovering anything.
 */
function SessionTable({ sessions }: { sessions: Session[] }) {
  return (
    <details className="tlTable card">
      <summary className="tlTable__summary">
        <span>All {sessions.length} sessions</span>
        <span className="tlTable__summaryHint">time · category · label · evidence</span>
      </summary>
      <div className="tlTable__scroll">
        <table className="tlTable__table">
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Duration</th>
              <th scope="col">Category</th>
              <th scope="col">Label</th>
              <th scope="col">Conf.</th>
              <th scope="col">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr key={session.id}>
                <td className="tnum tlTable__time">{timeRange(session.start_ts, session.end_ts)}</td>
                <td className="tnum">{duration(session.duration_s)}</td>
                <td>
                  <span className="tlTable__cat">
                    <span
                      className="swatch"
                      style={{ ["--swatch" as string]: categoryColor(session.category) }}
                      aria-hidden="true"
                    />
                    {session.category}
                  </span>
                </td>
                <td>{session.label}</td>
                <td>
                  <ConfidenceMeter confidence={session.confidence} variant="inline" />
                </td>
                <td className="tlTable__evidence">{session.evidence_summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
