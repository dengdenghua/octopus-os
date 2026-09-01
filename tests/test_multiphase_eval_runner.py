from __future__ import annotations

from benchmarks.multiphase_runner import MultiPhaseTrialRunner


def test_multiphase_runner_uses_a_fresh_runner_per_phase() -> None:
    created: list[int] = []
    prompts: list[str] = []

    def factory(index: int):
        created.append(index)

        def runner(prompt: str):
            prompts.append(prompt)
            yield {"kind": "text_delta", "delta": f"phase-{index + 1}"}

        return runner

    events = list(
        MultiPhaseTrialRunner(
            phases=["research and checkpoint", "resume from checkpoint"],
            runner_factory=factory,
        )("manifest summary")
    )

    assert created == [0, 1]
    assert prompts == ["research and checkpoint", "resume from checkpoint"]
    assert [event["kind"] for event in events] == [
        "phase_start",
        "text_delta",
        "phase_end",
        "phase_start",
        "text_delta",
        "phase_end",
    ]
    assert events[1]["phase_index"] == 1
    assert events[4]["phase_index"] == 2


def test_multiphase_runner_stops_after_phase_error() -> None:
    created: list[int] = []

    def factory(index: int):
        created.append(index)
        return lambda _prompt: iter([{"kind": "error", "error": "failed"}])

    events = list(MultiPhaseTrialRunner(phases=["one", "two"], runner_factory=factory)("summary"))

    assert created == [0]
    assert events[-1] == {
        "kind": "phase_end",
        "phase_index": 1,
        "phase_count": 2,
        "failed": True,
    }

