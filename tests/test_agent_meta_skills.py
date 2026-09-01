from __future__ import annotations

import json

from runtime.execution.suckers.agent_meta_skills import (
    _todo_write,
    register_agent_meta_skills,
)
from runtime.execution.suckers.registry import Skill, SkillRegistry


def _without_ids(items: list[dict]) -> list[dict]:
    return [{key: value for key, value in item.items() if key != "id"} for item in items]


def test_todo_write_accepts_model_friendly_aliases() -> None:
    result = _todo_write(
        todos=[
            {"text": "List project files", "status": "completed"},
            {
                "title": "Read roadmap",
                "status": "in_progress",
                "active_form": "Reading roadmap",
            },
            {"task": "Summarize findings", "status": "not_a_status"},
        ]
    )

    assert result["ok"] is True
    assert result["count"] == 3
    assert _without_ids(result["todos"]) == [
        {
            "content": "List project files",
            "status": "completed",
            "activeForm": "List project files",
        },
        {
            "content": "Read roadmap",
            "status": "in_progress",
            "activeForm": "Reading roadmap",
        },
        {
            "content": "Summarize findings",
            "status": "pending",
            "activeForm": "Summarize findings",
        },
    ]


def test_todo_write_accepts_json_string_todos() -> None:
    result = _todo_write(
        todos=(
            '[{"text":"Confirm task","status":"completed"},'
            '{"text":"Check constraints","status":"in_progress",'
            '"activeForm":"Checking constraints"}]'
        )
    )

    assert result["ok"] is True
    assert result["count"] == 2
    assert _without_ids(result["todos"]) == [
        {
            "content": "Confirm task",
            "status": "completed",
            "activeForm": "Confirm task",
        },
        {
            "content": "Check constraints",
            "status": "in_progress",
            "activeForm": "Checking constraints",
        },
    ]


def test_todo_write_accepts_tasks_description_alias() -> None:
    result = _todo_write(
        tasks=[
            {
                "id": "1",
                "description": "Audit frontend streaming",
                "status": "completed",
            },
            {
                "id": "2",
                "description": "Check browser regression",
                "status": "in_progress",
            },
        ]
    )

    assert result["ok"] is True
    assert result["count"] == 2
    assert _without_ids(result["todos"]) == [
        {
            "content": "Audit frontend streaming",
            "status": "completed",
            "activeForm": "Audit frontend streaming",
        },
        {
            "content": "Check browser regression",
            "status": "in_progress",
            "activeForm": "Check browser regression",
        },
    ]
    assert [item["id"] for item in result["todos"]] == ["1", "2"]


