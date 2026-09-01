from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.codex_backend.account import (
    CodexAccountLeaseError,
    CodexAccountService,
    codex_account_home,
    refresh_codex_execution_auth_home,
    resolve_codex_execution_auth_home,
)
from runtime.execution.codex_backend.command import resolve_codex_app_server_command
from runtime.execution.codex_backend.model_profile import (
    CodexModelPreference,
    CodexModelPreferenceStore,
    codex_proxy_route_available,
    resolve_codex_execution_profile,
)
from runtime.execution.codex_backend.paths import resolve_codex_state_root
from runtime.execution.codex_backend.security import CodexSecurityPolicy, CodexSidecarSecurity
from runtime.execution.codex_backend.types import (
    CodexAppServerConfig,
    ConfigurationError,
    Notification,
    RequestTimeoutError,
)
from runtime.execution.codex_backend.upstream_update import CodexUpstreamUpdateService
from runtime.safety.auth.scope import TenantScope
from runtime.sensing.gateway._config_models import CodexLoginBody
from runtime.sensing.gateway.config_router import create_config_router

_FAKE_COMMAND = ("fake-codex", "app-server", "--strict-config", "--listen", "stdio://")


class _FakeControlClient:
    def __init__(self, config: CodexAppServerConfig, *, serial: int) -> None:
        self.config = config
        self.serial = serial
        self.account: dict[str, object] | None = None
        self.notifications: list[Notification] = []
        self.cancelled: list[str] = []
        self.closed = False
        self.model_gate: tuple[Any, Any] | None = None
        self.model_calls = 0
        self.refresh_reads = 0
        self.logout_calls = 0

    @property
    def home(self) -> Path:
        return Path(self.config.env_overrides["CODEX_HOME"])

    async def start(self) -> dict[str, object]:
        return {"userAgent": "fake-control"}

    async def account_read(self, *, refresh_token: bool = False) -> dict[str, object]:
        if refresh_token and self.account is not None:
            self.refresh_reads += 1
            auth = self.home / "auth.json"
            auth.write_text(
                json.dumps({"refreshed_generation": self.refresh_reads}),
                encoding="utf-8",
            )
            auth.chmod(0o600)
        return {"account": self.account, "requiresOpenaiAuth": self.account is None}

    async def login_api_key(self, api_key: str) -> dict[str, object]:
        self.account = {"type": "apiKey"}
        auth = self.home / "auth.json"
        auth.write_text(json.dumps({"OPENAI_API_KEY": api_key}), encoding="utf-8")
        auth.chmod(0o600)
        return {"type": "apiKey"}

    async def login_chatgpt(self, *, device_code: bool = False) -> dict[str, object]:
        login_id = f"login-{self.serial}"
        if device_code:
            return {
                "type": "chatgptDeviceCode",
                "loginId": login_id,
                "verificationUrl": "https://example.test/device",
                "userCode": "ABCD-EFGH",
            }
        return {
            "type": "chatgpt",
            "loginId": login_id,
            "authUrl": "https://example.test/login",
        }

    async def cancel_login(self, login_id: str) -> dict[str, object]:
        self.cancelled.append(login_id)
        return {}

    async def logout_account(self) -> dict[str, object]:
        self.logout_calls += 1
        self.account = None
        (self.home / "auth.json").unlink(missing_ok=True)
        return {}

    async def list_models(self, **_kwargs: object) -> dict[str, object]:
        self.model_calls += 1
        if self.model_gate is not None:
            entered, release = self.model_gate
            entered.set()
            await release.wait()
        return {
            "data": [
                {
                    "id": "gpt-5.6-codex",
                    "displayName": "GPT-5.6 Codex",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "high"},
                        {"reasoningEffort": "xhigh"},
                    ],
                    "defaultReasoningEffort": "high",
                    "hidden": False,
                    "isDefault": True,
                    "inputModalities": ["text", "image"],
                }
            ],
            "nextCursor": None,
        }

    async def read_account_rate_limits(self) -> dict[str, object]:
        return {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "limitName": "Codex",
                    "primary": {
                        "usedPercent": 25,
                        "windowDurationMins": 15,
                        "resetsAt": 1_730_947_200,
                    },
                    "secondary": None,
                    "planType": "plus",
                    "rateLimitReachedType": None,
                }
            },
            "rateLimitResetCredits": {"availableCount": 2},
        }

    async def read_account_usage(self) -> dict[str, object]:
        return {
            "summary": {
                "lifetimeTokens": 1_234_567,
                "peakDailyTokens": 45_678,
                "longestRunningTurnSec": 540,
                "currentStreakDays": 8,
                "longestStreakDays": 14,
            },
            "dailyUsageBuckets": [{"startDate": "2026-06-18", "tokens": 12_345}],
        }

    async def list_apps(self, **_kwargs: object) -> dict[str, object]:
        return {
            "data": [
                {
                    "id": "google_drive",
                    "name": "Google Drive",
                    "description": "Search and read Drive files.",
                    "logoUrl": "https://example.test/drive.png",
                    "installUrl": "https://chatgpt.com/apps/google-drive",
                    "isAccessible": True,
                    "isEnabled": False,
                },
                {
                    "id": "blocked_app",
                    "name": "Blocked",
                    "isAccessible": False,
                    "isEnabled": False,
                },
            ],
            "nextCursor": None,
        }

    async def next_notification(self, *, timeout_s: float | None = None) -> Notification:
        if self.notifications:
            return self.notifications.pop(0)
        raise RequestTimeoutError("no notification")

    async def close(self) -> None:
        self.closed = True


