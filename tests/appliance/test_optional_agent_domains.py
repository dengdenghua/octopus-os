"""Public-source guarantees for optional Agent integration domains."""

from __future__ import annotations

from appliance.pm_skills import register_pm_skills


def test_unconfigured_pm_extension_does_not_load_agent_skill_runtime(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_PM_URL", raising=False)

    assert register_pm_skills(object()) == 0
