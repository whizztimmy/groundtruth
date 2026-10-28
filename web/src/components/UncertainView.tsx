import { useState } from "react";

import { resolveUncertain, type Day } from "../api/client";
import type { Session, UncertainSpan } from "../api/types";
import { CATEGORY_ORDER, UNCERTAIN, categoryColor, categoryGloss } from "../lib/categories";
import { duration, durationCompact, timeRange } from "../lib/format";
import "./UncertainView.css";

interface Props {
  day: Day;
  onResolved: (spanId: string, session: Session) => void;
  /** Sends the reader to the timeline, where these spans show in context. */
  onBrowse: () => void;
}

/**
 * The uncertain queue.
 *
 * This is not an error list. A classifier that returns `uncertain` instead of a
 * plausible guess is behaving correctly — the architecture has no ground-truth
 * label set to validate against, so the system is built to commit to answers it
 * can be held to and to ask about the rest. Answering a span stores it as a
 * few-shot example for that process, which is how the taxonomy converges on one
 * person rather than on an average. The queue is therefore framed as the part of
 * the product that earns the rest of it.
 */
export function UncertainView({ day, onResolved, onBrowse }: Props) {
  const { uncertain } = day;
  const unansweredTotal = uncertain.reduce(
    (total, span) => total + (Date.parse(span.end_ts) - Date.parse(span.start_ts)) / 1000,
    0,
  );

  return (
    <section className="unc">
      <header className="unc__head">
        <p className="eyebrow">Open questions</p>
        <h1 className="unc__title">
          {uncertain.length === 0 ? (
            <>Nothing is waiting.</>
          ) : (
            <>
              {uncertain.length} span{uncertain.length === 1 ? "" : "s"} the classifier would not guess at
            </>
          )}
        </h1>
        <p className="unc__lede">
          {uncertain.length === 0 ? (
            <>
              Every span in this day cleared the confidence threshold, so there is nothing to confirm. New
              questions appear here as they come up rather than being resolved silently.
            </>
          ) : (
            <>
              <span className="tnum">{duration(unansweredTotal)}</span> of this day is held as a question
              instead of a guess. Most trackers would have assigned it something plausible and never
              mentioned it. Answering one is a single click, and the answer becomes a few-shot example for
              that application — so the same ambiguity resolves itself next time.
            </>
          )}
        </p>
        <button type="button" className="unc__browse" onClick={onBrowse}>
          See these in the timeline
          <span aria-hidden="true">→</span>
        </button>
      </header>

      {uncertain.length === 0 ? (
        <div className="unc__empty card">
          <CheckMark />
          <p className="unc__emptyText">The queue is clear for this day.</p>
        </div>
      ) : (
        <ul className="unc__list">
          {uncertain.map((span) => (
            <SpanCard key={span.id} span={span} day={day} onResolved={onResolved} />
          ))}
        </ul>
      )}
    </section>
  );
}

type CardState =
  | { phase: "asking" }
  | { phase: "saving"; category: string }
  | { phase: "answered"; session: Session; local: boolean }
  | { phase: "failed"; message: string };