class _ControlFactory:
    def __init__(self) -> None:
        self.clients: list[_FakeControlClient] = []

    def __call__(self, config: CodexAppServerConfig) -> Any:
        client = _FakeControlClient(config, serial=len(self.clients) + 1)
        self.clients.append(client)
        return client


def _private_auth(home: Path, marker: str) -> None:
    home.mkdir(parents=True)
    path = home / "auth.json"
    path.write_text(json.dumps({"marker": marker}), encoding="utf-8")
    path.chmod(0o600)


def test_model_modes_have_disjoint_fallback_domains() -> None:
    custom = {
        "deepseek": {
            "id": "deepseek",
            "provider": "openai",
            "base_url": "https://deepseek.example/v1",
            "api_key": "host-secret",
            "models": ["deepseek-chat"],
        }
    }
    follow = resolve_codex_execution_profile(
        preference=CodexModelPreference(
            mode="follow_system",
            model="deepseek",
            reasoning_effort="xhigh",
        ),
        system_model="other-system-model",
        custom_models=custom,
    )
    assert follow.effective_model == "deepseek-chat"
    assert follow.model_source == "role"
    assert follow.reasoning_effort == "xhigh"
    assert follow.compatible is False
    assert follow.provider_profile is None

    account = resolve_codex_execution_profile(
        preference=CodexModelPreference(mode="chatgpt"),
        system_model="deepseek",
        system_effort="max",
        custom_models=custom,
    )
    assert account.effective_model is None
    assert account.reasoning_effort is None
    assert account.model_source == "codex_default"
    assert account.provider == "codex_account"


def test_follow_system_provider_is_always_isolated_behind_host_proxy() -> None:
    profile = resolve_codex_execution_profile(
        preference=CodexModelPreference(),
        system_model="proxy",
        custom_models={
            "proxy": {
                "id": "proxy",
                "provider": "openai",
                "base_url": "https://echo.internal/v1",
                "api_key": "",
                "default_headers": {},
                "codex_wire_api": "responses",
                "models": ["gpt-safe"],
            }
        },
        proxy_available=True,
    )
    assert profile.compatible is True
    assert profile.proxy_required is True
    assert profile.provider == "echo_responses_proxy"
    assert profile.provider_profile is None
    assert profile.effective_model == "gpt-safe"

    secret_profile = resolve_codex_execution_profile(
        preference=CodexModelPreference(),
        system_model="deepseek",
        custom_models={
            "deepseek": {
                "id": "deepseek",
                "provider": "openai",
                "base_url": "https://deepseek.example/v1",
                "api_key": "host-secret-never-forwarded",
                "default_headers": {"X-Upstream-Auth": "also-host-only"},
                "models": ["deepseek-chat"],
            }
        },
        proxy_available=True,
    )
    assert secret_profile.compatible is True
    assert secret_profile.effective_model == "deepseek-chat"
    assert secret_profile.provider_profile is None


