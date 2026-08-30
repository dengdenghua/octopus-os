"""Registrar for code_intelligence_skills · extracted from
code_intelligence_skills.py.

Contains only ``register_code_intelligence_skills``.  All handler functions
and module state (embedder / persisted index) remain in ``code_intelligence_skills``.

Import order: ``code_intelligence_skills`` defines all handlers first, THEN
imports ``register_code_intelligence_skills`` from this submodule at the
bottom of the file.  When this module is loaded, ``code_intelligence_skills``
is already in ``sys.modules`` with all handlers defined, so the imports below
succeed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .code_intelligence_skills import (
    _code_analyze,
    _code_dependency_graph,
    _code_edit_diff,
    _code_find_symbol,
    _code_search,
)
from .registry import Skill
from .testing import SkillExpect, SkillTestCase

if TYPE_CHECKING:
    from .registry import SkillRegistry


def register_code_intelligence_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="code_analyze",
            description=(
                "解析代码文件的 AST 结构。返回 functions/classes/imports/"
                "call_graph。支持 Python（精确 AST）和 JS/TS/Go/Rust/Java"
                "（正则 fallback）。Args: {path: string} 或 {content: string, language?: string}。"
                "用于理解代码结构、查找函数定义、分析依赖关系。"
            ),
            affinity=["code", "analysis"],
            cost_profile="low",
            trusted_source="skill://private/code_analyze",
            handler=_code_analyze,
            tests=[
                SkillTestCase(
                    name="empty_returns_error",
                    tier="golden",
                    args={},
                    expect=SkillExpect(output_contains=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="code_search",
            description=(
                "代码搜索。两种模式："
                "(1) 默认 mode='regex' — 语义/embedding 搜索，"
                "Args: {query: string, directory?: string, top_k?: int}。"
                "首次调用会索引目录，后续复用。无 sentence-transformers 时退化为文本搜索。"
                "(2) mode='ast' — tree-sitter 结构化搜索，"
                "Args: {mode:'ast', query_type:'function_calls'|'function_definitions'|"
                "'class_definitions'|'imports', target_name: string, root?: string, "
                "glob?: string}. 跳过注释/字符串中的伪命中。"
            ),
            affinity=["code", "search", "rag"],
            cost_profile="mid",
            trusted_source="skill://private/code_search",
            handler=_code_search,
            tests=[
                SkillTestCase(
                    name="empty_query_error",
                    tier="golden",
                    args={},
                    expect=SkillExpect(output_contains=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="code_edit_diff",
            description=(
                "Diff 级代码编辑。两种模式：(1) search/replace 精确文本替换 "
                "Args: {path, search, replace}；(2) unified diff 应用 "
                "Args: {path, diff}。比整文件覆写更安全精确。"
            ),
            affinity=["code", "fs_write"],
            cost_profile="low",
            trusted_source="skill://private/code_edit_diff",
            handler=_code_edit_diff,
            tests=[
                SkillTestCase(
                    name="missing_path_error",
                    tier="golden",
                    args={},
                    expect=SkillExpect(output_contains=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="code_find_symbol",
            description=(
                "跨文件查找符号定义位置。精确定位函数/类/变量在哪个文件哪一行定义。"
                "Args: {symbol: string, directory?: string}。"
                "用于'跳转到定义'、'查找所有引用'等代码导航场景。"
            ),
            affinity=["code", "analysis"],
            cost_profile="mid",
            trusted_source="skill://private/code_find_symbol",
            handler=_code_find_symbol,
            tests=[
                SkillTestCase(
                    name="empty_symbol_error",
                    tier="golden",
                    args={},
                    expect=SkillExpect(output_contains=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="code_dependency_graph",
            description=(
                "分析 Python 项目的文件间 import 依赖图。"
                "返回 nodes（文件）和 edges（import 关系），可用于理解项目结构。"
                "Args: {directory?: string, package?: string}。"
            ),
            affinity=["code", "analysis"],
            cost_profile="mid",
            trusted_source="skill://private/code_dependency_graph",
            handler=_code_dependency_graph,
            tests=[
                SkillTestCase(
                    name="missing_dir_error",
                    tier="golden",
                    args={"directory": "/nonexistent"},
                    expect=SkillExpect(output_contains=["error"]),
                ),
            ],
        )
    )
    return 5
