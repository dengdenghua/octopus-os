"""Implementation note."""

# ruff: noqa: SIM102
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Implementation note.


@dataclass
class LintIssue:
    rule_id: str
    severity: str  # "error" | "warning"
    path: Path
    line: int
    col: int
    message: str

    def format(self) -> str:
        return (
            f"{self.path}:{self.line}:{self.col}: {self.severity} [{self.rule_id}] {self.message}"
        )


@dataclass
class LintContext:
    """Implementation note."""

    path: Path
    tree: ast.AST
    source: str
    source_lines: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.source_lines = self.source.splitlines()

    def in_package(self, name: str) -> bool:
        """Implementation note."""
        parts = self.path.parts
        return name in parts


# Implementation note.


class Rule:
    rule_id: str = ""
    severity: str = "error"

    def check(self, ctx: LintContext) -> Iterable[LintIssue]:
        raise NotImplementedError

    def issue(self, ctx: LintContext, node: ast.AST, message: str) -> LintIssue:
        return LintIssue(
            rule_id=self.rule_id,
            severity=self.severity,
            path=ctx.path,
            line=getattr(node, "lineno", 0),
            col=getattr(node, "col_offset", 0),
            message=message,
        )


# LINT-01 (NO_BYPASS_IMMUNITY) retired: it matched ``beak.bite()`` /
# ``immunity.check()``, but those packages were removed in the biological→
# neutral rename, and LINT-03 permanently bans re-introducing such names,
# so the rule could never fire again. Kept out of ALL_RULES rather than
# shipped as a dead guard that inflates the effective rule count.


# ─── LINT-02 · NO_MAGIC_ORGAN_COUNT ───────────────────────


class NoMagicOrganCountRule(Rule):
    """Implementation note."""

    rule_id = "LINT-02"
    severity = "warning"

    PATTERNS = [
        re.compile(r"\brange\s*\(\s*8\s*\)"),
        re.compile(r"(\[[^\]]*\])\s*\*\s*8\b"),
    ]

    def check(self, ctx: LintContext) -> Iterable[LintIssue]:
        if not (ctx.in_package("arms") or ctx.in_package("ganglia") or ctx.in_package("cerebrum")):
            return
        for lineno, line in enumerate(ctx.source_lines, start=1):
            for pat in self.PATTERNS:
                m = pat.search(line)
                if m:
                    yield LintIssue(
                        rule_id=self.rule_id,
                        severity=self.severity,
                        path=ctx.path,
                        line=lineno,
                        col=m.start(),
                        message="magic number 8 near arms/ganglia/cerebrum — use config.arms.types instead",
                    )


# ─── LINT-03 · BIO_NAME_IN_CODE ──────────────────────────


class BioNameInCodeRule(Rule):
    """Implementation note."""

    rule_id = "LINT-03"

    BIO_NAMES = {
        "Cerebrum",
        "Ganglia",
        "Arm",
        "Sucker",
        "Beak",
        "Mantle",
        "Chromatophore",
        "Chromatophores",
        "Siphon",
        "Hemolymph",
        "Regeneration",
        "Genome",
        "Camouflage",
        "Ink",
        "Skin",
        "SpinalCord",
        "Immunity",
    }

    def check(self, ctx: LintContext) -> Iterable[LintIssue]:
        # Implementation note.
        for node in ast.walk(ctx.tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in self.BIO_NAMES:
                    yield self.issue(
                        ctx,
                        node,
                        f"'{node.name}' is a biological organ name; use engineering name per NAMING.md",
                    )
            # Implementation note.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lower = node.name.lower()
                for bio in self.BIO_NAMES:
                    if lower.startswith(bio.lower() + "_"):
                        yield self.issue(
                            ctx,
                            node,
                            f"function '{node.name}' uses bio prefix; rename to engineering name",
                        )
                        break


# ─── LINT-04 · NO_RAW_LLM_CALL ────────────────────────────


class NoRawLLMCallRule(Rule):
    """Implementation note."""

    rule_id = "LINT-04"

    FORBIDDEN_MODULES = {
        "anthropic",
        "openai",
        "google.genai",
        "google.generativeai",
        "cohere",
        "mistralai",
        "together",
    }

    def check(self, ctx: LintContext) -> Iterable[LintIssue]:
        # The model-router implementations ARE the abstraction boundary this
        # rule protects: they must import the vendor SDKs so the rest of the
        # tree can route through them. Exempt the router package. It was
        # historically named ``eyes``; the live location is
        # ``runtime/sensing/model_router`` (the rename left this exemption
        # pointing at the dead name, which is why anthropic_router.py tripped
        # LINT-04). Keep ``eyes`` too for backward compatibility.
        if ctx.in_package("eyes") or ctx.in_package("model_router"):
            return
        # Tests legitimately import SDKs to build fakes/fixtures.
        if ctx.in_package("tests"):
            return
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.FORBIDDEN_MODULES:
                        yield self.issue(
                            ctx, node, f"raw import of '{alias.name}'; use eyes.ModelRouter instead"
                        )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and any(
                    node.module == m or node.module.startswith(m + ".")
                    for m in self.FORBIDDEN_MODULES
                )
            ):
                yield self.issue(
                    ctx, node, f"raw import from '{node.module}'; use eyes.ModelRouter instead"
                )


# ─── LINT-05 · TASK_NEEDS_BUDGET ──────────────────────────