def test_proxy_profile_fails_closed_when_dispatcher_has_no_exact_model_route() -> None:
    class _Dispatcher:
        default_model = "configured-default"

        def call(self, _request: object) -> None:
            return None

        def has(self, model: str) -> bool:
            return model == "registered-model"

    dispatcher = _Dispatcher()
    unavailable = resolve_codex_execution_profile(
        system_model="unregistered-model",
        proxy_available=True,
        proxy_route_available=lambda model: codex_proxy_route_available(dispatcher, model),
    )
    assert unavailable.compatible is False
    assert unavailable.compatibility_reason == (
        "Selected system model has no exact Echo ModelRouter route"
    )

    registered = resolve_codex_execution_profile(
        system_model="registered-model",
        proxy_available=True,
        proxy_route_available=lambda model: codex_proxy_route_available(dispatcher, model),
    )
    assert registered.compatible is True


def test_preference_store_and_execution_auth_are_principal_scoped(tmp_path: Path) -> None:
    state_root = tmp_path / "codex-state"
    preferences = CodexModelPreferenceStore(state_root / "model_profile.json")
    alice = TenantScope("tenant", "alice")
    bob = TenantScope("tenant", "bob")
    preferences.write(alice, CodexModelPreference(mode="chatgpt", model="gpt-alice"))
    assert preferences.read(alice).model == "gpt-alice"
    assert preferences.read(bob) == CodexModelPreference()

    service = CodexAccountService(state_root, command=_FAKE_COMMAND)
    _private_auth(service.account_home(alice), "alice")
    _private_auth(service.account_home(bob), "bob")
    assert resolve_codex_execution_auth_home(
        state_root=state_root,
        scope=alice,
        deployment_mode="shared",
        legacy_source_home=tmp_path / "legacy",
    ) == service.account_home(alice)
    assert resolve_codex_execution_auth_home(
        state_root=state_root,
        scope=bob,
        deployment_mode="shared",
        legacy_source_home=tmp_path / "legacy",
    ) == service.account_home(bob)

    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "project"
    workspace.mkdir(parents=True)
    security = CodexSidecarSecurity(
        CodexSecurityPolicy(
            state_root=state_root,
            allowed_workspace_roots=(workspace_root,),
        )
    )
    execution = security.prepare(
        realm_id="realm",
        tenant_id="tenant:alice",
        thread_id="thread",
        task_id="turn",
        workspace=workspace,
    )
    source = resolve_codex_execution_auth_home(
        state_root=state_root,
        scope=alice,
        deployment_mode="shared",
    )
    assert source is not None
    assert security.seed_auth_from_codex_home(
        execution,
        source_codex_home=source,
        authority="server",
    )
    seeded = json.loads((execution.codex_home / "auth.json").read_text(encoding="utf-8"))
    assert seeded == {"marker": "alice"}


