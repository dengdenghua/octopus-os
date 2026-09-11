from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.execution.arms.tool_registry import ToolRegistry
from runtime.execution.misc.capability_catalog import (
    build_capability_catalog,
    filter_capability_entries,
)
from runtime.execution.misc.capability_permissions import (
    reset_capability_permissions,
    set_capability_group_enabled,
)
from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.safety.approval.approval_gate import assess_approval_risk


@pytest.fixture(autouse=True)
def _reset_capability_permissions() -> None:
    reset_capability_permissions()
    yield
    reset_capability_permissions()


def _mobile_skills_root(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "skills" / "tap"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: android.tap
description: Tap a coordinate on the Android screen.
parameters:
  - name: x
    type: integer
    required: true
  - name: y
    type: integer
    required: true
---

Tap the device screen.
""",
        encoding="utf-8",
    )
    return tmp_path / "skills"


def _registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="read_file",
            description="Read a file.",
            affinity=["file"],
            trusted_source="builtin://read_file",
            handler=lambda **_kwargs: {"ok": True},
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="exec_shell",
            description="Execute a shell command.",
            affinity=["shell"],
            trusted_source="builtin://exec_shell",
            handler=lambda **_kwargs: {"ok": True},
        ),
        verify_tests=False,
    )
    return registry


def test_capability_catalog_merges_runtime_tools_and_mobile_skills(
    tmp_path: Path,
) -> None:
    set_capability_group_enabled("shell", False)

    catalog = build_capability_catalog(
        registry=_registry(),
        tool_registry=_tool_registry(),
        mobile_skills_root=_mobile_skills_root(tmp_path),
    )
    by_id = {entry["id"]: entry for entry in catalog["capabilities"]}

    read_file = by_id["runtime:read_file"]
    exec_shell = by_id["runtime:exec_shell"]
    demo_fetch = by_id["tool:fetch_demo"]
    android_tap = by_id["mobile:android_tap"]

    assert read_file["available"] is True
    assert read_file["permission"]["group"] == "builtin"
    assert read_file["risk"]["level"] == "low"
    assert exec_shell["available"] is False
    assert exec_shell["permission"]["group"] == "shell"
    assert exec_shell["permission"]["enabled"] is False
    assert exec_shell["risk"]["level"] == "high"
    assert "permission_disabled" in exec_shell["planning_hints"]
    assert demo_fetch["source"] == "tool_registry"
    assert demo_fetch["provider"]["id"] == "demo"
    assert demo_fetch["risk"]["level"] == "medium"
    assert android_tap["source"] == "mobile_mcp"
    assert android_tap["canonical_name"] == "android.tap"
    assert android_tap["risk"]["level"] == "high"
    assert "mobile_device_control" in android_tap["risk"]["categories"]
    assert catalog["summary"]["by_source"]["runtime_skill"] == 2
    assert catalog["summary"]["by_source"]["tool_registry"] == 1
    assert catalog["summary"]["by_source"]["mobile_mcp"] == 1


def test_capability_catalog_filters_entries(tmp_path: Path) -> None:
    catalog = build_capability_catalog(
        registry=_registry(),
        mobile_skills_root=_mobile_skills_root(tmp_path),
    )

    mobile = filter_capability_entries(
        catalog["capabilities"],
        source="mobile_mcp",
    )
    high = filter_capability_entries(
        catalog["capabilities"],
        risk_level="high",
        q="tap",
    )

    assert mobile["total"] == 1
    assert mobile["capabilities"][0]["canonical_name"] == "android.tap"
    assert high["total"] == 1
    assert high["capabilities"][0]["id"] == "mobile:android_tap"


def test_mobile_approval_risk_is_high_for_canonical_and_mcp_names() -> None:
    canonical = assess_approval_risk("android.tap")
    mcp_safe = assess_approval_risk("android_tap")

    assert canonical.level == "high"
    assert mcp_safe.level == "high"
    assert "mobile_device_control" in canonical.categories
    assert "mobile_device_control" in mcp_safe.categories


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    provider = registry.register_provider(
        "demo",
        "Demo Provider",
        feature_flags=["demo.enabled"],
    )
    provider.is_ready = True

    async def _handler(_args: dict[str, Any]) -> dict[str, bool]:
        return {"ok": True}

    registry.register_tool(
        "fetch_demo",
        "Fetch data from a demo API.",
        {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        _handler,
        provider_id="demo",
    )
    return registry
