import type { ReactNode } from "react";

import type { Provenance } from "../api/client";
import { useTheme, type ThemeChoice } from "../lib/useTheme";
import "./AppShell.css";

export interface ViewTab {
  id: string;
  label: string;
  /** Shown under the nav label; keeps the three views' jobs distinct at a glance. */
  hint: string;
}

interface Props {
  tabs: readonly ViewTab[];
  active: string;
  onSelect: (id: string) => void;
  provenance?: Provenance;
  children: ReactNode;
}

const THEME_LABEL: Record<ThemeChoice, string> = {
  auto: "Auto",
  light: "Light",
  dark: "Dark",
};

export function AppShell({ tabs, active, onSelect, provenance, children }: Props) {
  const { choice, cycle } = useTheme();

  return (
    <>
      <header className="shell">
        <div className="shell__inner page">
          <div className="shell__brand">
            <Mark />
            <div>
              <div className="shell__name">GroundTruth</div>
              <div className="shell__tagline">what those hours actually were</div>
            </div>
          </div>

          <nav className="shell__nav" aria-label="Views">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className="shell__tab"
                aria-current={tab.id === active ? "page" : undefined}
                onClick={() => onSelect(tab.id)}
              >
                <span className="shell__tabLabel">{tab.label}</span>
                <span className="shell__tabHint">{tab.hint}</span>
              </button>
            ))}
          </nav>

          <div className="shell__meta">
            {provenance && <ProvenanceBadge provenance={provenance} />}
            <button
              type="button"
              className="pill shell__theme"
              onClick={cycle}
              aria-label={`Colour scheme: ${THEME_LABEL[choice]}. Activate to change.`}
            >
              <SchemeIcon choice={choice} />
              {THEME_LABEL[choice]}
            </button>
          </div>
        </div>
      </header>
      <main className="page">{children}</main>
    </>
  );
}

/**
 * States where the figures came from. A dashboard that cannot reach its server
 * should say so rather than let fixture numbers read as a live day — the whole
 * product is an argument about trusting what a tracker tells you.
 */
function ProvenanceBadge({ provenance }: { provenance: Provenance }) {
  const live = provenance.kind === "server";
  return (
    <span
      className={`pill shell__provenance${live ? " shell__provenance--live" : ""}`}
      title={
        live
          ? `Reading the live server at ${provenance.base}.`
          : `No server answered (${provenance.reason}). Showing the bundled sample day from web/src/data/day.json.`
      }
    >
      <span className={`shell__dot${live ? " shell__dot--live" : ""}`} aria-hidden="true" />
      {live ? "live" : "sample day"}
    </span>
  );
}

function Mark() {
  // Two readings of one span: an undifferentiated bar above, the same width
  // resolved into parts below. The logo is the product's argument.
  return (
    <svg className="shell__mark" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <rect x="2" y="7" width="24" height="5" rx="1.5" fill="var(--naive-mark)" />
      <rect x="2" y="16" width="7.5" height="5" rx="1.5" fill="var(--cat-deep-work)" />
      <rect x="11.5" y="16" width="5" height="5" rx="1.5" fill="var(--cat-research)" />
      <rect x="18.5" y="16" width="7.5" height="5" rx="1.5" fill="var(--cat-doomscrolling)" />
    </svg>
  );
}

function SchemeIcon({ choice }: { choice: ThemeChoice }) {
  return (
    <svg className="shell__schemeIcon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      {choice !== "light" && (
        <path d="M8 3a5 5 0 0 0 0 10z" fill="currentColor" opacity={choice === "auto" ? 0.55 : 1} />
      )}
      {choice === "light" && <circle cx="8" cy="8" r="2.4" fill="currentColor" />}
    </svg>
  );
}
