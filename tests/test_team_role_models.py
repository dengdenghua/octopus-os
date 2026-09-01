"""Per-role model-tier overrides for team mode (configurable cheap/primary)."""

from __future__ import annotations

from pathlib import Path

from runtime.safety.organization import team_role_models as trm
from runtime.safety.organization.topology import Role

_NO_CONFIG = Path("/nonexistent/team_role_models.json")


def test_default_tiering(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_TEAM_ROLE_MODELS", raising=False)
    monkeypatch.setattr(trm, "_CONFIG", _NO_CONFIG)
    # planner/generator/synthesizer = primary (not cheap); the rest = cheap
    assert trm.role_uses_cheap(Role.PLANNER) is False
    assert trm.role_uses_cheap(Role.GENERATOR) is False
    assert trm.role_uses_cheap(Role.SYNTHESIZER) is False
    assert trm.role_uses_cheap(Role.RESEARCHER) is True
    assert trm.role_uses_cheap(Role.CRITIC) is True


def test_override_forces_tier() -> None:
    assert trm.role_uses_cheap(Role.PLANNER, overrides={"planner": "cheap"}) is True
    assert trm.role_uses_cheap(Role.RESEARCHER, overrides={"researcher": "primary"}) is False
    assert trm.role_uses_cheap(Role.RESEARCHER, overrides={"researcher": "default"}) is True
    assert trm.role_uses_cheap(Role.PLANNER, overrides={}) is False  # no override → default


def test_env_overrides_win_and_validate(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_TEAM_ROLE_MODELS", '{"planner": "cheap", "bogus": "x"}')
    assert trm.role_uses_cheap(Role.PLANNER) is True
    assert trm.load_overrides() == {"planner": "cheap"}  # invalid tier dropped


def test_malformed_config_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_TEAM_ROLE_MODELS", "not json at all")
    monkeypatch.setattr(trm, "_CONFIG", _NO_CONFIG)
    assert trm.load_overrides() == {}


def test_save_and_load_round_trip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(trm, "_CONFIG", tmp_path / "team_role_models.json")
    monkeypatch.delenv("ECHO_TEAM_ROLE_MODELS", raising=False)
    saved = trm.save_overrides({"researcher": "primary", "junk": "nope", "planner": "cheap"})
    assert saved == {"researcher": "primary", "planner": "cheap"}  # junk dropped
    assert trm.load_overrides() == {"researcher": "primary", "planner": "cheap"}


def test_role_defaults_lists_known_roles() -> None:
    d = trm.role_defaults()
    assert d["planner"] == "primary" and d["generator"] == "primary"
    assert d["synthesizer"] == "primary"
    assert d["researcher"] == "cheap" and d["critic"] == "cheap"


def test_team_runner_delegates_and_overrides_flow(monkeypatch) -> None:
    from runtime.safety.organization import team_runner as tr

    monkeypatch.delenv("ECHO_TEAM_ROLE_MODELS", raising=False)
    monkeypatch.setattr(trm, "_CONFIG", _NO_CONFIG)
    assert tr._role_uses_cheap_model(Role.PLANNER) is False  # default preserved
    assert tr._role_uses_cheap_model(Role.RESEARCHER) is True
    monkeypatch.setenv("ECHO_TEAM_ROLE_MODELS", '{"planner": "cheap"}')
    assert tr._role_uses_cheap_model(Role.PLANNER) is True  # override flows through


def test_router_exposes_routes_and_get_shape(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_TEAM_ROLE_MODELS", raising=False)
    monkeypatch.setattr(trm, "_CONFIG", _NO_CONFIG)
    from runtime.sensing.gateway.team_role_models_router import create_team_role_models_router

    router = create_team_role_models_router()
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/team/role-models" in paths

