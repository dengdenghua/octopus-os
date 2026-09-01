"""Catalog-verifying Docker installer used only inside the narrow sidecar."""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from typing import Any, Protocol

from appliance.hub.bundle_installer import HubBundleInstaller, HubBundleInstallRejected
from appliance.hub.bundle_updater import HubBundleUpdater
from appliance.hub.catalog import HubCatalog, HubDockerPackage
from appliance.hub.progress import (
    HubProgressCallback,
    emit_hub_progress,
    pull_image_with_progress,
)
from appliance.hub.runtime import owned_hub_services
from appliance.hub.service import HubService

INSTALL_RESULT_SCHEMA = "echo.hub.install-result.v1"
UPDATE_RESULT_SCHEMA = "echo.hub.update-result.v1"
UNINSTALL_RESULT_SCHEMA = "echo.hub.uninstall-result.v1"
START_RESULT_SCHEMA = "echo.hub.start-result.v1"
STOP_RESULT_SCHEMA = "echo.hub.stop-result.v1"
RESTART_RESULT_SCHEMA = "echo.hub.restart-result.v1"
_NAS_PROVIDER_LABEL = "sh.echo.hub.nas-provider"


class HubEngine(Protocol):
    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]: ...

    def pull_image(self, image: str) -> None: ...

    def create_volume(self, name: str, *, labels: dict[str, str]) -> None: ...

    def create_container(self, name: str, config: dict[str, Any]) -> str: ...

    def start(self, container_id: str) -> None: ...

    def inspect_container(self, container_id: str) -> dict[str, Any] | None: ...

    def stop(self, container_id: str) -> None: ...

    def remove_container(self, container_id: str, *, force: bool = False) -> None: ...

    def rename_container(self, container_id: str, name: str) -> None: ...

    def snapshot_volume(
        self,
        source: str,
        backup: str,
        *,
        labels: dict[str, str],
    ) -> None: ...

    def restore_volume(self, backup: str, destination: str) -> None: ...

    def remove_volume(self, name: str) -> None: ...


class HubInstallRejected(RuntimeError):
    """The request no longer matches a ready plan from the trusted catalog."""


