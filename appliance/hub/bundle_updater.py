"""Rollback-safe updates for catalog-verified multi-container Hub bundles."""

from __future__ import annotations

import contextlib
import re
from typing import Any

from appliance.hub.bundle_installer import (
    BundleEngine,
    HubBundleInstaller,
    HubBundleInstallRejected,
)
from appliance.hub.catalog import HubApp, HubCatalog
from appliance.hub.progress import (
    HubProgressCallback,
    emit_hub_progress,
    pull_image_with_progress,
)


class HubBundleUpdater(HubBundleInstaller):
    def __init__(
        self,
        catalog: HubCatalog,
        docker: BundleEngine,
        *,
        progress: HubProgressCallback | None = None,
    ) -> None:
        super().__init__(catalog, docker, progress=progress)

    def update(self, app: HubApp, *, plan_id: str) -> dict[str, Any]:
        bundle = app.bundle
        if bundle is None:
            raise HubBundleInstallRejected("Hub multi-container package is unavailable")
        old_services, old_plan_id, old_version = self._owned_services(app)
        old_major = int(old_version.split(".", 1)[0])
        new_major = int(bundle.upgrade_policy.application_version.split(".", 1)[0])
        if new_major < old_major or new_major - old_major > bundle.upgrade_policy.max_major_step:
            raise HubBundleInstallRejected(
                "Hub bundle update must advance at most one application major version"
            )
        order = list(bundle.upgrade_policy.service_order)
        was_running = {
            service_id: str(old_services[service_id].get("State") or "").casefold() == "running"
            for service_id in order
        }
        old_ids = {
            service_id: str(old_services[service_id].get("Id") or "") for service_id in order
        }
        old_public_image = str(old_services[bundle.public_service].get("Image") or "")
        data_volumes = self._volume_sources(app)
        secret_volumes = {
            service.id: f"echo-hub-{app.id}-secrets-{service.id}"
            for service in bundle.services
            if service.secrets
        }
        app_data_volumes = [
            data_volumes[volume.name] for volume in bundle.volumes if volume.source == "app-data"
        ]
        for volume_name in [*app_data_volumes, *secret_volumes.values()]:
            if self.docker.inspect_volume(volume_name) is None:
                raise HubBundleInstallRejected(
                    "Hub bundle update requires every retained data and secret volume"
                )
        for service in bundle.services:
            if service.secrets:
                self.docker.verify_secret_volume(
                    secret_volumes[service.id],
                    tuple(mount.secret for mount in service.secrets),
                )

        backups = [
            (data_volumes[name], f"{data_volumes[name]}-rollback-{plan_id[:12]}")
            for name in bundle.upgrade_policy.snapshot_volumes
        ]
        network_names = {
            network.name: f"echo-hub-{app.id}-{network.name}-{plan_id[:12]}"
            for network in bundle.networks
        }
        candidate_ids: dict[str, str] = {}
        created_networks: list[str] = []
        snapshots_ready = False
        candidate_start_attempted = False
        old_renamed: list[str] = []
        service_definitions = {service.id: service for service in bundle.services}
        try:
            images = tuple(dict.fromkeys(service.image for service in bundle.services))
            for index, image in enumerate(images, start=1):
                pull_image_with_progress(
                    self.docker,
                    image,
                    callback=self.progress,
                    item=index,
                    items=len(images),
                )
            emit_hub_progress(self.progress, "preparing", "creating-resources")
            for network in bundle.networks:
                docker_name = network_names[network.name]
                if self.docker.create_network(
                    docker_name,
                    internal=network.internal,
                    labels={
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.bundle-app-id": app.id,
                        "sh.echo.hub.bundle-network": network.name,
                        "sh.echo.hub.bundle-digest": bundle.digest,
                        "sh.echo.hub.plan-id": plan_id,
                    },
                ):
                    created_networks.append(docker_name)
            emit_hub_progress(
                self.progress,
                "stopping",
                "stopping-services",
                completed=0,
                total=len(order),
                unit="services",
            )
            for index, service_id in enumerate(reversed(order), start=1):
                self.docker.stop(old_ids[service_id])
                emit_hub_progress(
                    self.progress,
                    "stopping",
                    "stopping-services",
                    completed=index,
                    total=len(order),
                    unit="services",
                )
            if backups:
                emit_hub_progress(
                    self.progress,
                    "snapshotting",
                    "snapshotting-data",
                    completed=0,
                    total=len(backups),
                    unit="volumes",
                )
            for index, (source, backup) in enumerate(backups, start=1):
                self.docker.snapshot_volume(
                    source,
                    backup,
                    labels={
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.bundle-app-id": app.id,
                        "sh.echo.hub.bundle-volume-role": "update-rollback",
                        "sh.echo.hub.plan-id": plan_id,
                        "sh.echo.hub.source-volume": source,
                    },
                )
                emit_hub_progress(
                    self.progress,
                    "snapshotting",
                    "snapshotting-data",
                    completed=index,
                    total=len(backups),
                    unit="volumes",
                )
            snapshots_ready = True

            for service_id in order:
                canonical = self._container_name(
                    app.id,
                    service_id,
                    public=service_id == bundle.public_service,
                )
                config = self._container_config(
                    app,
                    service_id=service_id,
                    plan_id=plan_id,
                    data_volumes=data_volumes,
                    secret_volume=secret_volumes.get(service_id),
                    network_names=network_names,
                )
                candidate_ids[service_id] = self.docker.create_container(
                    f"{canonical}-candidate-{plan_id[:8]}", config
                )
            emit_hub_progress(
                self.progress,
                "starting",
                "starting-services",
                completed=0,
                total=len(order),
                unit="services",
            )
            for index, service_id in enumerate(order, start=1):
                candidate_start_attempted = True
                self.docker.start(candidate_ids[service_id])
                emit_hub_progress(
                    self.progress,
                    "starting",
                    "starting-services",
                    completed=index,
                    total=len(order),
                    unit="services",
                )
                emit_hub_progress(
                    self.progress,
                    "verifying",
                    "checking-health",
                    completed=index - 1,
                    total=len(order),
                    unit="services",
                )
                self._wait_ready(candidate_ids[service_id], service_definitions[service_id])
                emit_hub_progress(
                    self.progress,
                    "verifying",
                    "checking-health",
                    completed=index,
                    total=len(order),
                    unit="services",
                )
            for service_id in reversed(order):
                if not was_running[service_id]:
                    self.docker.stop(candidate_ids[service_id])

            for service_id in order:
                canonical = self._container_name(
                    app.id,
                    service_id,
                    public=service_id == bundle.public_service,
                )
                self.docker.rename_container(
                    old_ids[service_id], f"{canonical}-rollback-{old_ids[service_id][:12]}"
                )
                old_renamed.append(service_id)
            for service_id in order:
                self.docker.rename_container(
                    candidate_ids[service_id],
                    self._container_name(
                        app.id,
                        service_id,
                        public=service_id == bundle.public_service,
                    ),
                )
        except Exception as update_error:
            emit_hub_progress(self.progress, "rolling-back", "restoring-state")
            rollback_errors = self._rollback(
                app=app,
                order=order,
                old_ids=old_ids,
                was_running=was_running,
                old_renamed=old_renamed,
                candidate_ids=candidate_ids,
                created_networks=created_networks,
                backups=backups,
                restore_data=snapshots_ready and candidate_start_attempted,
            )
            if rollback_errors:
                raise HubBundleInstallRejected(
                    "Hub bundle update failed and automatic rollback could not finish; "
                    "old services remain stopped and data snapshots are retained"
                ) from update_error
            self._remove_snapshots(backups)
            raise

        try:
            for service_id in reversed(order):
                self.docker.remove_container(old_ids[service_id], force=False)
        except Exception as exc:
            raise HubBundleInstallRejected(
                "Hub bundle update succeeded, but old service cleanup needs manual attention"
            ) from exc

        old_network_cleanup_complete = True
        for network in reversed(bundle.networks):
            try:
                self.docker.remove_network(f"echo-hub-{app.id}-{network.name}-{old_plan_id[:12]}")
            except Exception:
                old_network_cleanup_complete = False
        self._remove_snapshots(backups)

        public_id = candidate_ids[bundle.public_service]
        public_image = service_definitions[bundle.public_service].image
        return {
            "schema": "echo.hub.update-result.v1",
            "appId": app.id,
            "previousContainerId": old_ids[bundle.public_service][:12],
            "containerId": public_id[:12],
            "serviceContainerIds": {
                service_id: container_id[:12] for service_id, container_id in candidate_ids.items()
            },
            "previousImage": old_public_image,
            "image": public_image,
            "state": "running" if was_running[bundle.public_service] else "stopped",
            "dataVolumesRetained": True,
            "secretVolumesRetained": True,
            "nasDataRetained": True,
            "oldNetworkCleanupComplete": old_network_cleanup_complete,
            "rollback": {
                "oldServicesRestoredOnFailure": True,
                "dataVolumesRestoredOnFailure": True,
                "runningStatePreserved": True,
            },
        }

    def _rollback(
        self,
        *,
        app: HubApp,
        order: list[str],
        old_ids: dict[str, str],
        was_running: dict[str, bool],
        old_renamed: list[str],
        candidate_ids: dict[str, str],
        created_networks: list[str],
        backups: list[tuple[str, str]],
        restore_data: bool,
    ) -> list[Exception]:
        bundle = app.bundle
        if bundle is None:
            return [HubBundleInstallRejected("bundle disappeared during rollback")]
        errors: list[Exception] = []
        for service_id in reversed(order):
            container_id = candidate_ids.get(service_id)
            if container_id is None:
                continue
            with contextlib.suppress(Exception):
                self.docker.stop(container_id)
            try:
                self.docker.remove_container(container_id, force=True)
            except Exception as exc:
                errors.append(exc)
        for network_name in reversed(created_networks):
            try:
                self.docker.remove_network(network_name)
            except Exception as exc:
                errors.append(exc)
        if restore_data and not errors:
            for source, backup in backups:
                try:
                    self.docker.restore_volume(backup, source)
                except Exception as exc:
                    errors.append(exc)
                    break
        for service_id in reversed(old_renamed):
            try:
                self.docker.rename_container(
                    old_ids[service_id],
                    self._container_name(
                        app.id,
                        service_id,
                        public=service_id == bundle.public_service,
                    ),
                )
            except Exception as exc:
                errors.append(exc)
        if not errors:
            for service_id in order:
                if was_running[service_id]:
                    try:
                        self.docker.start(old_ids[service_id])
                    except Exception as exc:
                        errors.append(exc)
                        break
        return errors

    def _owned_services(self, app: HubApp) -> tuple[dict[str, dict[str, Any]], str, str]:
        bundle = app.bundle
        if bundle is None:
            raise HubBundleInstallRejected("Hub multi-container package is unavailable")
        owned = [
            container
            for container in self.docker.list_containers(include_stopped=True)
            if self._bundle_app_id(container) == app.id
        ]
        service_map: dict[str, dict[str, Any]] = {}
        installed_plan_ids: set[str] = set()
        installed_versions: set[str] = set()
        expected_services = {service.id for service in bundle.services}
        for container in owned:
            labels = container.get("Labels") or {}
            names = container.get("Names") or []
            if not isinstance(labels, dict) or not isinstance(names, list):
                raise HubBundleInstallRejected("Hub bundle update target is malformed")
            service_id = str(labels.get("sh.echo.hub.bundle-service") or "")
            installed_plan_id = str(labels.get("sh.echo.hub.plan-id") or "")
            installed_version = str(labels.get("sh.echo.hub.bundle-version") or "")
            expected_name = self._container_name(
                app.id, service_id, public=service_id == bundle.public_service
            )
            if (
                service_id in service_map
                or service_id not in expected_services
                or labels.get("sh.echo.hub.managed") != "true"
                or re.fullmatch(r"[0-9a-f]{64}", installed_plan_id) is None
                or re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", installed_version) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(labels.get("sh.echo.hub.bundle-digest") or ""))
                is None
                or f"/{expected_name}" not in names
            ):
                raise HubBundleInstallRejected(
                    "Hub bundle update target is incomplete or ambiguous"
                )
            if service_id == bundle.public_service and labels.get("sh.echo.hub.app-id") != app.id:
                raise HubBundleInstallRejected("Hub bundle public service identity is invalid")
            service_map[service_id] = container
            installed_plan_ids.add(installed_plan_id)
            installed_versions.add(installed_version)
        if (
            set(service_map) != expected_services
            or len(installed_plan_ids) != 1
            or len(installed_versions) != 1
        ):
            raise HubBundleInstallRejected("Hub bundle update target is incomplete or ambiguous")
        return (
            service_map,
            next(iter(installed_plan_ids)),
            next(iter(installed_versions)),
        )

    def _remove_snapshots(self, backups: list[tuple[str, str]]) -> None:
        for _source, backup in backups:
            with contextlib.suppress(Exception):
                self.docker.remove_volume(backup)


__all__ = ["HubBundleUpdater"]