def test_local_execution_can_inherit_host_login_before_principal_is_seeded(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "codex-state"
    legacy_home = tmp_path / "legacy-codex"
    scope = TenantScope("legacy:oct:alice@example.com", "oct:alice@example.com")
    _private_auth(legacy_home, "host-chatgpt")

    assert not (codex_account_home(state_root, scope) / "auth.json").exists()
    assert (
        resolve_codex_execution_auth_home(
            state_root=state_root,
            scope=scope,
            deployment_mode="local",
            legacy_source_home=legacy_home,
            allow_local_principal_inheritance=True,
        )
        == legacy_home
    )


def test_shared_execution_never_inherits_host_login_even_when_requested(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "codex-state"
    legacy_home = tmp_path / "legacy-codex"
    scope = TenantScope("tenant", "alice")
    _private_auth(legacy_home, "host-chatgpt")

    assert (
        resolve_codex_execution_auth_home(
            state_root=state_root,
            scope=scope,
            deployment_mode="shared",
            legacy_source_home=legacy_home,
            allow_local_principal_inheritance=True,
        )
        is None
    )


def test_two_preference_store_instances_serialize_cross_thread_writes(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    scope = TenantScope("tenant", "alice")
    stores = (CodexModelPreferenceStore(path), CodexModelPreferenceStore(path))

    def _writer(index: int) -> None:
        store = stores[index % 2]
        for offset in range(25):
            model = f"gpt-writer-{index}-{offset}"
            assert (
                store.write(
                    scope,
                    CodexModelPreference(mode="chatgpt", model=model),
                ).model
                == model
            )
            assert store.read(scope).mode == "chatgpt"

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_writer, (0, 1)))

    final = stores[0].read(scope)
    assert final.mode == "chatgpt"
    assert final.model is not None and final.model.startswith("gpt-writer-")


def test_preference_store_sanitizes_legacy_mix_and_rejects_new_mix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "mode": "follow_system",
                "model": "echo-mix",
                "reasoning_effort": "high",
                "app_ids": ["google_drive"],
            }
        ),
        encoding="utf-8",
    )
    store = CodexModelPreferenceStore(path)

    migrated = store.read(None)
    assert migrated.model is None
    assert migrated.reasoning_effort == "high"
    assert migrated.app_ids == ("google_drive",)
    with pytest.raises(ConfigurationError, match="not an executable Coder"):
        store.write(
            None,
            CodexModelPreference(mode="follow_system", model="mix"),
        )


@pytest.mark.asyncio
async def test_model_catalog_is_reused_for_profile_validation(tmp_path: Path) -> None:
    factory = _ControlFactory()
    service = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=factory,
    )

    assert service.cached_models(None, include_hidden=True) is None
    first = await service.list_models(None, include_hidden=True)
    second = await service.list_models(None, include_hidden=True)

    assert first == second
    assert first is not second
    assert service.cached_models(None, include_hidden=True) == first
    assert factory.clients[0].model_calls == 1
    await service.close_all()


@pytest.mark.asyncio
async def test_login_survives_poll_cancel_and_two_principals_are_isolated(tmp_path: Path) -> None:
    factory = _ControlFactory()
    service = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=factory,
    )
    alice = TenantScope("tenant", "alice")
    bob = TenantScope("tenant", "bob")

    alice_login = await service.login(alice, login_type="chatgpt")
    bob_login = await service.login(bob, login_type="chatgptDeviceCode")
    assert alice_login["login_id"] != bob_login["login_id"]
    assert (await service.read_account(alice)).login_pending is True
    assert (await service.read_account(bob)).login_pending is True
    assert len(factory.clients) == 2
    assert factory.clients[0].home != factory.clients[1].home

    stale = await service.cancel_login(alice, login_id=str(bob_login["login_id"]))
    assert stale["cancelled"] is False
    cancelled = await service.cancel_login(alice, login_id=str(alice_login["login_id"]))
    assert cancelled["cancelled"] is True
    assert (await service.read_account(bob)).login_pending is True

    await service.close_all()
    assert all(client.closed for client in factory.clients)


@pytest.mark.asyncio
async def test_local_desktop_can_seed_host_chatgpt_login_for_authenticated_principal(
    tmp_path: Path,
) -> None:
    legacy_home = tmp_path / "legacy-codex"
    _private_auth(legacy_home, "host-chatgpt")
    factory = _ControlFactory()
    service = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=factory,
        legacy_source_home=legacy_home,
        allow_local_principal_inheritance=True,
    )
    scope = TenantScope("local-tenant", "desktop-user")

    await service.read_account(scope)

    seeded = json.loads((service.account_home(scope) / "auth.json").read_text(encoding="utf-8"))
    assert seeded == {"marker": "host-chatgpt"}
    assert (service.account_home(scope) / "auth.json").stat().st_mode & 0o077 == 0
    await service.close_all()


