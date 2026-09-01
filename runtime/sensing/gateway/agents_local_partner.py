"""LocalPartner subsystem — detection + secure registration.

Extracted from ``agents_router.py`` (2026-06) to keep that file under
the god-file threshold. LocalPartner registers external CLI tools
(Claude Code, Codex, OpenClaw) as agents in the registry so the team
can dispatch tasks to them via shell.

Security model:
  * Aliases (user-provided display names) are validated against a
    strict regex that blocks markdown/control chars — see
    ``validate_alias`` and ``_LOCAL_PARTNER_ALIAS_RE``.
  * Executable paths from ``shutil.which`` are checked against the
    current working directory to defeat PATH-poisoning attacks
    (see ``safe_executable``).
  * Admin role required at the router layer — see
    ``identity_has_admin_role`` and the ``/api/agents/local-partners``
    endpoints in ``agents_router.py``.

Module organization:
  * ``LOCAL_PARTNER_SPECS`` — the registry of supported partners
  * ``validate_alias`` / ``identity_has_admin_role`` — security gates
  * ``safe_executable`` — PATH-poisoning defense
  * ``which_command`` / ``dir_registered`` — detection helpers
  * ``to_wire`` / ``soul_template`` — output formatters
  * ``write_partner_agent`` — the registration writer
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from typing import Any

from runtime.execution.misc.agent_avatar import pixel_agent_avatar_svg

from .agents_models import LocalPartnerWire

# ── Security primitives ────────────────────────────────────────────
#
# These constants and helpers fence off the shape of user-controllable
# values that flow into LLM context (SOUL.md, IDENTITY.md) or trigger
# command resolution (shutil.which).
#
# ``_LOCAL_PARTNER_ALIAS_RE`` is intentionally tight:
#   * 1..64 chars
#   * letters / digits / CJK / space / a few punctuation marks
#   * no control chars, no slashes, no markdown break-out chars
#
# Tightening past prompt-injection still leaves SOUL.md as a markdown
# file the LLM may eventually read — so we additionally require alias
# to not look like an instruction stub. We don't claim immunity, just
# defense in depth.

# Allowed alias characters: letters, digits, CJK, regular space,
# hyphen, underscore, dot. Notably NOT \s (which would allow \n / \r
# / \t and enable line-break-based prompt injection into SOUL.md).
# Length capped at 64. Rejecting markdown structural chars
# (`*` `_` `[` `]` `(` `)` `>` `#`) prevents trivial markdown
# break-out from the SOUL template.
_LOCAL_PARTNER_ALIAS_RE = re.compile(
    r"^[A-Za-z0-9一-龥　-〿 .\-_]{1,64}$",
)


def validate_alias(value: str | None) -> str:
    """Reject aliases that could pollute SOUL.md / IDENTITY.md or DoS disk.

    Raises ``ValueError`` on bad input — caller must convert to HTTP 400.
    """
    if value is None:
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    if len(candidate) > 64:
        raise ValueError("alias must be 64 chars or fewer")
    if not _LOCAL_PARTNER_ALIAS_RE.fullmatch(candidate):
        raise ValueError("alias may only contain letters, digits, CJK, spaces, '.', '-', '_'")
    return candidate


def identity_has_admin_role(identity: Any) -> bool:
    """Conservative admin check.

    True iff the resolved identity carries the ``admin`` role. This is
    the gate for endpoints that mutate global agent registry / write
    files under ``default_agents_root()``.
    """
    if identity is None:
        return False
    roles = getattr(identity, "roles", ()) or ()
    return "admin" in {str(role).lower() for role in roles}


def safe_executable(executable_path: str) -> bool:
    """Reject executables that resolve into the current working
    directory subtree. Defense against the most common PATH-poisoning
    scenario: an attacker drops a fake ``claude.cmd`` in cwd and
    Windows' default ``.``-in-PATH resolves to it before the real one.

    Note we INTENTIONALLY do not reject paths under the user's home —
    legitimate per-user installs of Claude Code, Codex, etc. live
    there (``~/AppData/Local/Programs/...`` on Windows, ``~/.local/bin``
    on Linux). Rejecting home-paths would block every real install.

    Returns True iff the resolved path lives outside cwd. When path
    resolution fails we REJECT (fail-closed) — a resolve error means
    we cannot verify the path is safe, and accepting it would open a
    PATH-poisoning vector.
    """
    from pathlib import Path

    try:
        resolved = Path(executable_path).resolve()
    except (OSError, RuntimeError):
        return False  # fail-closed on resolve error

    try:
        cwd = Path.cwd().resolve()
    except (OSError, RuntimeError):
        return False  # fail-closed on resolve error

    try:
        resolved.relative_to(cwd)
    except ValueError:
        return True
    return False


# ── Partner specs registry ─────────────────────────────────────────

LOCAL_PARTNER_SPECS: dict[str, dict[str, Any]] = {
    "claude-code": {
        "id": "claude-code",
        "agent_id": "local_claude_code",
        "name": "Claude Code",
        "default_alias": "Claude Code 伙伴",
        "description": "检测本机 Claude Code CLI，注册为可被团队指派的本地开发伙伴。",
        "commands": ["claude", "claude.cmd", "claude.exe", "claude.ps1"],
        "tool_groups": ["web_read", "fs_writer", "git", "shell"],
        "tags": ["local", "partner", "coding", "claude"],
        "icon": "CC",
    },
    "codex-cli": {
        "id": "codex-cli",
        "agent_id": "local_codex_cli",
        "name": "Codex CLI",
        "default_alias": "Codex CLI 伙伴",
        "description": "检测本机 Codex CLI，注册为可被团队指派的本地工程伙伴。",
        "commands": ["codex", "codex.cmd", "codex.exe", "codex.ps1"],
        "tool_groups": ["web_read", "fs_writer", "git", "shell"],
        "tags": ["local", "partner", "coding", "codex"],
        "icon": "CX",
    },
    "openclaw": {
        "id": "openclaw",
        "agent_id": "local_openclaw",
        "name": "OpenClaw",
        "default_alias": "OpenClaw 伙伴",
        "description": "检测本机 OpenClaw 自动化能力，注册为可被团队指派的本地执行伙伴。",
        "commands": ["openclaw", "openclaw.cmd", "openclaw.exe", "openclaw.ps1"],
        "tool_groups": ["desktop_operator", "shell"],
        "tags": ["local", "partner", "automation", "desktop"],
        "icon": "OC",
    },
}


# ── Detection ──────────────────────────────────────────────────────


def which_command(commands: list[str]) -> tuple[str | None, str | None]:
    """Probe a list of candidate commands; return (name, path) for the
    first match, or (None, None) if none found."""
    for command in commands:
        path = shutil.which(command)
        if path:
            return command, path
    return None, None


def dir_registered(agent_id: str) -> bool:
    """True iff ``agents/<agent_id>/profile.jsonc`` exists on disk."""
    try:
        from runtime.execution.agents.loader import default_agents_root

        return (default_agents_root() / agent_id / "profile.jsonc").is_file()
    except (OSError, ImportError):
        return False


def to_wire(
    spec: dict[str, Any],
    registry: Any,
    *,
    which_fn: Callable[[list[str]], tuple[str | None, str | None]] | None = None,
) -> LocalPartnerWire:
    """Materialize a partner spec into its current-state wire form.

    ``which_fn`` is injectable so callers (e.g. agents_router) can swap
    in a re-exported alias that tests monkeypatch. When ``None`` we use
    the module-local ``which_command``.
    """
    probe = which_fn or which_command
    command, executable = probe(list(spec["commands"]))
    agent_id = str(spec["agent_id"])
    in_registry = bool(getattr(registry, "has", lambda _agent_id: False)(agent_id))
    registered = in_registry or dir_registered(agent_id)
    status = "registered" if registered else ("detected" if executable else "missing")
    return LocalPartnerWire(
        id=str(spec["id"]),
        agent_id=agent_id,
        name=str(spec["name"]),
        default_alias=str(spec["default_alias"]),
        description=str(spec["description"]),
        detected=bool(executable),
        registered=registered,
        status=status,
        command=command,
        executable=executable,
    )


# ── SOUL.md template + agent writer ────────────────────────────────


def soul_template(*, alias: str, partner_name: str, command: str) -> str:
    """Render the SOUL.md persona block for a registered partner."""
    return f"""# Soul

