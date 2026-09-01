"""Fresh-session phase orchestration for interruption and resume evals."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from benchmarks.eval_harness import TrialRunner

PhaseRunnerFactory = Callable[[int], TrialRunner]
PhaseCompleteHook = Callable[[int], None]


@dataclass(frozen=True)
class MultiPhaseTrialRunner:
    phases: Sequence[str]
    runner_factory: PhaseRunnerFactory
    on_phase_complete: PhaseCompleteHook | None = None

    def __call__(self, _manifest_prompt: str):
        total = len(self.phases)
        events: list[dict[str, object]] = []
        for index, phase_prompt in enumerate(self.phases):
            phase_number = index + 1
            events.append(
                {
                    "kind": "phase_start",
                    "phase_index": phase_number,
                    "phase_count": total,
                }
            )
            phase_failed = False
            for raw_event in self.runner_factory(index)(phase_prompt):
                event = dict(raw_event) if isinstance(raw_event, dict) else {"kind": "event"}
                event["phase_index"] = phase_number
                event["phase_count"] = total
                events.append(event)
                if event.get("kind") in {"error", "protocol_error"}:
                    phase_failed = True
            if not phase_failed and self.on_phase_complete is not None and phase_number < total:
                try:
                    self.on_phase_complete(index)
                except Exception as exc:
                    events.append(
                        {
                            "kind": "error",
                            "error": f"phase transition failed: {exc}",
                            "phase_index": phase_number,
                            "phase_count": total,
                        }
                    )
                    phase_failed = True
            events.append(
                {
                    "kind": "phase_end",
                    "phase_index": phase_number,
                    "phase_count": total,
                    "failed": phase_failed,
                }
            )
            if phase_failed:
                break
        return iter(events)


__all__ = ["MultiPhaseTrialRunner", "PhaseCompleteHook", "PhaseRunnerFactory"]

