from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class VerificationRequirement:
    """A deterministic verification obligation derived from touched files."""

    key: str
    label: str
    command_hints: tuple[str, ...]
    paths: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class ProjectVerificationProfile:
    """Verification commands discovered from the current project."""

    python_hints: tuple[str, ...] = ()
    frontend_hints: tuple[str, ...] = ()
    schema_hints: tuple[str, ...] = ()
    api_hints: tuple[str, ...] = ()


_PY_EXTENSIONS = (".py", ".pyi")
_FRONTEND_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_STATIC_WEB_EXTENSIONS = (".html", ".htm", ".css")
_CONFIG_EXTENSIONS = (".json", ".toml", ".yaml", ".yml")

_FRONTEND_CONFIG_NAMES = frozenset(
    {
        "package.json",
        "pnpm-lock.yaml",
        "package-lock.json",
        "yarn.lock",
        "tsconfig.json",
        "tsconfig.app.json",
        "vite.config.ts",
        "vite.config.js",
        "vitest.config.ts",
        "eslint.config.js",
        "eslint.config.mjs",
    }
)

_PYTHON_CONFIG_NAMES = frozenset(
    {
        "pyproject.toml",
        "pytest.ini",
        "mypy.ini",
        "ruff.toml",
    }
)

_SCHEMA_HINT_SEGMENTS = frozenset(
    {
        "schema",
        "schemas",
        "migration",
        "migrations",
        "alembic",
    }
)

_API_HINT_SEGMENTS = frozenset(
    {
        "api",
        "router",
        "routers",
        "routes",
        "contract",
        "contracts",
    }
)


def normalize_policy_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def classify_path(path: str | None) -> str | None:
    """Classify a touched path into the verification policy bucket."""

    if not path:
        return None
    normalized = normalize_policy_path(path)
    if not normalized:
        return None
    lowered = normalized.lower()
    name = PurePosixPath(lowered).name
    parts = tuple(part for part in PurePosixPath(lowered).parts if part)

    if lowered.endswith(_PY_EXTENSIONS):
        if any(part in _SCHEMA_HINT_SEGMENTS for part in parts):
            return "python-schema"
        if any(part in _API_HINT_SEGMENTS for part in parts):
            return "python-api"
        return "python"

    if lowered.endswith(_FRONTEND_EXTENSIONS):
        return "frontend"

    if lowered.endswith(_STATIC_WEB_EXTENSIONS):
        return "static-web"

    if name in _FRONTEND_CONFIG_NAMES:
        return "frontend"

    if name in _PYTHON_CONFIG_NAMES:
        return "python"

    if lowered.endswith(".sql") or any(part in _SCHEMA_HINT_SEGMENTS for part in parts):
        return "schema"

    if lowered.endswith(_CONFIG_EXTENSIONS) and any(part in _API_HINT_SEGMENTS for part in parts):
        return "api-contract"

    return None


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for raw in items:
        item = str(raw).strip()
        if item:
            seen.setdefault(item, None)
    return tuple(seen)


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_make_targets(root: Path) -> set[str]:
    makefile = root / "Makefile"
    try:
        lines = makefile.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return set()

    targets: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ".", "\t")):
            continue
        head = stripped.split(":", 1)[0].strip()
        if not head or "=" in head or " " in head:
            continue
        targets.add(head)
    return targets


def _package_manager_for(package_dir: Path) -> str:
    if (package_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (package_dir / "yarn.lock").exists():
        return "yarn"
    if (package_dir / "bun.lockb").exists():
        return "bun"
    return "npm"


def _script_command(package_dir: Path, root: Path, manager: str, script: str) -> str:
    prefix = ""
    try:
        relative = package_dir.relative_to(root)
    except ValueError:
        relative = Path()
    if str(relative) not in ("", "."):
        prefix = f"cd {relative.as_posix()} && "
    if manager == "npm":
        return f"{prefix}npm run {script}"
    return f"{prefix}{manager} {script}"


def _package_scripts(package_dir: Path) -> dict[str, str]:
    package = _read_json_file(package_dir / "package.json")
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(key): str(value) for key, value in scripts.items() if isinstance(key, str)}