def test_todo_write_accepts_name_alias() -> None:
    result = _todo_write(
        items=[
            {
                "name": "Query call_agent_parallel schema",
                "status": "completed",
            },
        ]
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert _without_ids(result["todos"]) == [
        {
            "content": "Query call_agent_parallel schema",
            "status": "completed",
            "activeForm": "Query call_agent_parallel schema",
        },
    ]


def test_todo_write_keeps_stable_ids_across_status_updates_and_reordering() -> None:
    first = _todo_write(
        items=[
            {"content": "Inspect architecture", "status": "in_progress"},
            {"content": "Run verification", "status": "pending"},
        ]
    )
    first_ids = {item["content"]: item["id"] for item in first["todos"]}

    updated = _todo_write(
        items=[
            {"content": "Run verification", "status": "in_progress"},
            {"content": "Inspect architecture", "status": "completed"},
        ]
    )

    assert {item["content"]: item["id"] for item in updated["todos"]} == first_ids


def test_todo_write_allows_only_one_in_progress_item() -> None:
    result = _todo_write(
        todos=[
            {"text": "Read project", "status": "in_progress"},
            {"text": "Patch files", "status": "in_progress"},
            {"text": "Run tests", "status": "pending"},
        ]
    )

    assert result["ok"] is True
    assert [item["status"] for item in result["todos"]] == [
        "in_progress",
        "pending",
        "pending",
    ]
    assert result["normalized"] is True
    assert "Only one todo can be in_progress" in result["warnings"][0]


def test_todo_write_rejects_list_under_unrecognized_key() -> None:
    # Regression for thread tJnjK3LevqUdg97iD0KaSJ: the model called
    # todo_write(list=[...]) 9 times.  The tool silently returned
    # ok=True / count=0 each time, so the model kept retrying the same
    # wrong shape until the turn three-struck into an interrupt.  The
    # tool must now return ok=False with the accepted key names so the
    # model can self-correct on the next round.
    result = _todo_write(
        list=[
            {"title": "市场调研", "status": "completed"},
            {"title": "领域选择", "status": "in_progress"},
        ]
    )

    assert result["ok"] is False
    assert result["count"] == 0
    assert "list" in result["error"]
    assert "items" in result["error"]


def test_todo_write_rejects_serialized_params_string() -> None:
    # Second regression for the same thread: the model wrapped the
    # entire payload as a JSON string under ``params``.  The P1 fix
    # only checked for list-typed extras, so this string slipped
    # through and the tool returned ok=True / count=0 again.
    import json as _json

    params_str = _json.dumps(
        {"todo_list": [{"id": 1, "content": "市场调研", "status": "completed"}]}
    )
    result = _todo_write(params=params_str)

    assert result["ok"] is False
    assert "params" in result["error"]
    assert "items" in result["error"]


def test_todo_write_rejects_dict_under_unrecognized_key() -> None:
    # The model may also pass a dict under a wrong key (e.g.
    # ``params={"todo_list": [...]}``).  This must be caught too.
    result = _todo_write(params={"todo_list": [{"content": "test", "status": "pending"}]})

    assert result["ok"] is False
    assert "params" in result["error"]


def test_todo_write_allows_explicit_empty_list_to_clear_plan() -> None:
    # An explicit empty list under a RECOGNIZED key is a valid call
    # (clears the plan).  The unrecognized-key guard must not fire.
    result = _todo_write(items=[])
    assert result["ok"] is True
    assert result["count"] == 0


def test_query_skill_returns_full_registered_skill_details() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="sample_tool",
            summary="Short sample summary.",
            description="Full sample description with argument details.",
            affinity=["demo", "read"],
            cost_profile="mid",
            trusted_source="skill://public/sample_tool",
            handler=lambda **kw: kw,
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("query_skill").handler(name="sample_tool")

    assert result["ok"] is True
    assert result["name"] == "sample_tool"
    assert result["summary"] == "Short sample summary."
    assert result["description"] == "Full sample description with argument details."
    assert result["affinity"] == ["demo", "read"]
    assert result["cost_profile"] == "mid"
    assert result["enabled"] is True


def test_query_skill_missing_returns_error_payload() -> None:
    registry = SkillRegistry()
    register_agent_meta_skills(registry)

    result = registry.get("query_skill").handler(name="missing_tool")

    assert result["ok"] is False
    assert result["error"] == "skill not found: missing_tool"


def test_search_skills_finds_registered_skill_by_description() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="hidden_parallel_tool",
            summary="Delegate independent work.",
            description="Spawn multiple subagents for independent research lanes.",
            affinity=["delegation", "subagent", "parallel"],
            trusted_source="skill://public/hidden_parallel_tool",
            handler=lambda **kw: kw,
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("search_skills").handler(
        query="subagent parallel",
        limit=5,
    )

    assert result["ok"] is True
    assert result["count"] >= 1
    assert result["results"][0]["name"] == "hidden_parallel_tool"


def test_execute_skill_runs_discovered_read_only_skill() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="hidden_read_tool",
            summary="Read hidden data.",
            affinity=["read"],
            trusted_source="skill://public/hidden_read_tool",
            handler=lambda query="": {"value": query.upper()},
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("execute_skill").handler(
        name="hidden_read_tool",
        args={"query": "echo"},
    )

    assert result == {
        "ok": True,
        "name": "hidden_read_tool",
        "result": {"value": "ECHO"},
    }


def test_execute_skill_rejects_side_effecting_or_ambiguous_skill() -> None:
    registry = SkillRegistry()
    calls: list[str] = []
    registry.register(
        Skill(
            name="hidden_write_tool",
            affinity=["write"],
            trusted_source="skill://public/hidden_write_tool",
            handler=lambda **_: calls.append("called"),
        )
    )
    registry.register(
        Skill(
            name="untagged_tool",
            trusted_source="skill://public/untagged_tool",
            handler=lambda **_: calls.append("called"),
        )
    )
    register_agent_meta_skills(registry)

    write_result = registry.get("execute_skill").handler(
        name="hidden_write_tool",
        args={},
    )
    ambiguous_result = registry.get("execute_skill").handler(
        name="untagged_tool",
        args={},
    )

    assert write_result["ok"] is False
    assert ambiguous_result["ok"] is False
    assert "normal tool/capability path" in write_result["error"]
    assert calls == []


def test_execute_skill_rejects_meta_recursion_and_disabled_skill() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="disabled_reader",
            affinity=["read"],
            trusted_source="skill://public/disabled_reader",
            handler=lambda: "should not run",
        )
    )
    register_agent_meta_skills(registry)
    registry.set_enabled("disabled_reader", False)

    disabled = registry.get("execute_skill").handler(name="disabled_reader", args={})
    recursive = registry.get("execute_skill").handler(name="query_skill", args={})

    assert disabled == {
        "ok": False,
        "name": "disabled_reader",
        "error": "skill is disabled",
    }
    assert recursive["ok"] is False
    assert "meta skills" in recursive["error"]


