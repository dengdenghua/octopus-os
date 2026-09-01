"""Write-scope resolver · mode-gated filesystem permission domain.

Background
----------

Every agent (current + future-registered) writes files through skills
that accept a ``sandbox_dir`` parameter. Historically the LLM chose that
value itself — which means "scope" was cooperative, not enforced.

This module makes scope **Session-derived** and **mode-gated**:

    ┌────────┬──────────────────────────────────────────────────┐
    │ mode   │ what the agent can write                         │
    ├────────┼──────────────────────────────────────────────────┤
    │ chat   │ workspaces/<thread_id>/output/final/             │
    │ team   │ chat output + teams/<team_id>/workspace/         │
    │ code   │ chat scope  +  extra_workspaces (user-granted)   │
    └────────┴──────────────────────────────────────────────────┘

**Code-mode is available to every agent by default.** The old per-agent
``capabilities.code_mode_unlock`` gate was removed — any agent can use the
`code` tier. Fine-grained tool/permission scoping lives in the skills &
permissions system, not a global flag. Write reach is still bounded: the
`code` tier only *adds* user-granted ``extra_workspaces`` on top of the
agent's own workspace.

This module is the **single source of truth**. Skills that enforce
path boundaries (`write_skills._ensure_sandbox`, the executor's
sandbox-arg injector) call `resolve_write_scope(session)` to get the
allowed roots · they never re-implement the mode ladder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from runtime.platform.process.paths import app_paths

if TYPE_CHECKING:  # pragma: no cover
    from runtime.platform.process.session import Session

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Workspace root resolution · lives under $ECHO_DATA_DIR
# ═══════════════════════════════════════════════════════════
#
# We anchor workspaces under ``$ECHO_DATA_DIR`` (falls back to CWD) so
# that unit tests and dev runs don't pollute the home directory. The
# per-agent workspace carries thread-level subdirs so two chats running
# on the same agent can't stomp each other's files.


def _data_root() -> Path:
    """Base directory for workspace state. Respects the shared path contract."""
    return app_paths().data_dir


def agent_workspace_root(agent_id: str) -> Path:
    """Per-agent workspace root: ``<data>/agents/<id>/workspace``.

    Created lazily by the skill that first writes. We don't mkdir here
    because import-time side effects on a path resolver are a bad idea
    — callers that actually need the dir call ``path.mkdir(parents=True,
    exist_ok=True)``.
    """
    return _data_root() / "agents" / agent_id / "workspace"


def thread_workspace(agent_id: str, thread_id: str) -> Path:
    """Legacy per-agent thread path retained for persisted data readers."""
    return agent_workspace_root(agent_id) / thread_id


def thread_artifact_root(
    thread_id: str,
    *,
    explicit_root: str | Path | None = None,
) -> Path:
    """Canonical folder for user-visible files generated in a thread."""
    if explicit_root is not None:
        return Path(explicit_root).expanduser().resolve()
    from runtime.platform.runtime_policy.workspaces import WorkspaceManager

    manager = WorkspaceManager(_data_root() / "workspaces")
    return manager.path_for(thread_id) / "output" / "final"


def team_workspace_root(team_id: str) -> Path:
    """Shared team workspace: ``<data>/teams/<team_id>/workspace``."""
    return _data_root() / "teams" / team_id / "workspace"


# ═══════════════════════════════════════════════════════════
# Scope dataclass + resolver
# ═══════════════════════════════════════════════════════════


# Recognized modes. Anything else collapses to ``chat`` (safest tier).
# ``browser`` keeps chat-level write confinement while allowing an explicitly
# bound project workspace to be read for URLs and upload fixtures.
#
# ``plan`` is the strictest mode · write scope is empty. The agent can
# read and call pure-inspection skills but every ``sandbox_dir``-
# using skill fails at the executor layer because ``scope.allows()``
# returns False for every path. To leave plan mode the agent must
# explicitly call the ``exit_plan_mode`` skill · which swaps the
# thread's mode to the requested follow-on (``chat`` / ``team`` /
# ``code``) after user confirmation. ``exit_plan_mode`` is a
# protocol-level tool of the plan-mode contract.
_VALID_MODES = frozenset({"plan", "chat", "team", "code", "browser"})


@dataclass(frozen=True, slots=True)
class WriteScope:
    """Resolved filesystem permission domain for one turn.

    Fields
    ------
    mode :
        The effective mode after resolver constraints. Code mode is
        available to every agent; ``team`` can still degrade to
        ``chat`` when no ``team_id`` is present.
    roots :
        Allowed write-paths, ordered from "most specific / primary" to
        "most permissive / extra". `roots[0]` is the default sandbox —
        skills that take ``sandbox_dir`` and didn't pass one should
        default to this. Every write target must be equal to or
        contained under one of these.
    requested_mode :
        What the caller asked for before resolver constraints. Surfaced
        so diagnostics can explain why the effective mode differs.
    """

    mode: str
    roots: tuple[Path, ...]
    requested_mode: str = "chat"

    @property
    def primary(self) -> Path | None:
        """The default write-root · ``None`` when no roots exist (plan mode).

        Pre-plan-mode this always returned ``roots[0]`` unconditionally ·
        plan tier's empty ``roots`` would have crashed here. Callers
        that used ``scope.primary`` without null-check should either:
        * check ``scope.mode != "plan"`` first, or
        * handle ``None`` as "this mode writes nowhere · block caller".
        """
        return self.roots[0] if self.roots else None

    def allows(self, path: str | Path) -> bool:
        """True if *path* resolves under any allowed root.

        Symlink-aware: we resolve the path and check the *real* target,
        so a symlink inside the workspace pointing outside cannot bypass
        the scope boundary.
        """
        if not self.roots:
            return False
        try:
            p = Path(path).expanduser().resolve(strict=False)
        except OSError:
            return False
        for root in self.roots:
            try:
                rr = root.resolve(strict=False)
                p.relative_to(rr)
                return True
            except ValueError:
                continue
        return False


@dataclass(frozen=True, slots=True)
class ExecutionScope:
    """Turn-level execution permission domain.

    ``WriteScope`` is kept for compatibility with existing skill handlers
    and executor checks. This higher-level shape separates read and write
    roots so sandboxed code mode can keep the selected project readable
    while confining writes to ``.echo-work/<thread_id>``.
    """

    mode: str
    requested_mode: str
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    permission_mode: str = "default"
    approval_policy: str = "on-request"
    sandbox_mode: str = "full"
    execution_environment: str = "sandbox"
    shell_policy: str = "ask"
    network_policy: str = "ask"
    browser_policy: str = "ask"

    @property
    def primary_write(self) -> Path | None:
        return self.writable_roots[0] if self.writable_roots else None

    @property
    def primary_read(self) -> Path | None:
        return self.readable_roots[0] if self.readable_roots else None

    def allows_write(self, path: str | Path) -> bool:
        return _path_allowed_by_roots(path, self.writable_roots)

    def allows_read(self, path: str | Path) -> bool:
        return _path_allowed_by_roots(path, self.readable_roots)


def _path_allowed_by_roots(path: str | Path, roots: tuple[Path, ...]) -> bool:
    if not roots:
        return False
    try:
        p = Path(path).expanduser().resolve(strict=False)
    except OSError:
        return False
    for root in roots:
        try:
            p.relative_to(root.resolve(strict=False))
            return True
        except ValueError:
            continue
    return False


def _path_is_same_or_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _normalize_permission_mode(value: Any) -> str:
    raw = str(value or "default").strip().lower()
    if raw in {"acceptedits", "accept-edits"}:
        return "acceptEdits"
    if raw in {
        "bypasspermissions",
        "bypass-permissions",
        "bypass",
        "yolo",
        "full",
    }:
        return "bypassPermissions"
    if raw == "plan":
        return "plan"
    return "default"


def _normalize_approval_policy(value: Any, *, permission_mode: str) -> str:
    raw = str(value or "").strip()
    if raw in {"never", "on-request", "untrusted"}:
        return raw
    if permission_mode == "bypassPermissions":
        return "never"
    return "on-request"


def _normalize_sandbox_mode(value: Any) -> str:
    raw = str(value or "").strip()
    return raw if raw in {"sandbox", "full"} else "full"


def _normalize_execution_environment(value: Any, *, permission_mode: str) -> str:
    raw = str(value or "").strip()
    if raw in {"sandbox", "local"}:
        return raw
    if permission_mode == "bypassPermissions":
        return "local"
    return "sandbox"


def agent_has_capability(agent: Any, name: str) -> bool:
    """Check whether ``agent`` has the named capability flag set.

    This is the **preferred** reader for ``Agent.capabilities`` in
    runtime code · it encodes the contract from ADR-005:

    * Absent key → ``False`` (default-closed)
    * Present and truthy → ``True``
    * Present and falsy (``False`` / ``0`` / ``""`` / ``None``) → ``False``

    Using this helper (vs. inline ``caps.get(name)``) keeps reviewers
    from accidentally introducing the ``caps.get(name, True)``
    default-open anti-pattern the ADR bans.

    Passing ``agent=None`` returns False · matches "no agent resolved
    yet" = no privileges.
    """
    if agent is None:
        return False
    caps = getattr(agent, "capabilities", None) or {}
    return bool(caps.get(name))


def _agent_has_code_mode(session: Session | None) -> bool:
    """Code mode is available to every agent by default · the per-agent
    ``code_mode_unlock`` capability flag was removed. Tool/permission
    scoping now lives entirely in the skills & permissions system, not a
    global gate. Still requires a session — ``extra_workspaces`` and
    ``workspace_path`` live in its metadata."""
    return session is not None


def _extra_workspaces_from_metadata(
    session: Session | None,
) -> list[Path]:
    """User-granted extra write paths (code mode only).

    Stored in ``session.metadata['extra_workspaces']`` as a list of
    absolute paths · populated by the compat router from the thread
    metadata that the Code-page UI writes via PATCH. We validate each
    entry exists and is absolute · silently drop the rest rather than
    error out the whole turn over a stale config entry.
    """
    if session is None:
        return []
    raw = (session.metadata or {}).get("extra_workspaces")
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[Path] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            raw_path = Path(item).expanduser()
        except (OSError, ValueError):
            _log.debug("extra_workspaces: skipped invalid path %r", item)
            continue
        if not raw_path.is_absolute():
            _log.debug("extra_workspaces: skipped relative path %r", item)
            continue
        try:
            p = raw_path.resolve()
        except OSError:
            _log.debug("extra_workspaces: resolve failed for %r", item)
            continue
        if not p.is_dir():
            _log.debug("extra_workspaces: not a directory %r", item)
            continue
        # Detect overlapping paths — skip if already covered by an existing entry
        is_overlap = False
        for existing in out:
            try:
                p.relative_to(existing)
                is_overlap = True
                _log.debug("extra_workspaces: %r is subpath of %r, skipped", item, str(existing))
                break
            except ValueError:  # noqa: BLE001 — is_subpath raises on unusual paths; treat as non-overlap
                pass
        if not is_overlap:
            out.append(p)
    return out


def resolve_write_scope(session: Session | None) -> WriteScope:
    """Return the allowed write-scope for the current turn.

    The resolver is pure — no side effects, no directory creation. Run
    it at skill-call time · don't cache, the Session's metadata can
    change between turns.

    Contract
    --------
    * Always returns at least **one** root — if ``session.agent`` is
      unset (e.g. a legacy path without a persona), we fall back to
      ``<data>/workspace/tmp`` so writes still have somewhere to land
      without being free-form.
    * Mode ``code`` is available to every agent (the per-agent
      ``code_mode_unlock`` gate was removed) · it adds user-granted
      ``extra_workspaces`` on top of the agent's own workspace.
    * Mode ``team`` requires ``session.metadata['team_id']`` — without
      it we stay in ``chat`` scope (can't grant team access if we
      don't know which team).
    """
    meta = (session.metadata if session else None) or {}
    requested = str(meta.get("mode") or "chat")
    if requested not in _VALID_MODES:
        requested = "chat"

    # ── Plan mode · no write roots at all ───────────────────
    # Plan mode is the strictest tier · all write-skills fail at
    # the executor layer because every target path gets rejected
    # by ``scope.allows()``. Agent must explicitly call
    # ``exit_plan_mode`` skill to transition to a permissive tier.
    if requested == "plan":
        return WriteScope(
            mode="plan",
            roots=(),
            requested_mode="plan",
        )

    # ── Primary root · thread artifact output (or dev fallback) ──
    if session is not None:
        thread_id = session.thread_id or "default"
        explicit_artifact_root = meta.get("_artifact_output_root")
        primary = thread_artifact_root(
            thread_id,
            explicit_root=(
                explicit_artifact_root
                if isinstance(explicit_artifact_root, str) and explicit_artifact_root.strip()
                else None
            ),
        )
    else:
        primary = _data_root() / "workspace" / "tmp"

    roots: list[Path] = [primary]

    # ── Team mode · add team workspace if team_id provided ──────
    mode = "chat"
    team_id = meta.get("team_id")
    if requested in ("team", "code") and isinstance(team_id, str) and team_id:
        roots.append(team_workspace_root(team_id))
        mode = "team"

    # ── Code mode · agent-gated unlock of extra_workspaces ──────
    if requested == "code" and _agent_has_code_mode(session):
        roots.extend(_extra_workspaces_from_metadata(session))
        # User-selected per-turn folder (the WorkDirSelector in
        # /workspace/code) becomes the *primary* sandbox so write
        # skills that default to ``scope.primary`` land files in
        # the folder the user just clicked.
        #
        # ``sandbox_mode`` decides what "primary" actually means
        # under that folder:
        #
        #   * ``full`` (default) — primary = workspace_path itself.
        #     The AI mutates the main codebase in-place. User has
        #     git as the safety net.
        #   * ``sandbox`` — primary = ``<workspace_path>/
        #     .echo-work/<thread_id>/``. The AI writes only into
        #     that sub-directory; main code stays untouched. Used
        #     when the AI is iterating on its own architecture so
        #     a failed self-evolution can't corrupt the user's
        #     real source. The full ``workspace_path`` remains in
        #     the allow-list (read access preserved).
        wp = meta.get("workspace_path")
        if isinstance(wp, str) and wp.strip():
            try:
                wp_path = Path(wp).expanduser()
                if wp_path.is_absolute():
                    wp_path = wp_path.resolve()
                    sb_mode = str(meta.get("sandbox_mode") or "full")
                    if sb_mode == "sandbox":
                        thread_id = (session.thread_id if session else None) or "default"
                        sandboxed = wp_path / ".echo-work" / thread_id
                        # Always include the parent workspace_path
                        # for read access. Insert sandboxed as
                        # primary (roots[0]) so writes default there.
                        roots.insert(0, wp_path)
                        roots.insert(0, sandboxed)
                    else:
                        roots.insert(0, wp_path)
            except (OSError, ValueError):  # noqa: BLE001 — workspace path resolve failed; proceed without scope root
                pass
        mode = "code"

    # Deduplicate while preserving order (primary-first matters).
    seen: set[Path] = set()
    deduped: list[Path] = []
    for r in roots:
        if r in seen:
            continue
        seen.add(r)
        deduped.append(r)

    return WriteScope(
        mode=mode,
        roots=tuple(deduped),
        requested_mode=requested,
    )


def resolve_execution_scope(session: Session | None) -> ExecutionScope:
    """Return the unified execution scope for a turn.

    This is a non-breaking wrapper over ``resolve_write_scope``. Existing
    callers can keep using ``WriteScope`` while new code migrates to the
    clearer read/write split and policy fields.
    """
    meta = (session.metadata if session else None) or {}
    write_scope = resolve_write_scope(session)
    permission_mode = _normalize_permission_mode(meta.get("permission_mode"))
    approval_policy = _normalize_approval_policy(
        meta.get("approval_policy"),
        permission_mode=permission_mode,
    )
    sandbox_mode = _normalize_sandbox_mode(meta.get("sandbox_mode"))
    execution_environment = _normalize_execution_environment(
        meta.get("execution_environment"),
        permission_mode=permission_mode,
    )

    readable_roots = write_scope.roots
    writable_roots = write_scope.roots
    workspace_path: Path | None = None

    wp = meta.get("workspace_path")
    if isinstance(wp, str) and wp.strip():
        try:
            candidate = Path(wp).expanduser()
            if candidate.is_absolute():
                workspace_path = candidate.resolve()
        except (OSError, ValueError):
            workspace_path = None

    browser_project_read = (
        write_scope.requested_mode == "browser"
        and str(meta.get("workspace_scope") or "").strip().lower() == "project"
    )
    if (write_scope.mode == "code" or browser_project_read) and workspace_path is not None:
        readable_roots = (
            workspace_path,
            *tuple(root for root in write_scope.roots if root != workspace_path),
        )

    # Uploaded attachments are a separate, server-owned read domain.  The
    # gateway derives these roots from WorkspaceManager rather than trusting
    # attachment paths supplied by the browser.  Keep them read-only even in
    # full-access code mode.
    raw_attachment_roots = meta.get("attachment_read_roots")
    if isinstance(raw_attachment_roots, list):
        attachment_roots: list[Path] = []
        for raw_root in raw_attachment_roots:
            if not isinstance(raw_root, str) or not raw_root.strip():
                continue
            try:
                root = Path(raw_root).expanduser()
                if not root.is_absolute():
                    continue
                root = root.resolve()
            except (OSError, ValueError):
                continue
            if root.is_dir() and root not in readable_roots and root not in attachment_roots:
                attachment_roots.append(root)
        readable_roots = (*readable_roots, *attachment_roots)

    if write_scope.mode == "plan" or permission_mode == "plan":
        writable_roots = ()

    if (
        write_scope.mode == "code"
        and sandbox_mode == "sandbox"
        and workspace_path is not None
        and write_scope.primary is not None
    ):
        writable_roots = tuple(
            root
            for root in write_scope.roots
            if root == write_scope.primary or not _path_is_same_or_under(root, workspace_path)
        )

    # “完全访问” means exactly what the settings UI promises: local file
    # operations are not confined to the selected workspace. Keep the normal
    # task/workspace roots first so relative paths still land in the expected
    # place, and append the filesystem root for explicitly absolute paths.
    if permission_mode == "bypassPermissions" and execution_environment == "local":
        filesystem_root = Path(Path.cwd().anchor or "/").resolve(strict=False)
        if filesystem_root not in readable_roots:
            readable_roots = (*readable_roots, filesystem_root)
        if filesystem_root not in writable_roots:
            writable_roots = (*writable_roots, filesystem_root)

    if permission_mode == "bypassPermissions" or execution_environment == "local":
        shell_policy = "allow"
        network_policy = "allow"
        browser_policy = "allow"
    elif write_scope.mode == "plan" or permission_mode == "plan":
        shell_policy = "deny"
        network_policy = "deny"
        browser_policy = "deny"
    else:
        shell_policy = "ask"
        network_policy = "allow"
        browser_policy = "ask"

    return ExecutionScope(
        mode=write_scope.mode,
        requested_mode=write_scope.requested_mode,
        readable_roots=readable_roots,
        writable_roots=writable_roots,
        permission_mode=permission_mode,
        approval_policy=approval_policy,
        sandbox_mode=sandbox_mode,
        execution_environment=execution_environment,
        shell_policy=shell_policy,
        network_policy=network_policy,
        browser_policy=browser_policy,
    )


__all__ = [
    "ExecutionScope",
    "WriteScope",
    "agent_workspace_root",
    "thread_artifact_root",
    "thread_workspace",
    "team_workspace_root",
    "resolve_execution_scope",
    "resolve_write_scope",
]
