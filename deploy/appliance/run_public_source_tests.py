#!/usr/bin/env python3
"""Run the PR-safe test slice for the unified Echo OS source tree."""

from __future__ import annotations

import argparse
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
    "tests/appliance/test_btrfs_provisioning_lab.py",
    "tests/appliance/test_btrfs_provision_functional_lab.py",
    "tests/appliance/test_btrfs_reboot_functional_lab.py",
    "tests/appliance/test_btrfs_scrub_functional_lab.py",
    "tests/appliance/test_btrfs_snapshot_functional_lab.py",
    "tests/appliance/test_btrfs_replacement_lab.py",
    "tests/appliance/test_btrfs_scrub_schedule.py",
    "tests/appliance/test_btrfs_snapshot_lock_policy.py",
    "tests/appliance/test_btrfs_snapshot_schedule.py",
    "tests/appliance/test_compose_security.py",
    "tests/appliance/test_delivery_source_preflight.py",
    "tests/appliance/test_delivery_workflow_policy.py",
    "tests/appliance/test_disk_idle_policy.py",
    "tests/appliance/test_data_access.py",
    "tests/appliance/test_device_endurance_lab.py",
    "tests/appliance/test_dependency_lock.py",
    "tests/appliance/test_desktop_root.py",
    "tests/appliance/test_external_storage.py",
    "tests/appliance/test_hub_oci_storage.py",
    "tests/appliance/test_hub_control.py",
    "tests/appliance/test_hub_runtime.py",
    "tests/appliance/test_host_migration.py",
    "tests/appliance/test_image_release.py",
    "tests/appliance/test_install_orchestration.py",
    "tests/appliance/test_iso_workspace_admission.py",
    "tests/appliance/test_maintenance_lock_contract.py",
    "tests/appliance/test_mdraid_replacement_lab.py",
    "tests/appliance/test_mdraid_check_schedule.py",
    "tests/appliance/test_nas_data_backup.py",
    "tests/appliance/test_nas_data_backup_schedule.py",
    "tests/appliance/test_nas_alert_deadman.py",
    "tests/appliance/test_nas_alert_delivery.py",
    "tests/appliance/test_nas_email_alert_delivery.py",
    "tests/appliance/test_nas_backup_routes.py",
    "tests/appliance/test_nas_backup_credential_policy.py",
    "tests/appliance/test_nas_backup_remote_policy.py",
    "tests/appliance/test_nas_backup_schedule_policy.py",
    "tests/appliance/test_nas_backup_recovery.py",
    "tests/appliance/test_nas_backup_restore_policy.py",
    "tests/appliance/test_nas.py",
    "tests/appliance/test_native_agent_recovery_contract.py",
    "tests/appliance/test_native_auth_provisioning.py",
    "tests/appliance/test_native_dlna.py",
    "tests/appliance/test_native_firewall.py",
    "tests/appliance/test_native_hub_firewall.py",
    "tests/appliance/test_native_ext4.py",
    "tests/appliance/test_native_ext4_check.py",
    "tests/appliance/test_native_btrfs.py",
    "tests/appliance/test_native_btrfs_health.py",
    "tests/appliance/test_native_btrfs_replace.py",
    "tests/appliance/test_native_btrfs_scrub.py",
    "tests/appliance/test_native_btrfs_snapshot.py",
    "tests/appliance/test_native_mdraid.py",
    "tests/appliance/test_native_mdraid_check.py",
    "tests/appliance/test_native_mdraid_replace.py",
    "tests/appliance/test_native_storage.py",
    "tests/appliance/test_native_storage_broker.py",
    "tests/appliance/test_native_webdav.py",
    "tests/appliance/test_native_webdav_control.py",
    "tests/appliance/test_native_webdav_delivery.py",
    "tests/appliance/test_native_time_machine.py",
    "tests/appliance/test_native_storage_pool.py",
    "tests/appliance/test_native_storage_observation.py",
    "tests/appliance/test_native_storage_write_readiness.py",
    "tests/appliance/test_native_smb_path_guard.py",
    "tests/appliance/test_native_service_health.py",
    "tests/appliance/test_native_smart.py",
    "tests/appliance/test_native_ups.py",
    "tests/appliance/test_nut_device_config.py",
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
    "tests/appliance/test_provision_nginx.py",
    "tests/appliance/test_provision_storage_stack.py",
    "tests/appliance/test_release_candidate_preflight.py",
    "tests/appliance/test_release_candidate_bundle.py",
    "tests/appliance/test_release_evidence_index.py",
    "tests/appliance/test_lan_discovery_proxy.py",
    "tests/appliance/test_remote_access.py",
    "tests/appliance/test_remote_access_delivery.py",
    "tests/appliance/test_rclone_backup_mount.py",
    "tests/appliance/test_rk3576_profile.py",
    "tests/appliance/test_running_appliance_verifier.py",
    "tests/appliance/test_script_source_integrity.py",
    "tests/appliance/test_state_backup.py",
    "tests/appliance/test_state_restore_orchestration.py",
    "tests/appliance/test_state_schema.py",
    "tests/appliance/test_smart_schedule.py",
    "tests/appliance/test_storage_integration.py",
    "tests/appliance/test_storage_recovery_lab.py",
    "tests/appliance/test_storage_provisioning_lab.py",
    "tests/appliance/test_tls_delivery.py",
    "tests/appliance/test_upgrade_orchestration.py",
    "tests/appliance/test_upgrade_transaction.py",
    "tests/appliance/test_ups_shutdown_guard.py",
    "tests/appliance/test_ups_shutdown_policy.py",
    "tests/appliance/test_vm_evidence_audit.py",
    "tests/appliance/test_web_security.py",
    "tests/appliance/test_zfs_runtime_verifier.py",
)

