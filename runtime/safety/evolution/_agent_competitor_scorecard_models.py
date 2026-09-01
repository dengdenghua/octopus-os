from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMPETITORS: tuple[str, ...] = (
    "codex",
    "claude_code",
    "openclaw",
    "hermes",
    "echo",
)
ECHO_COMPETITOR = "echo"
EXTERNAL_COMPETITORS: tuple[str, ...] = tuple(
    competitor for competitor in COMPETITORS if competitor != ECHO_COMPETITOR
)
DEFAULT_TARGET_SCORE = 95
SCORECARD_CALIBRATION_AS_OF = "2026-08-04"
SCORECARD_CALIBRATION_SOURCE_REVISION = "a41dc160a4056563891cc069fcbcf6b961cf56d9"
SCORECARD_CALIBRATION_MAX_AGE_DAYS = 90
BASELINE_CONTEXT: dict[str, Any] = {
    "as_of": SCORECARD_CALIBRATION_AS_OF,
    "source": "git_commit",
    "source_revision": SCORECARD_CALIBRATION_SOURCE_REVISION,
    "source_path": "runtime/safety/evolution/_agent_competitor_scorecard_models.py",
    "max_age_days": SCORECARD_CALIBRATION_MAX_AGE_DAYS,
    "score_kind": "architecture_capability_estimate",
    "score_basis": (
        "version-controlled static architecture estimate calibrated against the public "
        "Codex surface; it remains separate from behavioral release evidence"
    ),
    "codex_surface": (
        "combined Codex desktop app, CLI, cloud execution, skills/plugins, "
        "browser/computer-use, automations, and multi-agent collaboration"
    ),
    "excludes": "legacy CLI-only comparisons",
    "behavioral_authority": (
        "independent commit-bound release authority; it does not mutate static overall scores"
    ),
    "official_references": (
        "https://openai.com/index/introducing-the-codex-app/",
        "https://openai.com/index/codex-for-almost-everything/",
    ),
}


@dataclass(frozen=True)
class ScoreDimension:
    id: str
    title: str
    weight: int
    why: str
    scores: dict[str, int]
    echo_evidence_ids: tuple[str, ...]
    echo_next_actions: tuple[str, ...]


