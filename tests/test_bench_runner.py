from __future__ import annotations

from benchmarks import bench_runner


def test_realtime_bench_runner_repeats_and_grades(monkeypatch, capsys, tmp_path) -> None:
    calls: list[str] = []

    class FakeRunner:
        def __init__(self, **kwargs) -> None:
            assert kwargs["url"] == "ws://test/api/realtime"

        def __call__(self, prompt: str):
            calls.append(prompt)
            yield {"kind": "text_delta", "delta": "ECHO_EVAL_OK"}

    monkeypatch.setattr(bench_runner, "RealtimeTrialRunner", FakeRunner)
    output = tmp_path / "smoke.json"

    code = bench_runner.main(
        [
            "--url",
            "ws://test/api/realtime",
            "--k",
            "3",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert len(calls) == 3
    assert output.exists()
    assert "pass^k  = 100.00%" in capsys.readouterr().out

