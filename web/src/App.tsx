import { useCallback, useEffect, useState } from "react";

import { AppShell, type ViewTab } from "./components/AppShell";
import { ContrastView } from "./components/ContrastView";
import { TimelineView } from "./components/TimelineView";
import { UncertainView } from "./components/UncertainView";
import { useDay } from "./lib/useDay";
import "./App.css";

const TABS: readonly ViewTab[] = [
  { id: "contrast", label: "Contrast", hint: "the same day, read twice" },
  { id: "timeline", label: "Timeline", hint: "where the hours went" },
  { id: "uncertain", label: "Uncertain", hint: "what it won't guess" },
];

const IDS = new Set(TABS.map((tab) => tab.id));
const fromHash = () => {
  const id = location.hash.replace(/^#\/?/, "");
  return IDS.has(id) ? id : TABS[0].id;
};

/**
 * Three views over one day. Routing is the location hash rather than a router
 * dependency — it keeps the views linkable for a demo without adding a package to
 * do what six lines do.
 */
export function App() {
  const [view, setView] = useState(fromHash);
  const { state, reload, applyResolution } = useDay();

  useEffect(() => {
    const sync = () => setView(fromHash());
    addEventListener("hashchange", sync);
    return () => removeEventListener("hashchange", sync);
  }, []);

  const select = useCallback((id: string) => {
    location.hash = `#/${id}`;
    setView(id);
  }, []);

  const provenance = state.status === "ready" ? state.day.provenance : undefined;

  return (
    <AppShell tabs={TABS} active={view} onSelect={select} provenance={provenance}>
      {state.status === "loading" && <Loading />}
      {state.status === "error" && <Failure message={state.message} onRetry={reload} />}
      {state.status === "ready" && (
        <>
          {view === "contrast" && (
            <ContrastView day={state.day} onOpenUncertain={() => select("uncertain")} />
          )}
          {view === "timeline" && <TimelineView day={state.day} />}
          {view === "uncertain" && (
            <UncertainView day={state.day} onResolved={applyResolution} onBrowse={() => select("timeline")} />
          )}
        </>
      )}
    </AppShell>
  );
}

function Loading() {
  return (
    <div className="state" role="status">
      <div className="state__bars" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <p className="state__text">Reading the day…</p>
    </div>
  );
}

function Failure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="state" role="alert">
      <p className="state__title">The day could not be read.</p>
      <p className="state__text">{message}</p>
      <button type="button" className="state__retry" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}
