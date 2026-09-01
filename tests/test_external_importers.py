from __future__ import annotations

import json

from runtime.safety.recovery.external_importers import (
    build_external_session_dataset,
    collect_external_session_failures,
    discover_external_session_roots,
    import_external_sessions,
)


def test_import_external_sessions_reads_successful_json_session(tmp_path) -> None:
    session = tmp_path / "claude" / "session.json"
    session.parent.mkdir()
    session.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "messages": [
                    {"role": "user", "content": "帮我写项目计划"},
                    {"role": "assistant", "content": "已完成项目计划。"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = import_external_sessions([tmp_path])

    assert report.scanned_files == 1
    assert len(report.samples) == 1
    sample = report.samples[0]
    assert sample.success is True
    assert sample.goal == "帮我写项目计划"
    assert sample.source == "claude_session"
    dataset = build_external_session_dataset([tmp_path])
    assert dataset.all_examples[0].source == "claude_session_success"


def test_collect_external_session_failures_reads_jsonl_error(tmp_path) -> None:
    session = tmp_path / "copilot-history.jsonl"
    rows = [
        {"role": "user", "content": "修复登录 bug"},
        {"role": "assistant", "content": "Traceback: failed to edit file", "status": "error"},
    ]
    session.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    failures = collect_external_session_failures([tmp_path])

    assert len(failures) == 1
    assert failures[0]["goal"] == "修复登录 bug"
    assert failures[0]["source"] == "copilot_session"
    assert "failed to edit file" in failures[0]["last_error"]


def test_import_external_sessions_reads_text_transcript(tmp_path) -> None:
    session = tmp_path / "chat.md"
    session.write_text(
        """
User: 调研智能睡眠市场
Assistant: 我会搜索资料。
Error: timeout while searching
""".strip(),
        encoding="utf-8",
    )

    report = import_external_sessions([tmp_path])

    assert len(report.samples) == 1
    assert report.samples[0].success is False
    assert report.failures[0]["goal"] == "调研智能睡眠市场"
    assert "timeout" in report.failures[0]["last_error"]


def test_discover_external_session_roots_prefers_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ECHO_EVOLUTION_SESSION_PATHS", str(tmp_path))

    roots = discover_external_session_roots()

    assert roots == [tmp_path]
