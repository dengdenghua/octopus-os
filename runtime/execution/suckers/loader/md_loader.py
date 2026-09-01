from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path
from typing import Any

from ..registry import Skill
from ..testing import SkillExpect, SkillTestCase

try:
    import yaml  # type: ignore[import-untyped]

    YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    YAML_AVAILABLE = False
    yaml = None  # type: ignore[assignment]


_FRONTMATTER_PATTERN = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|$)",
    re.DOTALL,
)


class SkillLoadError(ValueError):
    pass


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def load_skill_from_md(md_path: str | Path) -> Skill:
    if not YAML_AVAILABLE:
        raise SkillLoadError("PyYAML not installed · `pip install pyyaml`")

    path = Path(md_path)
    if not path.exists():
        raise SkillLoadError(f"file not found: {path}")
    if not path.is_file():
        raise SkillLoadError(f"not a file: {path}")

    text = path.read_text(encoding="utf-8")
    meta = _parse_frontmatter(text, source=str(path))
    handler_spec = meta.get("handler")
    if not handler_spec:
        raise SkillLoadError(f"{path}: missing 'handler' field")

    handler = _resolve_handler(handler_spec, base_dir=path.parent)
    tests = _parse_tests(meta.get("tests") or [], source=str(path))

    for required in ("name", "trusted_source"):
        if not meta.get(required):
            raise SkillLoadError(f"{path}: missing required field {required!r}")

    try:
        return Skill(
            name=meta["name"],
            description=meta.get("description", ""),
            affinity=list(meta.get("affinity", [])),
            cost_profile=meta.get("cost_profile", "low"),
            trusted_source=meta["trusted_source"],
            handler=handler,
            tests=tests,
        )
    except Exception as e:  # Implementation note.
        raise SkillLoadError(f"{path}: Skill schema invalid: {e}") from e


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def load_skills_from_dir(
    dir_path: str | Path,
    *,
    glob: str = "*.md",
    recursive: bool = False,
    skip_errors: bool = False,
) -> list[Skill]:
    base = Path(dir_path)
    if not base.is_dir():
        raise SkillLoadError(f"not a directory: {base}")

    paths = sorted(base.rglob(glob) if recursive else base.glob(glob))
    skills: list[Skill] = []
    for p in paths:
        try:
            skills.append(load_skill_from_md(p))
        except SkillLoadError:
            if not skip_errors:
                raise
    return skills


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _parse_frontmatter(text: str, source: str) -> dict[str, Any]:
    m = _FRONTMATTER_PATTERN.match(text)
    if not m:
        raise SkillLoadError(f"{source}: no YAML frontmatter found (expected '---' delimiters)")
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise SkillLoadError(f"{source}: YAML parse failed: {e}") from e
    if not isinstance(data, dict):
        raise SkillLoadError(f"{source}: frontmatter must be a mapping, got {type(data).__name__}")
    return data


def _parse_tests(raw_tests: list[Any], source: str) -> list[SkillTestCase]:
    cases: list[SkillTestCase] = []
    for i, raw in enumerate(raw_tests):
        if not isinstance(raw, dict):
            raise SkillLoadError(
                f"{source}: tests[{i}] must be a mapping, got {type(raw).__name__}"
            )
        expect_raw = raw.get("expect") or {}
        if not isinstance(expect_raw, dict):
            raise SkillLoadError(f"{source}: tests[{i}].expect must be mapping")
        try:
            expect = SkillExpect(**expect_raw)
            case = SkillTestCase(
                name=raw.get("name", f"case_{i}"),
                tier=raw.get("tier", "golden"),
                args=dict(raw.get("args", {})),
                expect=expect,
            )
        except Exception as e:  # noqa: BLE001
            raise SkillLoadError(f"{source}: tests[{i}] invalid: {e}") from e
        cases.append(case)
    return cases


def _resolve_handler(spec: str, *, base_dir: Path) -> Any:
    """handler_spec → callable。"""
    if ":" not in spec:
        raise SkillLoadError(f"handler spec must be '<module>:<func>' (got {spec!r})")
    module_part, _, func_name = spec.rpartition(":")
    if not module_part or not func_name:
        raise SkillLoadError(f"handler spec malformed: {spec!r}")

    if module_part.endswith(".py") or module_part.startswith(("./", "/", ".\\")):
        module = _load_module_from_path(module_part, base_dir=base_dir)
    else:
        try:
            module = importlib.import_module(module_part)
        except ImportError as e:
            raise SkillLoadError(f"cannot import {module_part!r}: {e}") from e

    if not hasattr(module, func_name):
        raise SkillLoadError(f"module {module_part!r} has no attribute {func_name!r}")
    fn = getattr(module, func_name)
    if not callable(fn):
        raise SkillLoadError(f"{spec}: target is not callable")
    return fn


def _load_module_from_path(rel_or_abs: str, *, base_dir: Path) -> Any:
    path = Path(rel_or_abs)
    path = (base_dir / rel_or_abs).resolve() if not path.is_absolute() else path.resolve()

    if path.suffix != ".py":
        raise SkillLoadError(f"handler file must be .py: {path}")

    if not rel_or_abs.startswith("/") and not Path(rel_or_abs).is_absolute():
        base_resolved = base_dir.resolve()
        try:
            path.relative_to(base_resolved)
        except ValueError:
            raise SkillLoadError(
                f"handler file escapes base dir: {path} not under {base_resolved}"
            ) from None

    if not path.exists():
        raise SkillLoadError(f"handler file not found: {path}")

    mod_name = f"_echo_agent_skill_{path.stem}_{id(path)}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise SkillLoadError(f"cannot build spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module
