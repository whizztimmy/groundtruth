import { useCallback, useEffect, useState } from "react";

import { fetchDay, type Day } from "../api/client";
import type { Session, UncertainSpan } from "../api/types";

type State =
  | { status: "loading" }
  | { status: "ready"; day: Day }
  | { status: "error"; message: string };

/**
 * Loads the day once and owns the only mutation the dashboard performs: answering
 * an uncertain span. A resolved span leaves the queue and becomes a session, which
 * is what the server does too — the server re-segments and returns that session, so
 * applying it locally keeps the three views consistent without a refetch.
 */
export function useDay() {
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchDay()
      .then((day) => {
        if (!cancelled) setState({ status: "ready", day });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({ status: "error", message: error instanceof Error ? error.message : "could not load the day" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(load, [load]);

  /**
   * Folds an answered span into the day: the span leaves `uncertain`, the returned
   * session joins `sessions`, and the summary moves that time from the `uncertain`
   * group to the answered category. Shares are recomputed against the unchanged
   * total, since answering a span does not change how long the day was.
   */
  const applyResolution = useCallback((spanId: string, session: Session) => {
    setState((current) => {
      if (current.status !== "ready") return current;
      const { day } = current;
      const span = day.uncertain.find((u) => u.id === spanId);
      if (!span) return current;

      const moved = session.duration_s;
      const total = day.summary.total_tracked_s;
      const next = new Map(day.summary.groups.map((g) => [g.key, g.duration_s]));
      next.set("uncertain", Math.max(0, (next.get("uncertain") ?? 0) - moved));
      next.set(session.category, (next.get(session.category) ?? 0) + moved);

      return {
        status: "ready",
        day: {
          ...day,
          uncertain: day.uncertain.filter((u) => u.id !== spanId),
          sessions: [...day.sessions, session].sort((a, b) => Date.parse(a.start_ts) - Date.parse(b.start_ts)),
          summary: {
            ...day.summary,
            groups: [...next.entries()]
              .filter(([, duration_s]) => duration_s > 0)
              .map(([key, duration_s]) => ({ key, duration_s, share: duration_s / total }))
              .sort((a, b) => b.duration_s - a.duration_s),
          },
        },
      };
    });
  }, []);

  return { state, reload: load, applyResolution };
}

export type { Day, UncertainSpan };