function SpanCard({
  span, day, onResolved,
}: {
  span: UncertainSpan;
  day: Day;
  onResolved: (spanId: string, session: Session) => void;
}) {
  const [state, setState] = useState<CardState>({ phase: "asking" });
  const [customLabel, setCustomLabel] = useState("");
  const [picking, setPicking] = useState(false);
  const seconds = (Date.parse(span.end_ts) - Date.parse(span.start_ts)) / 1000;

  /**
   * One click is the whole interaction: the chosen category is the answer, and the
   * label defaults to that category unless a more specific one was typed. The
   * contract wants both fields, and a category is a truthful label — so requiring
   * the user to compose a phrase before they can answer would be ceremony.
   */
  const answer = async (category: string) => {
    setState({ phase: "saving", category });
    const label = customLabel.trim() || category;
    try {
      const { session, applied } = await resolveUncertain(span, { category, label }, day.provenance);
      setState({ phase: "answered", session, local: applied === "local" });
      // Let the confirmation be read before the card leaves the queue.
      setTimeout(() => onResolved(span.id, session), 2400);
    } catch (error) {
      setState({ phase: "failed", message: error instanceof Error ? error.message : "could not save" });
    }
  };

  if (state.phase === "answered") {
    return (
      <li className="uncCard uncCard--answered card">
        <div className="uncCard__answeredTop">
          <span className="swatch" style={{ ["--swatch" as string]: categoryColor(state.session.category) }} />
          <span className="uncCard__answeredCat">{state.session.category}</span>
          <span className="uncCard__answeredDur tnum">{durationCompact(state.session.duration_s)}</span>
        </div>
        {/* A one-click answer defaults its label to the category, so printing both
            would just say the same word twice. The span's own hours are the more
            useful second line in that case. */}
        <p className="uncCard__answeredLabel">
          {state.session.label === state.session.category
            ? timeRange(state.session.start_ts, state.session.end_ts)
            : state.session.label}
        </p>
        <p className="uncCard__answeredNote">
          {state.local
            ? `Recorded against ${span.process} in this session. With the server running it would be stored as a few-shot example and the day re-segmented.`
            : `Stored as a few-shot example for ${span.process}. The day has been re-segmented.`}
        </p>
      </li>
    );
  }

  const saving = state.phase === "saving";

  return (
    <li className="uncCard card">
      <div className="uncCard__when">
        <span className="uncCard__range tnum">{timeRange(span.start_ts, span.end_ts)}</span>
        <span className="uncCard__dur tnum">{duration(seconds)}</span>
        <span className="pill uncCard__process">{span.process}</span>
      </div>

      <p className="uncCard__evidence">
        <span className="uncCard__evidenceMark hatched" aria-hidden="true" />
        {span.evidence}
      </p>

      <div className="uncCard__answer">
        <span className="uncCard__ask">What was this?</span>
        <div className="uncCard__options">
          {span.suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="uncCard__option"
              style={{ ["--option-color" as string]: categoryColor(suggestion) }}
              disabled={saving}
              onClick={() => answer(suggestion)}
              title={categoryGloss(suggestion)}
            >
              <span className="swatch" style={{ ["--swatch" as string]: categoryColor(suggestion) }} aria-hidden="true" />
              {suggestion}
              {saving && state.category === suggestion && <span className="uncCard__spinner" aria-hidden="true" />}
            </button>
          ))}
          <button
            type="button"
            className="uncCard__more"
            aria-expanded={picking}
            disabled={saving}
            onClick={() => setPicking((open) => !open)}
          >
            {picking ? "Fewer" : "Something else"}
          </button>
        </div>

        {picking && (
          <div className="uncCard__drawer">
            <label className="uncCard__labelField">
              <span className="uncCard__labelHint">Give it a specific label (optional)</span>
              <input
                type="text"
                value={customLabel}
                placeholder="e.g. comparing inference providers"
                onChange={(event) => setCustomLabel(event.target.value)}
                disabled={saving}
              />
            </label>
            <div className="uncCard__all">
              {CATEGORY_ORDER.filter((key) => key !== UNCERTAIN && !span.suggestions.includes(key)).map((key) => (
                <button
                  key={key}
                  type="button"
                  className="uncCard__option uncCard__option--quiet"
                  disabled={saving}
                  onClick={() => answer(key)}
                  title={categoryGloss(key)}
                >
                  <span className="swatch" style={{ ["--swatch" as string]: categoryColor(key) }} aria-hidden="true" />
                  {key}
                </button>
              ))}
            </div>
          </div>
        )}

        {state.phase === "failed" && (
          <p className="uncCard__error" role="alert">
            {state.message} — nothing was stored, so the span is still open.
          </p>
        )}
      </div>
    </li>
  );
}

function CheckMark() {
  return (
    <svg viewBox="0 0 24 24" className="unc__check" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="10.5" fill="none" stroke="var(--line-strong)" strokeWidth="1.5" />
      <path
        d="M7.5 12.4l3 3 6-6.5"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
