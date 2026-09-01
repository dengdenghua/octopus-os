"""Atomic installer for catalog-verified multi-container Hub bundles."""

from __future__ import annotations

import contextlib
import os
import re
import secrets as crypto_secrets
import string
import time
from typing import Any, Protocol

from appliance.hub.catalog import HubApp, HubCatalog
from appliance.hub.progress import (
    HubProgressCallback,
    emit_hub_progress,
    pull_image_with_progress,
)


class BundleEngine(Protocol):
    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]: ...

    def pull_image(self, image: str) -> None: ...

    def inspect_volume(self, name: str) -> dict[str, Any] | None: ...

    def create_volume(self, name: str, *, labels: dict[str, str]) -> bool: ...

    def remove_volume(self, name: str) -> None: ...

    def write_secret_volume(self, volume: str, files: dict[str, bytes]) -> None: ...

    def verify_secret_volume(self, volume: str, file_names: tuple[str, ...]) -> None: ...

    def ensure_nas_subdirectory(self, relative_path: str) -> str: ...

    def create_network(
        self,
        name: str,
        *,
        internal: bool,
        labels: dict[str, str],
    ) -> bool: ...

    def remove_network(self, name: str) -> None: ...

    def snapshot_volume(
        self,
        source: str,
        backup: str,
        *,
        labels: dict[str, str],
    ) -> None: ...

    def restore_volume(self, backup: str, destination: str) -> None: ...

    def create_container(self, name: str, config: dict[str, Any]) -> str: ...

    def start(self, container_id: str) -> None: ...

    def inspect_container(self, container_id: str) -> dict[str, Any] | None: ...

    def stop(self, container_id: str) -> None: ...

    def remove_container(self, container_id: str, *, force: bool = False) -> None: ...

    def rename_container(self, container_id: str, name: str) -> None: ...


class HubBundleInstallRejected(RuntimeError):
    """A bundle cannot be installed without crossing its verified boundary."""


