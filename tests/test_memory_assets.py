from __future__ import annotations

from runtime.memory.assets import can_read_asset, fact_to_asset


def _asset(**overrides):
    fact = {
        "id": "asset-1",
        "content": "Team release checklist",
        "owner": "alice",
        "visibility": "private",
        "team_id": "core",
        **overrides,
    }
    return fact_to_asset(fact)


def test_private_asset_is_owner_only() -> None:
    asset = _asset()

    assert can_read_asset(asset, actor="alice") is True
    assert can_read_asset(asset, actor="bob", team_id="core") is False


def test_team_and_restricted_asset_permissions() -> None:
    team_asset = _asset(visibility="team")
    restricted = _asset(
        visibility="restricted",
        allowed_users=["bob"],
        allowed_roles=["reviewer"],
        allowed_agents=["release-agent"],
    )

    assert can_read_asset(team_asset, actor="bob", team_id="core") is True
    assert can_read_asset(team_asset, actor="bob", team_id="other") is False
    assert can_read_asset(restricted, actor="bob") is True
    assert can_read_asset(restricted, actor="carol", roles=["reviewer"]) is True
    assert can_read_asset(restricted, actor="carol", agent_id="release-agent") is True
    assert can_read_asset(restricted, actor="carol") is False


def test_agent_visibility_only_equips_selected_agents() -> None:
    asset = _asset(
        visibility="agent",
        agent_id="builder",
        allowed_agents=["reviewer"],
    )

    assert can_read_asset(asset, actor="bob", agent_id="builder") is True
    assert can_read_asset(asset, actor="bob", agent_id="reviewer") is True
    assert can_read_asset(asset, actor="bob", agent_id="researcher") is False

