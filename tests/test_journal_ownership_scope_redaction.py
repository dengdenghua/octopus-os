"""Regression coverage for PII-shaped journal ownership identifiers."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from runtime.memory.journal._journal_models import UserMessageEvent
from runtime.memory.journal.journal import (
    AssistantChunkEvent,
    FileOpEvent,
    JSONLJournal,
    TokenUsageEvent,
    ToolEffectIntentEvent,
)
from runtime.platform.observability.redactor import Redactor
from runtime.safety.auth.scope import TenantScope
from runtime.sensing.gateway.streaming_journal import StreamingJournal

_TENANT = "legacy:oct:alice@example.com"
_OWNER = "oct:alice@example.com"


def _event() -> TokenUsageEvent:
    return TokenUsageEvent(
        tenant_id=_TENANT,
        owner_actor_id=_OWNER,
        input_tokens=12,
        output_tokens=3,
        model="test-model",
    )


def test_canonical_write_pseudonymizes_pii_scope_without_collapsing_ownership(
    tmp_path,
) -> None:
    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path, redactor=Redactor())

    durable = journal.write_canonical(_event())

    raw = path.read_text(encoding="utf-8")
    row = json.loads(raw)
    assert "alice@example.com" not in raw
    assert "[REDACTED:email]" not in raw
    assert durable.tenant_id == row["tenant_id"]
    assert durable.owner_actor_id == row["owner_actor_id"]
    assert durable.tenant_id.startswith("echo-scope-tenant-vone-")
    assert durable.owner_actor_id.startswith("echo-scope-owner-vone-")
    assert durable.tenant_id != durable.owner_actor_id


def test_raw_request_scope_can_read_its_pseudonymized_journal_rows(tmp_path) -> None:
    journal = JSONLJournal(tmp_path / "events.jsonl", redactor=Redactor())
    journal.write(_event())

    visible = journal.read_all(scope=TenantScope(tenant_id=_TENANT, actor_id=_OWNER))
    hidden = journal.read_all(
        scope=TenantScope(
            tenant_id="legacy:oct:bob@example.com",
            actor_id="oct:bob@example.com",
        )
    )

    assert len(visible) == 1
    assert hidden == []


def test_redacted_reader_keeps_historical_raw_scope_rows_visible(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    JSONLJournal(path).write(_event())
    journal = JSONLJournal(path, redactor=Redactor())

    visible = journal.read_all(scope=TenantScope(tenant_id=_TENANT, actor_id=_OWNER))
    hidden = journal.read_all(
        scope=TenantScope(
            tenant_id="legacy:oct:bob@example.com",
            actor_id="oct:bob@example.com",
        )
    )

    assert len(visible) == 1
    assert visible[0].tenant_id == _TENANT
    assert visible[0].owner_actor_id == _OWNER
    assert hidden == []


def test_safe_scope_values_keep_their_existing_durable_identity(tmp_path) -> None:
    journal = JSONLJournal(tmp_path / "events.jsonl", redactor=Redactor())
    event = TokenUsageEvent(tenant_id="tenant-a", owner_actor_id="owner-a")

    durable = journal.write_canonical(event)

    assert durable.tenant_id == "tenant-a"
    assert durable.owner_actor_id == "owner-a"


def test_streaming_subscribers_receive_the_exact_pseudonymized_scope(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    journal = StreamingJournal(JSONLJournal(path, redactor=Redactor()))
    received = []
    journal.subscribe(received.append)

    journal.write(_event())

    assert len(received) == 1
    assert received[0].tenant_id.startswith("echo-scope-tenant-vone-")
    assert received[0].owner_actor_id.startswith("echo-scope-owner-vone-")
    assert received[0].model_dump(mode="json") == json.loads(path.read_text(encoding="utf-8"))


def test_payload_redaction_cannot_corrupt_phone_shaped_structural_uuid(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path, redactor=Redactor())
    canary = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC"
    event_id = UUID("eb432a35-71e8-450a-a0f6-e5810065551e")
    event = FileOpEvent(
        event_id=event_id,
        task_id=uuid4(),
        arm_id="arm-a",
        path="secrets.txt",
        action="write",
        bytes_delta=len(canary),
        diff=f"token={canary}",
    )

    durable = journal.write_canonical(event)
    raw = path.read_text(encoding="utf-8")

    assert durable.event_id == event_id
    assert json.loads(raw)["event_id"] == str(event_id)
    assert canary not in raw
    assert "[REDACTED:api_key]" in raw


def test_tool_effect_hashes_remain_exact_when_they_look_like_phone_numbers(
    tmp_path,
) -> None:
    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path, redactor=Redactor())
    effect_key = "effect:v1:bb132e6e0370a4e45da624068947c09c3872a052fe8951932b2e2c1f5fed4d0e"
    fingerprint = "9a317316471aec9ea7237e581f887c8dbcfa6b38492587d2f52adc9c0dff2cde"
    event = ToolEffectIntentEvent(
        task_id=UUID("523e5d3f-0e33-4c2b-9fa4-c9e49826832b"),
        arm_id="react_arm",
        effect_key=effect_key,
        call_id="2dcda849-3b19-4b60-b4b1-67bc3529853c",
        step_id=2,
        node_id="react_n1",
        sucker_id="list_cwd",
        args_fingerprint=fingerprint,
        side_effecting=False,
    )

    durable = journal.write_canonical(event)
    row = json.loads(path.read_text(encoding="utf-8"))

    assert durable.effect_key == row["effect_key"] == effect_key
    assert durable.args_fingerprint == row["args_fingerprint"] == fingerprint
    assert "[REDACTED:phone]" not in row["effect_key"]
    assert "[REDACTED:phone]" not in row["args_fingerprint"]


def test_packed_chunks_redact_payloads_without_rewriting_member_structure(
    tmp_path,
) -> None:
    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path, redactor=Redactor())
    canary = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC"
    ids = [
        UUID("eb432a35-71e8-450a-a0f6-e5810065551e"),
        UUID("c53f2179-d219-4be7-bf2f-b9008eceb652"),
        UUID("783e1105-1f4c-4f5b-a025-121994dfa1f7"),
    ]
    for index, event_id in enumerate(ids):
        journal.write(
            AssistantChunkEvent(
                event_id=event_id,
                iteration=1,
                kind="text-delta",
                delta=f"chunk-{index} {canary}",
            )
        )
    journal.write(UserMessageEvent(text="flush"))

    raw = path.read_text(encoding="utf-8")
    rows = raw.splitlines()
    restored = journal.read_by_type("assistant/chunk")

    assert "__chunk_row__" in rows[0]
    assert canary not in raw
    assert "[REDACTED:api_key]" in raw
    assert [event.event_id for event in restored] == ids
    assert all("[REDACTED:api_key]" in event.delta for event in restored)

