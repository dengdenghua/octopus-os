"""Runtime-owned filesystem paths.

App-local state is rooted from one contract:

1. ``ECHO_DATA_DIR`` points directly at the data directory.
2. ``ECHO_HOME`` points at the runtime home; data lives under
   ``<home>/data``.
3. Without env overrides, discover the project root from the current
   directory and use ``<project>/data``.

Keeping that contract in one small module prevents routers from drifting
between cwd-relative paths and source-tree-relative paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Captured at import, while the working directory is still guaranteed to
# exist. Used only as the fallback in :func:`_launch_dir`.
try:
    _LAUNCH_DIR = Path.cwd()
except OSError:  # pragma: no cover — cwd already gone before we loaded
    _LAUNCH_DIR = Path("/")


@dataclass(frozen=True)
class AppPaths:
    root: Path
    data_dir_override: Path | None = None

    @property
    def data_dir(self) -> Path:
        return self.data_dir_override or self.root / "data"

    @property
    def custom_models_path(self) -> Path:
        return self.data_dir / "custom_models.json"

    @property
    def user_memory_path(self) -> Path:
        return self.data_dir / "user_memory.json"

    @property
    def user_memory_config_path(self) -> Path:
        return self.data_dir / "user_memory_config.json"

    @property
    def threads_path(self) -> Path:
        return self.data_dir / "threads.jsonl"

    @property
    def agent_trace_path(self) -> Path:
        return self.data_dir / "agent_trace.sqlite"

    @property
    def tool_effects_path(self) -> Path:
        """Transactional receipts shared by all local server workers."""

        return self.data_dir / "tool_effects.sqlite3"

    @property
    def experience_ledger_path(self) -> Path:
        return self.data_dir / "experience_ledger.json"

    @property
    def review_queue_path(self) -> Path:
        return self.data_dir / "review_queue.json"

    @property
    def subagent_policy_path(self) -> Path:
        return self.data_dir / "subagent_policy.json"

    @property
    def promotion_audit_path(self) -> Path:
        return self.data_dir / "promotion_audit.json"

    @property
    def governance_audit_chain_path(self) -> Path:
        return self.data_dir / "governance_audit_chain.jsonl"

    @property
    def governance_audit_secret_path(self) -> Path:
        return self.data_dir / "governance_audit_chain.secret"

    @property
    def org_audit_chain_path(self) -> Path:
        return self.data_dir / "org_audit_chain.jsonl"

    @property
    def org_audit_secret_path(self) -> Path:
        return self.data_dir / "org_audit_chain.secret"

    @property
    def decision_audit_chain_path(self) -> Path:
        """协作决策查看/导出的审计链（``decision_visibility.DecisionAccessAudit``）。"""

        return self.data_dir / "decision_access_audit.jsonl"

    @property
    def proposal_ledger_path(self) -> Path:
        return self.data_dir / "proposal_ledger.jsonl"

    @property
    def evolution_experiments_path(self) -> Path:
        """Controlled same-task engine and genome experiment trials."""

        return self.data_dir / "evolution_experiments.jsonl"

    @property
    def evolution_candidates_path(self) -> Path:
        """Append-only typed candidate lineage events."""

        return self.data_dir / "evolution_candidates.jsonl"

    @property
    def candidate_canary_state_dir(self) -> Path:
        """Per-candidate staged rollout state."""

        return self.data_dir / "candidate_canary_states"

    @property
    def candidate_runtime_outcomes_path(self) -> Path:
        """Durable governed-candidate turn activation inbox."""

        return self.data_dir / "candidate_runtime_outcomes.json"

    @property
    def auto_verifier_metrics_path(self) -> Path:
        return self.data_dir / "auto_verifier_metrics.jsonl"

    @property
    def auto_verifier_decisions_path(self) -> Path:
        return self.data_dir / "auto_verifier_decisions.jsonl"

    @property
    def cron_jobs_path(self) -> Path:
        return self.data_dir / "cron_jobs.json"

    @property
    def loop_runs_path(self) -> Path:
        return self.data_dir / "loop_runs.json"

    @property
    def task_runs_path(self) -> Path:
        return self.data_dir / "task_runs.json"

    @property
    def feature_flags_path(self) -> Path:
        return self.data_dir / "feature_flags.json"

    @property
    def permissions_path(self) -> Path:
        """Where the tool-approval policy (allow/deny rules) is persisted.

        Static rules: a short allow/deny list the UI writes to when
        the user clicks "trust this". Loaded at
        ``CerebrumRuntime`` construction; a miss falls through to the
        gateway-backed interactive provider.
        """
        return self.data_dir / "permissions.json"

    @property
    def browser_policy_path(self) -> Path:
        """Where browser relay site policy is persisted.

        The browser relay can drive a user's real browser through the
        companion extension, so its allow/block host policy must survive
        process restarts instead of living only in the in-memory router.
        """
        return self.data_dir / "browser_policy.json"

    @property
    def hooks_path(self) -> Path:
        """Where declarative tool-edge hooks are configured.

        Pre/post tool-use hooks live here. Each entry names a script
        the runtime runs at the boundary of a matching tool call.
        See :mod:`runtime.safety.hooks`.
        """
        return self.data_dir / "hooks.json"

    @property
    def codex_plugins_path(self) -> Path:
        """Writable Codex-compatible plugin root for installed deployments."""

        return self.data_dir / "plugins" / "codex"


def _looks_like_project_root(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "runtime").is_dir()


def _launch_dir() -> Path:
    """The directory to resolve relative paths against.

    Normally ``cwd``, but a long-lived server can outlive its own working
    directory: delete or move the directory the process was started in and
    every later ``Path.cwd()`` raises a bare ``FileNotFoundError`` with no
    filename attached. Because ``app_paths()`` funnels through here, that
    single deleted directory turned into ``[Errno 2] No such file or
    directory`` on unrelated work — a turn would fail with an error naming
    nothing at all. Remember the launch directory at import time and fall
    back to it, so a vanished cwd degrades to a stale-but-valid root rather
    than an exception on every path lookup.
    """
    try:
        return Path.cwd()
    except OSError:
        return _LAUNCH_DIR


def project_root(start: str | Path | None = None) -> Path:
    """Return the nearest Echo project root, falling back to ``start``/cwd.

    ``start`` may point at a file or directory. The fallback keeps tests and
    scratch invocations usable when no source-tree sentinels are present.
    """
    raw = Path(start).expanduser() if start is not None else _launch_dir()
    base = raw.resolve(strict=False)
    if not base.is_dir():
        base = base.parent
    for candidate in (base, *base.parents):
        if _looks_like_project_root(candidate):
            return candidate
    return base


def app_paths(root: str | Path | None = None) -> AppPaths:
    if root is not None:
        return AppPaths(Path(root).expanduser().resolve())
    data_dir = os.environ.get("ECHO_DATA_DIR")
    if data_dir:
        resolved = Path(data_dir).expanduser().resolve()
        return AppPaths(resolved.parent, resolved)
    home = os.environ.get("ECHO_HOME")
    if home:
        return AppPaths(Path(home).expanduser().resolve())
    return AppPaths(project_root())


def resources_root() -> Path:
    """Root for deployment-owned assets: skills/, prompts/, protocols/,
    agents/ presets.

    Assets are read-only except for the registry-managed ``skills/public``
    materialization directory.

    In a source checkout this is the project root. In the Docker image
    the code is pip-installed and the working dir is the data volume
    (``/data``), so ``project_root()`` resolves to ``/data`` — which has
    no deployment-owned assets. The image copies them to ``/app/resources``
    and sets ``ECHO_RESOURCES_DIR`` so loaders find agents, prompts,
    protocols, and the registry skill lock. Prompt skills additionally have a
    package-relative fallback via :func:`bundled_market_skills_dir`.
    Honour the env var, else resolve relative to this package so source
    checkouts and editable installs find bundled assets regardless of cwd.
    """
    env = os.environ.get("ECHO_RESOURCES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # cwd may sit outside the source tree (tests chdir to a tmp dir; some
    # entrypoints run from elsewhere). project_root() would then fall back to
    # cwd and silently lose the bundled assets. Try the source-tree root
    # relative to THIS package file first: paths.py lives at
    # runtime/platform/process/paths.py, so parents[3] is the repo root. In a
    # source checkout / editable install that root carries skills/, prompts/,
    # etc. In a non-editable pip install it won't look like a project root, so
    # fall through to project_root() (and the container sets the env var above).
    pkg_root = Path(__file__).resolve().parents[3]
    if _looks_like_project_root(pkg_root):
        return pkg_root
    return project_root()


def bundled_market_skills_dir() -> Path:
    """Return the prompt-skill fallback bundled inside the Python package.

    ``resources_root()`` intentionally follows ``ECHO_RESOURCES_DIR`` for
    deployment-owned assets.  A non-editable wheel, however, cannot assume a
    repository-shaped resource root next to the current working directory.
    The deterministic fallback therefore lives under the installed
    ``runtime`` package and is located relative to this module.
    """

    # paths.py -> process/ -> platform/ -> runtime/
    return Path(__file__).resolve().parents[2] / "execution" / "all_skills"


__all__ = [
    "AppPaths",
    "app_paths",
    "bundled_market_skills_dir",
    "project_root",
    "resources_root",
]
