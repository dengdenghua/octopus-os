"""Tests for ``_format_skill_catalog`` progressive disclosure (lane C).

The catalog should only list ``name + ≤30字 short description`` to
keep the system-prompt block small (better prompt-cache hit rate)
and stable. Full schema is fetched on-demand via ``query_skill``.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_context import _format_skill_catalog


class _FakeSkill:
    def __init__(self, *, name: str, description: str = "", summary: str = "", affinity=None):
        self.name = name
        self.description = description
        self.summary = summary
        self.effective_summary = ""
        self.affinity = affinity or []


class _FakeRegistry:
    def __init__(self, skills: list[_FakeSkill]):
        self._by_name = {s.name: s for s in skills}

    def all_names(self):
        return list(self._by_name.keys())

    def get(self, name):
        return self._by_name[name]

    def is_enabled(self, name):  # noqa: ARG002 — stub
        return True


def test_uses_summary_when_available() -> None:
    reg = _FakeRegistry(
        [
            _FakeSkill(name="read_file", description="A long " * 50, summary="读文件"),
        ]
    )
    out = _format_skill_catalog(reg)
    assert "- read_file: 读文件" in out
    assert "A long " not in out


def test_falls_back_to_truncated_description_when_no_summary() -> None:
    long_desc = (
        "用途: 读取一个文件的全部或部分内容。"
        "何时不用: 多文件用 glob_files。"
        "关键参数: path, offset, limit。"
    )
    reg = _FakeRegistry(
        [
            _FakeSkill(name="read_file", description=long_desc),
        ]
    )
    out = _format_skill_catalog(reg)
    # Should appear in the catalog under read_file
    assert "- read_file:" in out
    # Find the line for read_file
    line = next(L for L in out.splitlines() if L.startswith("  - read_file:"))
    short = line.split(":", 1)[1].strip()
    # ≤30 chars after the colon (just the short description, not the
    # leading "  - read_file: " prefix). The 30-char cap is stricter
    # than typical Chinese sentence ends, so most descriptions get
    # truncated at the first 。 ／ . ／ newline.
    assert len(short) <= 30


def test_breaks_at_first_sentence_terminator() -> None:
    desc = "短句一。这是后面被截掉的内容很长很长不应该出现在 catalog 中"
    reg = _FakeRegistry(
        [
            _FakeSkill(name="x", description=desc),
        ]
    )
    out = _format_skill_catalog(reg)
    line = next(L for L in out.splitlines() if L.startswith("  - x:"))
    short = line.split(":", 1)[1].strip()
    assert short == "短句一"


def test_appends_query_skill_hint() -> None:
    reg = _FakeRegistry(
        [
            _FakeSkill(name="some_skill", summary="example"),
        ]
    )
    out = _format_skill_catalog(reg)
    assert "query_skill" in out


def test_prioritizes_delegation_tools_before_catalog_truncation() -> None:
    skills = [_FakeSkill(name=f"filler_{i}", summary="filler") for i in range(80)]
    skills.extend(
        [
            _FakeSkill(name="call_agent", summary="serial delegation"),
            _FakeSkill(name="call_agent_parallel", summary="parallel delegation"),
            _FakeSkill(name="bb_write", summary="write blackboard"),
            _FakeSkill(name="bb_read", summary="read blackboard"),
            _FakeSkill(name="bb_keys", summary="list blackboard keys"),
            _FakeSkill(name="search_skills", summary="search all skills"),
        ]
    )
    reg = _FakeRegistry(skills)

    out = _format_skill_catalog(
        reg,
        max_skills=40,
        user_context={"mode": "code"},
    )

    assert "\n  - call_agent_parallel:" in out
    assert "\n  - bb_write:" in out
    assert "\n  - bb_read:" in out
    assert "\n  - bb_keys:" in out
    assert "\n  - search_skills:" in out
    assert "\n  - call_agent:" not in out


def test_prioritizes_common_general_tools_in_toolbar_order() -> None:
    names = [
        *(f"filler_{i}" for i in range(80)),
        "web_search",
        "read_file",
        "edit_file",
        "exec_shell",
        "git_status",
        "browser_navigate",
        "call_agent_parallel",
        "search_skills",
        "query_skill",
        "todo_write",
    ]
    reg = _FakeRegistry([_FakeSkill(name=name, summary=f"{name} summary") for name in names])

    out = _format_skill_catalog(
        reg,
        max_skills=12,
        user_context={"mode": "code", "browser_surface": "chrome"},
    )
    lines = [line for line in out.splitlines() if line.startswith("  - ")]
    visible_names = [line.split(":", 1)[0].replace("  - ", "") for line in lines]

    assert visible_names[:10] == [
        "todo_write",
        "search_skills",
        "query_skill",
        "read_file",
        "edit_file",
        "exec_shell",
        "git_status",
        "web_search",
        "call_agent_parallel",
        "browser_navigate",
    ]


def test_default_catalog_limit_is_one_hundred() -> None:
    reg = _FakeRegistry([_FakeSkill(name=f"skill_{i:03d}", summary="sample") for i in range(120)])

    out = _format_skill_catalog(reg)
    lines = [line for line in out.splitlines() if line.startswith("  - ")]

    assert len(lines) == 100
    assert "还有 20 个,可搜索发现" in out


def test_empty_registry_returns_empty_string() -> None:
    reg = _FakeRegistry([])
    assert _format_skill_catalog(reg) == ""


def test_goal_activation_promotes_relevant_catalog_entries() -> None:
    names = [
        *(f"filler_{i}" for i in range(80)),
        "web_search",
        "fetch_url",
        "deep-research",
        "query_skill",
    ]
    reg = _FakeRegistry([_FakeSkill(name=name, summary=f"{name} summary") for name in names])

    out = _format_skill_catalog(
        reg,
        max_skills=8,
        goal="调研一个值得进入的细分赛道，输出竞品格局",
    )

    assert "\n  - web_search:" in out
    assert "\n  - fetch_url:" in out
    assert "\n  - deep-research:" in out


def test_no_description_at_all_falls_back_to_marker() -> None:
    reg = _FakeRegistry(
        [
            _FakeSkill(name="bare"),
        ]
    )
    out = _format_skill_catalog(reg)
    assert "- bare: (无描述)" in out
