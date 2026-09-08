"""Regression tests for the side-effect-free Codex readiness probe."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.codex_backend.model_profile import (
    CodexModelPreference,
    resolve_codex_execution_profile,
)
from runtime.execution.codex_backend.readiness import inspect_codex_readiness

_COMMAND = ("codex", "app-server", "--strict-config", "--listen", "stdio://")


def _account_profile():
    return resolve_codex_execution_profile(
        preference=CodexModelPreference(mode="chatgpt"),
        system_model="gpt-5.6-codex",
    )


def _incompatible_profile():
    return resolve_codex_execution_profile(
        preference=CodexModelPreference(),
        system_model="deepseek",
        custom_models={
            "deepseek": {
                "id": "deepseek",
                "provider": "openai",
                "base_url": "https://example.test/v1",
                "models": ["deepseek-chat"],
            }
        },
        proxy_available=False,
    )


def _enable_probe(monkeypatch) -> None:
    import runtime.execution.codex_backend.readiness as readiness

    monkeypatch.setattr(readiness, "resolution", lambda _name: (True, "default"))
    monkeypatch.setattr(readiness, "resolve_codex_app_server_command", lambda: _COMMAND)


def test_account_mode_requires_existing_auth_home(monkeypatch) -> None:
    _enable_probe(monkeypatch)

    result = inspect_codex_readiness(
        _account_profile(),
        auth_source=lambda: None,
    )

    assert result.available is False
    assert result.reason == "account_required"


def test_account_mode_is_ready_when_auth_and_tools_exist(monkeypatch, tmp_path: Path) -> None:
    _enable_probe(monkeypatch)
    auth_home = tmp_path / "codex-home"

    result = inspect_codex_readiness(
        _account_profile(),
        auth_source=lambda: auth_home,
    )

    assert result.available is True
    assert result.reason is None


def test_probe_reports_independent_prerequisite_failures(monkeypatch) -> None:
    _enable_probe(monkeypatch)

    import runtime.execution.codex_backend.readiness as readiness

    # The real resolver raises ConfigurationError; use the public class so the
    # test also proves the probe does not leak implementation details.
    from runtime.execution.codex_backend.types import ConfigurationError

    monkeypatch.setattr(
        readiness,
        "resolve_codex_app_server_command",
        lambda: (_ for _ in ()).throw(ConfigurationError("missing")),
    )
    assert inspect_codex_readiness(_account_profile(), auth_source=lambda: None).reason == (
        "executable_unavailable"
    )

    _enable_probe(monkeypatch)
    assert inspect_codex_readiness(
        _account_profile(), auth_source=lambda: None, tools_available=False
    ).reason == "tools_unavailable"

    assert inspect_codex_readiness(
        _incompatible_profile(), auth_source=lambda: None
    ).reason == "model_incompatible"


def test_proxy_profile_does_not_require_codex_account(monkeypatch) -> None:
    _enable_probe(monkeypatch)

    profile = resolve_codex_execution_profile(
        preference=CodexModelPreference(),
        system_model="proxy",
        custom_models={
            "proxy": {
                "id": "proxy",
                "provider": "openai",
                "base_url": "https://echo.example/v1",
                "models": ["gpt-safe"],
            }
        },
        proxy_available=True,
    )

    result = inspect_codex_readiness(profile, auth_source=lambda: None)

    assert result.available is True
    assert result.reason is None


def test_explicit_feature_disable_wins_before_other_checks(monkeypatch) -> None:
    import runtime.execution.codex_backend.readiness as readiness

    monkeypatch.setattr(readiness, "resolution", lambda _name: (False, "env"))
    monkeypatch.setattr(
        readiness,
        "resolve_codex_app_server_command",
        lambda: (_ for _ in ()).throw(AssertionError("must not resolve command")),
    )

    result = inspect_codex_readiness(_account_profile(), auth_source=lambda: None)

    assert result.available is False
    assert result.reason == "disabled"
