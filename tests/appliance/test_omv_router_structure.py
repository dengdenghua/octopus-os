from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLIANCE = REPO_ROOT / "appliance"

ROUTE_MODULES = {
    "omv_read_routes.py": 8,
    "omv_account_routes.py": 6,
    "omv_sharing_routes.py": 8,
    "omv_quota_routes.py": 2,
}
REGISTER_FUNCTIONS = {
    "register_omv_read_routes",
    "register_omv_account_routes",
    "register_omv_sharing_routes",
    "register_omv_quota_routes",
}


def _tree(name: str) -> ast.Module:
    return ast.parse((APPLIANCE / name).read_text(encoding="utf-8"))


def _route_decorators(tree: ast.AST) -> list[ast.Call]:
    decorators: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
                and decorator.func.attr in {"get", "post"}
            ):
                decorators.append(decorator)
    return decorators


def test_omv_router_factory_only_composes_bounded_route_modules() -> None:
    tree = _tree("omv_router.py")
    assert _route_decorators(tree) == []

    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_omv_router"
    )
    registered = {
        node.func.id
        for node in ast.walk(factory)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("register_omv_")
    }
    assert registered == REGISTER_FUNCTIONS


def test_omv_route_surface_is_explicitly_partitioned() -> None:
    actual = {name: len(_route_decorators(_tree(name))) for name in ROUTE_MODULES}
    assert actual == ROUTE_MODULES
    assert sum(actual.values()) == 24


def test_omv_client_is_transport_only_and_protocol_layers_do_not_import_httpx() -> None:
    client = _tree("omv_client.py")
    protocol = _tree("omv_protocol.py")
    response = _tree("omv_response.py")

    assert [
        node.name
        for node in client.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ] == []
    assert [node.name for node in client.body if isinstance(node, ast.ClassDef)] == ["OmvClient"]
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "validate_devicefile"
        for node in protocol.body
    )
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "_filesystem" for node in response.body
    )
    for tree in (protocol, response):
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "httpx" not in imported_roots


def test_omv_client_preserves_the_legacy_validation_import_surface() -> None:
    from appliance import omv_client, omv_protocol

    names = {
        "OmvControlRejected",
        "OmvUnavailable",
        "validate_account_name",
        "validate_devicefile",
        "validate_group_desired",
        "validate_nfs_desired",
        "validate_omv_uuid",
        "validate_quota_desired",
        "validate_share_privilege_desired",
        "validate_shared_folder_desired",
        "validate_smb_desired",
        "validate_user_desired",
        "validate_user_password_desired",
    }
    for name in names:
        assert getattr(omv_client, name) is getattr(omv_protocol, name)


def test_omv_bridge_business_module_does_not_own_http_transport() -> None:
    tree = _tree("omv_bridge.py")
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "OmvBridgeHttpServer" not in class_names
    assert "OmvBridgeRequestHandler" not in class_names

    imported_modules = {
        alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
    }
    imported_from = {node.module for node in tree.body if isinstance(node, ast.ImportFrom)}
    assert "socketserver" not in imported_modules
    assert "http.server" not in imported_from


def test_omv_bridge_preserves_http_and_error_import_compatibility() -> None:
    from appliance import omv_bridge, omv_bridge_errors, omv_bridge_http

    assert omv_bridge.create_server is omv_bridge_http.create_server
    assert omv_bridge.OmvBridgeHttpServer is omv_bridge_http.OmvBridgeHttpServer
    assert omv_bridge.OmvBridgeRequestHandler is omv_bridge_http.OmvBridgeRequestHandler
    assert omv_bridge.OmvBridgeError is omv_bridge_errors.OmvBridgeError
    assert omv_bridge.OmvBridgeConflict is omv_bridge_errors.OmvBridgeConflict
    assert omv_bridge.OmvBridgeValidationError is omv_bridge_errors.OmvBridgeValidationError


def test_omv_account_controls_are_composed_outside_the_service_facade() -> None:
    bridge = _tree("omv_bridge.py")
    accounts = _tree("omv_bridge_accounts.py")
    service = next(
        node
        for node in bridge.body
        if isinstance(node, ast.ClassDef) and node.name == "OmvReadOnlyService"
    )
    mixin = next(
        node
        for node in accounts.body
        if isinstance(node, ast.ClassDef) and node.name == "OmvAccountControlMixin"
    )
    account_methods = {
        "apply_group",
        "apply_user",
        "apply_user_password",
        "plan_group",
        "plan_user",
        "plan_user_password",
    }
    service_methods = {node.name for node in service.body if isinstance(node, ast.FunctionDef)}
    mixin_methods = {node.name for node in mixin.body if isinstance(node, ast.FunctionDef)}
    assert service_methods.isdisjoint(account_methods)
    assert account_methods <= mixin_methods


def test_omv_service_keeps_account_control_runtime_compatibility() -> None:
    from appliance.omv_bridge import OmvReadOnlyService
    from appliance.omv_bridge_accounts import OmvAccountControlMixin

    assert issubclass(OmvReadOnlyService, OmvAccountControlMixin)
    for name in (
        "apply_group",
        "apply_user",
        "apply_user_password",
        "plan_group",
        "plan_user",
        "plan_user_password",
    ):
        assert callable(getattr(OmvReadOnlyService, name))


