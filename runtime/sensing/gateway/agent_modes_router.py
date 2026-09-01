"""Agent project/code mode detection endpoints.

The Agent page treats a selected local directory as project/code mode.
This router provides the small local detector used by the frontend mode
switcher so the runtime can start with concrete project signals instead
of guessing from the prompt alone.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from pydantic import BaseModel, Field

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]
    Field = None  # type: ignore[assignment, misc]


IGNORED_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}

MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "tsconfig.json",
    "docker-compose.yml",
    "Dockerfile",
}

LOCK_NAMES = {
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
}

ARCHITECTURE_DIR_HINTS = {
    "architecture",
    "docs/architecture",
    "adr",
    "rfcs",
}

# Detection keeps the original project-kind vocabulary; the interactive
# selector uses task strategies.  The current-mode endpoint accepts both so a
# frontend choice never fails merely because it came from the newer UX.
CURRENT_AGENT_MODES = frozenset(
    {
        "builder",
        "coder",
        "architect",
        "develop",
        "audit",
        "uxui",
    }
)


if FASTAPI_AVAILABLE:

    class VerificationCommand(BaseModel):
        kind: str
        command: str
        source: str

    class DetectionSignals(BaseModel):
        workspace_path: str | None = None
        exists: bool | None = None
        file_count: int | None = None
        manifests: list[str] = Field(default_factory=list)
        structure_dirs: list[str] = Field(default_factory=list)
        git_commits: int | None = None
        has_readme: bool | None = None
        lock_files: list[str] = Field(default_factory=list)
        commands: list[VerificationCommand] = Field(default_factory=list)
        raw_score: float | None = None

    class DetectResponse(BaseModel):
        recommended_mode: str
        confidence: float
        reason: str
        signals: DetectionSignals

    class TemplateInfo(BaseModel):
        name: str
        display_name: str
        description: str
        language: str
        framework: str
        tags: list[str] = Field(default_factory=list)

    class ModeInfo(BaseModel):
        name: str
        display_name: str
        description: str
        icon: str
        templates: list[TemplateInfo] | None = None

    class ModesResponse(BaseModel):
        modes: list[ModeInfo]

    class CurrentModeBody(BaseModel):
        mode: str
        session_id: str | None = None

    class CurrentModeResponse(BaseModel):
        ok: bool
        mode: str
        session_id: str | None = None


def create_agent_modes_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    allow_local_workspace_access: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    if not FASTAPI_AVAILABLE:  # pragma: no cover
        return None

    def _auth_dep(request: Request) -> None:
        from runtime.safety.auth.principal import resolve_principal

        resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _workspace_detection_dep(request: Request) -> None:
        # Scanning an arbitrary host path remains operator-only in shared
        # deployments. On the authenticated, loopback-only desktop server the
        # signed-in user is already allowed to choose and execute inside a
        # local workspace, so requiring an operator role here only breaks the
        # mode detector for ordinary Echo accounts.
        if allow_local_workspace_access:
            _auth_dep(request)
            return
        _operator_dep(request)

    router = APIRouter(tags=["agent-modes"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/agent-modes", response_model=ModesResponse)
    def list_agent_modes() -> ModesResponse:
        return ModesResponse(
            modes=[
                ModeInfo(
                    name="builder",
                    display_name="Builder",
                    description="Create a new project or runnable slice from scratch.",
                    icon="hammer",
                    templates=[
                        TemplateInfo(
                            name="web-app",
                            display_name="Web App",
                            description="Frontend app with build and preview workflow.",
                            language="TypeScript",
                            framework="Vite/React",
                            tags=["frontend", "preview"],
                        ),
                        TemplateInfo(
                            name="python-tool",
                            display_name="Python Tool",
                            description="Small Python package or CLI with tests.",
                            language="Python",
                            framework="pytest",
                            tags=["cli", "tests"],
                        ),
                    ],
                ),
                ModeInfo(
                    name="coder",
                    display_name="Coder",
                    description="Modify, debug, refactor, and verify an existing codebase.",
                    icon="code",
                    templates=None,
                ),
                ModeInfo(
                    name="architect",
                    display_name="Architect",
                    description="Plan safer cross-module changes, migrations, and contracts.",
                    icon="building",
                    templates=None,
                ),
            ],
        )

    @router.get(
        "/api/agent-modes/detect",
        response_model=DetectResponse,
        dependencies=[Depends(_workspace_detection_dep)],
    )
    def detect_agent_mode(
        workspace_path: str = Query(..., min_length=1),
    ) -> DetectResponse:
        root = Path(workspace_path).expanduser()
        if not root.is_absolute():
            raise HTTPException(status_code=400, detail="workspace_path must be absolute")
        if not root.exists() or not root.is_dir():
            return DetectResponse(
                recommended_mode="builder",
                confidence=0.55,
                reason="Workspace folder does not exist yet; start in builder mode.",
                signals=DetectionSignals(
                    workspace_path=str(root),
                    exists=False,
                    file_count=0,
                    raw_score=0.0,
                ),
            )

        signals = _scan_project(root)
        mode, confidence, reason = _recommend_mode(signals)
        return DetectResponse(
            recommended_mode=mode,
            confidence=confidence,
            reason=reason,
            signals=signals,
        )

    @router.put("/api/agent-modes/current", response_model=CurrentModeResponse)
    def set_current_mode(body: CurrentModeBody) -> CurrentModeResponse:
        mode = body.mode.strip().lower()
        if mode not in CURRENT_AGENT_MODES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "unknown agent mode; expected develop/audit/uxui or builder/coder/architect"
                ),
            )
        return CurrentModeResponse(ok=True, mode=mode, session_id=body.session_id)

    return router


def _scan_project(root: Path) -> Any:
    manifests: list[str] = []
    locks: list[str] = []
    structure_dirs: list[str] = []
    file_count = 0
    visited_dirs = 0
    stack: list[tuple[Path, int]] = [(root, 0)]

    try:
        top_children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        top_children = []
    for child in top_children:
        if child.is_dir() and child.name not in IGNORED_DIRS:
            structure_dirs.append(child.name)
        if len(structure_dirs) >= 16:
            break

    while stack and visited_dirs < 200 and file_count < 3000:
        current, depth = stack.pop()
        visited_dirs += 1
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                if depth < 3 and child.name not in IGNORED_DIRS:
                    stack.append((child, depth + 1))
                continue
            if not child.is_file():
                continue
            file_count += 1
            rel = child.relative_to(root).as_posix()
            if child.name in MANIFEST_NAMES and rel not in manifests:
                manifests.append(rel)
            if child.name in LOCK_NAMES and rel not in locks:
                locks.append(rel)
            if file_count >= 3000:
                break

    has_readme = any(
        child.is_file() and child.name.lower().startswith("readme") for child in top_children
    )
    git_commits = _git_commit_count(root)
    raw_score = min(
        1.0,
        (0.35 if manifests else 0)
        + (0.2 if locks else 0)
        + (0.15 if has_readme else 0)
        + (0.2 if git_commits else 0)
        + min(file_count, 500) / 5000,
    )
    return DetectionSignals(
        workspace_path=str(root),
        exists=True,
        file_count=file_count,
        manifests=manifests[:12],
        structure_dirs=structure_dirs[:16],
        git_commits=git_commits,
        has_readme=has_readme,
        lock_files=locks[:12],
        commands=_detect_verification_commands(root, manifests, locks)[:12],
        raw_score=round(raw_score, 3),
    )


def _detect_verification_commands(root: Path, manifests: list[str], locks: list[str]) -> list[Any]:
    commands: list[dict[str, str]] = []
    commands.extend(_commands_from_package_json(root, locks))
    commands.extend(_commands_from_makefile(root))
    commands.extend(_commands_from_python(root, manifests, locks))
    return _dedupe_commands(commands)


def _dedupe_commands(commands: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    priority = {"typecheck": 0, "lint": 1, "test": 2, "build": 3}
    for item in sorted(commands, key=lambda c: priority.get(c.get("kind", ""), 9)):
        kind = item.get("kind", "").strip()
        command = item.get("command", "").strip()
        source = item.get("source", "").strip()
        if not kind or not command or not source:
            continue
        key = (kind, command)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"kind": kind, "command": command, "source": source})
    return deduped


def _commands_from_package_json(root: Path, locks: list[str]) -> list[dict[str, str]]:
    data = _read_json_file(root / "package.json")
    if not isinstance(data, dict):
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    package_manager = _node_package_manager(locks)
    script_aliases = {
        "typecheck": ("typecheck", "type-check", "check:types", "tsc"),
        "lint": ("lint", "lint:check", "eslint"),
        "test": ("test", "test:unit", "vitest", "jest"),
        "build": ("build", "compile"),
    }
    commands: list[dict[str, str]] = []
    for kind, names in script_aliases.items():
        script_name = next((name for name in names if isinstance(scripts.get(name), str)), "")
        if not script_name:
            continue
        commands.append(
            {
                "kind": kind,
                "command": _node_script_command(package_manager, script_name),
                "source": f"package.json scripts.{script_name}",
            }
        )
    return commands


def _node_package_manager(locks: list[str]) -> str:
    lock_names = {Path(lock).name for lock in locks}
    if "pnpm-lock.yaml" in lock_names:
        return "pnpm"
    if "yarn.lock" in lock_names:
        return "yarn"
    if "bun.lockb" in lock_names or "bun.lock" in lock_names:
        return "bun"
    return "npm"


def _node_script_command(package_manager: str, script_name: str) -> str:
    if package_manager == "npm":
        return "npm test" if script_name == "test" else f"npm run {script_name}"
    if package_manager == "yarn":
        return f"yarn {script_name}"
    return f"{package_manager} run {script_name}"


def _commands_from_makefile(root: Path) -> list[dict[str, str]]:
    makefile = root / "Makefile"
    text = _read_small_text(makefile)
    if not text:
        return []
    targets = set()
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?![=])", line)
        if match:
            targets.add(match.group(1))
    aliases = {
        "typecheck": ("typecheck", "type-check", "check-types"),
        "lint": ("lint", "check", "ruff", "eslint"),
        "test": ("test", "tests", "unit"),
        "build": ("build", "compile"),
    }
    commands: list[dict[str, str]] = []
    for kind, names in aliases.items():
        target = next((name for name in names if name in targets), "")
        if target:
            commands.append(
                {
                    "kind": kind,
                    "command": f"make {target}",
                    "source": f"Makefile target {target}",
                }
            )
    return commands


def _commands_from_python(
    root: Path, manifests: list[str], locks: list[str]
) -> list[dict[str, str]]:
    has_pyproject = "pyproject.toml" in {Path(path).name for path in manifests}
    has_requirements = "requirements.txt" in {Path(path).name for path in manifests}
    has_poetry = "poetry.lock" in {Path(path).name for path in locks + manifests}
    if not (has_pyproject or has_requirements or has_poetry):
        return []

    pyproject = _read_small_text(root / "pyproject.toml") or ""
    requirements = _read_small_text(root / "requirements.txt") or ""
    combined = f"{pyproject}\n{requirements}".lower()
    runner = "poetry run " if has_poetry else ""
    pytest_command = (
        f"{runner}pytest"
        if runner
        else ".venv/bin/pytest"
        if (root / ".venv" / "bin" / "pytest").exists()
        else "python -m pytest"
    )
    commands: list[dict[str, str]] = []
    if "pytest" in combined or (root / "tests").is_dir():
        commands.append(
            {
                "kind": "test",
                "command": pytest_command,
                "source": "pyproject.toml/requirements/tests",
            }
        )
    if "ruff" in combined or "[tool.ruff" in combined:
        commands.append(
            {
                "kind": "lint",
                "command": f"{runner}ruff check .".strip(),
                "source": "pyproject.toml/requirements",
            }
        )
    if "mypy" in combined or "[tool.mypy" in combined:
        commands.append(
            {
                "kind": "typecheck",
                "command": f"{runner}mypy .".strip(),
                "source": "pyproject.toml/requirements",
            }
        )
    if has_pyproject and ("build-system" in combined or "[build-system]" in combined):
        commands.append(
            {
                "kind": "build",
                "command": f"{runner}python -m build".strip(),
                "source": "pyproject.toml build-system",
            }
        )
    return commands


def _read_small_text(path: Path, *, max_bytes: int = 65536) -> str | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return None
    if len(data) > max_bytes:
        data = data[:max_bytes]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="ignore")


def _read_json_file(path: Path) -> Any:
    text = _read_small_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _git_commit_count(root: Path) -> int | None:
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _recommend_mode(signals: Any) -> tuple[str, float, str]:
    manifests = list(getattr(signals, "manifests", []) or [])
    structure_dirs = set(getattr(signals, "structure_dirs", []) or [])
    file_count = int(getattr(signals, "file_count", 0) or 0)
    locks = list(getattr(signals, "lock_files", []) or [])
    git_commits = getattr(signals, "git_commits", None)

    arch_hit = bool(
        ARCHITECTURE_DIR_HINTS.intersection(structure_dirs)
        or any(path.startswith("docs/architecture/") for path in manifests)
    )
    if arch_hit and (file_count > 80 or git_commits):
        return (
            "architect",
            0.72,
            "Architecture or ADR structure detected in an established project.",
        )
    if manifests or locks or file_count > 20 or git_commits:
        return (
            "coder",
            0.82 if manifests or locks else 0.68,
            "Existing codebase signals detected from manifests, lock files, or git history.",
        )
    return (
        "builder",
        0.84,
        "Workspace is empty or very small, so starting from a runnable slice is best.",
    )


__all__ = ["create_agent_modes_router"]