# These files exercise the embedded Agent runtime. They remain in a separate
# list only to keep the source-contract inventory readable; both lists run in
# the same job from the same checkout and distribution.
EMBEDDED_RUNTIME_TESTS = (
    "tests/appliance/test_account_security.py",
    "tests/appliance/test_accounts.py",
    "tests/appliance/test_agent_capabilities.py",
    "tests/appliance/test_agent_authorization.py",
    "tests/appliance/test_agent_compat.py",
    "tests/appliance/test_agent_assets.py",
    "tests/appliance/test_android_device_sync_lab.py",
    "tests/appliance/test_app_registry.py",
    "tests/appliance/test_approval.py",
    "tests/appliance/test_audit.py",
    "tests/appliance/test_audit_evidence.py",
    "tests/appliance/test_auth.py",
    "tests/appliance/test_capabilities.py",
    "tests/appliance/test_diagnostics.py",
    "tests/appliance/test_docker_proxy.py",
    "tests/appliance/test_docker_credential.py",
    "tests/appliance/test_native_docker_control.py",
    "tests/appliance/test_native_docker_health.py",
    "tests/appliance/test_device_link.py",
    "tests/appliance/test_entrypoint.py",
    "tests/appliance/test_document_worker_packaging.py",
    "tests/appliance/test_extension.py",
    "tests/appliance/test_files.py",
    "tests/appliance/test_file_shares.py",
    "tests/appliance/test_file_recursive_authorization.py",
    "tests/appliance/test_file_operation_tasks.py",
    "tests/appliance/test_file_organization_directories.py",
    "tests/appliance/test_file_organization_documents.py",
    "tests/appliance/test_file_organization_io.py",
    "tests/appliance/test_file_organization_plan.py",
    "tests/appliance/test_file_organization_preview_limits.py",
    "tests/appliance/test_file_organization_router.py",
    "tests/appliance/test_file_organization_service.py",
    "tests/appliance/test_file_organization_store.py",
    "tests/appliance/test_file_organization_tools.py",
    "tests/appliance/test_invoice_classification.py",
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
    "tests/appliance/test_photo_readiness.py",
    "tests/appliance/test_photo_job_persistence.py",
    "tests/appliance/test_photo_job_lifecycle.py",
    "tests/appliance/test_photo_empty_cleanup.py",
    "tests/appliance/test_photo_safe_file.py",
    "tests/appliance/test_photo_search_scope.py",
    "tests/appliance/test_photo_tools.py",
    "tests/appliance/test_paperless_functional_lab.py",
    "tests/appliance/test_state_recovery.py",
    "tests/appliance/test_task_projection.py",
    "tests/appliance/test_sync.py",
    "tests/appliance/test_totp.py",
    "tests/appliance/test_windows_state.py",
)

