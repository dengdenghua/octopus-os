// React wrapper around ``classifyVitals``. The marks are mutated off the
// notification stream (a ref, no re-render), so a timer is what advances
// "age since last activity" during silence — that silence is precisely
// what we're trying to detect, and no notification arrives to trigger it.

import { useEffect, useRef, useState, type MutableRefObject } from "react";

import {
  classifyVitals,
  emptyVitals,
  type StreamVitals,
  type VitalsMarks,
  type VitalsThresholds,
} from "./stream-vitals";

// Recompute cadence while a turn is live. 500ms is well under the 1.5s
// streaming-fresh window, so a phase transition never lags by more than a
// tick, yet the setState is throttled below (see ``sameSnapshot``) so we
// don't re-render consumers twice a second for no visible change.
const TICK_MS = 500;

function sameSnapshot(a: StreamVitals, b: StreamVitals): boolean {
  // Only the phase and whole-second elapsed are user-visible (the label
  // renders seconds). Collapse everything else so a streaming turn causes
  // at most ~1 vitals-driven render per second.
  return (
    a.phase === b.phase &&
    a.stalled === b.stalled &&
    Math.floor(a.elapsedMs / 1000) === Math.floor(b.elapsedMs / 1000)
  );
}

export function useStreamVitals(input: {
  marksRef: MutableRefObject<VitalsMarks>;
  connected: boolean;
  turnActive: boolean;
  hasRunningWork: boolean;
  thresholds?: VitalsThresholds;
  /** Injectable clock for tests. Defaults to Date.now. */
  now?: () => number;
}): StreamVitals {
  const { marksRef, connected, turnActive, hasRunningWork, thresholds } = input;
  const [vitals, setVitals] = useState<StreamVitals>(emptyVitals);

  // Keep the clock in a ref so changing it doesn't restart the timer.
  const nowRef = useRef(input.now ?? Date.now);
  nowRef.current = input.now ?? Date.now;

  useEffect(() => {
    const compute = () => {
      const next = classifyVitals(
        { marks: marksRef.current, connected, turnActive, hasRunningWork },
        nowRef.current(),
        thresholds,
      );
      setVitals((prev) => (sameSnapshot(prev, next) ? prev : next));
    };
    compute();
    // No live turn → no need to tick; the immediate compute settles it to
    // "idle" and we idle the timer.
    if (!turnActive) return;
    const id = setInterval(compute, TICK_MS);
    return () => clearInterval(id);
  }, [marksRef, connected, turnActive, hasRunningWork, thresholds]);

  return vitals;
}
