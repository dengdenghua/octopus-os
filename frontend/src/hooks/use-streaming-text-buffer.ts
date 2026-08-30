import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Smooth playback for streamed text.
 *
 * Incoming deltas update refs immediately, while one persistent animation-frame
 * loop controls what is visible. This is important: restarting a 40 ms timer for
 * every delta can starve the renderer when tokens arrive faster than the timer.
 */
export interface StreamingTextBufferOptions {
  targetText: string;
  /** When false, the stream has settled. */
  enabled?: boolean;
  /** Keep a short animated drain after settlement. Default true. */
  drainOnFinish?: boolean;
  /** Bump this when the message identity changes. */
  resetKey?: string | number;
  /** Target time between visible updates. Default 40 ms. */
  targetIntervalMs?: number;
  /** Minimum approximate UTF-16 code units revealed per update. */
  minCharsPerTick?: number;
  /**
   * Ceiling for the minimum per-tick reveal (``minCharsPerTick`` is
   * clamped by it). The backlog-proportional lane is intentionally
   * uncapped so fast providers cannot accumulate seconds of invisible
   * text (the old fixed cap produced the visible "answer suddenly dumps
   * the last chunk" effect on settlement).
   */
  maxCharsPerTick?: number;
  /** Backlog ratio used to accelerate playback. */
  backlogDivisor?: number;
  /** Reveal a backlog at or below this size immediately. */
  fastDrainThreshold?: number;
  /** Maximum animated tail after settlement. Default 96 ms. */
  maxFinishDelayMs?: number;
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Shared playback presets so the overall streaming cadence is tuned in one
 * place. Tuning rationale:
 *
 * - ``finalAnswer``: preserve a light visual cadence without letting the
 *   renderer trail the transport by more than roughly one perceptual frame.
 * - ``liveThinking``: private reasoning arrives in larger bursts than the
 *   final answer; a slightly faster cadence (32ms, up to 10 chars) keeps
 *   the live window from accumulating seconds of invisible backlog on
 *   fast providers while staying readable.
 * - ``burstDrain``: terminal-only verdicts (sub-agent final answer) that
 *   never streamed token-by-token — the whole text appears at once, so the
 *   drain window is wider (160ms) and the floor higher (2 chars/tick) to
 *   read as a smooth materialisation rather than a flash.
 */
export const STREAMING_TYPE_PRESETS = {
  finalAnswer: {
    targetIntervalMs: 32,
    backlogDivisor: 10,
    fastDrainThreshold: 2,
    maxFinishDelayMs: 96,
  },
  liveThinking: {
    targetIntervalMs: 32,
    maxCharsPerTick: 10,
    backlogDivisor: 12,
    fastDrainThreshold: 2,
    maxFinishDelayMs: 120,
  },
  burstDrain: {
    targetIntervalMs: 32,
    minCharsPerTick: 2,
    maxCharsPerTick: 12,
    backlogDivisor: 8,
    fastDrainThreshold: 4,
    maxFinishDelayMs: 160,
  },
} as const satisfies Record<string, Partial<StreamingTextBufferOptions>>;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(REDUCED_MOTION_QUERY).matches
  );
}

let graphemeSegmenter: Intl.Segmenter | null | undefined;

function getGraphemeSegmenter(): Intl.Segmenter | null {
  if (graphemeSegmenter !== undefined) return graphemeSegmenter;
  graphemeSegmenter =
    typeof Intl !== "undefined" && "Segmenter" in Intl
      ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
      : null;
  return graphemeSegmenter;
}

/** Advance by roughly `amount` code units without splitting a visible glyph. */
function advanceToGraphemeBoundary(
  text: string,
  current: number,
  amount: number,
): number {
  const desired = Math.min(text.length, current + amount);
  if (desired >= text.length) return text.length;

  const segmenter = getGraphemeSegmenter();
  if (segmenter) {
    // `current` is always a boundary, so segmenting only the remaining suffix
    // avoids walking the entire accumulated answer on every frame.
    let consumed = 0;
    for (const part of segmenter.segment(text.slice(current))) {
      consumed += part.segment.length;
      if (current + consumed >= desired) return current + consumed;
    }
    return text.length;
  }

  // Older runtimes: at least keep UTF-16 surrogate pairs intact.
  const code = text.charCodeAt(desired - 1);
  if (code >= 0xd800 && code <= 0xdbff) {
    const next = text.charCodeAt(desired);
    if (next >= 0xdc00 && next <= 0xdfff) return desired + 1;
  }
  return desired;
}