# Runtime regressions that exercise the data paths used by the OS. Keep these
# explicit: the complete Agent suite has additional optional dependencies.
DATA_RUNTIME_TESTS = (
    "tests/test_document_text_extractor.py",
    "tests/test_document_extraction_limits.py",
    "tests/test_document_extraction_process.py",
    "tests/test_document_process_limits.py",
    "tests/test_document_process_linux.py",
    "tests/test_document_worker_environment.py",
    "tests/test_document_rollback.py",
    "tests/test_file_rollback_io.py",
    "tests/test_rollback_api_scope.py",
    "tests/test_file_op_events.py",
    "tests/test_rewind.py",
    "tests/test_observability_tenant_scope.py",
    "tests/test_fs_content.py",
    "tests/test_uploads_workspace.py",
    "tests/test_image_library_tools.py",
    "tests/test_image_index_library_integrity.py",
    "tests/test_image_index_incremental.py",
    "tests/test_image_index_identity.py",
    "tests/test_profile_journal_persistence.py",
    "tests/test_tool_read_refresh_policy.py",
    "tests/test_tool_service_path_scope.py",
    "tests/test_image_model_runtime.py",
    "tests/test_workspace_crypto_failure.py",
    "tests/test_design_plugin_readiness.py",
    "tests/test_task_execution.py",
    "tests/test_execution_boundary.py",
    "tests/test_engine_history.py",
    "tests/test_engine_compaction_policy.py",
    "tests/test_model_services.py",
    "tests/test_engine_lazy_imports.py",
    "tests/test_host_tool_broker.py",
    "tests/test_host_mcp.py",
    "tests/test_opencode_backend.py",
    "tests/test_opencode_entry.py",
    "tests/test_team_patterns.py",
    "tests/test_realtime_model_override.py",
    "tests/test_codex_dynamic_tools.py",
    "tests/test_realtime_context_memory.py",
    "tests/test_web_fetch_skill.py",
    "tests/test_thread_turn_claim.py",
    "tests/test_local_auth_rate_limit.py",
)


def _parse_args(argv: list[str] | None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # This gate intentionally accepts no test-selection or passthrough options:
    # callers cannot silently narrow the reviewed source-contract inventory.
    _parse_args(argv)
    # Invoking this documented gate by file path makes Python put
    # deploy/appliance, not the checkout root, at sys.path[0].  Pytest's
    # in-process entrypoint does not repair that import path, so a clean CI
    # environment could fail collection before exercising any source contract.
    # Add only this verifier-derived checkout root so the embedded runtime and
    # appliance layer resolve exactly as they do in the unified wheel.
    repository_import_root = str(REPO_ROOT)
    if repository_import_root not in sys.path:
        sys.path.insert(0, repository_import_root)

    # Run this from the developer checkout as well as CI. A clean CI checkout
    # cannot see files that were accidentally omitted from the commit, while
    # this shared runner can fail before reporting a misleading green suite.
    from tools.lint import untracked_source_check

    if untracked_source_check.main([]) != 0:
        raise SystemExit("public source-contract gate found untracked source files")

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
    selected = classified | set(DATA_RUNTIME_TESTS)
    if len(selected) != len(classified) + len(DATA_RUNTIME_TESTS):
        raise SystemExit("data runtime test classifications overlap")
    missing_or_unsafe = [
        relative
        for relative in selected
        if not (REPO_ROOT / relative).is_file() or (REPO_ROOT / relative).is_symlink()
    ]
    if missing_or_unsafe:
        raise SystemExit(f"public source-contract tests are missing or unsafe: {missing_or_unsafe}")
    return pytest.main(
        [
            "-q",
            "--confcutdir=tests/appliance",
            *sorted(selected),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