## Persona

你是 {alias}，一个接入到 Echo 人力池的本地伙伴。你的背后对应本机已经安装的 {partner_name} 工作流。

## Working Style

- 优先用中文和用户协作，保持简洁、可执行。
- 当任务明确需要调用本地伙伴能力时，通过 shell 运行 `{command}`，并把关键结果整理回对话。
- 调用外部命令前先判断是否必要；涉及文件写入、网络、账号态或长任务时说明将要做什么。
- 如果本地工具返回错误,先给出降级方案,而不是把用户卡在工具细节里。
"""


def write_partner_agent(
    *,
    spec: dict[str, Any],
    alias: str,
    command: str,
    executable: str,
    runtime: Any,
    registry: Any,
) -> Any:
    """Write a LocalPartner agent's profile + SOUL/IDENTITY/AGENTS docs
    to disk and register it in the agent registry. Returns the loaded
    Agent instance.

    Idempotent: if the agent dir already exists with a profile.jsonc,
    we just reload + re-register without overwriting any existing
    customizations the user made.
    """
    import uuid

    from runtime.execution.agents.loader import default_agents_root, load_agent
    from runtime.platform.io import atomic_write_text

    agent_id = str(spec["agent_id"])
    root = default_agents_root()
    agent_dir = root / agent_id
    if agent_dir.exists():
        if not (agent_dir / "profile.jsonc").is_file():
            raise ValueError(f"agent folder exists without profile: {agent_id}")
        agent = load_agent(agent_dir, runtime, root / "_shared")
        if hasattr(registry, "replace"):
            registry.replace(agent)
        elif not registry.has(agent_id):
            registry.register(agent)
        return agent

    agent_dir.mkdir(parents=True)
    for rel in (
        "agent-core",
        "agent-core/.soul_history",
        "agent-core/diary",
        "agent-core/skills",
        "memory",
        "permissions",
        "project",
        "runtime",
        "sessions",
        "skills",
    ):
        (agent_dir / rel).mkdir(parents=True, exist_ok=True)

    did = f"DID-{uuid.uuid4().hex[:12].upper()}-{uuid.uuid4().hex[:6].upper()}"
    profile = {
        "id": agent_id,
        "templateId": str(spec["id"]),
        "templateVersion": "1.0.0",
        "name": alias,
        "icon": str(spec.get("icon") or "L"),
        "did": did,
        "description": str(spec["description"]),
        "avatar": "avatar.svg",
        "model": {"provider": "auto", "name": "auto"},
        "runtime": "local_partner",
        "creator": "user",
        "category": "automation",
        "tags": list(spec.get("tags") or []),
        "defaultProject": {"dir": "project"},
        "capabilities": {
            "local_partner": True,
            "local_partner_id": str(spec["id"]),
            "local_partner_command": command,
            "local_partner_executable": executable,
        },
    }
    atomic_write_text(
        agent_dir / "profile.jsonc",
        (
            f"// Echo local partner profile · {agent_id}\n"
            "// Created by local partner registration\n\n"
            + json.dumps(profile, ensure_ascii=False, indent=2)
        ),
    )
    soul = soul_template(
        alias=alias,
        partner_name=str(spec["name"]),
        command=command,
    )
    atomic_write_text(agent_dir / "agent-core" / "SOUL.md", soul, newline=None)
    atomic_write_text(
        agent_dir / "agent-core" / "IDENTITY.md",
        f"""# Identity

- **Name**: {alias}
- **Role**: Local partner bridge for {spec["name"]}

## Boundary

- You are registered from a local executable detected on this machine.
- Respect the current workspace and the user's requested task.
""",
        newline=None,
    )
    atomic_write_text(
        agent_dir / "agent-core" / "AGENTS.md",
        """# Working rules

Before using the local partner command, understand the user's task and current workspace. Keep outputs concise and user-facing.
""",
        newline=None,
    )
    atomic_write_text(
        agent_dir / "agent-core" / "tool-registry.jsonc",
        (
            "// Tool registry for this local partner\n\n"
            + json.dumps(
                {
                    "arms": list(spec.get("tool_groups") or []),
                    "extra_affinity": ["local_partner", str(spec["id"])],
                    "private_skills": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        ),
    )
    atomic_write_text(agent_dir / "avatar.svg", pixel_agent_avatar_svg(alias), newline=None)

    agent = load_agent(agent_dir, runtime, root / "_shared")
    registry.register(agent)
    return agent