class TaskNeedsBudgetRule(Rule):
    """Implementation note."""

    rule_id = "LINT-05"

    REQUIRED_KWARGS = {"max_tokens", "max_cost_usd"}
    # Implementation note.
    TASK_CTOR_SUFFIXES = ("Task",)

    def check(self, ctx: LintContext) -> Iterable[LintIssue]:
        # Implementation note.
        if ctx.in_package("tests"):
            return
        # Only the budget-bearing ``runtime.platform.models.Task`` is in
        # scope. Other modules define dataclasses named ``Task`` with
        # no budget contract (e.g. ``runtime.memory.cowork.store.Task``
        # is a plan unit). Skip files that don't import the pipeline
        # Task type.
        imports_pipeline_task = False
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("runtime.platform.models"):
                    if any(alias.name == "Task" for alias in node.names):
                        imports_pipeline_task = True
                        break
        if not imports_pipeline_task:
            return
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callable_name(node.func)
            if not name:
                continue
            # Implementation note.
            if not (name == "Task" or name.endswith(".Task")):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            missing = self.REQUIRED_KWARGS - kwargs
            if missing:
                yield self.issue(
                    ctx, node, f"Task() missing required budget kwargs: {sorted(missing)}"
                )


# ─── LINT-09 · REFLEX_NO_GENERATE ─────────────────────────


class ReflexNoGenerateRule(Rule):
    """Implementation note."""

    rule_id = "LINT-09"

    GENERATE_METHODS = {"generate", "complete", "create_message", "chat"}
    LLM_LIKE_HINTS = {"llm", "model", "anthropic", "openai", "cohere", "claude", "gpt"}

    def check(self, ctx: LintContext) -> Iterable[LintIssue]:
        if not (ctx.in_package("spinal_cord") or ctx.in_package("reflex")):
            return
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call):
                name = _callable_name(node.func)
                if not name:
                    continue
                parts = name.lower().split(".")
                if len(parts) < 2:
                    continue
                method = parts[-1]
                obj = parts[-2]
                if method in self.GENERATE_METHODS and any(h in obj for h in self.LLM_LIKE_HINTS):
                    yield self.issue(
                        ctx,
                        node,
                        f"reflex layer must not call LLM: '{name}' — use rule/cache/edge_slm only",
                    )


# LINT-10 (CRDT_NOT_LWW) retired: it required both the ``dna`` and
# ``genome`` packages, which were removed in the biological→neutral rename
# (and LINT-03 bans re-introducing those names). With no possible target
# it is kept out of ALL_RULES rather than shipped as a dead guard.


# Implementation note.


def _callable_name(node: ast.AST) -> str | None:
    """Implementation note."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _callable_name(node.value)
        if base is None:
            return node.attr
        return f"{base}.{node.attr}"
    return None


# ─── Runner ─────────────────────────────────────────────────

ALL_RULES: list[Rule] = [
    NoMagicOrganCountRule(),
    BioNameInCodeRule(),
    NoRawLLMCallRule(),
    TaskNeedsBudgetRule(),
    ReflexNoGenerateRule(),
]


def lint_file(path: Path, rules: list[Rule]) -> list[LintIssue]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as e:
        return [LintIssue("LINT-PARSE", "warning", path, 0, 0, f"parse error: {e}")]
    ctx = LintContext(path=path, tree=tree, source=source)
    issues: list[LintIssue] = []
    for rule in rules:
        issues.extend(rule.check(ctx))
    return issues


def lint_tree(root: Path, rules: list[Rule]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    paths = [root] if root.is_file() else list(root.rglob("*.py"))
    for path in paths:
        if any(part.startswith(".") for part in path.parts):
            continue
        if "__pycache__" in path.parts:
            continue
        # Skip Kimi-style learned-skill executable scripts.
        # ``runtime/execution/all_skills/<skill-slug>/`` holds
        # user-contributed / LLM-extracted skills where the directory
        # name is a slug (contains hyphens · not a valid Python package
        # identifier). Their embedded ``scripts/`` trees are runtime
        # ARTIFACTS, not ARCHITECTURE, so applying the bio-name /
        # raw-LLM-call lint makes no sense there. Uses hyphen presence
        # as the signal (Python packages can't have ``-`` in name).
        parts = path.parts
        if "all_skills" in parts:
            try:
                i = parts.index("all_skills")
                if i + 1 < len(parts) and "-" in parts[i + 1]:
                    continue
            except ValueError:
                pass
        issues.extend(lint_file(path, rules))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="File or directory paths")
    parser.add_argument("--rule", action="append", help="Limit to specific rule IDs (repeatable)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    rules = ALL_RULES
    if args.rule:
        keep = set(args.rule)
        rules = [r for r in ALL_RULES if r.rule_id in keep]

    all_issues: list[LintIssue] = []
    for p in args.paths:
        if not p.exists():
            print(f"error: path not found: {p}", file=sys.stderr)
            return 2
        all_issues.extend(lint_tree(p, rules))

    errors = [i for i in all_issues if i.severity == "error"]

    if args.json:
        import json

        payload = [
            {
                "rule": i.rule_id,
                "severity": i.severity,
                "file": str(i.path),
                "line": i.line,
                "col": i.col,
                "message": i.message,
            }
            for i in all_issues
        ]
        print(json.dumps(payload, indent=2))
    else:
        for i in all_issues:
            print(i.format())
        print(
            f"\n{len(all_issues)} issue(s): "
            f"{len(errors)} error, {len(all_issues) - len(errors)} warning",
            file=sys.stderr,
        )

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