class HubBundleInstaller:
    def __init__(
        self,
        catalog: HubCatalog,
        docker: BundleEngine,
        *,
        progress: HubProgressCallback | None = None,
    ) -> None:
        self.catalog = catalog
        self.docker = docker
        self.progress = progress

    def install(self, app: HubApp, *, plan_id: str) -> dict[str, Any]:
        bundle = app.bundle
        if bundle is None:
            raise HubBundleInstallRejected("Hub multi-container package is unavailable")

        data_volumes = self._volume_sources(app)
        service_secret_names = {
            service.id: tuple(mount.secret for mount in service.secrets)
            for service in bundle.services
            if service.secrets
        }
        secret_volumes = {
            service_id: f"echo-hub-{app.id}-secrets-{service_id}"
            for service_id in service_secret_names
        }
        data_existing = {
            name: self.docker.inspect_volume(docker_name) is not None
            for name, docker_name in data_volumes.items()
            if next(volume for volume in bundle.volumes if volume.name == name).source == "app-data"
        }
        secret_existing = {
            service_id: self.docker.inspect_volume(docker_name) is not None
            for service_id, docker_name in secret_volumes.items()
        }
        if data_existing and any(data_existing.values()) and not all(data_existing.values()):
            raise HubBundleInstallRejected("Hub bundle data volumes are incomplete")
        if secret_existing and any(secret_existing.values()) and not all(secret_existing.values()):
            raise HubBundleInstallRejected("Hub bundle secret volumes are incomplete")
        if any(data_existing.values()) and secret_existing and not all(secret_existing.values()):
            raise HubBundleInstallRejected(
                "Hub bundle data exists but its persistent secrets are unavailable"
            )

        created_containers: list[str] = []
        created_networks: list[str] = []
        created_volumes: list[str] = []
        container_ids: dict[str, str] = {}
        revealed_secrets: dict[str, str] = {}
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
            for volume in bundle.volumes:
                if volume.source != "app-data":
                    continue
                docker_name = data_volumes[volume.name]
                if self.docker.create_volume(
                    docker_name,
                    labels=self._volume_labels(app.id, volume.name, role="data"),
                ):
                    created_volumes.append(docker_name)

            generated = {
                secret.name: self._generate_secret(secret.generation, secret.bytes)
                for secret in bundle.secrets
            }
            if secret_existing and all(secret_existing.values()):
                for service_id, docker_name in secret_volumes.items():
                    self.docker.create_volume(
                        docker_name,
                        labels=self._volume_labels(app.id, service_id, role="secrets"),
                    )
                    self.docker.verify_secret_volume(
                        docker_name,
                        service_secret_names[service_id],
                    )
                generated = {}
            else:
                for service_id, docker_name in secret_volumes.items():
                    if not self.docker.create_volume(
                        docker_name,
                        labels=self._volume_labels(app.id, service_id, role="secrets"),
                    ):
                        raise HubBundleInstallRejected(
                            "Hub bundle secret volume state changed during install"
                        )
                    created_volumes.append(docker_name)
                    self.docker.write_secret_volume(
                        docker_name,
                        {
                            name: generated[name].encode("ascii")
                            for name in service_secret_names[service_id]
                        },
                    )
                revealed_secrets = {
                    secret.name: generated[secret.name]
                    for secret in bundle.secrets
                    if secret.reveal_once
                }

            network_names: dict[str, str] = {}
            for network in bundle.networks:
                docker_name = f"echo-hub-{app.id}-{network.name}-{plan_id[:12]}"
                network_names[network.name] = docker_name
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

            service_map = {service.id: service for service in bundle.services}
            for service_id in bundle.upgrade_policy.service_order:
                service = service_map[service_id]
                name = self._container_name(
                    app.id,
                    service.id,
                    public=service.id == bundle.public_service,
                )
                config = self._container_config(
                    app,
                    service_id=service.id,
                    plan_id=plan_id,
                    data_volumes=data_volumes,
                    secret_volume=secret_volumes.get(service.id),
                    network_names=network_names,
                )
                container_id = self.docker.create_container(name, config)
                container_ids[service.id] = container_id
                created_containers.append(container_id)

            service_count = len(bundle.upgrade_policy.service_order)
            emit_hub_progress(
                self.progress,
                "starting",
                "starting-services",
                completed=0,
                total=service_count,
                unit="services",
            )
            for index, service_id in enumerate(bundle.upgrade_policy.service_order, start=1):
                service = service_map[service_id]
                container_id = container_ids[service_id]
                self.docker.start(container_id)
                emit_hub_progress(
                    self.progress,
                    "starting",
                    "starting-services",
                    completed=index,
                    total=service_count,
                    unit="services",
                )
                emit_hub_progress(
                    self.progress,
                    "verifying",
                    "checking-health",
                    completed=index - 1,
                    total=service_count,
                    unit="services",
                )
                self._wait_ready(container_id, service)
                emit_hub_progress(
                    self.progress,
                    "verifying",
                    "checking-health",
                    completed=index,
                    total=service_count,
                    unit="services",
                )
        except Exception as install_error:
            emit_hub_progress(self.progress, "rolling-back", "restoring-state")
            cleanup_errors: list[Exception] = []
            for container_id in reversed(created_containers):
                with contextlib.suppress(Exception):
                    self.docker.stop(container_id)
                try:
                    self.docker.remove_container(container_id, force=True)
                except Exception as exc:
                    cleanup_errors.append(exc)
            for network_name in reversed(created_networks):
                try:
                    self.docker.remove_network(network_name)
                except Exception as exc:
                    cleanup_errors.append(exc)
            for volume_name in reversed(created_volumes):
                try:
                    self.docker.remove_volume(volume_name)
                except Exception as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                raise HubBundleInstallRejected(
                    "Hub bundle install failed and automatic cleanup could not finish"
                ) from install_error
            raise

        public_id = container_ids[bundle.public_service]
        public_image = service_map[bundle.public_service].image
        return {
            "schema": "echo.hub.install-result.v1",
            "appId": app.id,
            "planId": plan_id,
            "catalogDigest": self.catalog.digest,
            "containerId": public_id[:12],
            "serviceContainerIds": {
                service_id: container_id[:12] for service_id, container_id in container_ids.items()
            },
            "state": "running",
            "image": public_image,
            "revealedSecrets": revealed_secrets,
            "rollback": {
                "newContainersRemovedOnFailure": True,
                "newNetworksRemovedOnFailure": True,
                "newVolumesRemovedOnFailure": True,
                "retainedVolumesPreserved": True,
            },
        }

    def uninstall(self, app: HubApp) -> dict[str, Any]:
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
        for container in owned:
            labels = container.get("Labels") or {}
            names = container.get("Names") or []
            if not isinstance(labels, dict) or not isinstance(names, list):
                raise HubBundleInstallRejected("Hub bundle uninstall target is malformed")
            service_id = str(labels.get("sh.echo.hub.bundle-service") or "")
            plan_id = str(labels.get("sh.echo.hub.plan-id") or "")
            expected_name = self._container_name(
                app.id,
                service_id,
                public=service_id == bundle.public_service,
            )
            if (
                service_id in service_map
                or service_id not in {service.id for service in bundle.services}
                or labels.get("sh.echo.hub.managed") != "true"
                or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(labels.get("sh.echo.hub.bundle-digest") or ""))
                is None
                or f"/{expected_name}" not in names
            ):
                raise HubBundleInstallRejected(
                    "Hub bundle uninstall target is incomplete or ambiguous"
                )
            if service_id == bundle.public_service and labels.get("sh.echo.hub.app-id") != app.id:
                raise HubBundleInstallRejected("Hub bundle public service identity is invalid")
            service_map[service_id] = container
            installed_plan_ids.add(plan_id)
        expected_services = {service.id for service in bundle.services}
        if set(service_map) != expected_services or len(installed_plan_ids) != 1:
            raise HubBundleInstallRejected("Hub bundle uninstall target is incomplete or ambiguous")

        order = list(bundle.upgrade_policy.service_order)
        was_running = {
            service_id: str(service_map[service_id].get("State") or "").casefold() == "running"
            for service_id in order
        }
        stopped: list[str] = []
        try:
            emit_hub_progress(
                self.progress,
                "stopping",
                "stopping-services",
                completed=0,
                total=len(order),
                unit="services",
            )
            for index, service_id in enumerate(reversed(order), start=1):
                container_id = str(service_map[service_id].get("Id") or "")
                self.docker.stop(container_id)
                stopped.append(service_id)
                emit_hub_progress(
                    self.progress,
                    "stopping",
                    "stopping-services",
                    completed=index,
                    total=len(order),
                    unit="services",
                )
        except Exception:
            emit_hub_progress(self.progress, "rolling-back", "restoring-state")
            for service_id in reversed(stopped):
                if was_running[service_id]:
                    with contextlib.suppress(Exception):
                        self.docker.start(str(service_map[service_id].get("Id") or ""))
            raise

        removed: list[str] = []
        try:
            emit_hub_progress(
                self.progress,
                "removing",
                "removing-services",
                completed=0,
                total=len(order),
                unit="services",
            )
            for index, service_id in enumerate(reversed(order), start=1):
                container_id = str(service_map[service_id].get("Id") or "")
                self.docker.remove_container(container_id, force=False)
                removed.append(service_id)
                emit_hub_progress(
                    self.progress,
                    "removing",
                    "removing-services",
                    completed=index,
                    total=len(order),
                    unit="services",
                )
        except Exception as exc:
            emit_hub_progress(self.progress, "rolling-back", "restoring-state")
            for service_id in order:
                if service_id not in removed and was_running[service_id]:
                    with contextlib.suppress(Exception):
                        self.docker.start(str(service_map[service_id].get("Id") or ""))
            raise HubBundleInstallRejected(
                "Hub bundle removal stopped after Docker rejected a verified container"
            ) from exc

        installed_plan_id = next(iter(installed_plan_ids))
        network_cleanup_complete = True
        for network in reversed(bundle.networks):
            try:
                self.docker.remove_network(
                    f"echo-hub-{app.id}-{network.name}-{installed_plan_id[:12]}"
                )
            except Exception:
                network_cleanup_complete = False

        public = service_map[bundle.public_service]
        public_id = str(public.get("Id") or "")
        return {
            "schema": "echo.hub.uninstall-result.v1",
            "appId": app.id,
            "containerId": public_id[:12],
            "serviceContainerIds": {
                service_id: str(container.get("Id") or "")[:12]
                for service_id, container in service_map.items()
            },
            "state": "not-installed",
            "dataVolumesRetained": True,
            "secretVolumesRetained": True,
            "nasDataRetained": True,
            "networkCleanupComplete": network_cleanup_complete,
        }

    @staticmethod
    def _bundle_app_id(container: dict[str, Any]) -> str | None:
        labels = container.get("Labels") or {}
        if not isinstance(labels, dict):
            return None
        value = str(labels.get("sh.echo.hub.bundle-app-id") or "").strip()
        return value or None

    @staticmethod
    def _container_name(app_id: str, service_id: str, *, public: bool) -> str:
        if public:
            return f"echo-hub-{app_id}"
        return f"echo-hub-{app_id}--{service_id}"

    @staticmethod
    def _volume_labels(app_id: str, logical_name: str, *, role: str) -> dict[str, str]:
        return {
            "sh.echo.hub.managed": "true",
            "sh.echo.hub.bundle-app-id": app_id,
            "sh.echo.hub.bundle-volume": logical_name,
            "sh.echo.hub.bundle-volume-role": role,
        }

    def _volume_sources(self, app: HubApp) -> dict[str, str]:
        bundle = app.bundle
        if bundle is None:
            raise HubBundleInstallRejected("Hub multi-container package is unavailable")
        sources: dict[str, str] = {}
        for volume in bundle.volumes:
            if volume.source == "app-data":
                sources[volume.name] = f"echo-hub-{app.id}-{volume.name}"
            else:
                if volume.relative_path is None:  # defensive after catalog validation
                    raise HubBundleInstallRejected("Hub NAS volume path is unavailable")
                sources[volume.name] = self.docker.ensure_nas_subdirectory(volume.relative_path)
        return sources

    def _container_config(
        self,
        app: HubApp,
        *,
        service_id: str,
        plan_id: str,
        data_volumes: dict[str, str],
        secret_volume: str | None,
        network_names: dict[str, str],
    ) -> dict[str, Any]:
        bundle = app.bundle
        if bundle is None:  # defensive
            raise HubBundleInstallRejected("Hub multi-container package is unavailable")
        service = next(item for item in bundle.services if item.id == service_id)
        public = service.id == bundle.public_service
        labels = {
            "sh.echo.hub.managed": "true",
            "sh.echo.hub.bundle-app-id": app.id,
            "sh.echo.hub.bundle-service": service.id,
            "sh.echo.hub.bundle-service-count": str(len(bundle.services)),
            "sh.echo.hub.bundle-version": bundle.upgrade_policy.application_version,
            "sh.echo.hub.version": app.version,
            "sh.echo.hub.catalog-digest": self.catalog.digest,
            "sh.echo.hub.package-digest": bundle.digest,
            "sh.echo.hub.bundle-digest": bundle.digest,
            "sh.echo.hub.plan-id": plan_id,
        }
        if public:
            labels.update(
                {
                    "sh.echo.hub.app-id": app.id,
                    "sh.echo.name": app.name_zh,
                    "sh.echo.description": app.summary,
                }
            )
        else:
            labels["sh.echo.hide"] = "1"

        volume_definitions = {volume.name: volume for volume in bundle.volumes}
        mounts = []
        for mount in service.mounts:
            definition = volume_definitions[mount.volume]
            item: dict[str, Any] = {
                "Type": "volume" if definition.source == "app-data" else "bind",
                "Source": data_volumes[mount.volume],
                "Target": mount.target,
                "ReadOnly": mount.read_only,
            }
            if definition.source == "nas-data":
                item["BindOptions"] = {"Propagation": "rprivate"}
            mounts.append(item)
        if secret_volume is not None:
            mounts.append(
                {
                    "Type": "volume",
                    "Source": secret_volume,
                    "Target": "/run/secrets",
                    "ReadOnly": True,
                }
            )

        host_network = service.network_mode == "host"
        exposed = (
            {}
            if host_network
            else {f"{port.container}/{port.protocol}": {} for port in service.ports}
        )
        port_bindings = (
            {}
            if host_network
            else {
                f"{port.container}/{port.protocol}": [
                    {"HostIp": "0.0.0.0", "HostPort": str(port.host)}  # nosec B104
                ]
                for port in service.ports
            }
        )
        cap_add = {
            "unprivileged": [],
            "data-root-dropper": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
            "web-root-dropper": [
                "CHOWN",
                "DAC_OVERRIDE",
                "FOWNER",
                "NET_BIND_SERVICE",
                "SETGID",
                "SETUID",
            ],
        }[service.runtime.profile]
        host_config: dict[str, Any] = {
            "PortBindings": port_bindings,
            "Mounts": mounts,
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "ReadonlyRootfs": service.runtime.read_only_rootfs,
            "PidsLimit": service.runtime.pids,
            "Memory": service.runtime.memory_mib * 1024 * 1024,
            "ShmSize": service.runtime.shm_size_mib * 1024 * 1024,
            "Init": True,
        }
        if cap_add:
            host_config["CapAdd"] = cap_add
        if host_network:
            host_config["NetworkMode"] = "host"
        config: dict[str, Any] = {
            "Image": service.image,
            "Env": [
                f"{key}={self._effective_environment(key, value)}"
                for key, value in service.environment
            ],
            "Labels": labels,
            "ExposedPorts": exposed,
            "HostConfig": host_config,
            "StopTimeout": 60,
        }
        if not host_network:
            config["Hostname"] = f"echo-{app.id}-{service.id}"
            config["NetworkingConfig"] = {
                "EndpointsConfig": {
                    network_names[name]: {"Aliases": [service.id]} for name in service.networks
                }
            }
        if service.secret_environment:
            script = "\n".join(
                [
                    *(
                        f'export {key}="$(cat /run/secrets/{secret_name})"'
                        for key, secret_name in service.secret_environment
                    ),
                    'exec "$@"',
                ]
            )
            config["Entrypoint"] = [
                "/bin/sh",
                "-c",
                script,
                "echo-secret-entrypoint",
            ]
            config["Cmd"] = [*service.entrypoint, *service.command]
        elif service.entrypoint:
            config["Entrypoint"] = list(service.entrypoint)
        if service.command and not service.secret_environment:
            config["Cmd"] = list(service.command)
        if service.healthcheck is not None:
            config["Healthcheck"] = {
                "Test": ["CMD", *service.healthcheck.command],
                "Interval": service.healthcheck.interval_seconds * 1_000_000_000,
                "Timeout": service.healthcheck.timeout_seconds * 1_000_000_000,
                "Retries": service.healthcheck.retries,
                "StartPeriod": service.healthcheck.start_period_seconds * 1_000_000_000,
            }
        return config

    @staticmethod
    def _effective_environment(key: str, value: str) -> str:
        """Resolve the three bounded host identity sentinels used by trusted bundles."""

        if value != "system":
            return value
        if key in {"TZ", "PAPERLESS_TIME_ZONE"}:
            return os.environ.get("TZ", "UTC")
        identity_name = {
            "PUID": "ECHO_PUID",
            "PGID": "ECHO_PGID",
            "USERMAP_UID": "ECHO_PUID",
            "USERMAP_GID": "ECHO_PGID",
        }.get(key)
        if identity_name is None:
            return value
        configured = os.environ.get(identity_name, "1000")
        try:
            numeric = int(configured, 10)
        except (TypeError, ValueError) as exc:
            raise HubBundleInstallRejected(
                f"Hub appliance identity {identity_name} is invalid"
            ) from exc
        if str(numeric) != configured or not 100 <= numeric <= 2_147_483_647:
            raise HubBundleInstallRejected(f"Hub appliance identity {identity_name} is invalid")
        return configured

    @staticmethod
    def _generate_secret(generation: str, size: int) -> str:
        if generation == "random-base64url":
            return crypto_secrets.token_urlsafe(size)
        if generation == "random-alphanumeric":
            alphabet = string.ascii_letters + string.digits
            return "".join(crypto_secrets.choice(alphabet) for _ in range(size))
        raise HubBundleInstallRejected("Hub bundle secret generation is unsupported")

    def _wait_ready(self, container_id: str, service: Any) -> None:
        healthcheck = service.healthcheck
        timeout_seconds = 30
        if healthcheck is not None:
            timeout_seconds = min(
                900,
                max(
                    30,
                    healthcheck.start_period_seconds
                    + healthcheck.interval_seconds * (healthcheck.retries + 2),
                ),
            )
        deadline = time.monotonic() + timeout_seconds
        while True:
            inspected = self.docker.inspect_container(container_id)
            state = inspected.get("State") if isinstance(inspected, dict) else None
            if not isinstance(state, dict) or state.get("Running") is not True:
                raise HubBundleInstallRejected(
                    f"Hub bundle service {service.id} did not stay running"
                )
            if healthcheck is None:
                return
            health = state.get("Health")
            status = health.get("Status") if isinstance(health, dict) else None
            if status == "healthy":
                return
            if status == "unhealthy":
                raise HubBundleInstallRejected(f"Hub bundle service {service.id} became unhealthy")
            if time.monotonic() >= deadline:
                raise HubBundleInstallRejected(
                    f"Hub bundle service {service.id} health check timed out"
                )
            time.sleep(0.25)


__all__ = ["HubBundleInstallRejected", "HubBundleInstaller"]