def test_omv_sharing_and_quota_controls_are_stateless_composed_mixins() -> None:
    bridge = _tree("omv_bridge.py")
    service = next(
        node
        for node in bridge.body
        if isinstance(node, ast.ClassDef) and node.name == "OmvReadOnlyService"
    )
    expected_bases = {
        "OmvAccountControlMixin",
        "OmvInventoryMixin",
        "OmvQuotaControlMixin",
        "OmvSharingControlMixin",
    }
    assert {base.id for base in service.bases if isinstance(base, ast.Name)} == expected_bases

    delegated_methods = {
        "apply_filesystem_quota",
        "apply_nfs_share",
        "apply_share_privilege",
        "apply_shared_folder",
        "apply_smb_share",
        "plan_filesystem_quota",
        "plan_nfs_share",
        "plan_share_privilege",
        "plan_shared_folder",
        "plan_smb_share",
    }
    service_methods = {node.name for node in service.body if isinstance(node, ast.FunctionDef)}
    assert service_methods.isdisjoint(delegated_methods)

    for filename, class_name in (
        ("omv_bridge_accounts.py", "OmvAccountControlMixin"),
        ("omv_bridge_quota.py", "OmvQuotaControlMixin"),
        ("omv_bridge_sharing.py", "OmvSharingControlMixin"),
    ):
        tree = _tree(filename)
        mixin = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        assert all(
            not isinstance(node, ast.FunctionDef) or node.name != "__init__" for node in mixin.body
        )


def test_omv_service_keeps_sharing_and_quota_runtime_compatibility() -> None:
    from appliance.omv_bridge import OmvReadOnlyService
    from appliance.omv_bridge_quota import OmvQuotaControlMixin
    from appliance.omv_bridge_sharing import OmvSharingControlMixin

    assert issubclass(OmvReadOnlyService, OmvSharingControlMixin)
    assert issubclass(OmvReadOnlyService, OmvQuotaControlMixin)
    for name in (
        "apply_filesystem_quota",
        "apply_nfs_share",
        "apply_share_privilege",
        "apply_shared_folder",
        "apply_smb_share",
        "plan_filesystem_quota",
        "plan_nfs_share",
        "plan_share_privilege",
        "plan_shared_folder",
        "plan_smb_share",
    ):
        assert callable(getattr(OmvReadOnlyService, name))


def test_omv_bridge_facade_only_owns_shared_state_and_process_assembly() -> None:
    bridge = _tree("omv_bridge.py")
    service = next(
        node
        for node in bridge.body
        if isinstance(node, ast.ClassDef) and node.name == "OmvReadOnlyService"
    )
    assert {node.name for node in service.body if isinstance(node, ast.FunctionDef)} == {"__init__"}
    assert {node.name for node in bridge.body if isinstance(node, ast.ClassDef)} == {
        "OmvReadOnlyService"
    }

    runner_tree = _tree("omv_bridge_runners.py")
    runner_classes = {node.name for node in runner_tree.body if isinstance(node, ast.ClassDef)}
    assert runner_classes == {
        "LsblkTopologyRunner",
        "OmvCommandRunner",
        "OmvEngineSecretRunner",
        "ProcMdstatReader",
    }


def test_omv_bridge_preserves_inventory_and_runner_runtime_compatibility() -> None:
    from appliance import omv_bridge, omv_bridge_runners
    from appliance.omv_bridge_inventory import OmvInventoryMixin

    assert issubclass(omv_bridge.OmvReadOnlyService, OmvInventoryMixin)
    for name in (
        "LsblkTopologyRunner",
        "OmvCommandRunner",
        "OmvEngineSecretRunner",
        "ProcMdstatReader",
    ):
        assert getattr(omv_bridge, name) is getattr(omv_bridge_runners, name)
    for name in (
        "filesystems",
        "raid_arrays",
        "sharing_overview",
        "smart",
        "smart_devices",
        "storage_topology",
    ):
        assert callable(getattr(omv_bridge.OmvReadOnlyService, name))


def test_container_identifier_validation_has_one_owner() -> None:
    for relative in ("approval.py", "app_registry/router.py"):
        source = (APPLIANCE / relative).read_text(encoding="utf-8")
        assert "_CONTAINER_ID" not in source
        assert "is_container_id" in source


def test_repository_entrypoints_distinguish_os_from_legacy_agent_material() -> None:
    english_readme = (REPO_ROOT / "README.en.md").read_text(encoding="utf-8")
    legacy_wiki = (REPO_ROOT / "CODE_WIKI.md").read_text(encoding="utf-8")
    test_ownership = (REPO_ROOT / "tests/README.md").read_text(encoding="utf-8")
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "`runtime/` is organized" not in english_readme
    assert "one versioned distribution" in english_readme
    assert legacy_wiki.startswith("> [!WARNING]\n")
    assert "tests/appliance/" in test_ownership
    assert project["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests/appliance"]
