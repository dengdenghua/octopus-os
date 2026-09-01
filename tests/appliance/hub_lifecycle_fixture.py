from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deploy.appliance import hub_lifecycle_lab as lab

ROOT = Path(__file__).resolve().parents[2]


def catalog_response(
    *, architecture: str = "amd64", installed: tuple[str, ...] = ()
) -> dict[str, Any]:
    source = json.loads((ROOT / "appliance/hub/catalog.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    installed_set = set(installed)
    apps = copy.deepcopy(source["apps"])
    for app in apps:
        is_installed = app["id"] in installed_set
        app["installation"] = {
            "installed": is_installed,
            "containerId": "a" * 12 if is_installed else None,
            "state": "running" if is_installed else "not-installed",
            "status": "Up 10 seconds (healthy)" if is_installed else "",
            "image": None,
        }
        app["installable"] = not is_installed
        app["installBlockers"] = ["PORT_IN_USE", "ALREADY_INSTALLED"] if is_installed else []
        app["updateAvailable"] = False
    return {
        "schema": "echo.hub.catalog-response.v1",
        "version": source["version"],
        "digest": digest,
        "publisher": source["publisher"],
        "architecture": architecture,
        "runtime": {"available": True, "error": None},
        "total": len(apps),
        "apps": apps,
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    source = candidate["source"]
    appliance = candidate["evidence"]["appliance"]
    operations = appliance["operationsBundle"]
    return {
        "indexPath": "/root/echo-delivery-release-evidence-index.json",
        "indexId": candidate["indexId"],
        "indexSha256": "9" * 64,
        "osRepository": source["repository"],
        "sourceRevision": source["commit"],
        "agentRepository": source["agentRepository"],
        "agentRevision": source["agentCommit"],
        "releaseTag": source["releaseTag"],
        "applianceManifestSha256": appliance["manifestSha256"],
        "immutableReference": appliance["immutableReference"],
        "operationsArtifactId": operations["artifactId"],
        "operationsArchiveSha256": operations["sha256"],
    }


def _installation(app_id: str, contract: Mapping[str, Any], *, cycle: int) -> dict[str, Any]:
    volume_definitions = {volume["name"]: volume for volume in contract["volumes"]}
    services: dict[str, Any] = {}
    for service in contract["services"]:
        mounts = []
        for mount in service["mounts"]:
            logical_name = mount.get("name") or mount.get("volume")
            volume = volume_definitions[logical_name]
            if volume["source"] == "app-data":
                mount_type = "volume"
                source = f"echo-hub-{app_id}-{logical_name}"
            else:
                mount_type = "bind"
                relative = volume["relativePath"]
                source = "/srv/echo-nas" if relative is None else f"/srv/echo-nas/{relative}"
            mounts.append(
                {
                    "type": mount_type,
                    "sourceSha256": _sha256(source),
                    "destination": mount["target"],
                    "rw": not mount["readOnly"],
                }
            )
        if service["hasSecrets"]:
            source = f"echo-hub-{app_id}-secrets-{service['id']}"
            mounts.append(
                {
                    "type": "volume",
                    "sourceSha256": _sha256(source),
                    "destination": "/run/secrets",
                    "rw": False,
                }
            )
        services[service["id"]] = {
            "containerId": _sha256(f"{app_id}:{service['id']}:{cycle}")[:12],
            "image": service["image"],
            "running": True,
            "healthy": True if service["healthcheck"] else None,
            "mounts": sorted(mounts, key=lambda item: (item["destination"], item["type"])),
            "ports": (
                {}
                if service.get("networkMode") == "host"
                else {
                    f"{port['container']}/{port['protocol']}": [
                        {"HostIp": "0.0.0.0", "HostPort": str(port["host"])}
                    ]
                    for port in service["ports"]
                }
            ),
            "networks": [
                {
                    "name": network_name,
                    "internal": next(
                        network["internal"]
                        for network in contract["networks"]
                        if network["name"] == network_name
                    ),
                    "id": _sha256(f"{app_id}:{network_name}")[:12],
                }
                for network_name in service["networks"]
            ],
        }
    volumes: dict[str, Any] = {}
    for volume in contract["volumes"]:
        if volume["source"] != "app-data":
            continue
        logical_name = volume["name"]
        name = f"echo-hub-{app_id}-{logical_name}"
        labels = (
            {
                "sh.echo.hub.managed": "true",
                "sh.echo.hub.app-id": app_id,
                "sh.echo.hub.volume-name": logical_name,
            }
            if contract["kind"] == "package"
            else {
                "sh.echo.hub.managed": "true",
                "sh.echo.hub.bundle-app-id": app_id,
                "sh.echo.hub.bundle-volume": logical_name,
                "sh.echo.hub.bundle-volume-role": "data",
            }
        )
        volumes[logical_name] = {
            "name": name,
            "mountpointSha256": _sha256(f"/var/lib/docker/volumes/{name}/_data"),
            "labels": labels,
        }
    for service in contract["services"]:
        if not service["hasSecrets"]:
            continue
        logical_name = f"secrets-{service['id']}"
        name = f"echo-hub-{app_id}-{logical_name}"
        volumes[logical_name] = {
            "name": name,
            "mountpointSha256": _sha256(f"/var/lib/docker/volumes/{name}/_data"),
            "labels": {
                "sh.echo.hub.managed": "true",
                "sh.echo.hub.bundle-app-id": app_id,
                "sh.echo.hub.bundle-volume": service["id"],
                "sh.echo.hub.bundle-volume-role": "secrets",
            },
        }
    return {"services": services, "volumes": volumes}


def _runtime_health(contract: Mapping[str, Any]) -> dict[str, Any]:
    service_count = len(contract["services"])
    return {
        "status": "healthy",
        "serviceCount": service_count,
        "runningServices": service_count,
        "healthyServices": service_count,
        "restartCount": 0,
        "cpuPercent": service_count * 1.5,
        "memoryUsageBytes": service_count * 128 * 1024**2,
        "memoryLimitBytes": service_count * 1024 * 1024**2,
        "pids": service_count * 12,
    }


def _public_endpoint(contract: Mapping[str, Any], *, cycle: int) -> dict[str, Any]:
    sample = f"echo-hub-ready:{contract['endpoint']['port']}:{cycle}".encode()
    return {
        **contract["endpoint"],
        "status": 200,
        "mediaType": "text/html",
        "sampleBytes": len(sample),
        "sampleSha256": hashlib.sha256(sample).hexdigest(),
        "sampleTruncated": False,
        "attempts": 1,
    }


def _control_operation(
    *,
    app_id: str,
    operation: str,
    service_order: list[str],
    catalog_digest: str,
    public_container_id: str,
) -> dict[str, Any]:
    plan_id = _sha256(f"control-plan:{app_id}:{operation}")
    return {
        "operation": operation,
        "operationId": _sha256(f"control-operation:{app_id}:{operation}")[:32],
        "planId": plan_id,
        "serviceOrder": service_order,
        "result": {
            "schema": f"echo.hub.{operation}-result.v1",
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "containerId": public_container_id,
            "state": "stopped" if operation == "stop" else "running",
            "serviceCount": len(service_order),
            "dataVolumesRetained": True,
            "nasDataRetained": True,
            "rollback": {"previousRunningStateRestoredOnFailure": True},
        },
    }


def _lifecycle_control(
    *,
    app_id: str,
    contract: Mapping[str, Any],
    installation: Mapping[str, Any],
    catalog_digest: str,
) -> dict[str, Any]:
    service_order = [service["id"] for service in contract["services"]]
    public_service = next(service for service in contract["services"] if service["public"])
    public_container_id = installation["services"][public_service["id"]]["containerId"]
    operations = {
        operation: _control_operation(
            app_id=app_id,
            operation=operation,
            service_order=service_order,
            catalog_digest=catalog_digest,
            public_container_id=public_container_id,
        )
        for operation in ("stop", "start", "restart")
    }
    stopped_services = {
        service_id: {
            "state": "exited",
            "health": "healthy",
            "restartCount": 0,
            "oomKilled": False,
            "exitCode": 0,
        }
        for service_id in service_order
    }
    return {
        "serviceOrder": service_order,
        "stop": {
            "operation": operations["stop"],
            "containers": {
                service_id: {
                    "containerId": installation["services"][service_id]["containerId"],
                    "running": False,
                    "state": "exited",
                    "exitCode": 0,
                }
                for service_id in service_order
            },
            "runtime": {
                "status": "stopped",
                "serviceCount": len(service_order),
                "runningServices": 0,
                "healthyServices": 0,
                "restartCount": 0,
                "services": stopped_services,
                "diagnosticsStatus": "stopped",
            },
        },
        "start": {
            "operation": operations["start"],
            "installation": copy.deepcopy(installation),
            "publicEndpoint": _public_endpoint(contract, cycle=11),
            "runtimeHealth": _runtime_health(contract),
        },
        "restart": {
            "operation": operations["restart"],
            "installation": copy.deepcopy(installation),
            "publicEndpoint": _public_endpoint(contract, cycle=12),
            "runtimeHealth": _runtime_health(contract),
        },
    }


def hub_lifecycle_material(
    candidate: Mapping[str, Any],
    *,
    architecture: str = "amd64",
) -> tuple[bytes, bytes]:
    catalog = lab._catalog_snapshot(catalog_response(architecture=architecture))
    release = _candidate_identity(candidate)
    operations = {
        "artifactId": release["operationsArtifactId"],
        "archiveSha256": release["operationsArchiveSha256"],
        "imageReference": release["immutableReference"],
        "manifestSha256": "a" * 64,
        "labToolSha256": "b" * 64,
        "labToolSize": 8192,
        "rootPath": f"/root/echo-appliance-operations-{release['operationsArtifactId']}",
        "hubLabSha256": "c" * 64,
        "hubLabSize": 16384,
    }
    runtime = {
        "main": {"containerId": "d" * 64, "image": release["immutableReference"]},
        "proxy": {"containerId": "e" * 64, "image": release["immutableReference"]},
        "discovery": {"containerId": "f" * 64, "image": release["immutableReference"]},
    }
    identity = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": lab.PLAN_KIND,
        "baseUrl": "https://127.0.0.1:8000",
        "releaseCandidate": release,
        "operationsBundle": operations,
        "runtime": runtime,
        "baseline": {
            "namedVolumesAbsent": True,
            "nasDirectoriesAbsent": True,
            "checkedVolumeCount": len(lab._storage_volume_names(catalog)),
            "checkedNasDirectoryCount": len(
                {
                    volume["relativePath"]
                    for contract in catalog["apps"].values()
                    for volume in contract["volumes"]
                    if volume["source"] == "nas-data"
                }
            ),
        },
        "catalog": catalog,
        "apps": list(lab.APPS),
        "phases": list(lab.PHASES),
        "retention": {
            "namedVolumes": True,
            "nasData": True,
            "generatedSecrets": True,
        },
    }
    plan_id = hashlib.sha256(lab._canonical(identity)).hexdigest()
    plan = {
        **identity,
        "planId": plan_id,
        "confirmation": f"RUN ECHO HUB LIFECYCLE {plan_id}",
    }
    first: dict[str, Any] = {}
    second: dict[str, Any] = {}
    for app_id in lab.APPS:
        contract = catalog["apps"][app_id]
        first_installation = _installation(app_id, contract, cycle=1)
        first[app_id] = {
            "installation": first_installation,
            "revealedSecretNames": (
                ["admin-password"]
                if app_id in {"nextcloud", "qbittorrent", "syncthing", "paperless-ngx"}
                else []
            ),
            "publicEndpoint": _public_endpoint(contract, cycle=1),
            "runtimeHealth": _runtime_health(contract),
            "lifecycleControl": _lifecycle_control(
                app_id=app_id,
                contract=contract,
                installation=first_installation,
                catalog_digest=catalog["digest"],
            ),
        }
        second[app_id] = {
            "installation": _installation(app_id, contract, cycle=2),
            "revealedSecretNames": [],
            "publicEndpoint": _public_endpoint(contract, cycle=2),
            "runtimeHealth": _runtime_health(contract),
        }
    result: dict[str, Any] = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": lab.RESULT_KIND,
        "planId": plan_id,
        "releaseCandidate": release,
        "operationsBundle": operations,
        "runtime": runtime,
        "catalogDigest": catalog["digest"],
        "architecture": catalog["architecture"],
        "apps": list(lab.APPS),
        "firstInstall": first,
        "reinstall": second,
        "finalState": "not-installed-data-retained",
        "allPassed": True,
        "completedAtUnix": 1_700_000_000,
    }
    result["resultId"] = hashlib.sha256(lab._canonical(result)).hexdigest()
    plan_raw = lab._canonical(plan)
    result_raw = lab._canonical(result)
    lab.validate_evidence_bytes(plan_raw, result_raw, expected_candidate=release)
    return plan_raw, result_raw