def test_execute_skill_strips_model_controlled_overrides() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="safe_reader",
            affinity=["read"],
            trusted_source="skill://public/safe_reader",
            handler=lambda **kwargs: kwargs,
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("execute_skill").handler(
        name="safe_reader",
        args={"query": "ok", "allow_sensitive": True},
    )

    assert result["ok"] is True
    assert result["result"] == {"query": "ok"}
    assert result["stripped_overrides"] == ["allow_sensitive"]


def test_search_capabilities_finds_runtime_plugin_package() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo_plugin.list_items",
            summary="List demo plugin items.",
            description="List items from the demo plugin.",
            affinity=["demo", "plugin"],
            trusted_source="plugin://demo-plugin/list_items",
            handler=lambda **kw: {"ok": True, "items": [kw.get("kind", "default")]},
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("search_capabilities").handler(query="demo plugin")

    assert result["ok"] is True
    assert result["count"] >= 1
    assert result["results"][0]["id"] == "demo-plugin"
    assert result["results"][0]["registered_actions"] == ["demo_plugin.list_items"]


def test_query_capability_returns_package_level_view() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo_plugin.list_items",
            summary="List demo plugin items.",
            affinity=["demo", "plugin"],
            trusted_source="plugin://demo-plugin/list_items",
            handler=lambda **kw: {"ok": True},
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("query_capability").handler(capability_id="demo-plugin")

    assert result["ok"] is True
    assert result["id"] == "demo-plugin"
    assert result["kind"] == "plugin"
    assert result["skills"][0]["name"] == "demo_plugin.list_items"


def test_use_capability_runs_registered_child_action() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo_plugin.list_items",
            summary="List demo plugin items.",
            affinity=["demo", "plugin"],
            trusted_source="plugin://demo-plugin/list_items",
            handler=lambda **kw: {"ok": True, "kind": kw.get("kind")},
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("use_capability").handler(
        capability_id="demo-plugin",
        action="list_items",
        args={"kind": "task"},
    )

    assert result["ok"] is True
    assert result["capability_id"] == "demo-plugin"
    assert result["action"] == "demo_plugin.list_items"
    assert result["result"] == {"ok": True, "kind": "task"}


def test_codex_plugin_skill_injection_registers_runtime_actions(tmp_path) -> None:
    plugin_dir = tmp_path / "demo-plugin"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "0.1.0",
                "interface": {
                    "displayName": "Demo Plugin",
                    "capabilities": [{"name": "demo", "type": "codex"}],
                },
            }
        ),
        encoding="utf-8",
    )
    skill_dir = plugin_dir / "skills" / "hello"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: hello\n"
        "description: Say hello from the plugin.\n"
        "---\n"
        "\n"
        "# Hello\n"
        "\n"
        "Use this plugin instruction.\n",
        encoding="utf-8",
    )

    from runtime.execution.suckers.codex_plugin_skills import (
        load_codex_plugin_skills,
    )

    registry = SkillRegistry()
    register_agent_meta_skills(registry)
    report = load_codex_plugin_skills(
        registry,
        ("demo-plugin",),
        roots=[tmp_path],
    )

    assert report.handled_plugin_ids == ("demo-plugin",)
    assert registry.has("demo-plugin__hello")

    query = registry.get("query_capability").handler(capability_id="demo-plugin")
    assert query["ok"] is True
    assert query["registered_actions"] == ["demo-plugin__hello"]
    assert query["skills"][0]["registered"] is True
    assert query["skills"][0]["registered_as"] == "demo-plugin__hello"

    result = registry.get("use_capability").handler(
        capability_id="demo-plugin",
        action="hello",
    )
    assert result["ok"] is True
    assert result["action"] == "demo-plugin__hello"
    assert result["result"]["plugin"] == "demo-plugin"
    assert result["result"]["plugin_skill"] == "hello"
    assert "Use this plugin instruction." in result["result"]["instructions"]


