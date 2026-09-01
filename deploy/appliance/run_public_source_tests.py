#!/usr/bin/env python3
"""Run the PR-safe test slice for the unified Echo OS source tree."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS = (
    "tests/appliance/test_agent_bundle.py",
    "tests/appliance/test_agent_ui_bridge.py",
    "tests/appliance/test_android_device_sync_reference.py",
    "tests/appliance/test_appliance_release_workflow.py",
    "tests/appliance/test_audit_evidence_orchestration.py",
    "tests/appliance/test_backup_orchestration.py",
    "tests/appliance/test_bare_metal_recovery_lab.py",
    "tests/appliance/test_compose_security.py",
    "tests/appliance/test_delivery_source_preflight.py",
    "tests/appliance/test_delivery_workflow_policy.py",
    "tests/appliance/test_data_access.py",
    "tests/appliance/test_device_endurance_lab.py",
    "tests/appliance/test_dependency_lock.py",
    "tests/appliance/test_desktop_root.py",
    "tests/appliance/test_external_storage.py",
    "tests/appliance/test_hub_oci_storage.py",
    "tests/appliance/test_hub_control.py",
    "tests/appliance/test_hub_runtime.py",
    "tests/appliance/test_image_release.py",
    "tests/appliance/test_install_orchestration.py",
    "tests/appliance/test_maintenance_lock_contract.py",
    "tests/appliance/test_nas_data_backup.py",
    "tests/appliance/test_native_agent_recovery_contract.py",
    "tests/appliance/test_omv_bridge.py",
    "tests/appliance/test_omv_health.py",
    "tests/appliance/test_operations_bundle.py",
    "tests/appliance/test_operations_systemd.py",
    "tests/appliance/test_omv_host_bundle.py",
    "tests/appliance/test_omv_host_installer.py",
    "tests/appliance/test_omv_platform_preflight.py",
    "tests/appliance/test_omv_plugin_lifecycle.py",
    "tests/appliance/test_omv_plugin_package.py",
    "tests/appliance/test_omv_real_x86_ci.py",
    "tests/appliance/test_omv_real_x86_evidence.py",
    "tests/appliance/test_omv_router_structure.py",
    "tests/appliance/test_optional_agent_domains.py",
    "tests/appliance/test_physical_acceptance.py",
    "tests/appliance/test_physical_acceptance_capture.py",
    "tests/appliance/test_power_state_recovery_lab.py",
    "tests/appliance/test_product_delivery_bundle.py",
    "tests/appliance/test_protocol_interoperability_lab.py",
    "tests/appliance/test_release_candidate_preflight.py",
    "tests/appliance/test_release_candidate_bundle.py",
    "tests/appliance/test_release_evidence_index.py",
    "tests/appliance/test_lan_discovery_proxy.py",
    "tests/appliance/test_remote_access.py",
    "tests/appliance/test_remote_access_delivery.py",
    "tests/appliance/test_running_appliance_verifier.py",
    "tests/appliance/test_state_backup.py",
    "tests/appliance/test_state_restore_orchestration.py",
    "tests/appliance/test_state_schema.py",
    "tests/appliance/test_storage_integration.py",
    "tests/appliance/test_storage_recovery_lab.py",
    "tests/appliance/test_tls_delivery.py",
    "tests/appliance/test_upgrade_orchestration.py",
    "tests/appliance/test_upgrade_transaction.py",
    "tests/appliance/test_web_security.py",
)

# These files exercise the embedded Agent runtime. They remain in a separate
# list only to keep the source-contract inventory readable; both lists run in
# the same job from the same checkout and distribution.
EMBEDDED_RUNTIME_TESTS = (
    "tests/appliance/test_account_security.py",
    "tests/appliance/test_accounts.py",
    "tests/appliance/test_agent_capabilities.py",
    "tests/appliance/test_agent_compat.py",
    "tests/appliance/test_agent_assets.py",
    "tests/appliance/test_android_device_sync_lab.py",
    "tests/appliance/test_app_registry.py",
    "tests/appliance/test_approval.py",
    "tests/appliance/test_audit.py",
    "tests/appliance/test_audit_evidence.py",
    "tests/appliance/test_auth.py",
    "tests/appliance/test_capabilities.py",
    "tests/appliance/test_docker_proxy.py",
    "tests/appliance/test_device_link.py",
    "tests/appliance/test_entrypoint.py",
    "tests/appliance/test_extension.py",
    "tests/appliance/test_files.py",
    "tests/appliance/test_hub.py",
    "tests/appliance/test_hub_bundle.py",
    "tests/appliance/test_hub_bundle_installer.py",
    "tests/appliance/test_hub_lifecycle_lab.py",
    "tests/appliance/test_hub_operations.py",
    "tests/appliance/test_lan_discovery_functional_lab.py",
    "tests/appliance/test_native_agent.py",
    "tests/appliance/test_omv_router.py",
    "tests/appliance/test_pm_skills.py",
    "tests/appliance/test_photos.py",
    "tests/appliance/test_paperless_functional_lab.py",
    "tests/appliance/test_state_recovery.py",
    "tests/appliance/test_task_projection.py",
    "tests/appliance/test_sync.py",
)


def main() -> int:
    # Invoking this documented gate by file path makes Python put
    # deploy/appliance, not the checkout root, at sys.path[0].  Pytest's
    # in-process entrypoint does not repair that import path, so a clean CI
    # environment could fail collection before exercising any source contract.
    # Add only this verifier-derived checkout root so the embedded runtime and
    # appliance layer resolve exactly as they do in the unified wheel.
    repository_import_root = str(REPO_ROOT)
    if repository_import_root not in sys.path:
        sys.path.insert(0, repository_import_root)

    classified = set(TESTS) | set(EMBEDDED_RUNTIME_TESTS)
    if len(classified) != len(TESTS) + len(EMBEDDED_RUNTIME_TESTS):
        raise SystemExit("source-contract test classifications overlap")
    discovered = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests/appliance").glob("test_*.py")
    }
    if discovered != classified:
        raise SystemExit(
            "appliance test classification is stale: "
            f"unclassified={sorted(discovered - classified)} "
            f"missing={sorted(classified - discovered)}"
        )
    missing_or_unsafe = [
        relative
        for relative in classified
        if not (REPO_ROOT / relative).is_file() or (REPO_ROOT / relative).is_symlink()
    ]
    if missing_or_unsafe:
        raise SystemExit(f"public source-contract tests are missing or unsafe: {missing_or_unsafe}")
    return pytest.main(
        [
            "-q",
            "--confcutdir=tests/appliance",
            *sorted(classified),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
