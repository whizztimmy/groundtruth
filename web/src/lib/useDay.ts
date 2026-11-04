import { useCallback, useEffect, useState } from "react";

import { fetchDay, type Day } from "../api/client";
import type { Session, UncertainSpan } from "../api/types";

type State =
  | { status: "loading" }
  | { status: "ready"; day: Day }
  | { status: "error"; message: string };

/**
 * Polling interval, in milliseconds.
 *
 * Half the agent's sync interval, so a batch that reaches the server is on screen
 * within one poll of arriving rather than up to a full cycle later. Faster buys
 * nothing: the agent samples every couple of seconds but only syncs every sixty,
 * so the server has nothing new to say in between.
 */
const POLL_MS = 30_000;

/**
 * Loads the day, keeps it current while you watch, and owns the only mutation the
 * dashboard performs: answering an uncertain span. A resolved span leaves the queue
 * and becomes a session, which is what the server does too - the server re-segments
 * and returns that session, so applying it locally keeps the three views consistent
 * without a refetch.
 *
 * Refreshes are silent. A background poll that set `loading` would blank the day
 * every thirty seconds, which is worse than not refreshing at all: the dashboard is
 * meant to be left open on a second monitor while the agent runs.
 *
 * Polling stops while the tab is hidden and resumes with an immediate refresh, so a
 * dashboard left open overnight is not still requesting at 4am, and one you come
 * back to is current before you have finished looking at it.
 */
export function useDay() {
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback((silent = false) => {
    let cancelled = false;
    if (!silent) setState({ status: "loading" });
    fetchDay()
      .then((day) => {
        if (!cancelled) setState({ status: "ready", day });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // A failed silent poll keeps the day already on screen. The server being
        // restarted mid-afternoon should not replace a correct timeline with an
        // error, and the next tick will recover on its own.
        if (silent) return;
        setState({ status: "error", message: error instanceof Error ? error.message : "could not load the day" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(load, [load]);

  useEffect(() => {
    let cancelPoll: (() => void) | undefined;
    let timer: number | undefined;

    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer);
      timer = undefined;
    };

    const start = () => {
      if (timer !== undefined) return;
      timer = window.setInterval(() => {
        cancelPoll?.();
        cancelPoll = load(true);
      }, POLL_MS);
    };

    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        cancelPoll?.();
        cancelPoll = load(true);
        start();
      }
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
      cancelPoll?.();
    };
  }, [load]);

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