@pytest.mark.asyncio
async def test_shared_principal_never_inherits_host_chatgpt_login(tmp_path: Path) -> None:
    legacy_home = tmp_path / "legacy-codex"
    _private_auth(legacy_home, "host-chatgpt")
    service = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=_ControlFactory(),
        legacy_source_home=legacy_home,
    )
    scope = TenantScope("shared-tenant", "alice")

    await service.read_account(scope)

    assert not (service.account_home(scope) / "auth.json").exists()
    await service.close_all()


@pytest.mark.asyncio
async def test_two_service_instances_cannot_own_one_principal_control_home(
    tmp_path: Path,
) -> None:
    first_factory = _ControlFactory()
    second_factory = _ControlFactory()
    first = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=first_factory,
    )
    second = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=second_factory,
    )
    scope = TenantScope("tenant", "alice")
    await first.read_account(scope)

    with pytest.raises(CodexAccountLeaseError):
        await second.read_account(scope)

    await first.close_all()
    assert (await second.read_account(scope)).account is None
    await second.close_all()


@pytest.mark.asyncio
async def test_reaper_never_closes_an_inflight_model_request(tmp_path: Path) -> None:
    factory = _ControlFactory()
    service = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=factory,
        idle_timeout_s=0.001,
    )
    await service.read_account(None)
    entered = asyncio.Event()
    release = asyncio.Event()
    factory.clients[0].model_gate = (entered, release)
    task = asyncio.create_task(service.list_models(None))
    await entered.wait()
    await asyncio.sleep(0.005)

    assert await service.reap_idle() == 0
    assert factory.clients[0].closed is False
    release.set()
    await task
    await service.close_all()


@pytest.mark.asyncio
async def test_execution_refreshes_master_auth_before_copying_it(tmp_path: Path) -> None:
    factory = _ControlFactory()
    state_root = tmp_path / "state"
    service = CodexAccountService(
        state_root,
        command=_FAKE_COMMAND,
        client_factory=factory,
    )
    scope = TenantScope("tenant", "alice")
    await service.login(scope, login_type="apiKey", api_key="initial-key")

    refreshed_home = await refresh_codex_execution_auth_home(
        state_root=state_root,
        scope=scope,
    )

    assert refreshed_home == service.account_home(scope)
    assert factory.clients[0].refresh_reads == 1
    master = json.loads((refreshed_home / "auth.json").read_text(encoding="utf-8"))
    assert master == {"refreshed_generation": 1}

    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "project"
    workspace.mkdir(parents=True)
    security = CodexSidecarSecurity(
        CodexSecurityPolicy(state_root=state_root, allowed_workspace_roots=(workspace_root,))
    )
    execution = security.prepare(
        realm_id="realm",
        tenant_id="tenant:alice",
        thread_id="thread",
        task_id="turn",
        workspace=workspace,
    )
    assert security.seed_auth_from_codex_home(
        execution,
        source_codex_home=refreshed_home,
        authority="server",
    )
    seeded = json.loads((execution.codex_home / "auth.json").read_text(encoding="utf-8"))
    assert seeded == master
    await service.close_all()


