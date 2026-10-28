import { useId } from "react";

import type { SummaryGroup } from "../api/types";
import { duration, percent } from "../lib/format";
import "./StackedDay.css";

export interface StackSegment {
  key: string;
  duration_s: number;
  share: number;
  color: string;
  /** Hatched rather than filled — reserved for the unanswered-span total. */
  textured?: boolean;
}

interface Props {
  segments: StackSegment[];
  /** Describes the whole bar for screen readers, which cannot read a fill. */
  caption: string;
  /**
   * When false the bar renders in a single neutral with its divisions closed up:
   * one undifferentiated block. Flipping it to true sweeps colour and the 2px
   * gaps across the day, left to right — the contrast view's whole gesture.
   */
  revealed?: boolean;
  /** Segments at or above this share get a direct label beneath the bar. */
  labelThreshold?: number;
}

/**
 * One day as a single 100%-width stacked bar.
 *
 * Both decompositions in the contrast view use this component at the same width,
 * stacked one above the other, so the comparison needs no shared axis to be
 * honest: the bars are the same eight hours cut two different ways, and equal
 * length means equal time. Widths come straight from the server's `share`.
 */
export function StackedDay({ segments, caption, revealed = true, labelThreshold = 0.07 }: Props) {
  const captionId = useId();
  let cursor = 0;
  const placed = segments.map((segment, index) => {
    const leftPct = cursor * 100;
    cursor += segment.share;
    return { ...segment, index, leftPct, widthPct: segment.share * 100 };
  });

  return (
    <div className="stack">
      <div
        className={`stack__track${revealed ? " stack__track--revealed" : ""}`}
        role="img"
        aria-labelledby={captionId}
      >
        {placed.map((segment) => (
          <div
            key={segment.key}
            className={`stack__seg${segment.textured ? " stack__seg--textured" : ""}`}
            style={{
              left: `${segment.leftPct}%`,
              width: `${segment.widthPct}%`,
              // Staggering by index is what makes the reveal read as a sweep across
              // the day rather than every slice changing at once.
              transitionDelay: `${segment.index * 42}ms`,
              ["--seg-color" as string]: segment.color,
            }}
            title={`${segment.key} — ${duration(segment.duration_s)} (${percent(segment.share)})`}
          />
        ))}
      </div>

      <p id={captionId} className="visually-hidden">
        {caption}{" "}
        {placed.map((s) => `${s.key}: ${duration(s.duration_s)}, ${percent(s.share)}`).join(". ")}
      </p>

      {/* Selective direct labels. Only the segments wide enough to carry one get a
          label; the rest are named in the ranked list below and on hover, so no
          figure depends on reading a colour. */}
      <div className="stack__labels" aria-hidden="true">
        {placed
          .filter((segment) => segment.share >= labelThreshold)
          .map((segment) => {
            const center = segment.leftPct + segment.widthPct / 2;
            const edge = center < 9 ? "start" : center > 91 ? "end" : "mid";
            return (
              <div
                key={segment.key}
                className={`stack__label stack__label--${edge}`}
                style={{
                  left: edge === "end" ? undefined : `${edge === "start" ? 0 : center}%`,
                  right: edge === "end" ? 0 : undefined,
                  transitionDelay: `${segment.index * 42 + 90}ms`,
                  opacity: revealed ? 1 : 0,
                }}
              >
                <span className="stack__labelKey">{segment.key}</span>
                <span className="stack__labelDur tnum">{duration(segment.duration_s)}</span>
              </div>
            );
          })}
      </div>
    </div>
  );
}

/** Maps summary rows onto segments, preserving the order they are given in. */
export function toSegments(
  groups: SummaryGroup[],
  colorFor: (key: string) => string,
  texturedKey?: string,
): StackSegment[] {
  return groups.map((group) => ({
    key: group.key,
    duration_s: group.duration_s,
    share: group.share,
    color: colorFor(group.key),
    textured: texturedKey !== undefined && group.key === texturedKey,
  }));
}