@lru_cache(maxsize=16)
def _project_verification_profile_cached(root_text: str) -> ProjectVerificationProfile:
    root = Path(root_text)
    make_targets = _read_make_targets(root)

    python_hints: list[str] = []
    for target in ("test-fast", "test-unit", "test", "lint"):
        if target in make_targets:
            python_hints.append(f"make {target}")
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        python_hints.append("python -m pytest")
    if (root / "pyproject.toml").exists() or (root / "ruff.toml").exists():
        python_hints.append("ruff check")
    python_hints.extend(("python -m compileall", "mypy"))

    frontend_hints: list[str] = []
    for target in ("frontend-typecheck", "frontend-build"):
        if target in make_targets:
            frontend_hints.append(f"make {target}")

    package_dirs = [root / "frontend", root]
    for package_dir in package_dirs:
        if not (package_dir / "package.json").exists():
            continue
        manager = _package_manager_for(package_dir)
        scripts = _package_scripts(package_dir)
        for script in ("typecheck", "check", "build", "test", "lint"):
            if script in scripts:
                frontend_hints.append(_script_command(package_dir, root, manager, script))

    schema_hints: list[str] = []
    for target in ("openapi-snapshot", "test-fast", "test"):
        if target in make_targets:
            schema_hints.append(f"make {target}")
    schema_hints.extend(("python -m pytest", "alembic check", "alembic upgrade head"))

    api_hints: list[str] = []
    for target in ("openapi-snapshot", "frontend-types", "test-fast", "test"):
        if target in make_targets:
            api_hints.append(f"make {target}")
    api_hints.extend(
        (
            "python -m pytest tests/test_openapi_snapshot.py -q",
            "python -m pytest",
            "pnpm test",
            "npm test",
        )
    )

    return ProjectVerificationProfile(
        python_hints=_unique(python_hints),
        frontend_hints=_unique(frontend_hints),
        schema_hints=_unique(schema_hints),
        api_hints=_unique(api_hints),
    )


def project_verification_profile(
    project_root: str | Path | None = None,
) -> ProjectVerificationProfile:
    root = Path(project_root) if project_root is not None else Path.cwd()
    try:
        root = root.resolve()
    except OSError:
        root = root.absolute()
    return _project_verification_profile_cached(str(root))


def verification_requirements_for_paths(
    paths: Iterable[str],
    *,
    project_root: str | Path | None = None,
) -> list[VerificationRequirement]:
    """Return de-duplicated required checks for the touched path set."""

    profile = project_verification_profile(project_root)
    buckets: dict[str, list[str]] = {}
    for raw in paths:
        normalized = normalize_policy_path(str(raw))
        bucket = classify_path(normalized)
        if bucket is None:
            continue
        buckets.setdefault(bucket, [])
        if normalized not in buckets[bucket]:
            buckets[bucket].append(normalized)

    requirements: list[VerificationRequirement] = []
    if any(key in buckets for key in ("python", "python-api", "python-schema")):
        py_paths = tuple(
            path
            for key in ("python", "python-api", "python-schema")
            for path in buckets.get(key, [])
        )
        requirements.append(
            VerificationRequirement(
                key="python-checks",
                label="Python tests or compile check",
                command_hints=_unique(
                    (
                        *profile.python_hints,
                        "python -m pytest",
                        "python -m compileall",
                        "ruff check",
                        "mypy",
                    )
                ),
                paths=py_paths,
            ),
        )

    if "frontend" in buckets:
        requirements.append(
            VerificationRequirement(
                key="frontend-typecheck",
                label="Frontend typecheck or build",
                command_hints=_unique(
                    (
                        *profile.frontend_hints,
                        "pnpm typecheck",
                        "npm run typecheck",
                        "npx tsc --noEmit",
                        "pnpm build",
                        "npm run build",
                        "pnpm test",
                        "npm test",
                    )
                ),
                paths=tuple(buckets["frontend"]),
            ),
        )

    if "static-web" in buckets:
        requirements.append(
            VerificationRequirement(
                key="static-web-artifact",
                label="Static web artifact smoke check",
                command_hints=_unique(
                    (
                        "read_file",
                        "browser_navigate",
                        "browser_screenshot",
                        "browser regression",
                        "node -c",
                        "html validate",
                        "htmlhint",
                        "npm run build",
                        "pnpm build",
                    )
                ),
                paths=tuple(buckets["static-web"]),
            ),
        )

    if any(key in buckets for key in ("schema", "python-schema")):
        schema_paths = tuple(
            path for key in ("schema", "python-schema") for path in buckets.get(key, [])
        )
        requirements.append(
            VerificationRequirement(
                key="schema-contract",
                label="Schema or migration compatibility check",
                command_hints=_unique(
                    (
                        *profile.schema_hints,
                        "python -m pytest",
                        "alembic check",
                        "alembic upgrade head",
                        "prisma validate",
                        "drizzle-kit check",
                    )
                ),
                paths=schema_paths,
            ),
        )

    if any(key in buckets for key in ("api-contract", "python-api")):
        api_paths = tuple(
            path for key in ("api-contract", "python-api") for path in buckets.get(key, [])
        )
        requirements.append(
            VerificationRequirement(
                key="api-contract",
                label="API contract or route test",
                command_hints=_unique(
                    (
                        *profile.api_hints,
                        "python -m pytest",
                        "pytest tests",
                        "pnpm test",
                        "npm test",
                        "openapi",
                    )
                ),
                paths=api_paths,
            ),
        )

    return requirements