def test_http_contract_secret_redaction_profile_and_lifespan(tmp_path: Path) -> None:
    custom_path = tmp_path / "custom_models.json"
    custom_path.write_text(
        json.dumps(
            {
                "deepseek": {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "provider": "openai",
                    "base_url": "https://deepseek.example/v1",
                    "api_key": "host-only-secret",
                    "models": ["deepseek-chat"],
                }
            }
        ),
        encoding="utf-8",
    )
    factory = _ControlFactory()
    accounts = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=factory,
    )
    preferences = CodexModelPreferenceStore(tmp_path / "profile.json")
    updates = CodexUpstreamUpdateService(
        tmp_path / "upstream_update.json",
        current_version="0.149.0",
        fetcher=lambda _url, _timeout: {
            "version": "0.150.0",
            "dist": {
                "integrity": "sha512-reviewed-metadata",
                "tarball": "https://registry.npmjs.org/codex.tgz",
            },
        },
    )
    stack = SimpleNamespace(
        config=SimpleNamespace(planner=SimpleNamespace(model="deepseek")),
        planner=None,
    )
    bundle = create_config_router(
        stack=stack,
        custom_models_path=custom_path,
        codex_account_service=accounts,
        codex_preference_store=preferences,
        codex_update_service=updates,
    )
    app = FastAPI()
    app.include_router(bundle.router)

    with TestClient(app) as client:
        openapi = app.openapi()
        paths = openapi["paths"]
        for path in (
            "/api/coder/codex/account",
            "/api/coder/codex/login",
            "/api/coder/codex/login/{login_id}/cancel",
            "/api/coder/codex/logout",
            "/api/coder/codex/models",
            "/api/coder/codex/rate-limits",
            "/api/coder/codex/usage",
            "/api/coder/codex/apps",
            "/api/coder/codex/model-profile",
            "/api/coder/codex/upstream-update",
            "/api/coder/codex/upstream-update/check",
            "/api/coder/codex/upstream-update/approve",
        ):
            assert path in paths
        schemas = openapi["components"]["schemas"]
        request_schema = paths["/api/coder/codex/login"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        api_key_schema = request_schema["properties"]["api_key"]["anyOf"][0]
        assert api_key_schema == {
            "type": "string",
            "format": "password",
            "writeOnly": True,
        }
        assert "api_key" not in schemas["CodexLoginResponse"]["properties"]

        follow = client.get("/api/coder/codex/model-profile")
        assert follow.status_code == 200
        assert follow.json()["mode"] == "follow_system"
        assert follow.json()["selected_model"] is None
        assert follow.json()["effective_model"] == "deepseek-chat"
        assert follow.json()["compatible"] is False
        assert follow.json()["proxy_required"] is True

        update = client.post("/api/coder/codex/upstream-update/check")
        assert update.status_code == 200
        assert update.json()["latest_version"] == "0.150.0"
        assert update.json()["approval_status"] == "pending"
        approved = client.post(
            "/api/coder/codex/upstream-update/approve",
            json={"version": "0.150.0"},
        )
        assert approved.status_code == 200
        assert approved.json()["approval_status"] == "approved_for_next_release"
        assert approved.json()["current_version"] == "0.149.0"

        selected_system = client.put(
            "/api/coder/codex/model-profile",
            json={
                "mode": "follow_system",
                "model": "deepseek",
                "reasoning_effort": "high",
            },
        )
        assert selected_system.status_code == 200
        assert selected_system.json()["effective_model"] == "deepseek-chat"
        assert selected_system.json()["selected_model"] == "deepseek"
        assert selected_system.json()["model_source"] == "role"
        assert selected_system.json()["reasoning_effort"] == "high"
        assert preferences.read(None).model == "deepseek"

        blocked_mix = client.put(
            "/api/coder/codex/model-profile",
            json={"mode": "follow_system", "model": "echo-mix"},
        )
        assert blocked_mix.status_code == 400
        assert preferences.read(None).model == "deepseek"

        switched = client.put(
            "/api/coder/codex/model-profile",
            json={"mode": "chatgpt"},
        )
        assert switched.status_code == 200
        assert switched.json()["effective_model"] is None
        assert switched.json()["model_source"] == "codex_default"

        models = client.get("/api/coder/codex/models")
        assert models.status_code == 200
        assert models.json()["models"][0]["id"] == "gpt-5.6-codex"

        limits = client.get("/api/coder/codex/rate-limits")
        assert limits.status_code == 200
        assert limits.json()["buckets"][0]["primary"] == {
            "used_percent": 25.0,
            "remaining_percent": 75.0,
            "window_duration_mins": 15,
            "resets_at": 1_730_947_200,
        }
        assert limits.json()["reset_credits_available"] == 2

        usage = client.get("/api/coder/codex/usage")
        assert usage.status_code == 200
        assert usage.json()["summary"]["lifetime_tokens"] == 1_234_567
        assert usage.json()["daily_usage_buckets"] == [
            {"start_date": "2026-06-18", "tokens": 12_345}
        ]

        apps = client.get("/api/coder/codex/apps")
        assert apps.status_code == 200
        assert apps.json()["apps"][0]["selected"] is False
        selected_apps = client.put(
            "/api/coder/codex/apps",
            json={"app_ids": ["google_drive"]},
        )
        assert selected_apps.status_code == 200
        assert selected_apps.json()["apps"][0]["selected"] is True
        assert preferences.read(None).app_ids == ("google_drive",)
        blocked = client.put(
            "/api/coder/codex/apps",
            json={"app_ids": ["blocked_app"]},
        )
        assert blocked.status_code == 400

        secret = "sk-never-echo-this"
        login = client.post(
            "/api/coder/codex/login",
            json={"type": "apiKey", "api_key": secret},
        )
        assert login.status_code == 200
        assert secret not in login.text
        assert secret not in repr(CodexLoginBody(type="apiKey", api_key=secret))
        rejected = client.post(
            "/api/coder/codex/login",
            json={"type": "chatgpt", "api_key": secret},
        )
        assert rejected.status_code == 400
        assert secret not in rejected.text
        missing_type = client.post(
            "/api/coder/codex/login",
            json={"api_key": secret},
        )
        assert missing_type.status_code == 400
        assert secret not in missing_type.text
        logout = client.post("/api/coder/codex/logout")
        assert logout.status_code == 200
        assert preferences.read(None) == CodexModelPreference(mode="follow_system")

    assert factory.clients
    assert all(control.closed for control in factory.clients)
    assert 'cli_auth_credentials_store = "file"' in (
        factory.clients[0].home / "config.toml"
    ).read_text(encoding="utf-8")


def test_logout_resets_preference_before_auth_and_stays_safe_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend.types import CodexAppServerError

    factory = _ControlFactory()
    accounts = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=factory,
    )
    preferences = CodexModelPreferenceStore(tmp_path / "profile.json")
    bundle = create_config_router(
        codex_account_service=accounts,
        codex_preference_store=preferences,
    )
    app = FastAPI()
    app.include_router(bundle.router)

    with TestClient(app) as client:
        assert (
            client.post(
                "/api/coder/codex/login",
                json={"type": "apiKey", "api_key": "sk-test-only"},
            ).status_code
            == 200
        )
        preferences.write(None, CodexModelPreference(mode="chatgpt", model="gpt-account"))

        original_write = preferences.write

        def _write_failure(*_args: object, **_kwargs: object) -> CodexModelPreference:
            raise OSError("profile disk unavailable")

        monkeypatch.setattr(preferences, "write", _write_failure)
        rejected = client.post("/api/coder/codex/logout")
        assert rejected.status_code == 503
        assert factory.clients[0].logout_calls == 0
        assert factory.clients[0].account is not None

        monkeypatch.setattr(preferences, "write", original_write)

        async def _logout_failure() -> dict[str, object]:
            raise CodexAppServerError("fixed fake failure")

        monkeypatch.setattr(factory.clients[0], "logout_account", _logout_failure)
        failed_logout = client.post("/api/coder/codex/logout")
        assert failed_logout.status_code == 503
        assert preferences.read(None) == CodexModelPreference(mode="follow_system")
        assert factory.clients[0].account is not None