class HubDockerInstaller:
    def __init__(
        self,
        catalog: HubCatalog,
        docker: HubEngine,
        *,
        docker_capacity_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.catalog = catalog
        self.docker = docker
        self._docker_capacity_provider = docker_capacity_provider

    def install(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback | None = None,
    ) -> dict[str, Any]:
        emit_hub_progress(progress, "validating", "checking-plan")
        if catalog_digest != self.catalog.digest:
            raise HubInstallRejected("Hub catalog changed; generate a new install plan")
        app = self.catalog.get(app_id)
        if app is None:
            raise HubInstallRejected("Hub app is not present in the trusted catalog")
        service = HubService(
            self.catalog,
            docker=self.docker,
            docker_capacity_provider=self._docker_capacity_provider,
        )
        plan = service.plan_install(app_id)
        if plan["planId"] != plan_id:
            raise HubInstallRejected("Hub install plan changed; review the new plan")
        if not plan["ready"]:
            codes = ",".join(str(item["code"]) for item in plan["blockers"])
            raise HubInstallRejected(f"Hub install is blocked: {codes}")
        if app.package is None and app.bundle is None:  # defensive: ready implies an artifact
            raise HubInstallRejected("Hub app package is unavailable")

        if app.bundle is not None:
            try:
                return HubBundleInstaller(self.catalog, self.docker, progress=progress).install(
                    app,
                    plan_id=plan_id,
                )
            except HubBundleInstallRejected as exc:
                raise HubInstallRejected(str(exc)) from exc

        package = app.package
        if package is None:  # narrowed after the bundle branch
            raise HubInstallRejected("Hub app package is unavailable")
        emit_hub_progress(progress, "preparing", "creating-resources")
        labels = self._labels(app.id, plan_id=plan_id)
        mounts = self._mounts(app.id, package)
        config = self._container_config(app.id, package, labels, mounts)
        container_id: str | None = None
        try:
            pull_image_with_progress(
                self.docker,
                package.image,
                callback=progress,
                item=1,
                items=1,
            )
            emit_hub_progress(progress, "preparing", "creating-resources")
            container_id = self.docker.create_container(f"echo-hub-{app.id}", config)
            emit_hub_progress(
                progress,
                "starting",
                "starting-services",
                completed=0,
                total=1,
                unit="services",
            )
            self.docker.start(container_id)
            emit_hub_progress(
                progress,
                "starting",
                "starting-services",
                completed=1,
                total=1,
                unit="services",
            )
            emit_hub_progress(
                progress,
                "verifying",
                "checking-health",
                completed=0,
                total=1,
                unit="services",
            )
            inspected = self.docker.inspect_container(container_id)
            state = inspected.get("State") if isinstance(inspected, dict) else None
            if not isinstance(state, dict) or state.get("Running") is not True:
                raise HubInstallRejected("Hub app did not reach the running state")
            emit_hub_progress(
                progress,
                "verifying",
                "checking-health",
                completed=1,
                total=1,
                unit="services",
            )
        except Exception:
            emit_hub_progress(progress, "rolling-back", "restoring-state")
            if container_id is not None:
                with contextlib.suppress(Exception):
                    self.docker.remove_container(container_id, force=True)
            raise
        return {
            "schema": INSTALL_RESULT_SCHEMA,
            "appId": app.id,
            "planId": plan_id,
            "catalogDigest": self.catalog.digest,
            "containerId": container_id[:12],
            "state": "running",
            "image": package.image,
            "rollback": {"containerRemovedOnFailure": True, "dataVolumesRetained": True},
        }

    def _control_targets(
        self,
        app_id: str,
    ) -> tuple[Any, list[str], dict[str, dict[str, Any]], str]:
        app = self.catalog.get(app_id)
        if app is None:
            raise HubInstallRejected("Hub app is not present in the trusted catalog")
        owned = owned_hub_services(
            app,
            self.docker.list_containers(include_stopped=True),
        )
        if not owned:
            raise HubInstallRejected("Hub control target is incomplete or ambiguous")
        if app.bundle is None:
            container = owned[0][1]
            container_id = str(container.get("Id") or "")
            return app, ["app"], {"app": container}, container_id
        targets = {definition.id: container for definition, container in owned}
        order = list(app.bundle.upgrade_policy.service_order)
        if set(targets) != set(order):
            raise HubInstallRejected("Hub control target is incomplete or ambiguous")
        public_id = str(targets[app.bundle.public_service].get("Id") or "")
        return app, order, targets, public_id

    def _service_running(self, container_id: str) -> bool:
        inspected = self.docker.inspect_container(container_id)
        state = inspected.get("State") if isinstance(inspected, dict) else None
        if not isinstance(state, dict):
            raise HubInstallRejected("Hub service state is unavailable")
        return state.get("Running") is True

    def _wait_control_ready(self, app: Any, service_id: str, container_id: str) -> None:
        if app.bundle is None:
            if not self._service_running(container_id):
                raise HubInstallRejected("Hub app did not reach the running state")
            return
        definition = next(service for service in app.bundle.services if service.id == service_id)
        HubBundleInstaller(self.catalog, self.docker)._wait_ready(container_id, definition)

    def _control(
        self,
        operation: str,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback | None = None,
    ) -> dict[str, Any]:
        if catalog_digest != self.catalog.digest:
            raise HubInstallRejected("Hub catalog changed; generate a new control plan")
        app = self.catalog.get(app_id)
        if app is None:
            raise HubInstallRejected("Hub app is not present in the trusted catalog")
        planner = getattr(
            HubService(
                self.catalog,
                docker=self.docker,
                docker_capacity_provider=self._docker_capacity_provider,
            ),
            f"plan_{operation}",
        )
        emit_hub_progress(progress, "validating", "checking-plan")
        plan = planner(app_id)
        if plan["planId"] != plan_id:
            raise HubInstallRejected("Hub control plan changed; review the new plan")
        if not plan["ready"]:
            codes = ",".join(str(item["code"]) for item in plan["blockers"])
            raise HubInstallRejected(f"Hub {operation} is blocked: {codes}")

        app, order, targets, public_id = self._control_targets(app_id)
        ids = {service_id: str(targets[service_id].get("Id") or "") for service_id in order}
        initially_running = {
            service_id for service_id in order if self._service_running(ids[service_id])
        }
        stopped_during_operation: list[str] = []
        started_during_operation: list[str] = []
        try:
            if operation in {"stop", "restart"}:
                emit_hub_progress(
                    progress,
                    "stopping",
                    "stopping-services",
                    completed=0,
                    total=len(order),
                    unit="services",
                )
                for index, service_id in enumerate(reversed(order), start=1):
                    if service_id in initially_running:
                        self.docker.stop(ids[service_id])
                        stopped_during_operation.append(service_id)
                    emit_hub_progress(
                        progress,
                        "stopping",
                        "stopping-services",
                        completed=index,
                        total=len(order),
                        unit="services",
                    )
                if operation == "stop":
                    emit_hub_progress(
                        progress,
                        "verifying",
                        "checking-health",
                        completed=0,
                        total=len(order),
                        unit="services",
                    )
                    for index, service_id in enumerate(order, start=1):
                        if self._service_running(ids[service_id]):
                            raise HubInstallRejected("Hub service did not stop")
                        emit_hub_progress(
                            progress,
                            "verifying",
                            "checking-health",
                            completed=index,
                            total=len(order),
                            unit="services",
                        )

            if operation in {"start", "restart"}:
                emit_hub_progress(
                    progress,
                    "starting",
                    "starting-services",
                    completed=0,
                    total=len(order),
                    unit="services",
                )
                for index, service_id in enumerate(order, start=1):
                    if not self._service_running(ids[service_id]):
                        self.docker.start(ids[service_id])
                        started_during_operation.append(service_id)
                    emit_hub_progress(
                        progress,
                        "starting",
                        "starting-services",
                        completed=index,
                        total=len(order),
                        unit="services",
                    )
                    emit_hub_progress(
                        progress,
                        "verifying",
                        "checking-health",
                        completed=index - 1,
                        total=len(order),
                        unit="services",
                    )
                    self._wait_control_ready(app, service_id, ids[service_id])
                    emit_hub_progress(
                        progress,
                        "verifying",
                        "checking-health",
                        completed=index,
                        total=len(order),
                        unit="services",
                    )
        except Exception as control_error:
            emit_hub_progress(progress, "rolling-back", "restoring-state")
            if operation == "start":
                for service_id in reversed(started_during_operation):
                    with contextlib.suppress(Exception):
                        self.docker.stop(ids[service_id])
            elif operation == "stop":
                for service_id in order:
                    if service_id in stopped_during_operation:
                        with contextlib.suppress(Exception):
                            self.docker.start(ids[service_id])
            else:
                for service_id in reversed(order):
                    if service_id not in initially_running:
                        with contextlib.suppress(Exception):
                            self.docker.stop(ids[service_id])
                for service_id in order:
                    if service_id in initially_running:
                        with contextlib.suppress(Exception):
                            self.docker.start(ids[service_id])
            raise HubInstallRejected(
                "Hub service control failed; Echo attempted to restore the previous state"
            ) from control_error

        result_schemas = {
            "start": START_RESULT_SCHEMA,
            "stop": STOP_RESULT_SCHEMA,
            "restart": RESTART_RESULT_SCHEMA,
        }
        return {
            "schema": result_schemas[operation],
            "appId": app.id,
            "planId": plan_id,
            "catalogDigest": self.catalog.digest,
            "containerId": public_id[:12],
            "state": "stopped" if operation == "stop" else "running",
            "serviceCount": len(order),
            "dataVolumesRetained": True,
            "nasDataRetained": True,
            "rollback": {"previousRunningStateRestoredOnFailure": True},
        }

    def start(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback | None = None,
    ) -> dict[str, Any]:
        return self._control(
            "start",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def stop(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback | None = None,
    ) -> dict[str, Any]:
        return self._control(
            "stop",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def restart(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback | None = None,
    ) -> dict[str, Any]:
        return self._control(
            "restart",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def update(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback | None = None,
    ) -> dict[str, Any]:
        emit_hub_progress(progress, "validating", "checking-plan")
        if catalog_digest != self.catalog.digest:
            raise HubInstallRejected("Hub catalog changed; generate a new update plan")
        app = self.catalog.get(app_id)
        if app is None:
            raise HubInstallRejected("Hub app is not present in the trusted catalog")
        service = HubService(
            self.catalog,
            docker=self.docker,
            docker_capacity_provider=self._docker_capacity_provider,
        )
        plan = service.plan_update(app_id)
        if plan["planId"] != plan_id:
            raise HubInstallRejected("Hub update plan changed; review the new plan")
        if not plan["ready"]:
            codes = ",".join(str(item["code"]) for item in plan["blockers"])
            raise HubInstallRejected(f"Hub update is blocked: {codes}")
        if app.package is None and app.bundle is None:  # defensive
            raise HubInstallRejected("Hub app package is unavailable")

        if app.bundle is not None:
            try:
                result = HubBundleUpdater(self.catalog, self.docker, progress=progress).update(
                    app,
                    plan_id=plan_id,
                )
            except HubBundleInstallRejected as exc:
                raise HubInstallRejected(str(exc)) from exc
            return {
                **result,
                "planId": plan_id,
                "catalogDigest": self.catalog.digest,
            }

        containers = [
            container
            for container in self.docker.list_containers(include_stopped=True)
            if self._owned_container(container, app_id)
        ]
        if len(containers) != 1:
            raise HubInstallRejected("Hub update target is missing or ambiguous")
        old = containers[0]
        old_id = str(old.get("Id") or "")
        old_image = str(old.get("Image") or "")
        was_running = str(old.get("State") or "").casefold() == "running"
        canonical_name = f"echo-hub-{app.id}"
        rollback_name = f"{canonical_name}-rollback-{old_id[:12]}"
        candidate_name = f"{canonical_name}-candidate-{plan_id[:12]}"
        package = app.package
        labels = self._labels(app.id, plan_id=plan_id)
        mounts = self._mounts(app.id, package)
        config = self._container_config(app.id, package, labels, mounts)
        writable_volumes = [
            f"echo-hub-{app.id}-{volume.name}"
            for volume in package.volumes
            if volume.source == "app-data" and not volume.read_only
        ]
        backups = [
            (
                volume_name,
                f"{volume_name}-rollback-{plan_id[:12]}",
            )
            for volume_name in writable_volumes
        ]

        candidate_id: str | None = None
        old_stopped = False
        old_renamed = False
        candidate_start_attempted = False
        snapshots_ready = False
        try:
            pull_image_with_progress(
                self.docker,
                package.image,
                callback=progress,
                item=1,
                items=1,
            )
            emit_hub_progress(progress, "preparing", "creating-resources")
            candidate_id = self.docker.create_container(candidate_name, config)
            if was_running:
                emit_hub_progress(
                    progress,
                    "stopping",
                    "stopping-services",
                    completed=0,
                    total=1,
                    unit="services",
                )
                self.docker.stop(old_id)
                old_stopped = True
                emit_hub_progress(
                    progress,
                    "stopping",
                    "stopping-services",
                    completed=1,
                    total=1,
                    unit="services",
                )
            if backups:
                emit_hub_progress(
                    progress,
                    "snapshotting",
                    "snapshotting-data",
                    completed=0,
                    total=len(backups),
                    unit="volumes",
                )
            for index, (volume_name, backup_name) in enumerate(backups, start=1):
                self.docker.snapshot_volume(
                    volume_name,
                    backup_name,
                    labels={
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.app-id": app.id,
                        "sh.echo.hub.role": "update-rollback",
                        "sh.echo.hub.plan-id": plan_id,
                        "sh.echo.hub.source-volume": volume_name,
                    },
                )
                emit_hub_progress(
                    progress,
                    "snapshotting",
                    "snapshotting-data",
                    completed=index,
                    total=len(backups),
                    unit="volumes",
                )
            snapshots_ready = True
            emit_hub_progress(progress, "switching", "switching-services")
            self.docker.rename_container(old_id, rollback_name)
            old_renamed = True
            self.docker.rename_container(candidate_id, canonical_name)
            candidate_start_attempted = True
            emit_hub_progress(
                progress,
                "starting",
                "starting-services",
                completed=0,
                total=1,
                unit="services",
            )
            self.docker.start(candidate_id)
            emit_hub_progress(
                progress,
                "starting",
                "starting-services",
                completed=1,
                total=1,
                unit="services",
            )
            emit_hub_progress(
                progress,
                "verifying",
                "checking-health",
                completed=0,
                total=1,
                unit="services",
            )
            inspected = self.docker.inspect_container(candidate_id)
            state = inspected.get("State") if isinstance(inspected, dict) else None
            if not isinstance(state, dict) or state.get("Running") is not True:
                raise HubInstallRejected("updated Hub app did not reach the running state")
            emit_hub_progress(
                progress,
                "verifying",
                "checking-health",
                completed=1,
                total=1,
                unit="services",
            )
            if not was_running:
                self.docker.stop(candidate_id)
            self.docker.remove_container(old_id, force=False)
        except Exception as update_error:
            emit_hub_progress(progress, "rolling-back", "restoring-state")
            cleanup_error: Exception | None = None
            if candidate_id is not None:
                with contextlib.suppress(Exception):
                    self.docker.stop(candidate_id)
                try:
                    self.docker.remove_container(candidate_id, force=True)
                except Exception as exc:
                    cleanup_error = exc
            restore_error: Exception | None = None
            if cleanup_error is None and snapshots_ready and candidate_start_attempted:
                try:
                    for volume_name, backup_name in backups:
                        self.docker.restore_volume(backup_name, volume_name)
                except Exception as exc:
                    restore_error = exc
            if old_renamed:
                try:
                    self.docker.rename_container(old_id, canonical_name)
                except Exception as exc:
                    restore_error = restore_error or exc
            if was_running and old_stopped and cleanup_error is None and restore_error is None:
                try:
                    self.docker.start(old_id)
                except Exception as exc:
                    restore_error = exc
            if cleanup_error is not None or restore_error is not None:
                raise HubInstallRejected(
                    "Hub update failed and automatic rollback could not finish; "
                    "the old container remains stopped and rollback snapshots are retained"
                ) from update_error
            for _volume_name, backup_name in backups:
                with contextlib.suppress(Exception):
                    self.docker.remove_volume(backup_name)
            raise
        for _volume_name, backup_name in backups:
            with contextlib.suppress(Exception):
                self.docker.remove_volume(backup_name)
        return {
            "schema": UPDATE_RESULT_SCHEMA,
            "appId": app.id,
            "planId": plan_id,
            "catalogDigest": self.catalog.digest,
            "previousContainerId": old_id[:12],
            "containerId": candidate_id[:12],
            "previousImage": old_image,
            "image": package.image,
            "state": "running" if was_running else "stopped",
            "dataVolumesRetained": True,
            "nasDataRetained": True,
            "rollback": {
                "oldContainerRestoredOnFailure": True,
                "dataVolumesRestoredOnFailure": True,
                "runningStatePreserved": True,
            },
        }

    def uninstall(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback | None = None,
    ) -> dict[str, Any]:
        emit_hub_progress(progress, "validating", "checking-plan")
        if catalog_digest != self.catalog.digest:
            raise HubInstallRejected("Hub catalog changed; generate a new uninstall plan")
        if self.catalog.get(app_id) is None:
            raise HubInstallRejected("Hub app is not present in the trusted catalog")
        app = self.catalog.get(app_id)
        if app is None:  # narrowed after the catalog membership check
            raise HubInstallRejected("Hub app is not present in the trusted catalog")
        service = HubService(
            self.catalog,
            docker=self.docker,
            docker_capacity_provider=self._docker_capacity_provider,
        )
        plan = service.plan_uninstall(app_id)
        if plan["planId"] != plan_id:
            raise HubInstallRejected("Hub uninstall plan changed; review the new plan")
        if not plan["ready"]:
            codes = ",".join(str(item["code"]) for item in plan["blockers"])
            raise HubInstallRejected(f"Hub uninstall is blocked: {codes}")

        if app.bundle is not None:
            try:
                result = HubBundleInstaller(self.catalog, self.docker, progress=progress).uninstall(
                    app
                )
            except HubBundleInstallRejected as exc:
                raise HubInstallRejected(str(exc)) from exc
            return {
                **result,
                "planId": plan_id,
                "catalogDigest": self.catalog.digest,
            }

        containers = [
            container
            for container in self.docker.list_containers(include_stopped=True)
            if self._owned_container(container, app_id)
        ]
        if len(containers) != 1:
            raise HubInstallRejected("Hub uninstall target is missing or ambiguous")
        container_id = str(containers[0].get("Id") or "")
        was_running = str(containers[0].get("State") or "").casefold() == "running"
        stopped = False
        try:
            emit_hub_progress(
                progress,
                "stopping",
                "stopping-services",
                completed=0,
                total=1,
                unit="services",
            )
            self.docker.stop(container_id)
            stopped = True
            emit_hub_progress(
                progress,
                "stopping",
                "stopping-services",
                completed=1,
                total=1,
                unit="services",
            )
            emit_hub_progress(
                progress,
                "removing",
                "removing-services",
                completed=0,
                total=1,
                unit="services",
            )
            self.docker.remove_container(container_id, force=False)
            emit_hub_progress(
                progress,
                "removing",
                "removing-services",
                completed=1,
                total=1,
                unit="services",
            )
        except Exception:
            if stopped and was_running:
                emit_hub_progress(progress, "rolling-back", "restoring-state")
                with contextlib.suppress(Exception):
                    self.docker.start(container_id)
            raise
        return {
            "schema": UNINSTALL_RESULT_SCHEMA,
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": self.catalog.digest,
            "containerId": container_id[:12],
            "state": "not-installed",
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }

    @staticmethod
    def _owned_container(container: dict[str, Any], app_id: str) -> bool:
        labels = container.get("Labels") or {}
        names = container.get("Names") or []
        if not isinstance(labels, dict) or not isinstance(names, list):
            return False
        return (
            labels.get("sh.echo.hub.managed") == "true"
            and labels.get("sh.echo.hub.app-id") == app_id
            and any(str(name) == f"/echo-hub-{app_id}" for name in names)
            and re.fullmatch(r"[0-9a-f]{64}", str(labels.get("sh.echo.hub.catalog-digest") or ""))
            is not None
            and re.fullmatch(r"[0-9a-f]{64}", str(labels.get("sh.echo.hub.plan-id") or ""))
            is not None
        )

    def _labels(self, app_id: str, *, plan_id: str) -> dict[str, str]:
        app = self.catalog.get(app_id)
        if app is None or app.package is None:
            raise HubInstallRejected("Hub app package is unavailable")
        return {
            "sh.echo.hub.managed": "true",
            "sh.echo.hub.app-id": app.id,
            "sh.echo.hub.catalog-digest": self.catalog.digest,
            "sh.echo.hub.package-digest": app.package.digest,
            "sh.echo.hub.plan-id": plan_id,
            "sh.echo.hub.version": app.version,
            "sh.echo.name": app.name_zh,
            "sh.echo.description": app.summary,
        }

    def _mounts(
        self,
        app_id: str,
        package: HubDockerPackage,
    ) -> list[dict[str, Any]]:
        mounts: list[dict[str, Any]] = []
        nas_source: str | None = None
        for volume in package.volumes:
            if volume.source == "app-data":
                volume_name = f"echo-hub-{app_id}-{volume.name}"
                # Volume ownership is deliberately stable across catalog and
                # plan revisions so app data survives upgrades. Container
                # labels remain plan-bound; volumes accept only this smaller
                # ownership identity when reattached.
                self.docker.create_volume(
                    volume_name,
                    labels={
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.app-id": app_id,
                        "sh.echo.hub.volume-name": volume.name,
                    },
                )
                mounts.append(
                    {
                        "Type": "volume",
                        "Source": volume_name,
                        "Target": volume.target,
                        "ReadOnly": volume.read_only,
                    }
                )
                continue
            if nas_source is None:
                nas_source = self._nas_source()
            mounts.append(
                {
                    "Type": "bind",
                    "Source": nas_source,
                    "Target": volume.target,
                    "ReadOnly": volume.read_only,
                    "BindOptions": {"Propagation": "rprivate"},
                }
            )
        return mounts

    def _nas_source(self) -> str:
        for container in self.docker.list_containers(include_stopped=True):
            labels = container.get("Labels") or {}
            if not isinstance(labels, dict) or str(labels.get(_NAS_PROVIDER_LABEL)) != "true":
                continue
            for mount in container.get("Mounts") or []:
                if not isinstance(mount, dict) or mount.get("Destination") != "/data/nas":
                    continue
                source = str(mount.get("Source") or "")
                if source.startswith("/") and "\x00" not in source:
                    return source
        raise HubInstallRejected("NAS data mount is unavailable to the Hub installer")

    @staticmethod
    def _container_config(
        app_id: str,
        package: HubDockerPackage,
        labels: dict[str, str],
        mounts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        exposed = {f"{item.container}/{item.protocol}": {} for item in package.ports}
        port_bindings = {
            f"{item.container}/{item.protocol}": [
                {"HostIp": "0.0.0.0", "HostPort": str(item.host)}  # nosec B104
            ]
            for item in package.ports
        }
        environment = []
        for key, value in package.environment:
            effective = os.environ.get("TZ", "UTC") if key == "TZ" and value == "system" else value
            environment.append(f"{key}={effective}")
        return {
            "Image": package.image,
            "Env": environment,
            "Labels": labels,
            "ExposedPorts": exposed,
            "HostConfig": {
                "PortBindings": port_bindings,
                "Mounts": mounts,
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "ReadonlyRootfs": package.runtime.read_only_rootfs,
                "PidsLimit": package.runtime.pids,
                "Memory": package.runtime.memory_mib * 1024 * 1024,
                "ShmSize": package.runtime.shm_size_mib * 1024 * 1024,
                "Init": True,
            },
            "Hostname": f"echo-{app_id}",
        }


__all__ = [
    "INSTALL_RESULT_SCHEMA",
    "UNINSTALL_RESULT_SCHEMA",
    "UPDATE_RESULT_SCHEMA",
    "HubDockerInstaller",
    "HubInstallRejected",
]
