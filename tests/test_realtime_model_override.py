from runtime.execution.codex_backend.role_runner import ServerCodexExecutionOverride
from runtime.protocol.items import TurnParams
from runtime.sensing.gateway._realtime_react_stream_helpers import (
    _apply_react_session_metadata,
)
from runtime.sensing.gateway.realtime_cerebrum import _build_intent
from runtime.sensing.gateway.realtime_turn_input import external_model_owner
from runtime.sensing.gateway.turn_session import build_turn_metadata


def test_task_codex_model_is_promoted_to_server_owned_override() -> None:
    metadata = {
        "execution_engine": "codex",
        "model_scope": "task",
        "model_name": "chatgpt/gpt-5.6-codex",
        "reasoning_effort": "high",
    }

    _apply_react_session_metadata(metadata, object(), object())

    override = metadata["_server_codex_execution_override"]
    assert isinstance(override, ServerCodexExecutionOverride)
    assert override.model == "gpt-5.6-codex"
    assert override.reasoning_effort == "high"
    assert override.source == "chatgpt"


def test_client_cannot_retain_private_codex_override_without_task_scope() -> None:
    forged = ServerCodexExecutionOverride(model="unsafe")
    metadata = {
        "execution_engine": "codex",
        "model_scope": "system",
        "_server_codex_execution_override": forged,
    }

    _apply_react_session_metadata(metadata, object(), object())

    assert "_server_codex_execution_override" not in metadata


def test_turn_metadata_preserves_engine_and_scope_for_server_resolution() -> None:
    metadata = build_turn_metadata(
        thread_id="thread-1",
        body={
            "context": {
                "execution_engine": "codex",
                "model_scope": "task",
                "model_name": "gpt-5.6-codex",
            }
        },
        store=None,
    )

    assert metadata["execution_engine"] == "codex"
    assert metadata["model_scope"] == "task"
    assert metadata["model_name"] == "gpt-5.6-codex"


def test_turn_metadata_carries_bounded_context_file_references() -> None:
    metadata = build_turn_metadata(
        thread_id="thread-1",
        body={
            "context": {
                "context_files": [
                    {
                        "path": "docs/report.pdf",
                        "workDir": "C:/workspace",
                        "sourceLabel": "本地数据库",
                        "resourceId": "appliance-file:v1:root:report",
                        "ignored": "drop-me",
                    },
                    {
                        "resourceId": "storage-file:v1:server:only-id",
                        "sourceLabel": "知识库",
                    },
                    {"path": "  "},
                ]
            }
        },
        store=None,
    )

    assert metadata["context_files"] == [
        {
            "path": "docs/report.pdf",
            "work_dir": "C:/workspace",
            "source_label": "本地数据库",
            "resource_id": "appliance-file:v1:root:report",
        },
        {
            "source_label": "知识库",
            "resource_id": "storage-file:v1:server:only-id",
        },
    ]


def test_auto_preview_preserves_external_model_owner() -> None:
    params = TurnParams.model_validate(
        {
            "threadId": "thread-opencode",
            "executionEngine": "auto",
            "input": [
                {
                    "type": "text",
                    "text": "继续",
                    "metadata": {"context": {"execution_engine": "opencode"}},
                }
            ],
        }
    )

    assert external_model_owner(params) == "opencode"


def test_audit_imperative_promotes_to_executable_development() -> None:
    params = TurnParams.model_validate(
        {
            "threadId": "thread-audit-repair",
            "input": [
                {
                    "type": "text",
                    "text": "请优化这个项目",
                    "metadata": {"context": {"mode": "audit", "audit_mode": True}},
                }
            ],
        }
    )

    intent = _build_intent("请优化这个项目", params)

    assert intent.user_context["audit_mode"] is False
    assert intent.user_context["mode"] == "code"
    assert intent.user_context["workflow_preset"] == "develop.iterate"
