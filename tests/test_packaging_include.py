"""Packaging regression: every runtime-imported package must ship in the wheel."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Production modules introduced by behavior-preserving splits or routing fixes
# must be present in a clean, tracked-source wheel.  Importing only the CLI can
# miss lazy router/plugin imports, which previously allowed a developer Docker
# build to pass while the tag build from a clean checkout was incomplete.
_REQUIRED_RUNTIME_WHEEL_FILES = {
    "runtime/core/cerebrum/react_execution_receipts.py",
    "runtime/execution/codex_backend/__init__.py",
    "runtime/execution/codex_backend/_config_validation.py",
    "runtime/execution/codex_backend/_security_support.py",
    "runtime/execution/codex_backend/_transport.py",
    "runtime/execution/codex_backend/account.py",
    "runtime/execution/codex_backend/approvals.py",
    "runtime/execution/codex_backend/backend.py",
    "runtime/execution/codex_backend/client.py",
    "runtime/execution/codex_backend/command.py",
    "runtime/execution/codex_backend/dynamic_tools.py",
    "runtime/execution/codex_backend/events.py",
    "runtime/execution/codex_backend/model_profile.py",
    "runtime/execution/codex_backend/paths.py",
    "runtime/execution/codex_backend/responses_proxy.py",
    "runtime/execution/codex_backend/role_context.py",
    "runtime/execution/codex_backend/role_runner.py",
    "runtime/execution/codex_backend/security.py",
    "runtime/execution/codex_backend/types.py",
    "runtime/execution/suckers/computer_macos.py",
    "runtime/memory/cowork/_collaboration_project_projection.py",
    "runtime/memory/cowork/_collaboration_project_actions.py",
    "runtime/memory/cowork/_collaboration_room_write.py",
    "runtime/memory/cowork/_collaboration_session_writes.py",
    "runtime/memory/cowork/_group_blackboard.py",
    "runtime/memory/cowork/_group_sqlite_coordination.py",
    "runtime/memory/cowork/_team_invitation_support.py",
    "runtime/memory/cowork/team_invitation_store.py",
    "runtime/memory/threads/_state_mutation_lock.py",
    "runtime/memory/threads/_permanent_deletion.py",
    "runtime/platform/process/thread_turn_claim.py",
    "runtime/platform/models/custom_model_selection.py",
    "runtime/platform/io/transactional.py",
    "runtime/platform/connectors/_token_refresher.py",
    "runtime/platform/plugins/_secure_fetch.py",
    "runtime/platform/plugins/contribution_registry.py",
    "runtime/platform/plugins/workbench_activation.py",
    "runtime/platform/plugins/workbench_package.py",
    "runtime/platform/plugins/bundled/_office_io.py",
    "runtime/platform/plugins/bundled/documents/__init__.py",
    "runtime/platform/plugins/bundled/github/__init__.py",
    "runtime/platform/plugins/bundled/pdf/__init__.py",
    "runtime/platform/plugins/bundled/presentations/__init__.py",
    "runtime/platform/plugins/bundled/spreadsheets/__init__.py",
    "runtime/projectos/_store_helpers.py",
    "runtime/projectos/_store_message_actions.py",
    "runtime/projectos/_store_project_deletion.py",
    "runtime/projectos/_store_task_claims.py",
    "runtime/projectos/_store_thread_bindings.py",
    "runtime/projectos/group_service.py",
    "runtime/projectos/message_actions.py",
    "runtime/sensing/gateway/_computer_appshot_routes.py",
    "runtime/sensing/gateway/_config_endpoints_codex.py",
    "runtime/sensing/gateway/_cowork_group_access.py",
    "runtime/sensing/gateway/_cowork_group_models.py",
    "runtime/sensing/gateway/_cowork_group_room_ensure.py",
    "runtime/sensing/gateway/_cowork_group_room_link.py",
    "runtime/sensing/gateway/_cowork_group_session.py",
    "runtime/sensing/gateway/_device_flow_models.py",
    "runtime/sensing/gateway/_evolution_ops_insights.py",
    "runtime/sensing/gateway/_projects_group_projections.py",
    "runtime/sensing/gateway/_realtime_claim_aware_emitter.py",
    "runtime/sensing/gateway/_realtime_gateway_session.py",
    "runtime/sensing/gateway/_realtime_subagent_journal_items.py",
    "runtime/sensing/gateway/_realtime_thread_delete_probe.py",
    "runtime/sensing/gateway/_realtime_turn_idempotency.py",
    "runtime/sensing/gateway/_thread_state_auto_title.py",
    "runtime/sensing/gateway/_thread_state_delete.py",
    "runtime/sensing/gateway/_thread_state_search_projection.py",
    "runtime/sensing/gateway/_team_room_binding.py",
    "runtime/sensing/gateway/_team_room_creation.py",
    "runtime/sensing/gateway/_team_room_delete.py",
    "runtime/sensing/gateway/_team_room_persistence.py",
    "runtime/sensing/gateway/_team_rooms_access.py",
    "runtime/sensing/gateway/_team_rooms_state.py",
    "runtime/sensing/gateway/_team_tasks_access.py",
    "runtime/sensing/gateway/realtime_codex_backend.py",
    "runtime/sensing/gateway/realtime_interrupt_control.py",
    "runtime/sensing/gateway/workbench_packages_router.py",
    "runtime/sensing/gateway/team_invitations_router.py",
    "runtime/sensing/gateway/team_rooms_models.py",
    "runtime/sensing/gateway/thread_access.py",
    "runtime/sensing/gateway/thread_workspace.py",
}

_REQUIRED_BUNDLED_WHEEL_FILES = {
    "runtime/platform/plugins/bundled/documents/LICENSE.txt",
    "runtime/platform/plugins/bundled/documents/SKILL.md",
    "runtime/platform/plugins/bundled/documents/plugin.yaml",
    "runtime/platform/plugins/bundled/github/LICENSE.txt",
    "runtime/platform/plugins/bundled/github/SKILL.md",
    "runtime/platform/plugins/bundled/github/plugin.yaml",
    "runtime/platform/plugins/bundled/pdf/SKILL.md",
    "runtime/platform/plugins/bundled/pdf/plugin.yaml",
    "runtime/platform/plugins/bundled/presentations/SKILL.md",
    "runtime/platform/plugins/bundled/presentations/plugin.yaml",
    "runtime/platform/plugins/bundled/spreadsheets/SKILL.md",
    "runtime/platform/plugins/bundled/spreadsheets/plugin.yaml",
    "runtime/platform/plugins/bundled/mx2025_viewer/page/index.html",
    "runtime/platform/plugins/bundled/mx2025_viewer/plugin.yaml",
    "runtime/platform/plugins/bundled/paper_trading_replica/README.md",
    "runtime/platform/plugins/bundled/paper_trading_replica/page/index.html",
    "runtime/platform/plugins/bundled/paper_trading_replica/plugin.yaml",
    "runtime/platform/plugins/bundled/project_wiki/plugin.yaml",
    "runtime/platform/plugins/bundled/whale_eye/plugin.yaml",
}

_EXPECTED_BUNDLED_PLUGIN_IDS = {
    "documents",
    "github",
    "mx2025_viewer",
    "paper_trading_replica",
    "pdf",
    "presentations",
    "project_wiki",
    "spreadsheets",
    "whale_eye",
}

_REQUIRED_TRACKED_RELEASE_FILES = (
    _REQUIRED_RUNTIME_WHEEL_FILES
    | _REQUIRED_BUNDLED_WHEEL_FILES
    | {
        ".github/workflows/behavioral-evidence.yml",
        ".github/workflows/engine-comparison-evidence.yml",
        "benchmarks/execution_metrics.py",
        "benchmarks/hardened_verifier_attestation.py",
        "benchmarks/hardened_verifier_smoke.py",
        "benchmarks/linux_hardened_verifier.py",
        "benchmarks/run_engine_comparison.py",
        "benchmarks/source_provenance.py",
        "benchmarks/trusted_verifier_contract.py",
        "benchmarks/trusted_verifier_controller.py",
        "benchmarks/trusted_verifier_worker.py",
        "benchmarks/verifier_sandbox.py",
        "deploy/k8s/networkpolicy.yaml",
        "deploy/systemd-config.yaml",
        "deploy/systemd.env.example",
        "frontend/config/public-asset-dedup.ts",
        "frontend/electron/desktop-config.cjs",
        "frontend/electron/desktop-protocol.cjs",
        "extras/desktop/generate-codex-native-notices.py",
        "extras/desktop/generate-codex-third-party-licenses.py",
        "extras/desktop/prepare-codex-win.cjs",
        "extras/desktop/licenses/codex-0.149.0/LICENSE",
        "extras/desktop/licenses/codex-0.149.0/NATIVE_PROVENANCE.json",
        "extras/desktop/licenses/codex-0.149.0/NATIVE_THIRD_PARTY_NOTICES.md",
        "extras/desktop/licenses/codex-0.149.0/NOTICE",
        "extras/desktop/licenses/codex-0.149.0/THIRD_PARTY_LICENSES-code-mode-host.html",
        "extras/desktop/licenses/codex-0.149.0/THIRD_PARTY_LICENSES-codex-cli.html",
        "extras/desktop/licenses/codex-0.149.0/THIRD_PARTY_LICENSES-windows-sandbox.html",
        "extras/desktop/licenses/codex-0.149.0/THIRD_PARTY_LICENSES.md",
        "extras/desktop/licenses/codex-0.149.0/cargo-about.hbs",
        "extras/desktop/licenses/codex-0.149.0/cargo-about.toml",
        "extras/desktop/licenses/ratatui-0.30.2/LICENSE",
        "extras/desktop/licenses/ripgrep-15.2.0/COPYING",
        "extras/desktop/licenses/ripgrep-15.2.0/LICENSE-MIT",
        "extras/desktop/licenses/ripgrep-15.2.0/THIRD_PARTY_LICENSES-ripgrep.html",
        "extras/desktop/licenses/ripgrep-15.2.0/THIRD_PARTY_LICENSES.md",
        "extras/desktop/licenses/ripgrep-15.2.0/UNLICENSE",
        "extras/desktop/licenses/ripgrep-15.2.0/cargo-about.hbs",
        "extras/desktop/licenses/ripgrep-15.2.0/cargo-about.toml",
        "extensions/echo-browser-relay/cursor-overlay.js",
        "frontend/src/app/workspace/team/join/page.test.tsx",
        "frontend/src/components/workspace/collab/cowork-room-message-actions.tsx",
        "frontend/src/components/workspace/collab/cowork-room-system-card.tsx",
        "frontend/src/components/workspace/collab/cowork-room-timeline.test.tsx",
        "frontend/src/components/workspace/collab/cowork-room-timeline.tsx",
        "frontend/src/components/workspace/collab/group-human-invite-button.test.tsx",
        "frontend/src/components/workspace/collab/group-human-invite-button.tsx",
        "frontend/src/components/workspace/collab/invite-dialog.test.tsx",
        "frontend/src/components/workspace/community/community-assets.ts",
        "frontend/src/components/workspace/group-task-strategy.ts",
        "frontend/src/components/workspace/automation-control-dock.test.tsx",
        "frontend/src/components/workspace/automation-control-dock.tsx",
        "frontend/src/components/workspace/chat-input-box/AutomationTargetControl.test.tsx",
        "frontend/src/components/workspace/chat-input-box/AutomationTargetControl.tsx",
        "frontend/src/components/workspace/coder-engine-control.test.tsx",
        "frontend/src/components/workspace/coder-engine-control.tsx",
        "frontend/src/components/browser/webview-adoption-lease.test.ts",
        "frontend/src/components/workspace/messages/message-list-timeline.test.tsx",
        "frontend/src/components/workspace/realtime/group-project-capability.test.ts",
        "frontend/src/components/workspace/realtime/group-project-capability.ts",
        "frontend/src/components/workspace/realtime/conversation-empty-state.test.tsx",
        "frontend/src/components/workspace/realtime/conversation-empty-state.tsx",
        "frontend/src/components/workspace/realtime/project-group-header-badge.test.tsx",
        "frontend/src/components/workspace/realtime/project-group-header-badge.tsx",
        "frontend/src/components/workspace/realtime/promote-group-to-project-dialog.test.tsx",
        "frontend/src/components/workspace/realtime/promote-group-to-project-dialog.tsx",
        "frontend/src/components/workspace/realtime/realtime-group-header-layout.test.tsx",
        "frontend/src/components/workspace/realtime/realtime-group-header-layout.tsx",
        "frontend/src/components/workspace/realtime/work-group-project-continuity.test.tsx",
        "frontend/src/components/workspace/settings/automation-capability-settings.test.tsx",
        "frontend/src/components/workspace/settings/automation-capability-settings.tsx",
        "frontend/src/components/workspace/settings/browser-automation-settings-page.tsx",
        "frontend/src/components/workspace/settings/desktop-automation-settings-page.tsx",
        "frontend/src/components/workspace/team-mode-picker.test.tsx",
        "frontend/src/components/workspace/workspace-route-outlet.tsx",
        "frontend/src/core/auth/return-to.test.ts",
        "frontend/src/core/auth/return-to.ts",
        "frontend/src/core/collaboration/group-task-strategy-context.test.ts",
        "frontend/src/core/collaboration/group-task-strategy-context.ts",
        "frontend/src/core/coder/api.test.ts",
        "frontend/src/core/coder/api.ts",
        "frontend/src/core/cowork/hooks.test.ts",
        "frontend/src/core/cowork/mentions.test.ts",
        "frontend/src/core/cowork/mentions.ts",
        "frontend/src/core/automation/target.test.ts",
        "frontend/src/core/automation/target.ts",
        "frontend/src/core/navigation/browser-cursor-overlay.test.ts",
        "frontend/src/core/navigation/open-target.test.ts",
        "frontend/src/core/navigation/open-target.ts",
        "frontend/src/core/projects/hooks.test.ts",
        "frontend/src/core/realtime/realtime-send-contract.test.tsx",
        "frontend/src/core/settings/automation-preferences.ts",
        "frontend/src/core/settings/automation-status-api.ts",
        "frontend/src/core/threads/optimistic-messages.test.ts",
        "frontend/src/core/threads/optimistic-messages.ts",
        "frontend/src/core/threads/realtime-send-regression.test.tsx",
        "frontend/src/core/teams/api.invites.test.ts",
        "tests/runtime/execution/codex_backend/test_security.py",
        "tests/test_audit_workflow_tool_policy.py",
        "tests/test_codex_appserver_client.py",
        "tests/test_codex_backend_approvals.py",
        "tests/test_codex_backend_events.py",
        "tests/test_codex_execution_backend.py",
        "tests/test_codex_execution_backend_live.py",
        "tests/test_coder_codex_control.py",
        "tests/test_coder_role_routing.py",
        "tests/test_codex_native_notices.py",
        "tests/test_codex_dynamic_tools.py",
        "tests/fixtures/codex_app_server_0_149/mcp_apps_approval_request.json",
        "tests/fixtures/codex_app_server_0_149/mcp_form_request.json",
        "tests/test_codex_responses_proxy.py",
        "tests/test_codex_role_context.py",
        "tests/test_computer_macos.py",
        "tests/test_desktop_config_packaging.py",
        "tests/test_documents_plugin.py",
        "tests/test_drive_codex_app_server.py",
        "tests/test_engine_comparison_evidence_workflow.py",
        "tests/test_event_log_durability.py",
        "tests/test_execution_metrics.py",
        "tests/test_github_plugin.py",
        "tests/test_hardened_verifier_smoke.py",
        "tests/test_agent_world_cache.py",
        "tests/test_linked_team_room_acl.py",
        "tests/test_linux_hardened_verifier.py",
        "tests/test_linux_hardened_verifier_attacks.py",
        "tests/test_project_group_creation.py",
        "tests/test_project_group_join_approval.py",
        "tests/test_project_group_message_actions.py",
        "tests/test_project_binding_lifecycle.py",
        "tests/test_react_parallel_dispatch.py",
        "tests/test_realtime_cross_worker_interrupt.py",
        "tests/test_realtime_ownerless_local_acl.py",
        "tests/test_realtime_recovery_subscriptions.py",
        "tests/test_realtime_send_integration.py",
        "tests/test_realtime_send_timeout.py",
        "tests/test_realtime_tool_audit_barrier.py",
        "tests/test_run_engine_comparison.py",
        "tests/test_source_provenance.py",
        "tests/test_team_invitation_security.py",
        "tests/test_thread_state_permanent_delete.py",
        "tests/test_thread_turn_claim.py",
        "tests/test_trusted_verifier_controller.py",
        "tests/test_trusted_verifier_worker.py",
        "tests/test_verifier_sandbox.py",
    }
)


def _find_include_patterns() -> list[str]:
    project = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return list(project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])


def _top_level_packages() -> list[str]:
    out = []
    for child in _REPO.iterdir():
        if child.is_dir() and (child / "__init__.py").is_file():
            out.append(child.name)
    return sorted(out)


def test_all_shippable_top_level_packages_are_included() -> None:
    """runtime, tools, echo_runtime and demos are imported from shipped
    code; none of them may silently drop out of the wheel."""
    include = _find_include_patterns()
    for pkg in _top_level_packages():
        if pkg == "tests":  # excluded by design
            continue
        assert pkg in include, f"top-level package {pkg!r} is absent from Hatch packages={include}"


def test_distribution_name_cannot_resolve_to_the_unrelated_pypi_project() -> None:
    project = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "echo-os"
    assert project["scripts"]["echo-agent"] == "runtime.cli:main"

    install_surfaces = [
        _REPO / "docs" / "deployment.md",
        _REPO / "docs" / "roadmap.md",
        _REPO / "deploy" / "echo-agent.service",
        _REPO / "runtime" / "sensing" / "_fastapi_guard.py",
        _REPO / "runtime" / "sensing" / "gateway" / "cron_router.py",
        _REPO / "runtime" / "platform" / "observability" / "health.py",
    ]
    for path in install_surfaces:
        text = path.read_text(encoding="utf-8")
        assert "echo-agent-runtime[" not in text, path
    assert "echo-os[serve," in install_surfaces[0].read_text(encoding="utf-8")


def test_release_critical_new_files_are_tracked() -> None:
    """A local green build must not depend on files omitted by git archive."""

    tracked = set(
        subprocess.run(
            [
                "git",
                "ls-tree",
                "-r",
                "--name-only",
                "HEAD",
                "--",
                *_REQUIRED_TRACKED_RELEASE_FILES,
            ],
            cwd=_REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    required = _REQUIRED_TRACKED_RELEASE_FILES
    if not os.environ.get("CI"):
        # Local verification runs before the user stages/commits the candidate;
        # CI still proves the exact git archive contains every required file.
        required = {path for path in required if not (_REPO / path).is_file()}
    missing = sorted(required - tracked)
    assert not missing, "release-critical files are absent from git archive: " + ", ".join(missing)


def _clean_packaging_source(tmp_path: Path) -> Path:
    """Materialize only tracked package inputs, excluding ignored skill caches."""

    source = tmp_path / "source"
    source.mkdir()
    package_inputs = [
        "pyproject.toml",
        "README.md",
        "MANIFEST.in",
        "LICENSE",
        "NOTICE",
        "skills.lock.json",
        "runtime",
        "tools",
        "echo_runtime",
        "demos",
    ]
    tracked_inputs = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD", "--", *package_inputs],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD", "--", *tracked_inputs],
        cwd=_REPO,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        root = source.resolve()
        for member in tar.getmembers():
            target = (source / member.name).resolve()
            assert target == root or root in target.parents, member.name
        tar.extractall(source, filter="data")  # noqa: S202 - trusted local git archive

    # Local verification runs before the fix is committed. Overlay modified
    # tracked package inputs so it exercises the working-tree implementation;
    # in CI the archive already contains those committed versions.
    changed = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMRTUXB",
            "HEAD",
            "--",
            *package_inputs,
        ],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if not os.environ.get("CI"):
        changed.extend(
            subprocess.run(
                [
                    "git",
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "--",
                    *package_inputs,
                ],
                cwd=_REPO,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        )
    for relative in changed:
        src = _REPO / relative
        if not src.is_file():
            continue
        dest = source / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    deleted = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=D",
            "HEAD",
            "--",
            *package_inputs,
        ],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for relative in deleted:
        dest = source / relative
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists() or dest.is_symlink():
            dest.unlink()
    return source


def _overlay_candidate_release_files(source: Path) -> None:
    """Overlay declared additions without teaching the clean-source path about them.

    The tracked-source test must continue to fail until new files enter Git.
    This candidate overlay gives developers a separate way to prove that the
    exact files proposed for release form a complete wheel before staging.
    """

    required = _REQUIRED_RUNTIME_WHEEL_FILES | _REQUIRED_BUNDLED_WHEEL_FILES
    for relative in sorted(required):
        src = _REPO / relative
        assert src.is_file(), f"declared release file does not exist: {relative}"
        dest = source / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _build_wheel(source: Path, tmp_path: Path) -> tuple[set[str], Path]:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheel_dir),
        ],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, f"{build.stdout}\n{build.stderr}"
    wheel = next(wheel_dir.glob("echo_os-*.whl"))
    installed = tmp_path / "installed-wheel"
    with zipfile.ZipFile(wheel) as package:
        packaged_names = set(package.namelist())
        package.extractall(installed)
    return packaged_names, installed


def _assert_bundled_plugins_in_wheel(
    packaged_names: set[str],
    installed: Path,
    tmp_path: Path,
) -> None:
    missing = sorted(_REQUIRED_BUNDLED_WHEEL_FILES - packaged_names)
    assert not missing, "wheel omitted bundled plugin resources: " + ", ".join(missing)
    remote_narrative = sorted(
        name
        for name in packaged_names
        if name.startswith("runtime/platform/plugins/bundled/narrative_studio/")
    )
    assert not remote_narrative, (
        "wheel must not embed the on-demand narrative plugin: " + ", ".join(remote_narrative)
    )
    remote_paper_trading = sorted(
        name
        for name in packaged_names
        if name.startswith("runtime/platform/plugins/bundled/paper_trading/")
    )
    assert not remote_paper_trading, (
        "wheel must not embed the on-demand paper-trading plugin: "
        + ", ".join(remote_paper_trading)
    )

    bundled_names = {
        name for name in packaged_names if name.startswith("runtime/platform/plugins/bundled/")
    }
    generated = sorted(
        name for name in bundled_names if "__pycache__" in name or name.endswith((".pyc", ".pyo"))
    )
    assert not generated, "wheel contains generated Python cache files: " + ", ".join(generated)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(installed)
    expected = repr(sorted(_EXPECTED_BUNDLED_PLUGIN_IDS))
    plugin_smoke = subprocess.run(
        [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "import runtime.platform.plugins.plugin_hub as plugin_hub; "
                    "root = Path(plugin_hub.__file__).resolve().parent; "
                    "hub = plugin_hub.PluginHub("
                    "plugin_dir=Path.cwd() / 'empty-plugins', "
                    "bundled_plugin_dir=root / 'bundled', "
                    "activation_root=Path.cwd() / 'activation', "
                    "data_root=Path.cwd() / 'plugin-data'); "
                "items = {item['id']: item for item in hub.discover()}; "
                f"expected = set({expected}); "
                "assert expected <= set(items), (expected, set(items)); "
                "assert all(items[name]['bundled'] is True for name in expected); "
                "assert 'narrative_studio' not in items; "
                "assert 'paper_trading' not in items; "
                "assert 'documents' in items['documents']['tags']; "
                "assert items['documents']['version'] == '0.2.0'; "
                "assert items['documents']['author'] == 'Echo'; "
                "assert 'github' in items['github']['tags']; "
                "assert items['github']['version'] == '0.1.0'; "
                "assert items['github']['author'].startswith('Echo')"
            ),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert plugin_smoke.returncode == 0, f"{plugin_smoke.stdout}\n{plugin_smoke.stderr}"


def test_clean_tracked_source_wheel_contains_bundled_market_skills(tmp_path: Path) -> None:
    """A clean wheel must retain an offline prompt catalog without skills/public."""

    source = _clean_packaging_source(tmp_path)
    packaged_names, installed = _build_wheel(source, tmp_path)
    skill_files = {
        name
        for name in packaged_names
        if re.fullmatch(r"runtime/execution/all_skills/[^/]+/SKILL\.md", name)
    }

    missing_runtime_files = sorted(_REQUIRED_RUNTIME_WHEEL_FILES - packaged_names)
    assert not missing_runtime_files, (
        "clean tracked-source wheel omitted production modules: " + ", ".join(missing_runtime_files)
    )
    assert len(skill_files) >= 3
    assert "runtime/execution/all_skills/database-inspector/SKILL.md" in skill_files
    assert "runtime/execution/all_skills/repo-audit/SKILL.md" in skill_files
    _assert_bundled_plugins_in_wheel(packaged_names, installed, tmp_path)

    empty_resources = tmp_path / "empty-resources"
    empty_resources.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(installed)
    env["ECHO_RESOURCES_DIR"] = str(empty_resources)
    installed_smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from runtime.execution.suckers import SkillRegistry; "
                "from runtime.execution.all_skills import register_all; "
                "registry = SkillRegistry(); "
                "register_all(registry); "
                "assert len(registry.all_names()) >= 3; "
                "assert registry.has('database-inspector')"
            ),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert installed_smoke.returncode == 0, f"{installed_smoke.stdout}\n{installed_smoke.stderr}"


def test_candidate_wheel_contains_declared_runtime_and_bundled_plugins(tmp_path: Path) -> None:
    """A not-yet-staged candidate can be complete without weakening the Git gate."""

    source = _clean_packaging_source(tmp_path)
    _overlay_candidate_release_files(source)
    packaged_names, installed = _build_wheel(source, tmp_path)

    missing_runtime_files = sorted(_REQUIRED_RUNTIME_WHEEL_FILES - packaged_names)
    assert not missing_runtime_files, "candidate wheel omitted production modules: " + ", ".join(
        missing_runtime_files
    )
    _assert_bundled_plugins_in_wheel(packaged_names, installed, tmp_path)


def test_docker_distribution_copies_bootstrap_code_and_lockfile() -> None:
    dockerfile = (_REPO / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (_REPO / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY deploy/appliance/agent-bundle.json ./agent-bundle.json" in dockerfile
    assert "COPY deploy/appliance/agent-dist/ ./agent-dist/" in dockerfile
    assert "COPY deploy/appliance/agent-resources/ ./agent-resources/" in dockerfile
    assert "COPY deploy/appliance/agent-codex/ ./agent-codex/" in dockerfile
    assert "python agent_bundle.py verify" in dockerfile
    assert "python agent_bundle.py verify-installed" in dockerfile
    assert "ECHO_RESOURCES_DIR=/app/resources" in dockerfile
    assert "!deploy/appliance/agent-resources/**" in dockerignore
    assert "!deploy/appliance/agent-codex/**" in dockerignore
