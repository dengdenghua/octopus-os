from __future__ import annotations

from runtime.safety.approval.approval_policy_store import load_policy
from runtime.safety.evolution.policy_review_rules import (
    build_automation_policy_rule_drafts,
    build_plugin_permission_rule_drafts,
    build_policy_review_rule_drafts,
    compute_automation_policy_rule_coverage,
    install_policy_review_rule_draft,
    verify_policy_review_rule_draft,
)
from runtime.safety.evolution.proposal_ledger import ProposalLedger


def test_policy_review_rule_draft_is_signed_from_replay_backed_proposal(
    tmp_path,
) -> None:
    ledger = ProposalLedger(tmp_path / "proposal_ledger.jsonl")
    proposal = ledger.propose(
        kind="review_queue_policy_review",
        description="Review repeated denials for exec_shell",
        metadata={
            "review_queue_item_id": "rq_1",
            "item": {
                "title": "Review repeated denials for exec_shell",
                "text": "Tool exec_shell was denied repeatedly.",
                "metadata": {
                    "tool_name": "exec_shell",
                    "latest_denial": {
                        "tool_name": "exec_shell",
                        "reason": "no destructive shell",
                    },
                },
            },
            "evidence": {
                "schema": "echo.policy_review_promotion_evidence.v1",
                "replay": {
                    "case_id": "task-run:abc123",
                    "fingerprint": "abc123",
                    "replayable": True,
                },
                "replay_gate": {"passed": True},
            },
        },
    )

    report = build_policy_review_rule_drafts(ledger_path=tmp_path / "proposal_ledger.jsonl")
    draft = report["drafts"][0]

    assert report["schema"] == "echo.policy_review_rule_drafts.v1"
    assert report["total"] == 1
    assert draft["draft_id"].startswith("prd_")
    payload = draft["signed_payload"]
    assert payload["proposal_id"] == proposal.proposal_id
    assert payload["rule"] == {
        "effect": "deny",
        "tool": "exec_shell",
        "args_contains": "",
        "reason": "no destructive shell",
    }
    assert payload["evidence"]["replay"]["case_id"] == "task-run:abc123"
    assert verify_policy_review_rule_draft(draft)["ok"] is True


def test_policy_review_rule_draft_signature_detects_tampering(tmp_path) -> None:
    ledger = ProposalLedger(tmp_path / "proposal_ledger.jsonl")
    ledger.propose(
        kind="review_queue_policy_review",
        description="Review repeated denials for exec_shell",
        metadata={
            "review_queue_item_id": "rq_1",
            "item": {
                "title": "Review repeated denials for exec_shell",
                "text": "Tool exec_shell was denied repeatedly.",
            },
            "evidence": {"replay_gate": {"passed": True}},
        },
    )
    draft = build_policy_review_rule_drafts(
        ledger_path=tmp_path / "proposal_ledger.jsonl",
    )["drafts"][0]
    draft["signed_payload"]["rule"]["tool"] = "read_file"

    check = verify_policy_review_rule_draft(draft)

    assert check["ok"] is False
    assert check["reason"] == "signature mismatch"


def test_install_policy_review_rule_draft_requires_confirmation(tmp_path) -> None:
    ledger = ProposalLedger(tmp_path / "proposal_ledger.jsonl")
    ledger.propose(
        kind="review_queue_policy_review",
        description="Review repeated denials for exec_shell",
        metadata={
            "review_queue_item_id": "rq_1",
            "item": {
                "title": "Review repeated denials for exec_shell",
                "text": "Tool exec_shell was denied repeatedly.",
            },
            "evidence": {"replay_gate": {"passed": True}},
        },
    )
    draft = build_policy_review_rule_drafts(
        ledger_path=tmp_path / "proposal_ledger.jsonl",
    )["drafts"][0]

    try:
        install_policy_review_rule_draft(
            draft,
            policy_path=tmp_path / "permissions.json",
            confirm_install=False,
        )
    except ValueError as exc:
        assert str(exc) == "confirm_install=true is required"
    else:
        raise AssertionError("expected confirmation failure")

    assert load_policy(tmp_path / "permissions.json").rules == ()


def test_install_policy_review_rule_draft_appends_deny_rule(tmp_path) -> None:
    ledger = ProposalLedger(tmp_path / "proposal_ledger.jsonl")
    ledger.propose(
        kind="review_queue_policy_review",
        description="Review repeated denials for exec_shell",
        metadata={
            "review_queue_item_id": "rq_1",
            "item": {
                "title": "Review repeated denials for exec_shell",
                "text": "Tool exec_shell was denied repeatedly.",
                "metadata": {
                    "tool_name": "exec_shell",
                    "latest_denial": {
                        "tool_name": "exec_shell",
                        "reason": "no destructive shell",
                    },
                },
            },
            "evidence": {"replay_gate": {"passed": True}},
        },
    )
    draft = build_policy_review_rule_drafts(
        ledger_path=tmp_path / "proposal_ledger.jsonl",
    )["drafts"][0]

    result = install_policy_review_rule_draft(
        draft,
        policy_path=tmp_path / "permissions.json",
        confirm_install=True,
    )
    policy = load_policy(tmp_path / "permissions.json")

    assert result["installed"] is True
    assert result["policy_rule_count"] == 1
    assert len(policy.rules) == 1
    assert policy.rules[0].effect == "deny"
    assert policy.rules[0].tool == "exec_shell"
    assert policy.rules[0].reason == "no destructive shell"


