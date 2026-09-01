import { useEffect, useRef } from "react";

/**
 * Bridges the realtime agent run state to the Godot desktop pet sidecar.
 *
 * The Electron main process owns the pet process (see electron/pet-sidecar.cjs)
 * and maps "agent.<state>" events onto the pet's behavior FSM. This hook only
 * needs to translate the already-derived run state into one of the six pet
 * events and fire it on transitions — it never starts/stops the pet itself.
 *
 * It is a hard no-op outside Electron (plain browser / dev without preload),
 * so enabling the pet never breaks web builds.
 *
 * The in-page sprite pet was removed — the Godot sidecar is the only pet, so
 * this hook exists purely for its side effect. `PetMood` is kept as the return
 * type for callers that still want to reflect run state locally.
 */
export type PetMood = "idle" | "thinking" | "working" | "waiting" | "success" | "error";

export type PetAgentInput = {
  /** "running" | "waiting" | "error" | null — already derived by the page. */
  runState: "running" | "waiting" | "error" | null;
  /** True once a run has settled with a completed answer. */
  settled: boolean;
  /** True when the settled run failed (vs. completed). */
  failed: boolean;
  /** True while streaming a live assistant answer (post-thinking). */
  streaming: boolean;
};

export type PetEventName =
  | "idle"
  | "thinking"
  | "working"
  | "waiting_user"
  | "success"
  | "error";

function petEventFor(input: PetAgentInput): PetEventName | null {
  // A failed run trumps everything so the pet reacts before the state resets.
  if (input.failed) return "error";
  if (input.runState === "waiting") return "waiting_user";
  if (input.runState === "error") return "error";
  if (input.runState === "running") return input.streaming ? "thinking" : "working";
  // Settled + no longer running → success (only when an answer completed).
  if (input.settled) return "success";
  return "idle";
}

export function usePetAgentEvents(input: PetAgentInput): PetMood {
  // Remember the last event so we only fire on real transitions.
  const lastRef = useRef<PetEventName | null>(null);

  useEffect(() => {
    if (typeof window === "undefined" || !window.echo?.isElectron) return;
    if (!window.echo.pet?.sendEvent) return;

    const event = petEventFor(input);
    if (!event || event === lastRef.current) return;
    lastRef.current = event;

    void window.echo.pet.sendEvent(event).catch(() => {
      /* pet sidecar may be unavailable — non-fatal */
    });
  }, [input]);

  const event = petEventFor(input);
  return event === "waiting_user" ? "waiting" : (event ?? "idle");
}