def test_pinned_plugin_actions_are_prioritized() -> None:
    from runtime.core.cerebrum.capability_router import activate_capabilities, order_skill_names

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo-plugin__hello",
            summary="Demo plugin hello.",
            description="Demo plugin hello.",
            affinity=["plugin", "plugin:demo-plugin"],
            trusted_source="plugin://demo-plugin/hello",
            handler=lambda **_: {"ok": True},
        )
    )
    registry.register(
        Skill(
            name="other_tool",
            summary="Other.",
            description="Other.",
            affinity=[],
            trusted_source="skill://public/other",
            handler=lambda **_: {"ok": True},
        )
    )
    register_agent_meta_skills(registry)

    activation = activate_capabilities("@plugin:demo-plugin please", registry=registry)
    ordered = order_skill_names(
        ["other_tool", "demo-plugin__hello", "use_capability", "query_capability"],
        activation=activation,
        registry=registry,
    )

    assert ordered.index("demo-plugin__hello") < ordered.index("other_tool")


def test_use_capability_blocks_risky_inner_action_under_taint() -> None:
    """Red-team #7 (critical): use_capability dispatches to the inner handler
    DIRECTLY, bypassing executor.execute_step and so the injection-taint
    chokepoint. A tainted turn must not be able to launder a risky action
    (exec_shell) through this low-risk-named meta-skill."""
    from runtime.safety.validation.prompt_injection import (
        mark_injection_taint,
        reset_injection_taint,
    )

    ran = {"shell": False}

    def _shell(**_kw):
        ran["shell"] = True
        return {"exit_code": 0}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="exec_shell",  # risk-recognized name; resolved action tail
            summary="run a shell command",
            affinity=["shell", "exec", "dangerous"],
            trusted_source="plugin://dangerpack/exec_shell",
            handler=_shell,
        )
    )
    register_agent_meta_skills(registry)

    reset_injection_taint()
    try:
        mark_injection_taint("high")  # untrusted content tainted the turn
        result = registry.get("use_capability").handler(
            capability_id="dangerpack",
            action="exec_shell",
            args={"command": "echo hi"},
        )
    finally:
        reset_injection_taint()

    assert result["ok"] is False
    assert "injection_taint_block" in result.get("error", "")
    assert ran["shell"] is False, "blocked inner handler must NOT have run"


def test_use_capability_allows_inner_action_on_clean_turn() -> None:
    """Control: with no taint, use_capability dispatches normally."""
    ran = {"shell": False}

    def _shell(**_kw):
        ran["shell"] = True
        return {"exit_code": 0}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="exec_shell",
            summary="run a shell command",
            affinity=["shell", "exec", "dangerous"],
            trusted_source="plugin://dangerpack/exec_shell",
            handler=_shell,
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("use_capability").handler(
        capability_id="dangerpack",
        action="exec_shell",
        args={"command": "x"},
    )
    assert result["ok"] is True
    assert ran["shell"] is True


def test_use_capability_blocks_denied_inner_action() -> None:
    """use_capability bypasses execute_step, so the capability-permission
    denylist must be re-applied at the inner dispatch: disabling the
    ``shell`` capability group must block an inner exec_shell."""
    from runtime.execution.misc.capability_permissions import (
        reset_capability_permissions,
        set_capability_group_enabled,
    )

    ran = {"shell": False}

    def _shell(**_kw):
        ran["shell"] = True
        return {"exit_code": 0}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="exec_shell",
            summary="run a shell command",
            affinity=["shell", "exec", "dangerous"],
            trusted_source="plugin://dangerpack/exec_shell",
            handler=_shell,
        )
    )
    register_agent_meta_skills(registry)

    reset_capability_permissions()
    try:
        set_capability_group_enabled("shell", False)
        result = registry.get("use_capability").handler(
            capability_id="dangerpack",
            action="exec_shell",
            args={"command": "echo hi"},
        )
    finally:
        reset_capability_permissions()

    assert result["ok"] is False
    assert "capability" in result.get("error", "")
    assert ran["shell"] is False, "denied inner handler must NOT have run"


def test_use_capability_blocks_untrusted_inner_action() -> None:
    """use_capability bypasses execute_step, so the immunity / trust gate
    must be re-applied at the inner dispatch: with the runtime TrustEngine
    bound, dispatching to an untrusted inner skill must be rejected."""
    from runtime.execution.tool_engine.skill_gate import use_trust_engine
    from runtime.safety.auth import TrustEngine

    ran = {"evil": False}

    def _evil(**_kw):
        ran["evil"] = True
        return {"ok": True}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="evil_tool",
            summary="untrusted inner tool",
            affinity=["plugin"],
            # non-public source → not matched by default trusted_sources
            trusted_source="plugin://evilpack/exfil",
            handler=_evil,
        )
    )
    register_agent_meta_skills(registry)

    # unknown_policy="reject" → an untrusted source returns verdict "reject",
    # which is the only immunity verdict execute_step (and the shared gate)
    # blocks on.
    engine = TrustEngine(unknown_policy="reject")
    with use_trust_engine(engine):
        result = registry.get("use_capability").handler(
            capability_id="evilpack",
            action="evil_tool",
            args={"x": 1},
        )

    assert result["ok"] is False
    assert "immune_reject" in result.get("error", "")
    assert ran["evil"] is False, "untrusted inner handler must NOT have run"


