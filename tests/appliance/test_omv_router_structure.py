from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLIANCE = REPO_ROOT / "appliance"

# 旧的 OMV RPC 后端(Web 路由层)已在「零残留」Phase 1 删除;
# /api/appliance/omv/* 与 /api/appliance/storage/* 由原生面同构提供。
# 这些模块名回归即等于重新引入功能重复的死代码。见 docs/OMV_ZERO_RESIDUE_ROADMAP.md。
#
# 注意:omv_bridge* 主机桥族群刻意保留 —— 它们是 deploy/omv/ 仍打包、并以
# `python3 -m appliance.omv_bridge` 在 x86 宿主机运行的主机桥守护进程,
# 属于路线图 Phase 3(deploy/omv/ 清理)范围,不属于 Phase 1 的死 Web 路由层。
_OMV_BACKEND_DEAD_ROUTER_MODULES = (
    "omv_router",
    "omv_client",
    "omv_account_routes",
    "omv_quota_routes",
    "omv_read_routes",
    "omv_sharing_routes",
    "omv_health",
    "omv_response",
    "omv_route_context",
)


def _tree(name: str) -> ast.Module:
    return ast.parse((APPLIANCE / name).read_text(encoding="utf-8"))


def test_native_storage_surface_does_not_import_optional_bridge_client() -> None:
    """零残留防腐:native 存储面不得依赖已删除的 OMV RPC 后端模块。

    原生面(native_storage_routes)已同构提供 /api/appliance/omv/* 与
    /api/appliance/storage/*;旧 OMV HTTP 传输(omv_client)及其桥接集群一旦
    被 native 面 import,即等于把死代码重新引入。
    """
    for relative in (
        "native_storage.py",
        "native_storage_routes.py",
        "omv_models.py",
        "accounts.py",
        "data_access.py",
    ):
        tree = _tree(relative)
        imported_modules = {
            node.module for node in tree.body if isinstance(node, ast.ImportFrom) and node.module
        }
        imported_modules.update(
            alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
        )
        for dead in _OMV_BACKEND_DEAD_ROUTER_MODULES:
            assert f"appliance.{dead}" not in imported_modules, relative


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
