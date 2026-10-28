import { useEffect, useRef, useState } from "react";

import type { Day } from "../api/client";
import type { SummaryGroup } from "../api/types";
import { UNCERTAIN, categoryColor, categoryGloss } from "../lib/categories";
import { activityCount, groupsByDuration, uncertainTotal } from "../lib/day";
import { dayLabel, duration, offsetLabel, percent } from "../lib/format";
import { StackedDay, toSegments } from "./StackedDay";
import "./ContrastView.css";

interface Props {
  day: Day;
  /** Jumps to the uncertain queue, so the honest remainder is one click away. */
  onOpenUncertain: () => void;
}

/**
 * The contrast view: the same day read twice.
 *
 * The two decompositions are stacked rather than placed in two columns, because
 * both describe the *same* total — aligning them at the same width means equal
 * length is equal time, and the eye compares the cuts directly with no axis to
 * reconcile. The ranked detail below is side by side, where the comparison is
 * between rows rather than between partitions.
 *
 * Note what the contract does and does not allow here. `naive_by_process` and
 * `groups` are two independent decompositions of one window; nothing links a
 * process to the categories it contained. So this view never claims that
 * "chrome.exe became research and doomscrolling" — it claims only that the same
 * eight hours answer differently depending on which question you ask, which is
 * both the honest reading and the stronger one.
 */
export function ContrastView({ day, onOpenUncertain }: Props) {
  const revealed = useReveal();
  const { summary } = day;

  const naive = groupsByDuration(summary.naive_by_process);
  const truth = groupsByDuration(summary.groups);
  const unanswered = uncertainTotal(summary.groups);

  // Both ranked lists are drawn against one scale — the largest row on either
  // side — so a bar in the left column is directly comparable to one on the right.
  const scale = Math.max(...naive.map((g) => g.share), ...truth.map((g) => g.share), 0.0001);

  return (
    <section className="contrast">
      <header className="contrast__head">
        <div>
          <h1 className="contrast__day">{dayLabel(summary.from)}</h1>
          <p className="contrast__sub">
            <span className="tnum">{duration(summary.total_tracked_s)}</span> tracked
            <Dot />
            {day.sessions.length} sessions
            <Dot />
            <span className="contrast__offset">{offsetLabel(summary.from)}</span>
          </p>
        </div>
        <button type="button" className="contrast__replay" onClick={revealed.replay}>
          <ReplayIcon />
          Replay
        </button>
      </header>

      <div className="contrast__hero card">
        <div className="contrast__row">
          <div className="contrast__rowHead">
            <span className="eyebrow">What a conventional tracker reports</span>
            <span className="contrast__rowMeta">by application · {naive.length} apps</span>
          </div>
          {/* Drawn divided and labelled like the bar below it, but in a single
              neutral: an application name carries no information about what was
              done, so it is given no colour to imply that it does. The absence is
              the argument, and it has to be a visible absence — hence the same
              anatomy, minus the meaning. */}
          <StackedDay
            segments={toSegments(naive, () => "var(--naive-mark)")}
            caption="The day by application name."
          />
        </div>

        <div className="contrast__pivot" aria-hidden="true">
          <span className="contrast__pivotLine" />
          <span className="contrast__pivotText">same {duration(summary.total_tracked_s)}, same device — only the question changed</span>
          <span className="contrast__pivotLine" />
        </div>

        <div className="contrast__row">
          <div className="contrast__rowHead">
            <span className="eyebrow eyebrow--accent">What GroundTruth reports</span>
            <span className="contrast__rowMeta">
              by activity · {activityCount(summary.groups)} activities
              {unanswered > 0 && <> · {duration(unanswered)} not guessed</>}
            </span>
          </div>
          <StackedDay
            segments={toSegments(truth, categoryColor, UNCERTAIN)}
            caption="The day by activity."
            revealed={revealed.on}
          />
        </div>
      </div>

      <div className="contrast__cols">
        <RankedList
          title="By application"
          note="What had focus."
          rows={naive}
          scale={scale}
          colorFor={() => "var(--naive-mark-strong)"}
          revealed
        />
        <RankedList
          title="By activity"
          note="What was happening."
          rows={truth}
          scale={scale}
          colorFor={categoryColor}
          glossFor={categoryGloss}
          texturedKey={UNCERTAIN}
          revealed={revealed.on}
          onRowActivate={(key) => key === UNCERTAIN && onOpenUncertain()}
        />
      </div>
    </section>
  );
}

interface RankedListProps {
  title: string;
  note: string;
  rows: SummaryGroup[];
  scale: number;
  colorFor: (key: string) => string;
  glossFor?: (key: string) => string | undefined;
  texturedKey?: string;
  revealed: boolean;
  onRowActivate?: (key: string) => void;
}

function RankedList({
  title, note, rows, scale, colorFor, glossFor, texturedKey, revealed, onRowActivate,
}: RankedListProps) {
  return (
    <div className="ranked card">
      <div className="ranked__head">
        <h2 className="ranked__title">{title}</h2>
        <p className="ranked__note">{note}</p>
      </div>
      <ul className="ranked__list">
        {rows.map((row, index) => {
          const actionable = onRowActivate && row.key === texturedKey;
          const gloss = glossFor?.(row.key);
          return (
            <li key={row.key} className="ranked__row">
              <span className="swatch" style={{ ["--swatch" as string]: colorFor(row.key) }} aria-hidden="true" />
              <span className="ranked__key">
                {actionable ? (
                  <button type="button" className="ranked__link" onClick={() => onRowActivate(row.key)}>
                    {row.key}
                    <span className="ranked__linkArrow" aria-hidden="true">→</span>
                  </button>
                ) : (
                  row.key
                )}
                {gloss && <span className="ranked__gloss">{gloss}</span>}
              </span>
              <span className="ranked__dur tnum">{duration(row.duration_s)}</span>
              <span className="ranked__pct tnum">{percent(row.share)}</span>
              <span className="ranked__track">
                <span
                  className={`ranked__fill${row.key === texturedKey ? " hatched" : ""}`}
                  style={{
                    width: revealed ? `${(row.share / scale) * 100}%` : "0%",
                    background: row.key === texturedKey ? undefined : colorFor(row.key),
                    transitionDelay: `${index * 34}ms`,
                  }}
                />
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Plays the reveal once on mount and on demand.
 *
 * The first frame is deliberately the un-revealed state so the transition has
 * somewhere to come from; a short delay lets layout settle first, otherwise the
 * sweep starts before the bar has its final width. Reduced-motion users get the
 * final state immediately — the global reduce rule collapses the durations, and
 * starting revealed means they never see the neutral intermediate at all.
 */
function useReveal() {
  const prefersReduced =
    typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [on, setOn] = useState(prefersReduced);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    if (prefersReduced) return;
    const id = window.setTimeout(() => setOn(true), 260);
    timers.current.push(id);
    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
  }, [prefersReduced]);

  const replay = () => {
    if (prefersReduced) return;
    timers.current.forEach(clearTimeout);
    setOn(false);
    const id = window.setTimeout(() => setOn(true), 80);
    timers.current = [id];
  };

  return { on, replay };
}

const Dot = () => <span className="contrast__bullet" aria-hidden="true" />;

function ReplayIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" className="contrast__replayIcon">
      <path
        d="M13.5 8a5.5 5.5 0 1 1-1.8-4.07"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path d="M13.8 1.6v3.2h-3.2" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
