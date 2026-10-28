import { confidencePercent } from "../lib/format";
import "./ConfidenceMeter.css";

/**
 * The confidence the classifier committed to, against the threshold it had to clear.
 *
 * ACCEPT mirrors `Confidence.ACCEPT` in `agent/groundtruth/types.py`: the agent
 * stores a classification at or above it, retries below it, and files anything
 * still short as `uncertain`. Showing the threshold rather than a bare percentage
 * is what makes the number mean something — it says what the figure had to beat.
 */
const ACCEPT = 0.75;

interface Props {
  confidence: number;
  /** `bar` for tooltips and lists; `inline` for a single dense row. */
  variant?: "bar" | "inline";
}

export function ConfidenceMeter({ confidence, variant = "bar" }: Props) {
  const stated = confidencePercent(confidence);
  const userStated = confidence >= 1;

  if (variant === "inline") {
    return (
      <span className="conf conf--inline tnum" title={userStated ? "Stated by you" : `Accepted above ${Math.round(ACCEPT * 100)}%`}>
        {userStated ? "stated" : stated}
      </span>
    );
  }

  return (
    <div className="conf">
      <div className="conf__row">
        <span className="conf__label">{userStated ? "Answered by you" : "Confidence"}</span>
        <span className="conf__value tnum">{userStated ? "certain" : stated}</span>
      </div>
      <div className="conf__track">
        <div className="conf__fill" style={{ width: `${Math.min(100, confidence * 100)}%` }} />
        {!userStated && (
          <div
            className="conf__threshold"
            style={{ left: `${ACCEPT * 100}%` }}
            title={`The agent accepts a label at ${Math.round(ACCEPT * 100)}% and retries below it.`}
          />
        )}
      </div>
    </div>
  );
}