DIMENSIONS: tuple[ScoreDimension, ...] = (
    ScoreDimension(
        id="general_agent_loop",
        title="General agent loop",
        weight=7,
        why="Handle broad user goals, choose tools, keep context, and finish without forcing a coding-only path.",
        scores={
            "codex": 97,
            "claude_code": 88,
            "openclaw": 86,
            "hermes": 88,
            "echo": 100,
        },
        echo_evidence_ids=(
            "code_execution_loop",
            "browser_computer_use",
            "record_replay_gate",
        ),
        echo_next_actions=(
            "Extend mixed-mode completion contracts to memory, email, calendar, and document workflows.",
            "Add behavioral head-to-head evals for mixed browser, repository, and verification turns.",
        ),
    ),
    ScoreDimension(
        id="digital_employee_workflows",
        title="Digital employee workflows",
        weight=7,
        why="Run persistent workspaces, recurring tasks, handoffs, memory, and accountable long-running work.",
        scores={
            "codex": 96,
            "claude_code": 78,
            "openclaw": 88,
            "hermes": 89,
            "echo": 97,
        },
        echo_evidence_ids=(
            "agent_organization_os",
            "long_term_learning",
            "browser_computer_use",
        ),
        echo_next_actions=(
            "Add first-class recurring digital-employee runs with replay-backed handoff summaries.",
            "Promote cowork project execution into an operator-visible employee timeline.",
        ),
    ),
    ScoreDimension(
        id="core_coding_loop",
        title="Core coding loop",
        weight=10,
        why="Plan, edit, run, verify, and recover inside a real repository.",
        scores={
            "codex": 99,
            "claude_code": 96,
            "openclaw": 76,
            "hermes": 80,
            "echo": 100,
        },
        echo_evidence_ids=("code_execution_loop",),
        echo_next_actions=(
            "Keep model-driven verifier repairs capped at two attempts and require fresh passing evidence.",
        ),
    ),
    ScoreDimension(
        id="repo_context",
        title="Repository context",
        weight=7,
        why="Sustain a correct mental model across large, dirty, multi-module worktrees.",
        scores={
            "codex": 98,
            "claude_code": 95,
            "openclaw": 82,
            "hermes": 84,
            "echo": 99,
        },
        echo_evidence_ids=("code_execution_loop", "long_term_learning"),
        echo_next_actions=(
            "Keep repo-context citations, dirty-worktree classification, and optimistic write fingerprints release-gated.",
            "Surface memory quality scores in code-mode context traces.",
        ),
    ),
    ScoreDimension(
        id="product_experience",
        title="IDE and product experience",
        weight=5,
        why="Make the working loop feel fast, obvious, and low-friction for operators.",
        scores={
            "codex": 98,
            "claude_code": 89,
            "openclaw": 82,
            "hermes": 82,
            "echo": 99,
        },
        echo_evidence_ids=("code_execution_loop", "browser_computer_use"),
        echo_next_actions=(
            "Keep auth, workspace, mode-switching, and keyboard remediation regressions in the frontend release gate.",
            "Keep source-case promotion operator-gated after automated replay reruns.",
        ),
    ),
    ScoreDimension(
        id="permissions_sandbox",
        title="Permissions and sandbox",
        weight=7,
        why="Prevent unsafe local execution while preserving useful autonomy.",
        scores={
            "codex": 97,
            "claude_code": 94,
            "openclaw": 84,
            "hermes": 84,
            "echo": 98,
        },
        echo_evidence_ids=("approvals_sandbox_security", "governance_audit"),
        echo_next_actions=(
            "Add expiring operator grants with explicit renewal and revocation receipts.",
            "Expose delegated-context stripping trends by tool and agent role.",
        ),
    ),
    ScoreDimension(
        id="record_replay_audit",
        title="Record, replay, and audit",
        weight=6,
        why="Make important behavior reproducible, reviewable, and rollback-friendly.",
        scores={
            "codex": 96,
            "claude_code": 86,
            "openclaw": 82,
            "hermes": 83,
            "echo": 97,
        },
        echo_evidence_ids=("record_replay_gate", "governance_audit"),
        echo_next_actions=(
            "Keep governance-chain export and replay-gate overrides in release audits.",
            "Expand replay latency budgets to pixel and multi-agent trace corpora.",
        ),
    ),
    ScoreDimension(
        id="subagents_parallelism",
        title="Multi-agent orchestration",
        weight=8,
        why="Delegate and coordinate work without polluting the main context or losing traceability.",
        scores={
            "codex": 98,
            "claude_code": 80,
            "openclaw": 84,
            "hermes": 85,
            "echo": 99,
        },
        echo_evidence_ids=("subagents_parallel_work", "agent_organization_os"),
        echo_next_actions=(
            "Expose timeout, queue, and cancellation-latency trends per agent role.",
            "Keep process-isolation compatibility and worker replacement limits release-gated.",
        ),
    ),
    ScoreDimension(
        id="model_provider_plugin_interop",
        title="Explicit model-provider plugin interoperability",
        weight=5,
        why=(
            "Install, validate, and hot-register external model gateways without "
            "handing orchestration, tools, memory, or secrets to an opaque local CLI."
        ),
        scores={
            "codex": 94,
            "claude_code": 82,
            "openclaw": 78,
            "hermes": 82,
            "echo": 98,
        },
        echo_evidence_ids=("model_provider_plugin_interop", "skills_plugins_hooks"),
        echo_next_actions=(
            "Keep credential references, provider probes, and live route teardown release-gated.",
            "Add retained health-check receipts and setup history in the provider plugin UI.",
        ),
    ),
    ScoreDimension(
        id="extensions_hooks",
        title="Extensions, hooks, and rules",
        weight=6,
        why="Let operators add durable local capabilities without patching core code.",
        scores={
            "codex": 98,
            "claude_code": 96,
            "openclaw": 90,
            "hermes": 88,
            "echo": 99,
        },
        echo_evidence_ids=("skills_plugins_hooks", "approvals_sandbox_security"),
        echo_next_actions=(
            "Keep publisher key rotation and revocation controls release-gated.",
            "Add UI install controls for plugin permission rule drafts.",
        ),
    ),
    ScoreDimension(
        id="browser_desktop",
        title="Browser and desktop ops",
        weight=7,
        why="Inspect screens, operate browsers, and validate visual state.",
        scores={
            "codex": 98,
            "claude_code": 84,
            "openclaw": 78,
            "hermes": 78,
            "echo": 99,
        },
        echo_evidence_ids=("browser_computer_use",),
        echo_next_actions=(
            "Keep resolved capture paths, fresh replay evidence, and zero pending P0 automation cases release-gated.",
        ),
    ),
    ScoreDimension(
        id="long_term_learning",
        title="Long-term memory and knowledge brain",
        weight=8,
        why="Carry proven experience forward across tasks, agents, and releases.",
        scores={
            "codex": 95,
            "claude_code": 78,
            "openclaw": 91,
            "hermes": 92,
            "echo": 97,
        },
        echo_evidence_ids=("long_term_learning", "self_evolution_canary"),
        echo_next_actions=(
            "Turn cowork artifacts, team tasks, and replay summaries into queryable OKF knowledge with scoreable recall.",
            "Expose replay citation coverage in the memory operator panel.",
        ),
    ),
    ScoreDimension(
        id="governance_operator",
        title="Governance operator loop",
        weight=5,
        why="Give humans clear control over promotion, override, evidence, and policy.",
        scores={
            "codex": 96,
            "claude_code": 88,
            "openclaw": 82,
            "hermes": 83,
            "echo": 97,
        },
        echo_evidence_ids=("governance_audit", "record_replay_gate"),
        echo_next_actions=(
            "Surface per-agent governance trend charts in the operator panel.",
            "Keep scheduled export retention and integrity-failure receipts release-gated.",
        ),
    ),
    ScoreDimension(
        id="ecosystem_maturity",
        title="Ecosystem maturity",
        weight=5,
        why="Documentation, enterprise polish, integrations, and broad user trust.",
        scores={
            "codex": 99,
            "claude_code": 90,
            "openclaw": 86,
            "hermes": 86,
            "echo": 100,
        },
        echo_evidence_ids=("skills_plugins_hooks", "agent_organization_os"),
        echo_next_actions=(
            "Keep signed compatibility fixtures and one-click install paths current for common MCP and app surfaces.",
            "Publish registry freshness and installation-success SLOs with each release.",
        ),
    ),
    ScoreDimension(
        id="differentiated_agent_os",
        title="Agent OS differentiation",
        weight=7,
        why="Durable teams, memory, governance, and self-evolution beyond task-local coding.",
        scores={
            "codex": 96,
            "claude_code": 82,
            "openclaw": 90,
            "hermes": 90,
            "echo": 97,
        },
        echo_evidence_ids=(
            "long_term_learning",
            "self_evolution_canary",
            "agent_organization_os",
            "governance_audit",
        ),
        echo_next_actions=(
            "Keep self-evolution rollback, team topology, and replay-backed memory certified together.",
            "Require replay-gate evidence before auto-promoting self-evolution changes.",
        ),
    ),
)
