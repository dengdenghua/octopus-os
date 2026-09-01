"""Signed draft rules derived from replay-backed policy review proposals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runtime.safety.approval.approval_gate import ApprovalRule
from runtime.safety.approval.approval_policy_store import append_rule
from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalRecord

_SCHEMA = "echo.policy_review_rule_drafts.v1"
_DRAFT_SCHEMA = "echo.policy_review_rule_draft.v1"
_PLUGIN_DRAFTS_SCHEMA = "echo.plugin_permission_rule_drafts.v1"
_PLUGIN_DRAFT_SCHEMA = "echo.plugin_permission_review_rule_draft.v1"
_PLUGIN_EVIDENCE_SCHEMA = "echo.plugin_permission_review_evidence.v1"
_AUTOMATION_DRAFTS_SCHEMA = "echo.automation_policy_rule_drafts.v1"
_AUTOMATION_DRAFT_SCHEMA = "echo.automation_policy_review_rule_draft.v1"
_AUTOMATION_EVIDENCE_SCHEMA = "echo.automation_policy_review_evidence.v1"
_SIGNATURE_SCHEMA = "echo.policy_review_rule_signature.v1"

_AUTOMATION_REVIEW_TARGETS: tuple[dict[str, str], ...] = (
    {
        "id": "live_browser_bridge",
        "tool": "live_browser_*",
        "surface": "browser",
        "reason": "Live browser bridge can operate authenticated user sessions.",
    },
    {
        "id": "browser_pool",
        "tool": "browser_*",
        "surface": "browser",
        "reason": "Browser automation can navigate, click, type, and exfiltrate page data.",
    },
    {
        "id": "desktop_execute",
        "tool": "computer_execute_token",
        "surface": "desktop",
        "reason": "Desktop execute tokens can move the mouse and type on the host.",
    },
    {
        "id": "desktop_vision_planner",
        "tool": "computer_plan_next",
        "surface": "desktop",
        "reason": "Desktop vision planning sends a current screenshot to a planner.",
    },
    {
        "id": "desktop_mouse",
        "tool": "mouse_*",
        "surface": "desktop",
        "reason": "Mouse automation can operate local applications.",
    },
    {
        "id": "desktop_keyboard",
        "tool": "keyboard_*",
        "surface": "desktop",
        "reason": "Keyboard automation can enter or submit local application data.",
    },
    {
        "id": "screen_capture",
        "tool": "screen_*",
        "surface": "desktop",
        "reason": "Screen capture can expose private on-screen data.",
    },
)


def build_policy_review_rule_drafts(
    *,
    ledger_path: str | Path | None = None,
    records: list[ProposalRecord] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return signed, non-applied static-policy rule drafts.

    The output is intentionally a draft envelope, not a direct write to the
    approval policy store. A later operator step can verify the signature and
    decide whether the rule is precise enough to install.
    """

    proposals = (
        records
        if records is not None
        else ProposalLedger(ledger_path or "data/proposal_ledger.jsonl").query(
            kind="review_queue_policy_review",
            limit=limit,
        )
    )
    drafts = [
        draft
        for draft in (_draft_from_proposal(record) for record in proposals[-limit:])
        if draft is not None
    ]
    return {
        "schema": _SCHEMA,
        "total": len(drafts),
        "drafts": drafts,
    }


def build_plugin_permission_rule_drafts(
    *,
    plugins: list[dict[str, Any]],
    limit: int = 100,
) -> dict[str, Any]:
    """Return signed deny-rule drafts for plugin lifecycle risks.

    Codex-format plugins can introduce MCP servers, commands, apps, and
    package-level capability activation. Discovery already marks inferred
    permissions as ``review_required``; this turns those findings into the
    same signed policy-review draft shape used by repeated tool denials.
    """

    drafts: list[dict[str, Any]] = []
    for plugin in plugins[: max(0, limit)]:
        if isinstance(plugin, dict):
            drafts.extend(_drafts_from_plugin_permission_review(plugin))
        if len(drafts) >= limit:
            drafts = drafts[:limit]
            break
    return {
        "schema": _PLUGIN_DRAFTS_SCHEMA,
        "total": len(drafts),
        "drafts": drafts,
    }