export function useStreamingTextBuffer({
  targetText,
  enabled = true,
  drainOnFinish = true,
  resetKey,
  targetIntervalMs = 40,
  minCharsPerTick = 1,
  maxCharsPerTick = 6,
  backlogDivisor = 12,
  fastDrainThreshold = 0,
  maxFinishDelayMs = 96,
}: StreamingTextBufferOptions): string {
  const [displayText, setDisplayText] = useState(targetText);
  const displayLengthRef = useRef(targetText.length);
  const targetRef = useRef(targetText);
  const enabledRef = useRef(enabled);
  const optionsRef = useRef({
    drainOnFinish,
    targetIntervalMs,
    minCharsPerTick,
    maxCharsPerTick,
    backlogDivisor,
    fastDrainThreshold,
    maxFinishDelayMs,
  });
  const frameRef = useRef<number | null>(null);
  const lastRevealAtRef = useRef<number | null>(null);
  const finishStartedAtRef = useRef<number | null>(null);
  const resetKeyRef = useRef(resetKey);
  const reducedMotionRef = useRef(prefersReducedMotion());

  const revealAll = useCallback(() => {
    const text = targetRef.current;
    displayLengthRef.current = text.length;
    finishStartedAtRef.current = null;
    setDisplayText(text);
  }, []);

  const animate = useCallback(
    (now: number) => {
      frameRef.current = null;
      const text = targetRef.current;
      const current = displayLengthRef.current;
      const opts = optionsRef.current;

      if (current >= text.length) {
        lastRevealAtRef.current = null;
        return;
      }

      if (
        reducedMotionRef.current ||
        (!enabledRef.current && !opts.drainOnFinish)
      ) {
        revealAll();
        lastRevealAtRef.current = null;
        return;
      }

      const lastReveal = lastRevealAtRef.current;
      if (lastReveal === null) {
        lastRevealAtRef.current = now;
      } else if (now - lastReveal >= opts.targetIntervalMs) {
        const backlog = text.length - current;
        let step: number;
        if (backlog <= opts.fastDrainThreshold) {
          step = backlog;
        } else {
          const baseCap = Math.max(1, opts.maxCharsPerTick);
          const minStep = Math.min(baseCap, Math.max(1, opts.minCharsPerTick));
          step = Math.max(
            minStep,
            Math.round(backlog / Math.max(1, opts.backlogDivisor)),
          );
        }
        if (!enabledRef.current && finishStartedAtRef.current !== null) {
          // Spread the remaining tail across the bounded finish window rather
          // than revealing everything in one last frame at the deadline. A
          // coalesced provider chunk can leave dozens of characters queued;
          // the old hard cut-over produced the visible “pause, then dump”
          // effect even though both transport lanes were streaming normally.
          const elapsed = Math.max(0, now - finishStartedAtRef.current);
          const remainingMs = Math.max(0, opts.maxFinishDelayMs - elapsed);
          const remainingTicks = Math.max(
            1,
            Math.ceil(remainingMs / Math.max(1, opts.targetIntervalMs)),
          );
          step = Math.max(step, Math.ceil(backlog / remainingTicks));
        }
        const next = advanceToGraphemeBoundary(text, current, step);
        displayLengthRef.current = next;
        setDisplayText(text.slice(0, next));
        // Preserve fractional frame time instead of resetting to `now`; this
        // keeps playback stable on 60/120 Hz displays.
        lastRevealAtRef.current = lastReveal + opts.targetIntervalMs;
      }

      if (displayLengthRef.current < targetRef.current.length) {
        frameRef.current = requestAnimationFrame(animate);
      } else {
        lastRevealAtRef.current = null;
        finishStartedAtRef.current = null;
      }
    },
    [revealAll],
  );

  const ensureAnimation = useCallback(() => {
    if (
      frameRef.current === null &&
      displayLengthRef.current < targetRef.current.length
    ) {
      // Make fresh growth visible on the very next paint. Subsequent reveals
      // retain the configured cadence.
      if (lastRevealAtRef.current === null) {
        lastRevealAtRef.current =
          performance.now() - optionsRef.current.targetIntervalMs;
      }
      frameRef.current = requestAnimationFrame(animate);
    }
  }, [animate]);

  // Update the live stream snapshot without cancelling the active frame loop.
  // This is the key starvation guard for high-frequency deltas.
  useEffect(() => {
    const previousTarget = targetRef.current;
    const wasEnabled = enabledRef.current;
    targetRef.current = targetText;
    enabledRef.current = enabled;
    optionsRef.current = {
      drainOnFinish,
      targetIntervalMs,
      minCharsPerTick,
      maxCharsPerTick,
      backlogDivisor,
      fastDrainThreshold,
      maxFinishDelayMs,
    };

    const identityChanged = resetKey !== resetKeyRef.current;
    if (identityChanged || targetText.length < displayLengthRef.current) {
      resetKeyRef.current = resetKey;
      revealAll();
      return;
    }

    if (wasEnabled && !enabled) {
      finishStartedAtRef.current = performance.now();
    } else if (enabled) {
      finishStartedAtRef.current = null;
    }

    if (
      reducedMotionRef.current ||
      (!enabled && !drainOnFinish) ||
      (targetText !== previousTarget &&
        typeof document !== "undefined" &&
        document.hidden)
    ) {
      revealAll();
      return;
    }
    ensureAnimation();
  }, [
    targetText,
    enabled,
    drainOnFinish,
    resetKey,
    targetIntervalMs,
    minCharsPerTick,
    maxCharsPerTick,
    backlogDivisor,
    fastDrainThreshold,
    maxFinishDelayMs,
    ensureAnimation,
    revealAll,
  ]);

  useEffect(() => {
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    ) {
      return;
    }
    const mql = window.matchMedia(REDUCED_MOTION_QUERY);
    const handleMotionChange = () => {
      reducedMotionRef.current = mql.matches;
      if (mql.matches) revealAll();
      else ensureAnimation();
    };
    reducedMotionRef.current = mql.matches;
    mql.addEventListener?.("change", handleMotionChange);
    return () => mql.removeEventListener?.("change", handleMotionChange);
  }, [ensureAnimation, revealAll]);

  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    },
    [],
  );

  return displayText;
}
