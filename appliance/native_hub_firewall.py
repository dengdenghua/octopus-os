"""Derive strict firewalld forwarding from catalog-owned Hub containers."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from appliance import native_firewall
from appliance.app_registry.docker_client import DockerClient, DockerUnavailable
from appliance.hub.catalog import HubApp, HubCatalog
from appliance.hub.runtime import owned_hub_services

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_PLAN_ID = re.compile(r"[0-9a-f]{64}")
_PRIVATE_V4 = tuple(
    ipaddress.ip_network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def _private_ipv4(value: Any) -> str:
    if not isinstance(value, str):
        raise OSError("Hub container has no bridge address")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise OSError("Hub container bridge address is invalid") from exc
    if address.version != 4 or not any(address in network for network in _PRIVATE_V4):
        raise OSError("Hub container bridge address is not private IPv4")
    return str(address)


def _artifact(app: HubApp) -> tuple[Any, tuple[Any, ...], str]:
    if app.package is not None:
        return None, app.package.ports, "bridge"
    bundle = app.bundle
    if bundle is None:
        raise OSError("Hub application has no installable artifact")
    public = next(
        (service for service in bundle.services if service.id == bundle.public_service),
        None,
    )
    if public is None:
        raise OSError("Hub bundle has no public service")
    return public, public.ports, public.network_mode


def _validate_labels(
    app: HubApp,
    labels: Any,
    catalog: HubCatalog,
    *,
    service: Any,
) -> str:
    if not isinstance(labels, dict):
        raise OSError("Hub container labels are missing")
    artifact = app.package or app.bundle
    if artifact is None:
        raise OSError("Hub application has no trusted artifact")
    artifact_digest = artifact.digest
    plan_id = str(labels.get("sh.echo.hub.plan-id") or "")
    if (
        labels.get("sh.echo.hub.managed") != "true"
        or labels.get("sh.echo.hub.catalog-digest") != catalog.digest
        or labels.get("sh.echo.hub.package-digest") != artifact_digest
        or labels.get("sh.echo.hub.version") != app.version
        or _PLAN_ID.fullmatch(plan_id) is None
    ):
        raise OSError("Hub container identity differs from the trusted catalog")
    if service is None:
        if labels.get("sh.echo.hub.app-id") != app.id:
            raise OSError("Hub container identity differs from the trusted catalog")
    else:
        bundle = app.bundle
        if bundle is None or (
            labels.get("sh.echo.hub.bundle-app-id") != app.id
            or labels.get("sh.echo.hub.bundle-service") != service.id
            or labels.get("sh.echo.hub.bundle-digest") != artifact_digest
            or labels.get("sh.echo.hub.bundle-version") != app.version
            or (labels.get("sh.echo.hub.app-id") == app.id) != (service.id == bundle.public_service)
        ):
            raise OSError("Hub service identity differs from the trusted bundle")
    return plan_id


def _validate_bindings(inspected: dict[str, Any], ports: tuple[Any, ...], mode: str) -> None:
    network = inspected.get("NetworkSettings")
    actual = network.get("Ports") if isinstance(network, dict) else None
    if not isinstance(actual, dict):
        raise OSError("Hub container port bindings are unavailable")
    expected = {
        f"{port.container}/{port.protocol}": [{"HostIp": "0.0.0.0", "HostPort": str(port.host)}]
        for port in ports
    }
    published = {key: value for key, value in actual.items() if value}
    if mode == "host":
        if published:
            raise OSError("host-network Hub container unexpectedly publishes Docker ports")
    elif published != expected:
        raise OSError("Hub container port bindings differ from the trusted catalog")


def _bridge_address(
    inspected: dict[str, Any],
    app: HubApp,
    *,
    service: Any,
    plan_id: str,
) -> str:
    network = inspected.get("NetworkSettings")
    if not isinstance(network, dict):
        raise OSError("Hub container network state is unavailable")
    networks = network.get("Networks")
    if not isinstance(networks, dict):
        raise OSError("Hub container network endpoints are unavailable")
    if service is None:
        endpoint = networks.get("bridge")
        fallback = network.get("IPAddress")
    else:
        if not service.networks:
            raise OSError("bridged Hub public service has no trusted network")
        name = f"echo-hub-{app.id}-{service.networks[0]}-{plan_id[:12]}"
        endpoint = networks.get(name)
        fallback = None
    if isinstance(endpoint, dict):
        return _private_ipv4(endpoint.get("IPAddress"))
    return _private_ipv4(fallback)


def desired_forwards(catalog: HubCatalog, docker: DockerClient) -> list[dict[str, Any]]:
    """Return exact rules for complete, running and catalog-identical applications."""

    containers = docker.list_containers(include_stopped=True)
    forwards: list[dict[str, Any]] = []
    for app in catalog.apps:
        candidates = [
            container
            for container in containers
            if isinstance(container, dict)
            and isinstance(container.get("Labels"), dict)
            and (
                container["Labels"].get("sh.echo.hub.app-id") == app.id
                or container["Labels"].get("sh.echo.hub.bundle-app-id") == app.id
            )
        ]
        if not candidates:
            continue
        owned = owned_hub_services(app, containers)
        if not owned:
            raise OSError("Hub container ownership is incomplete or ambiguous")
        service, ports, mode = _artifact(app)
        inspected = None
        plan_id = None
        inspected_plans: set[str] = set()
        for definition, container in owned:
            container_id = str(container.get("Id") or "")
            if _CONTAINER_ID.fullmatch(container_id) is None:
                raise OSError("Hub service container id is invalid")
            current = docker.inspect_container(container_id)
            if not isinstance(current, dict):
                raise OSError("Hub service container inspection is unavailable")
            public_service = service is None or definition.id == service.id
            expected_name = (
                f"/echo-hub-{app.id}" if public_service else f"/echo-hub-{app.id}--{definition.id}"
            )
            if current.get("Id") != container_id or current.get("Name") != expected_name:
                raise OSError("Hub service container inspection changed identity")
            config = current.get("Config")
            if not isinstance(config, dict):
                raise OSError("Hub service container configuration is unavailable")
            current_plan = _validate_labels(
                app,
                config.get("Labels"),
                catalog,
                service=definition,
            )
            inspected_plans.add(current_plan)
            if public_service:
                inspected = current
                plan_id = current_plan
        if inspected is None or plan_id is None or inspected_plans != {plan_id}:
            raise OSError("Hub application has no consistent owned public container")
        state = inspected.get("State")
        if not isinstance(state, dict) or state.get("Running") is not True:
            continue
        host_config = inspected.get("HostConfig")
        actual_mode = host_config.get("NetworkMode") if isinstance(host_config, dict) else None
        if mode == "host":
            if actual_mode != "host":
                raise OSError("Hub container network mode differs from the trusted catalog")
            address = None
        else:
            if actual_mode == "host":
                raise OSError("Hub container network mode differs from the trusted catalog")
            address = _bridge_address(
                inspected,
                app,
                service=service,
                plan_id=plan_id,
            )
        _validate_bindings(inspected, ports, mode)
        forwards.extend(
            {
                "appId": app.id,
                "hostPort": port.host,
                "containerPort": port.container,
                "protocol": port.protocol,
                "mode": mode,
                "address": address,
            }
            for port in ports
        )
    return native_firewall.desired_state(
        smb=False,
        nfs_clients=[],
        hub_forwards=forwards,
    )["hubForwards"]


def sync() -> dict[str, Any]:
    """Reconcile Hub forwarding; close all Hub ports when derivation is unsafe."""

    docker = DockerClient(base_url="", allow_direct_socket=True)
    try:
        forwards = desired_forwards(HubCatalog.load(), docker)
    except (DockerUnavailable, OSError, ValueError):
        native_firewall.sync_hub([])
        raise
    return native_firewall.sync_hub(forwards)


__all__ = ["desired_forwards", "sync"]