def build_automation_policy_rule_drafts(
    *,
    targets: list[dict[str, Any]] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return signed deny-rule drafts for browser and desktop automation.

    These drafts are intentionally installable deny rules. They let an
    operator start from a conservative "block until reviewed" posture for
    high-risk browser and desktop primitives, then selectively replace them
    with narrower allow rules after replay evidence proves the workflow.
    """

    raw_targets = targets if targets is not None else list(_AUTOMATION_REVIEW_TARGETS)
    drafts: list[dict[str, Any]] = []
    for target in raw_targets[: max(0, limit)]:
        if not isinstance(target, dict):
            continue
        draft = _draft_from_automation_policy_target(target)
        if draft is not None:
            drafts.append(draft)
        if len(drafts) >= limit:
            break
    return {
        "schema": _AUTOMATION_DRAFTS_SCHEMA,
        "total": len(drafts),
        "drafts": drafts,
    }


def compute_automation_policy_rule_coverage(
    *,
    targets: list[dict[str, Any]] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return a release-gate proof for high-risk automation policy drafts."""

    report = build_automation_policy_rule_drafts(targets=targets, limit=limit)
    drafts = [draft for draft in report.get("drafts") or [] if isinstance(draft, dict)]
    required_tools = {
        "live_browser_*",
        "browser_*",
        "computer_execute_token",
        "computer_plan_next",
        "mouse_*",
        "keyboard_*",
        "screen_*",
    }
    covered_tools: set[str] = set()
    controls_by_tool: dict[str, set[str]] = {}
    invalid_draft_ids: list[str] = []
    missing_controls: dict[str, list[str]] = {}
    required_controls = {
        "signed_policy_review_rule",
        "preview_confirm_execute",
        "replay_gate_before_promotion",
    }
    installable_deny_count = 0
    for draft in drafts:
        if verify_policy_review_rule_draft(draft).get("ok") is not True:
            invalid_draft_ids.append(str(draft.get("draft_id") or ""))
            continue
        payload = draft.get("signed_payload")
        payload = payload if isinstance(payload, dict) else {}
        rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else {}
        tool = str(rule.get("tool") or "")
        if not tool:
            invalid_draft_ids.append(str(draft.get("draft_id") or ""))
            continue
        covered_tools.add(tool)
        if rule.get("effect") == "deny":
            installable_deny_count += 1
        evidence = payload.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        controls = {str(item) for item in evidence.get("controls") or [] if str(item)}
        controls_by_tool[tool] = controls
        missing = sorted(required_controls - controls)
        if missing:
            missing_controls[tool] = missing
    missing_tools = sorted(required_tools - covered_tools)
    ready = (
        bool(drafts)
        and not missing_tools
        and not invalid_draft_ids
        and not missing_controls
        and installable_deny_count == len(drafts)
    )
    next_actions = [f"Add automation policy draft for {tool}." for tool in missing_tools]
    next_actions.extend(
        f"Add controls {', '.join(controls)} to automation policy draft for {tool}."
        for tool, controls in sorted(missing_controls.items())
    )
    if invalid_draft_ids:
        next_actions.append(
            f"Regenerate {len(invalid_draft_ids)} invalid automation policy draft(s)."
        )
    if installable_deny_count != len(drafts):
        next_actions.append("Keep automation policy drafts installable as deny rules.")
    return {
        "schema": "echo.automation_policy_rule_coverage.v1",
        "ready": ready,
        "total": len(drafts),
        "verified": len(drafts) - len(invalid_draft_ids),
        "installable_deny_count": installable_deny_count,
        "required_tools": sorted(required_tools),
        "covered_tools": sorted(covered_tools),
        "missing_tools": missing_tools,
        "invalid_draft_ids": invalid_draft_ids,
        "required_controls": sorted(required_controls),
        "controls_by_tool": {
            tool: sorted(controls) for tool, controls in sorted(controls_by_tool.items())
        },
        "missing_controls": missing_controls,
        "next_actions": next_actions,
    }


def verify_policy_review_rule_draft(draft: dict[str, Any]) -> dict[str, Any]:
    payload = draft.get("signed_payload") if isinstance(draft, dict) else None
    signature = draft.get("signature") if isinstance(draft, dict) else None
    if not isinstance(payload, dict) or not isinstance(signature, dict):
        return {
            "schema": "echo.policy_review_rule_signature_check.v1",
            "ok": False,
            "reason": "missing signed payload or signature",
        }
    expected = _signature_for_payload(payload)
    actual = str(signature.get("digest") or "")
    return {
        "schema": "echo.policy_review_rule_signature_check.v1",
        "ok": actual == expected,
        "reason": "ok" if actual == expected else "signature mismatch",
        "expected_digest": expected,
        "actual_digest": actual,
    }


def install_policy_review_rule_draft(
    draft: dict[str, Any],
    *,
    policy_path: str | Path,
    confirm_install: bool,
) -> dict[str, Any]:
    if confirm_install is not True:
        raise ValueError("confirm_install=true is required")
    check = verify_policy_review_rule_draft(draft)
    if check.get("ok") is not True:
        raise ValueError(str(check.get("reason") or "invalid draft signature"))
    payload = draft.get("signed_payload") if isinstance(draft, dict) else {}
    rule_payload = (
        payload.get("rule")
        if isinstance(payload, dict) and isinstance(payload.get("rule"), dict)
        else {}
    )
    if rule_payload.get("effect") != "deny":
        raise ValueError("only deny policy-review drafts can be installed")
    rule = ApprovalRule(
        effect="deny",
        tool=_clean_text(rule_payload.get("tool"), limit=120) or "*",
        args_contains=_clean_text(rule_payload.get("args_contains"), limit=240),
        reason=_clean_text(rule_payload.get("reason"), limit=600),
    )
    policy = append_rule(Path(policy_path), rule)
    return {
        "schema": "echo.policy_review_rule_install.v1",
        "installed": True,
        "draft_id": draft.get("draft_id"),
        "source_kind": _clean_text(
            payload.get("proposal_kind") or payload.get("schema"),
            limit=120,
        ),
        "rule": {
            "effect": rule.effect,
            "tool": rule.tool,
            "args_contains": rule.args_contains,
            "reason": rule.reason,
        },
        "policy_rule_count": len(policy.rules),
        "signature": draft.get("signature") if isinstance(draft.get("signature"), dict) else {},
    }


def _drafts_from_plugin_permission_review(plugin: dict[str, Any]) -> list[dict[str, Any]]:
    smoke = plugin.get("smoke") if isinstance(plugin.get("smoke"), dict) else {}
    permission = (
        smoke.get("permission_resolution")
        if isinstance(smoke.get("permission_resolution"), dict)
        else {}
    )
    if permission.get("review_required") is not True:
        return []
    permissions = [
        _clean_text(item, limit=160)
        for item in (permission.get("permissions") or [])
        if _clean_text(item, limit=160)
    ]
    executable_permissions = [item for item in permissions if not item.startswith("ui:")]
    if not executable_permissions:
        return []
    plugin_id = _clean_text(plugin.get("id") or plugin.get("name"), limit=120)
    if not plugin_id:
        return []
    surfaces = smoke.get("surfaces") if isinstance(smoke.get("surfaces"), dict) else {}
    evidence = {
        "schema": _PLUGIN_EVIDENCE_SCHEMA,
        "plugin_id": plugin_id,
        "plugin_name": _clean_text(plugin.get("name"), limit=160),
        "plugin_path": _clean_text(plugin.get("path"), limit=500),
        "plugin_smoke": smoke,
        "permission_resolution": permission,
        "surfaces": surfaces,
    }
    targets: list[dict[str, str]] = []
    if any(item.startswith("mcp:") for item in executable_permissions):
        server_names = _plugin_mcp_server_names(plugin)
        if server_names:
            for server_name in server_names:
                targets.append(
                    {
                        "tool": f"mcp__{_tool_fragment(server_name)}__*",
                        "args_contains": "",
                        "surface": "mcp",
                    }
                )
        else:
            targets.append(
                {
                    "tool": f"mcp__*{_tool_fragment(plugin_id)}*",
                    "args_contains": "",
                    "surface": "mcp",
                }
            )
    if any(
        bool(surfaces.get(key)) for key in ("capabilities", "skills", "apps", "commands", "mcp")
    ):
        targets.append(
            {
                "tool": "use_capability",
                "args_contains": plugin_id,
                "surface": "capability",
            }
        )
    drafts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        key = (target["tool"], target["args_contains"])
        if key in seen:
            continue
        seen.add(key)
        reason = (
            f"Plugin {plugin_id} requires permission review before enabling "
            f"{target['surface']} surface: {', '.join(executable_permissions)}"
        )
        signed_payload = {
            "schema": _PLUGIN_DRAFT_SCHEMA,
            "proposal_id": f"plugin:{plugin_id}:{target['surface']}",
            "proposal_kind": "plugin_permission_review",
            "review_queue_item_id": None,
            "plugin": {
                "id": plugin_id,
                "name": _clean_text(plugin.get("name"), limit=160),
                "path": _clean_text(plugin.get("path"), limit=500),
            },
            "rule": {
                "effect": "deny",
                "tool": target["tool"],
                "args_contains": target["args_contains"],
                "reason": reason,
            },
            "evidence": evidence,
            "review_required": True,
        }
        digest = _signature_for_payload(signed_payload)
        drafts.append(
            {
                "schema": _DRAFT_SCHEMA,
                "draft_id": f"prd_{digest[:20]}",
                "signed_payload": signed_payload,
                "signature": {
                    "schema": _SIGNATURE_SCHEMA,
                    "algorithm": "sha256:canonical-json",
                    "digest": digest,
                },
            }
        )
    return drafts


def _draft_from_automation_policy_target(target: dict[str, Any]) -> dict[str, Any] | None:
    target_id = _clean_text(target.get("id"), limit=120)
    tool = _clean_text(target.get("tool"), limit=120)
    surface = _clean_text(target.get("surface"), limit=120)
    reason = _clean_text(target.get("reason"), limit=600)
    if not target_id or not tool:
        return None
    signed_payload = {
        "schema": _AUTOMATION_DRAFT_SCHEMA,
        "proposal_id": f"automation:{target_id}",
        "proposal_kind": "automation_policy_review",
        "review_queue_item_id": None,
        "automation": {
            "id": target_id,
            "surface": surface,
            "tool": tool,
        },
        "rule": {
            "effect": "deny",
            "tool": tool,
            "args_contains": _clean_text(target.get("args_contains"), limit=240),
            "reason": reason or f"Automation policy review required for {tool}",
        },
        "evidence": {
            "schema": _AUTOMATION_EVIDENCE_SCHEMA,
            "source": "automation_safety_baseline",
            "target": {
                "id": target_id,
                "surface": surface,
                "tool": tool,
            },
            "controls": [
                "signed_policy_review_rule",
                "preview_confirm_execute",
                "replay_gate_before_promotion",
            ],
        },
        "review_required": True,
    }
    digest = _signature_for_payload(signed_payload)
    return {
        "schema": _DRAFT_SCHEMA,
        "draft_id": f"prd_{digest[:20]}",
        "signed_payload": signed_payload,
        "signature": {
            "schema": _SIGNATURE_SCHEMA,
            "algorithm": "sha256:canonical-json",
            "digest": digest,
        },
    }


def _draft_from_proposal(record: ProposalRecord) -> dict[str, Any] | None:
    if record.kind != "review_queue_policy_review":
        return None
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    item = metadata.get("item") if isinstance(metadata.get("item"), dict) else {}
    item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    latest = (
        item_metadata.get("latest_denial")
        if isinstance(item_metadata.get("latest_denial"), dict)
        else {}
    )
    tool_name = _clean_text(
        item_metadata.get("tool_name")
        or latest.get("tool_name")
        or _tool_name_from_title(item.get("title")),
        limit=120,
    )
    if not tool_name:
        return None
    reason = _clean_text(
        latest.get("reason") or item.get("text") or record.description,
        limit=240,
    )
    evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {}
    signed_payload = {
        "schema": _DRAFT_SCHEMA,
        "proposal_id": record.proposal_id,
        "proposal_kind": record.kind,
        "review_queue_item_id": metadata.get("review_queue_item_id"),
        "rule": {
            "effect": "deny",
            "tool": tool_name,
            "args_contains": "",
            "reason": reason or f"Replay-backed policy review for {tool_name}",
        },
        "evidence": evidence,
        "review_required": True,
    }
    digest = _signature_for_payload(signed_payload)
    return {
        "schema": _DRAFT_SCHEMA,
        "draft_id": f"prd_{digest[:20]}",
        "signed_payload": signed_payload,
        "signature": {
            "schema": _SIGNATURE_SCHEMA,
            "algorithm": "sha256:canonical-json",
            "digest": digest,
        },
    }


def _signature_for_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _plugin_mcp_server_names(plugin: dict[str, Any]) -> list[str]:
    names: list[str] = []
    runtime = plugin.get("runtime") if isinstance(plugin.get("runtime"), dict) else {}
    runtime_servers = (
        runtime.get("mcp_servers") if isinstance(runtime.get("mcp_servers"), list) else []
    )
    for item in runtime_servers:
        if isinstance(item, dict):
            name = _clean_text(item.get("name"), limit=120)
            if name:
                names.append(name)
    path_text = _clean_text(plugin.get("path"), limit=500)
    if path_text:
        plugin_dir = Path(path_text)
        for payload in (
            _read_json(plugin_dir / ".codex-plugin" / "plugin.json"),
            _read_json(plugin_dir / ".mcp.json"),
        ):
            raw = payload.get("mcpServers") if isinstance(payload, dict) else None
            if not isinstance(raw, dict):
                continue
            for name in raw:
                text = _clean_text(name, limit=120)
                if text:
                    names.append(text)
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tool_fragment(value: Any) -> str:
    text = _clean_text(value, limit=120)
    out = []
    for char in text:
        if char.isalnum() or char in {"_", "-"}:
            out.append(char)
        elif char.isspace() or char in {".", "/"}:
            out.append("_")
    fragment = "".join(out).strip("_")
    return fragment or "*"


def _tool_name_from_title(value: Any) -> str:
    text = _clean_text(value, limit=180)
    prefix = "Review repeated denials for "
    if text.startswith(prefix):
        return text[len(prefix) :].strip()
    return ""


def _clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


__all__ = [
    "build_automation_policy_rule_drafts",
    "build_plugin_permission_rule_drafts",
    "build_policy_review_rule_drafts",
    "compute_automation_policy_rule_coverage",
    "install_policy_review_rule_draft",
    "verify_policy_review_rule_draft",
]