def test_plugin_permission_review_drafts_are_signed_for_mcp_plugin(tmp_path) -> None:
    plugin_dir = tmp_path / "research"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        '{"name":"research","version":"0.1.0"}',
        encoding="utf-8",
    )
    (plugin_dir / ".mcp.json").write_text(
        '{"mcpServers":{"research":{"command":"node"}}}',
        encoding="utf-8",
    )
    plugin = {
        "id": "research",
        "name": "Research",
        "path": str(plugin_dir),
        "smoke": {
            "schema": "echo.codex_plugin_smoke.v1",
            "surfaces": {"capabilities": True, "skills": True, "mcp": True},
            "permission_resolution": {
                "schema": "echo.codex_plugin_permission_resolution.v1",
                "status": "review_required",
                "review_required": True,
                "permissions": ["mcp:execute:review_required", "ui:metadata:local"],
            },
        },
    }

    report = build_plugin_permission_rule_drafts(plugins=[plugin])
    drafts = report["drafts"]

    assert report["schema"] == "echo.plugin_permission_rule_drafts.v1"
    assert report["total"] == 2
    assert {draft["signed_payload"]["rule"]["tool"] for draft in drafts} == {
        "mcp__research__*",
        "use_capability",
    }
    use_capability = next(
        draft for draft in drafts if draft["signed_payload"]["rule"]["tool"] == "use_capability"
    )
    assert use_capability["signed_payload"]["rule"]["args_contains"] == "research"
    assert use_capability["signed_payload"]["proposal_kind"] == "plugin_permission_review"
    assert verify_policy_review_rule_draft(use_capability)["ok"] is True


def test_plugin_permission_review_ignores_metadata_only_plugin() -> None:
    report = build_plugin_permission_rule_drafts(
        plugins=[
            {
                "id": "theme",
                "name": "Theme",
                "smoke": {
                    "surfaces": {"capabilities": False, "mcp": False},
                    "permission_resolution": {
                        "review_required": True,
                        "permissions": ["ui:metadata:local"],
                    },
                },
            },
        ],
    )

    assert report["total"] == 0


def test_automation_policy_review_drafts_are_signed_for_browser_and_desktop() -> None:
    report = build_automation_policy_rule_drafts()
    drafts = report["drafts"]

    assert report["schema"] == "echo.automation_policy_rule_drafts.v1"
    assert report["total"] >= 7
    tools = {draft["signed_payload"]["rule"]["tool"] for draft in drafts}
    assert {
        "live_browser_*",
        "browser_*",
        "computer_execute_token",
        "computer_plan_next",
        "mouse_*",
        "keyboard_*",
        "screen_*",
    } <= tools
    desktop_execute = next(
        draft
        for draft in drafts
        if draft["signed_payload"]["rule"]["tool"] == "computer_execute_token"
    )
    assert desktop_execute["signed_payload"]["proposal_kind"] == ("automation_policy_review")
    assert desktop_execute["signed_payload"]["evidence"]["controls"] == [
        "signed_policy_review_rule",
        "preview_confirm_execute",
        "replay_gate_before_promotion",
    ]
    assert verify_policy_review_rule_draft(desktop_execute)["ok"] is True


def test_automation_policy_rule_coverage_proves_signed_installable_controls() -> None:
    coverage = compute_automation_policy_rule_coverage()

    assert coverage["schema"] == "echo.automation_policy_rule_coverage.v1"
    assert coverage["ready"] is True
    assert coverage["verified"] == coverage["total"]
    assert coverage["installable_deny_count"] == coverage["total"]
    assert coverage["missing_tools"] == []
    assert coverage["invalid_draft_ids"] == []
    assert coverage["missing_controls"] == {}
    assert "preview_confirm_execute" in coverage["required_controls"]
    assert set(coverage["required_tools"]) <= set(coverage["covered_tools"])


def test_automation_policy_rule_coverage_detects_missing_high_risk_target() -> None:
    coverage = compute_automation_policy_rule_coverage(
        targets=[
            {
                "id": "browser_pool",
                "tool": "browser_*",
                "surface": "browser",
                "reason": "Browser automation review.",
            },
        ],
    )

    assert coverage["ready"] is False
    assert "computer_execute_token" in coverage["missing_tools"]
    assert coverage["next_actions"]


def test_install_automation_policy_review_draft_appends_deny_rule(tmp_path) -> None:
    draft = next(
        item
        for item in build_automation_policy_rule_drafts()["drafts"]
        if item["signed_payload"]["rule"]["tool"] == "live_browser_*"
    )

    result = install_policy_review_rule_draft(
        draft,
        policy_path=tmp_path / "permissions.json",
        confirm_install=True,
    )
    policy = load_policy(tmp_path / "permissions.json")

    assert result["source_kind"] == "automation_policy_review"
    assert result["rule"]["tool"] == "live_browser_*"
    assert len(policy.rules) == 1
    assert policy.rules[0].effect == "deny"
    assert policy.rules[0].tool == "live_browser_*"


def test_install_plugin_permission_review_draft_appends_deny_rule(tmp_path) -> None:
    report = build_plugin_permission_rule_drafts(
        plugins=[
            {
                "id": "research",
                "name": "Research",
                "smoke": {
                    "surfaces": {"capabilities": True, "mcp": False},
                    "permission_resolution": {
                        "review_required": True,
                        "permissions": ["app:render:review_required"],
                    },
                },
            },
        ],
    )
    draft = report["drafts"][0]

    result = install_policy_review_rule_draft(
        draft,
        policy_path=tmp_path / "permissions.json",
        confirm_install=True,
    )
    policy = load_policy(tmp_path / "permissions.json")

    assert result["source_kind"] == "plugin_permission_review"
    assert result["rule"]["tool"] == "use_capability"
    assert result["rule"]["args_contains"] == "research"
    assert len(policy.rules) == 1
    assert policy.rules[0].effect == "deny"
    assert policy.rules[0].tool == "use_capability"