def required_verification_keys_for_paths(
    paths: Iterable[str],
    *,
    project_root: str | Path | None = None,
) -> set[str]:
    return {
        req.key
        for req in verification_requirements_for_paths(paths, project_root=project_root)
        if req.required
    }


def _command_contains_hint(text: str, hints: Iterable[str]) -> bool:
    haystack = " ".join(text.lower().split())
    for hint in hints:
        needle = " ".join(str(hint).lower().split())
        if needle and needle in haystack:
            return True
    return False


def command_satisfies_requirement(
    text: str,
    requirement: VerificationRequirement,
) -> bool:
    """Whether a tool action/observation text satisfies one requirement."""

    if _command_contains_hint(text, requirement.command_hints):
        return True

    haystack = f" {text.lower()} "
    dedicated_actions = {
        match.group(1).lower()
        for match in re.finditer(
            r"(?:^|\n)\s*(run_tests|run_checks|lint_check|typecheck|verify)\s*\(",
            text,
            re.IGNORECASE,
        )
    }

    def contains_any(markers: tuple[str, ...]) -> bool:
        return any(marker.strip() in haystack for marker in markers)

    if requirement.key == "python-checks":
        if dedicated_actions & {
            "run_tests",
            "run_checks",
            "lint_check",
            "typecheck",
            "verify",
        }:
            return True
        return contains_any(
            (
                " pytest",
                " python -m pytest",
                " unittest",
                " python -m compileall",
                " py_compile",
                " ruff check",
                " mypy",
                " pyright",
                " flake8",
                " black --check",
            )
        )
    if requirement.key == "frontend-typecheck":
        if dedicated_actions & {
            "run_tests",
            "run_checks",
            "lint_check",
            "typecheck",
            "verify",
        }:
            return True
        return contains_any(
            (
                " pnpm typecheck",
                " pnpm run typecheck",
                " npm run typecheck",
                " yarn typecheck",
                " npx tsc",
                " tsc --noemit",
                " tsc --noemit",
                " pnpm build",
                " pnpm run build",
                " npm run build",
                " yarn build",
                " pnpm test",
                " npm test",
                " vitest",
                " jest",
                " playwright test",
                " eslint",
            )
        )
    if requirement.key == "static-web-artifact":
        return contains_any(
            (
                " read_file",
                " browser_navigate",
                " browser_screenshot",
                " browser regression",
                " node -c",
                " html validate",
                " htmlhint",
                " pnpm build",
                " pnpm run build",
                " npm run build",
                " playwright",
            )
        )
    if requirement.key == "schema-contract":
        if dedicated_actions & {"run_tests", "run_checks", "verify"}:
            return True
        return contains_any(
            (
                " pytest",
                " python -m pytest",
                " alembic check",
                " alembic upgrade",
                " prisma validate",
                " drizzle-kit check",
                " migrate",
                " migration",
            )
        )
    if requirement.key == "api-contract":
        if dedicated_actions & {"run_tests", "run_checks", "verify"}:
            return True
        return contains_any(
            (
                " pytest",
                " python -m pytest",
                " pnpm test",
                " npm test",
                " vitest",
                " jest",
                " playwright test",
                " openapi",
                " contract",
            )
        )
    return any(hint.lower() in haystack for hint in requirement.command_hints)


def summarize_requirements(requirements: Iterable[VerificationRequirement]) -> str:
    parts: list[str] = []
    for req in requirements:
        hint = " / ".join(req.command_hints[:3])
        paths = ", ".join(req.paths[:3])
        if len(req.paths) > 3:
            paths += f", +{len(req.paths) - 3} more"
        suffix = f" for {paths}" if paths else ""
        parts.append(f"{req.label}{suffix} ({hint})")
    return "; ".join(parts)
