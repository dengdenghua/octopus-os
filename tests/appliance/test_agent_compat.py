"""Contract tests for the single Echo OS -> Agent compatibility boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from appliance.agent_api import contract, tasks


def test_only_compatibility_boundary_imports_agent_runtime() -> None:
    violations: list[str] = []
    for path in sorted(Path("appliance").rglob("*.py")):
        if Path("appliance/agent_api") in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module == "runtime" or module.startswith("runtime."):
                    violations.append(f"{path}:{node.lineno}:{module}")

    assert violations == []


def test_agent_private_module_literals_are_confined_to_boundary() -> None:
    violations: list[str] = []
    allowed = {Path("appliance/native_entrypoint.py")}
    for path in sorted(Path("appliance").rglob("*.py")):
        if path in allowed or Path("appliance/agent_api") in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("runtime.")
            ):
                violations.append(f"{path}:{node.lineno}:{node.value}")

    assert violations == []


def test_agent_contract_covers_every_statically_imported_runtime_symbol() -> None:
    declared = {
        (symbol.module, symbol.name) for symbols in contract._DOMAINS.values() for symbol in symbols
    }
    missing: list[str] = []
    for path in sorted(Path("appliance/agent_api").glob("*.py")):
        if path.name == "contract.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("runtime.")
            ):
                for alias in node.names:
                    if (node.module, alias.name) not in declared:
                        missing.append(f"{path}:{node.lineno}:{node.module}.{alias.name}")

    assert missing == []


def test_resume_checkpoint_metadata_normalizes_non_mapping_result(monkeypatch) -> None:
    from runtime.sensing.gateway import _realtime_turn_lifecycle_resume as resume_module

    monkeypatch.setattr(resume_module, "_resume_checkpoint_metadata", lambda *_args: ["invalid"])

    assert tasks.resume_checkpoint_metadata(object(), "task-1") is None


def test_resume_checkpoint_metadata_returns_a_detached_mapping(monkeypatch) -> None:
    from runtime.sensing.gateway import _realtime_turn_lifecycle_resume as resume_module

    source = {"checkpoint_id": 7, "iteration": 2, "phase": "verify"}
    monkeypatch.setattr(resume_module, "_resume_checkpoint_metadata", lambda *_args: source)

    result = tasks.resume_checkpoint_metadata(object(), "task-1")

    assert result == source
    assert result is not source


def test_versioned_contract_reports_required_task_domain() -> None:
    report = contract.require_agent_api_contract(
        required_domains=("tasks",),
        optional_domains=(),
    )

    assert report["schema"] == "echo.agent_api_contract.v1"
    assert report["compatible"] is True
    assert report["required"] == [{"id": "tasks", "compatible": True, "missing": []}]
    assert report["agentVersion"]
    assert report["runtimeVersion"]


def test_required_contract_fails_with_bounded_domain_reason(monkeypatch) -> None:
    real_import = contract.importlib.import_module

    def _import(name: str):
        if name == "runtime.platform.process.task_supervisor":
            return SimpleNamespace(TaskLeaseConflict=RuntimeError)
        return real_import(name)

    monkeypatch.setattr(contract.importlib, "import_module", _import)

    with pytest.raises(
        contract.AgentApiIncompatibleError,
        match=r"tasks\[lease_health\]",
    ):
        contract.require_agent_api_contract(
            required_domains=("tasks",),
            optional_domains=(),
        )


def test_auth_contract_covers_session_cleanup_symbol(monkeypatch) -> None:
    real_import = contract.importlib.import_module

    def _import(name: str):
        module = real_import(name)
        if name == "runtime.safety.auth.principal":
            return SimpleNamespace(
                SESSION_COOKIE_NAME=module.SESSION_COOKIE_NAME,
                LEGACY_SESSION_COOKIE_NAME=module.LEGACY_SESSION_COOKIE_NAME,
            )
        return module

    monkeypatch.setattr(contract.importlib, "import_module", _import)

    with pytest.raises(
        contract.AgentApiIncompatibleError,
        match=r"auth\[session_cookie_clear\]",
    ):
        contract.require_agent_api_contract(
            required_domains=("auth",),
            optional_domains=(),
        )


def test_default_contract_keeps_catalog_and_devices_optional(monkeypatch) -> None:
    real_import = contract.importlib.import_module

    def _import(name: str):
        if name in {
            "runtime.platform.plugins.cloud_catalog",
            "runtime.tentacle.coordinator",
        }:
            raise ImportError("optional Agent domain unavailable")
        return real_import(name)

    monkeypatch.setattr(contract.importlib, "import_module", _import)

    report = contract.require_agent_api_contract()

    assert report["compatible"] is True
    assert [item["id"] for item in report["required"]] == ["auth", "audit", "tasks"]
    optional = {item["id"]: item for item in report["optional"]}
    assert optional["catalog"]["compatible"] is False
    assert optional["devices"]["compatible"] is False


def test_image_contract_covers_every_private_safe_iteration_seam(monkeypatch) -> None:
    real_import = contract.importlib.import_module

    def _import(name: str):
        module = real_import(name)
        if name == "runtime.memory.hemolymph.image_semantic_index":
            return SimpleNamespace(
                build_index=module.build_index,
                search_by_text=module.search_by_text,
                _iter_images=module._iter_images,
                _load_image=module._load_image,
            )
        return module

    monkeypatch.setattr(contract.importlib, "import_module", _import)

    with pytest.raises(
        contract.AgentApiIncompatibleError,
        match=r"images\[safe_mtime\]",
    ):
        contract.require_agent_api_contract(
            required_domains=("images",),
            optional_domains=(),
        )


def test_capability_contract_checks_the_full_lifecycle_method_surface(monkeypatch) -> None:
    class IncompleteService:
        inspect = install_plan = install = authorize = disable = status = lambda *_args: None
        connection_profile = connect = disconnect = lambda *_args: None
        uninstall_plan = uninstall = rollback_plan = lambda *_args: None

    class Principal:
        @classmethod
        def create(cls, **_kwargs: object) -> object:
            return cls()

    class ServiceError(RuntimeError):
        pass

    monkeypatch.setattr(
        contract.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            CapabilityLifecycleService=IncompleteService,
            CapabilityPrincipal=Principal,
            CapabilityServiceError=ServiceError,
        ),
    )

    with pytest.raises(
        contract.AgentApiIncompatibleError,
        match=r"capabilities\[lifecycle_service\.rollback\]",
    ):
        contract.require_agent_api_contract(
            required_domains=("capabilities",),
            optional_domains=(),
        )