def test_shared_command_resolver_finds_packaged_chatgpt_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend import command as command_module

    resources = tmp_path / "ChatGPT.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    bundled = resources / "codex"
    bundled.write_text("#!/bin/sh\n", encoding="utf-8")
    bundled.chmod(0o755)
    monkeypatch.delenv("ECHO_CODEX_EXECUTABLE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setattr(command_module, "login_shell_path", lambda: str(resources))

    command = resolve_codex_app_server_command()

    assert command[0] == str(bundled.resolve())
    assert command[1:] == ("app-server", "--strict-config", "--listen", "stdio://")


@pytest.mark.asyncio
async def test_state_root_env_is_shared_by_config_login_and_execution_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured-codex-state"
    monkeypatch.setenv("ECHO_CODEX_STATE_DIR", str(configured))
    factory = _ControlFactory()
    service = CodexAccountService(
        resolve_codex_state_root(),
        command=_FAKE_COMMAND,
        client_factory=factory,
    )
    scope = TenantScope("tenant", "alice")
    await service.login(scope, login_type="apiKey", api_key="scoped-test-key")

    source = resolve_codex_execution_auth_home(
        state_root=resolve_codex_state_root(),
        scope=scope,
        deployment_mode="shared",
    )
    assert source == service.account_home(scope)
    assert configured in source.parents

    bundle = create_config_router(codex_preferences_path=tmp_path / "config-profile.json")
    assert bundle.codex_accounts.account_home(scope) == source
    await service.close_all()
    await bundle.codex_accounts.close_all()


def test_default_codex_state_root_stays_outside_source_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend import paths as codex_paths

    checkout = tmp_path / "checkout"
    data_dir = checkout / "data"
    data_dir.mkdir(parents=True)
    external = tmp_path / "user-data" / "codex_backend"
    monkeypatch.delenv("ECHO_CODEX_STATE_DIR", raising=False)
    monkeypatch.setattr(
        codex_paths,
        "app_paths",
        lambda: SimpleNamespace(root=checkout, data_dir=data_dir),
    )
    monkeypatch.setattr(codex_paths, "_platform_codex_state_root", lambda: external)

    assert codex_paths.resolve_codex_state_root() == external.resolve(strict=False)


@pytest.mark.asyncio
async def test_plugin_catalog_uses_local_official_cache_when_remote_is_unavailable(
    tmp_path: Path,
) -> None:
    legacy_home = tmp_path / "legacy-codex"
    checkout = legacy_home / ".tmp" / "plugins"
    marketplace = checkout / ".agents" / "plugins" / "marketplace.json"
    plugin_root = checkout / "plugins" / "linear"
    manifest = plugin_root / ".codex-plugin" / "plugin.json"
    icon = plugin_root / "assets" / "linear.svg"
    marketplace.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    icon.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps(
            {
                "name": "Codex official",
                "plugins": [
                    {
                        "name": "linear",
                        "source": {"source": "local", "path": "./plugins/linear"},
                        "policy": {"installation": "AVAILABLE"},
                        "category": "Productivity",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "name": "linear",
                "version": "1.2.3",
                "description": "Manage Linear projects.",
                "author": {"name": "OpenAI"},
                "interface": {
                    "displayName": "Linear",
                    "composerIcon": "./assets/linear.svg",
                },
            }
        ),
        encoding="utf-8",
    )
    icon.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")

    service = CodexAccountService(
        tmp_path / "state",
        command=_FAKE_COMMAND,
        client_factory=_ControlFactory(),
        legacy_source_home=legacy_home,
    )
    try:
        rows = await service.list_plugins(None, force_refetch=True)
        resolved_icon = await service.plugin_icon_path(
            None,
            catalog_id="codex-marketplace:linear@openai-curated",
        )
    finally:
        await service.close_all()

    assert len(rows) == 1
    assert rows[0]["name"] == "Linear"
    assert rows[0]["author"] == "OpenAI"
    assert rows[0]["category"] == "Productivity"
    assert rows[0]["_marketplace_path"] == str(marketplace)
    assert resolved_icon == icon



