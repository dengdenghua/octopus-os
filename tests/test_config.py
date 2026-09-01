"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.platform.config import (
    AgentConfig,
    BudgetConfig,
    ConfigLoadError,
    ImmunityConfig,
    LearnConfig,
    PlannerConfig,
    build_from_config,
    load_from_dict,
    load_from_yaml,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSchemaDefaults:
    def test_empty_config_valid(self):
        cfg = AgentConfig()
        assert cfg.name == "echo-agent"
        assert cfg.planner.type == "static"
        assert cfg.budget.max_tokens == 100_000
        assert cfg.immunity.unknown_policy == "quarantine"
        assert cfg.local_auth.allow_any_username is False
        assert cfg.intel_sources == []
        assert cfg.mcp_servers == []

    def test_immunity_default_excludes_wildcard_mcp(self):
        # SECURITY: the yaml-driven default must match the TrustEngine
        # in-process default — neither trusts mcp://* out of the box.
        from runtime.safety.auth.trust_engine import TrustEngine

        cfg = AgentConfig()
        assert "mcp://*" not in cfg.immunity.trusted_sources
        assert cfg.immunity.trusted_sources == TrustEngine().trusted_sources

    def test_planner_invalid_type_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PlannerConfig(type="weird")  # type: ignore[arg-type]

    def test_budget_must_be_positive(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BudgetConfig(max_tokens=-1)

    def test_learn_paths_must_be_local_filesystem_paths(self):
        from pydantic import ValidationError

        cfg = LearnConfig(rules_persist_path=" data/learned_rules.json ")
        assert cfg.rules_persist_path == "data/learned_rules.json"

        with pytest.raises(ValidationError):
            LearnConfig(memories_persist_path="")

        with pytest.raises(ValidationError):
            LearnConfig(static_rules_persist_path="https://example.test/rules.json")

        with pytest.raises(ValidationError):
            LearnConfig(learn_from_journal="data/journal.jsonl\x00")


# ═══════════════════════════════════════════════════════════
# load_from_dict
# ═══════════════════════════════════════════════════════════


class TestLoadFromDict:
    def test_partial_config_fills_defaults(self):
        cfg = load_from_dict({"name": "test-agent"})
        assert cfg.name == "test-agent"
        assert cfg.planner.type == "static"  # default

    def test_full_config(self):
        cfg = load_from_dict(
            {
                "name": "x",
                "planner": {"type": "llm", "model": "mock/test"},
                "budget": {"max_tokens": 1000, "max_usd": 0.05},
                "intel_sources": [{"source_id": "s1", "query": "q1"}],
            }
        )
        assert cfg.planner.type == "llm"
        assert cfg.budget.max_tokens == 1000
        assert len(cfg.intel_sources) == 1
        assert cfg.intel_sources[0].source_id == "s1"

    def test_invalid_schema_raises(self):
        with pytest.raises(ConfigLoadError):
            load_from_dict({"planner": {"type": "invalid_type"}})


class TestEnvInterpolation:
    def test_env_var_substituted(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-secret")
        cfg = load_from_dict(
            {
                "planner": {
                    "type": "llm",
                    "model": "claude-haiku-4-5",
                    "anthropic_api_key": "${MY_KEY}",
                }
            }
        )
        assert cfg.planner.anthropic_api_key == "sk-secret"

    def test_missing_env_becomes_empty(self, monkeypatch):
        monkeypatch.delenv("NOT_SET_12345", raising=False)
        cfg = load_from_dict({"planner": {"type": "llm", "mock_response": "${NOT_SET_12345}"}})
        assert cfg.planner.mock_response == ""

    def test_bare_env_var_substituted_only_as_complete_scalar(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-secret")
        cfg = load_from_dict({"planner": {"type": "llm", "anthropic_api_key": "$MY_KEY"}})
        assert cfg.planner.anthropic_api_key == "sk-secret"

    def test_bcrypt_hash_is_not_corrupted_by_env_interpolation(self, monkeypatch):
        monkeypatch.delenv("KWFHX0", raising=False)
        password_hash = "bcrypt:$2b$04$KWFHX0cmIsgqSTQ23AnuouwO21q.Yz8ZP017wkIhGLfDU6Yg4ruoW"
        cfg = load_from_dict(
            {
                "local_auth": {
                    "enabled": True,
                    "users": {"release-smoke": password_hash},
                }
            }
        )
        assert cfg.local_auth.users["release-smoke"] == password_hash

    def test_braced_env_var_can_still_be_embedded(self, monkeypatch):
        monkeypatch.setenv("REGION", "cn-east")
        cfg = load_from_dict({"planner": {"type": "llm", "mock_response": "region=${REGION}"}})
        assert cfg.planner.mock_response == "region=cn-east"

    def test_nested_list_interpolation(self, monkeypatch):
        monkeypatch.setenv("TRUST", "mcp://fs/*")
        cfg = load_from_dict({"immunity": {"trusted_sources": ["skill://public/*", "${TRUST}"]}})
        assert cfg.immunity.trusted_sources[1] == "mcp://fs/*"


# ═══════════════════════════════════════════════════════════
# load_from_yaml
# ═══════════════════════════════════════════════════════════


class TestLoadFromYaml:
    def test_valid_yaml_file(self, tmp_path: Path):
        path = tmp_path / "cfg.yaml"
        path.write_text(
            "name: test-yaml\nplanner:\n  type: llm\n  model: mock/test\n",
            encoding="utf-8",
        )
        cfg = load_from_yaml(path)
        assert cfg.name == "test-yaml"
        assert cfg.planner.model == "mock/test"

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(ConfigLoadError, match="not found"):
            load_from_yaml(tmp_path / "nope.yaml")

    def test_malformed_yaml(self, tmp_path: Path):
        path = tmp_path / "bad.yaml"
        path.write_text("planner:\n  type: [unclosed list", encoding="utf-8")
        with pytest.raises(ConfigLoadError, match="YAML parse failed"):
            load_from_yaml(path)

    def test_non_mapping_yaml_rejected(self, tmp_path: Path):
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ConfigLoadError, match="mapping"):
            load_from_yaml(path)

    def test_empty_yaml_uses_defaults(self, tmp_path: Path):
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        cfg = load_from_yaml(path)
        assert cfg.name == "echo-agent"


# ═══════════════════════════════════════════════════════════
# build_from_config
# ═══════════════════════════════════════════════════════════


class TestBuildFromConfig:
    def test_default_stack(self):
        stack = build_from_config(AgentConfig())
        # 5 builtins + web (httpx available) + maybe mcp
        assert len(stack.registry) >= 5
        assert not stack.is_llm_planner  # default static

    def test_llm_planner_stack(self):
        cfg = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/test", mock_response='{"nodes":[]}')
        )
        stack = build_from_config(cfg)
        assert stack.is_llm_planner

    def test_unknown_model_llm_stack_without_anthropic_key(
        self, monkeypatch, tmp_path: Path
    ):
        """An unrecognised planner model still builds and keeps a live router.

        The model name is deliberately not an Anthropic preset, so this covers
        the fallback path taken when no Anthropic credentials are present.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
        cfg = load_from_dict(
            {"planner": {"type": "llm", "model": "vendor-hosted-model"}}
        )

        stack = build_from_config(cfg)

        assert stack.is_llm_planner
        assert stack.planner.planner_model == "vendor-hosted-model"
        assert stack.planner.router.has("chatgpt")

    def test_static_stack_routes_research_to_web_search(self):
        cfg = AgentConfig(
            planner=PlannerConfig(type="static"),
            enable_web_skills=True,
        )
        stack = build_from_config(cfg)
        goal = "nas\u8c03\u7814"

        from runtime.platform.models import ParsedIntent

        graph = stack.planner.plan(ParsedIntent(raw=goal, intent_type="task", normalized_goal=goal))

        assert graph.strategy == "research_web_search"
        assert graph.nodes[0].skill_ref == "web_search"
        assert graph.nodes[0].args_template["query"] == goal

    def test_journal_file_uses_jsonl(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        cfg = AgentConfig(journal_file=str(path))
        stack = build_from_config(cfg)
        # Implementation note.
        from uuid import uuid4

        from runtime.platform.models import (
            ArmId,
            TaskId,
            Trajectory,
            TrajectoryOutcome,
        )

        traj = Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("x"),
            steps=[],
            outcome=TrajectoryOutcome(success=True),
        )
        stack.journal.write_trajectory(traj)
        assert path.exists()
        assert path.read_text(encoding="utf-8").strip() != ""

    def test_immunity_custom_trusted_sources(self):
        cfg = AgentConfig(
            immunity=ImmunityConfig(
                trusted_sources=["custom://source/*"],
                unknown_policy="reject",
            )
        )
        stack = build_from_config(cfg)
        assert "custom://source/*" in stack.immunity.trusted_sources
        assert stack.immunity.unknown_policy == "reject"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestExampleYamlFile:
    def test_example_config_loads(self):
        """Implementation note."""
        example = Path(__file__).parent.parent / "config.example.yaml"
        if not example.exists():
            pytest.skip("config.example.yaml not shipped")
        cfg = load_from_yaml(example)
        assert cfg.planner.type in ("static", "llm")
        # Implementation note.

    def test_example_config_builds_with_mock(self, tmp_path: Path):
        """Implementation note."""
        example = Path(__file__).parent.parent / "config.example.yaml"
        if not example.exists():
            pytest.skip("config.example.yaml not shipped")
        text = example.read_text(encoding="utf-8").replace("claude-haiku-4-5-20251001", "mock/test")
        patched = tmp_path / "patched.yaml"
        patched.write_text(text, encoding="utf-8")
        cfg = load_from_yaml(patched)
        stack = build_from_config(cfg)
        assert stack.registry is not None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestCLIConfigFlag:
    def test_run_with_config_flag(self, tmp_path: Path, capsys):
        from runtime.cli import main

        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(
            "planner:\n"
            "  type: llm\n"
            "  model: mock/planner\n"
            '  mock_response: \'{"reasoning":"t","nodes":[{"skill":"list_cwd","args":{}}]}\'\n'
            "budget:\n"
            "  max_tokens: 5000\n"
            "  max_usd: 0.05\n",
            encoding="utf-8",
        )
        rc = main(
            [
                "--no-color",
                "run",
                "list files",
                "--config",
                str(cfg_path),
            ]
        )
        assert rc in (0, 1)  # Implementation note.
        out = capsys.readouterr().out
        assert "config-driven" in out