def test_use_capability_blocks_credential_file_write() -> None:
    """use_capability bypasses execute_step, so the credential-file
    denylist must be re-applied: an inner write to ``.env`` is blocked even
    though the surrounding meta-skill is low-risk."""
    ran = {"wrote": False}

    def _write(**_kw):
        ran["wrote"] = True
        return {"ok": True}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="write_text_file",
            summary="write a file",
            affinity=["file", "write"],
            trusted_source="plugin://fspack/write",
            handler=_write,
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("use_capability").handler(
        capability_id="fspack",
        action="write_text_file",
        args={"path": ".env", "content": "SECRET=1"},
    )

    assert result["ok"] is False
    assert "file_safety" in result.get("error", "")
    assert ran["wrote"] is False, "credential write handler must NOT have run"


# ══════════════════════════════════════════════════════════════════
# P0/P1 regression — schema quality + unrecognized-key rejection.
# These prevent the "silent no-op loop" failure mode where a tool
# returns ok=True with an empty result because the model passed
# arguments under a wrong key (e.g. ``list`` instead of ``items``).
# ══════════════════════════════════════════════════════════════════


def test_todo_write_schema_declares_items_as_array() -> None:
    # P0: the auto-derived JSON Schema must declare ``items`` as
    # ``"type": "array"`` (not "string") so the model knows to send a
    # list.  Previously the ``Any`` annotation made the schema fall
    # back to "string", which confused the model into inventing other
    # key names like ``list``.
    from runtime.execution.tool_spec_builder import _input_schema_from_handler

    schema = _input_schema_from_handler(_todo_write)[0]
    assert schema["properties"]["items"]["type"] == "array"
    assert schema["properties"]["todos"]["type"] == "array"
    assert schema["properties"]["tasks"]["type"] == "array"


def test_search_skills_rejects_query_under_wrong_key() -> None:
    # P1: search_skills(q="foo") must return ok=False instead of
    # silently returning every skill with ok=True.
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo_tool",
            summary="demo",
            description="demo",
            affinity=[],
            trusted_source="skill://public/demo",
            handler=lambda **kw: {"ok": True},
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("search_skills").handler(q="demo")
    assert result["ok"] is False
    assert "query" in result["error"]


def test_search_capabilities_rejects_query_under_wrong_key() -> None:
    # P1: search_capabilities(search="demo") must return ok=False.
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo_plugin.list_items",
            summary="List demo items.",
            description="List demo items.",
            affinity=["demo"],
            trusted_source="plugin://demo-plugin/list_items",
            handler=lambda **kw: {"ok": True},
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("search_capabilities").handler(search="demo")
    assert result["ok"] is False
    assert "query" in result["error"]


def test_use_capability_rejects_args_under_wrong_key() -> None:
    # P1: use_capability(input={...}) must return ok=False instead of
    # silently calling the inner skill with empty args.
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo_plugin.list_items",
            summary="List demo items.",
            description="List demo items.",
            affinity=["demo"],
            trusted_source="plugin://demo-plugin/list_items",
            handler=lambda **kw: {"ok": True},
        )
    )
    register_agent_meta_skills(registry)

    result = registry.get("use_capability").handler(
        capability_id="demo-plugin",
        action="list_items",
        input={"kind": "task"},
    )
    assert result["ok"] is False
    assert "args" in result["error"]


# ══════════════════════════════════════════════════════════════════
# P2 regression — silent no-op observation detector.
# ══════════════════════════════════════════════════════════════════


def test_observation_is_noop_detects_empty_count() -> None:
    from runtime.core.cerebrum.react_action_outcomes import _observation_is_noop

    assert _observation_is_noop('{"ok": true, "count": 0, "todos": []}')
    assert _observation_is_noop('{"ok": true, "count": 0, "results": []}')


def test_observation_is_noop_ignores_non_empty_results() -> None:
    from runtime.core.cerebrum.react_action_outcomes import _observation_is_noop

    assert not _observation_is_noop('{"ok": true, "count": 2, "todos": [{"x": 1}]}')
    assert not _observation_is_noop("file contents here")
    assert not _observation_is_noop("")

