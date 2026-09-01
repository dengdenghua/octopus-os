from __future__ import annotations

import copy
import hashlib
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from appliance.hub import HubCatalog, HubService
from deploy.appliance import hub_lifecycle_lab as lab
from deploy.appliance import operations_bundle

REPOSITORY = Path(__file__).resolve().parents[2]
IMAGE_REFERENCE = f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}"


class _EmptyDocker:
    def ping(self) -> bool:
        return True

    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
        assert include_stopped is True
        return [
            {
                "Id": "e" * 64,
                "Image": IMAGE_REFERENCE,
                "State": "running",
                "Status": "Up 10 seconds (healthy)",
                "Names": ["/echo-lan-discovery"],
                "Labels": {"sh.echo.hub.lan-discovery-provider": "true"},
                "Ports": [],
            }
        ]

    def hub_storage_capacity(self) -> dict[str, Any]:
        return {
            "schema": "echo.hub.docker-storage.v1",
            "status": "observed",
            "totalBytes": 128 * 1024**3,
            "freeBytes": 64 * 1024**3,
            "usedPercent": 50.0,
        }


def _catalog() -> dict[str, Any]:
    return HubService(
        HubCatalog.load(),
        docker=_EmptyDocker(),
        architecture="amd64",
    ).list_catalog()


def _release(tmp_path: Path) -> tuple[Path, Path]:
    built = operations_bundle.build(REPOSITORY, tmp_path / "build", IMAGE_REFERENCE)
    extracted = operations_bundle.extract(Path(built["archive"]), tmp_path / "bundle")
    bundle_root = Path(extracted["destination"])
    candidate: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "echo.delivery-release-evidence-index",
        "source": {
            "repository": "dengdenghua/echo-os",
            "commit": "1" * 40,
            "agentRepository": "dengdenghua/echo-agent",
            "agentCommit": "2" * 40,
            "releaseTag": "echo-appliance-v1.0.0",
        },
        "evidence": {
            "candidatePreflight": {"reportId": "4" * 64},
            "appliance": {
                "manifestSha256": "5" * 64,
                "immutableReference": IMAGE_REFERENCE,
                "operationsBundle": {
                    "artifactId": built["artifactId"],
                    "sha256": built["archiveSha256"],
                    "imageReference": IMAGE_REFERENCE,
                },
            },
        },
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {"complete": False, "remainingGates": ["fixture"]},
    }
    unsigned = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    candidate["indexId"] = hashlib.sha256(unsigned).hexdigest()
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate, sort_keys=True) + "\n", encoding="utf-8")
    candidate_path.chmod(0o444)
    return candidate_path, bundle_root


def test_plan_binds_the_live_nine_app_catalog_and_exact_confirmation(tmp_path: Path) -> None:
    candidate, bundle_root = _release(tmp_path)
    output = tmp_path / "hub-lifecycle-plan.json"
    value = lab.build_plan(
        base_url="https://echo-nas.example:8443/",
        catalog=_catalog(),
        candidate_index=candidate,
        bundle_root=bundle_root,
        output=output,
        docker=_LifecycleDocker({}),
    )

    assert value["kind"] == lab.PLAN_KIND
    assert value["baseUrl"] == "https://echo-nas.example:8443"
    assert value["apps"] == list(lab.APPS)
    assert value["phases"] == list(lab.PHASES)
    assert set(value["catalog"]["apps"]) == set(lab.APPS)
    assert value["catalog"]["apps"]["immich"]["kind"] == "bundle"
    assert value["catalog"]["apps"]["open-webui"]["kind"] == "bundle"
    assert value["catalog"]["apps"]["qbittorrent"]["kind"] == "bundle"
    assert value["catalog"]["apps"]["syncthing"]["kind"] == "bundle"
    assert value["catalog"]["apps"]["paperless-ngx"]["kind"] == "bundle"
    assert value["catalog"]["apps"]["home-assistant"]["kind"] == "bundle"
    assert value["catalog"]["apps"]["jellyfin"]["kind"] == "package"
    open_webui = value["catalog"]["apps"]["open-webui"]
    assert [service["id"] for service in open_webui["services"]] == [
        "cache",
        "app",
    ]
    assert next(service for service in open_webui["services"] if service["id"] == "app")[
        "ports"
    ] == [{"container": 8080, "host": 3005, "protocol": "tcp"}]
    qbittorrent = value["catalog"]["apps"]["qbittorrent"]
    assert [service["id"] for service in qbittorrent["services"]] == ["app"]
    assert qbittorrent["volumes"] == [
        {
            "name": "config",
            "source": "app-data",
            "relativePath": None,
        },
        {
            "name": "downloads",
            "source": "nas-data",
            "relativePath": "downloads/qbittorrent",
        },
    ]
    syncthing = value["catalog"]["apps"]["syncthing"]
    assert syncthing["providers"] == ["lan-discovery"]
    assert [service["id"] for service in syncthing["services"]] == ["app"]
    assert syncthing["volumes"] == [
        {
            "name": "config",
            "source": "app-data",
            "relativePath": None,
        },
        {
            "name": "sync",
            "source": "nas-data",
            "relativePath": "sync/syncthing",
        },
    ]
    paperless = value["catalog"]["apps"]["paperless-ngx"]
    assert [service["id"] for service in paperless["services"]] == [
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    ]
    assert {
        volume["relativePath"] for volume in paperless["volumes"] if volume["source"] == "nas-data"
    } == {
        "documents/paperless/media",
        "documents/paperless/consume",
        "documents/paperless/export",
    }
    home_assistant = value["catalog"]["apps"]["home-assistant"]
    assert home_assistant["networks"] == []
    assert home_assistant["services"] == [
        {
            "id": "app",
            "image": (
                "ghcr.io/home-assistant/home-assistant@sha256:"
                "14931c6b13756317849f46da1d01b45937a1150db66c081cfe529d48215943fe"
            ),
            "public": True,
            "ports": [{"container": 8123, "host": 8123, "protocol": "tcp"}],
            "mounts": [{"volume": "config", "target": "/config", "readOnly": False}],
            "networks": [],
            "networkMode": "host",
            "hasSecrets": False,
            "runtime": {
                "profile": "unprivileged",
                "memoryMiB": 2048,
                "pids": 512,
                "shmSizeMiB": 64,
                "readOnlyRootfs": False,
            },
            "healthcheck": True,
        }
    ]
    assert value["baseline"]["checkedVolumeCount"] == len(
        lab._storage_volume_names(value["catalog"])
    )
    assert value["confirmation"] == f"RUN ECHO HUB LIFECYCLE {value['planId']}"
    assert value["releaseCandidate"]["immutableReference"] == IMAGE_REFERENCE
    assert (
        value["operationsBundle"]["hubLabSha256"]
        == hashlib.sha256((bundle_root / "hub_lifecycle_lab.py").read_bytes()).hexdigest()
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o400
    assert lab.load_plan(output) == value

    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["catalog"]["digest"] = "0" * 64
    output.chmod(0o600)
    output.write_text(json.dumps(changed), encoding="utf-8")
    output.chmod(0o400)
    with pytest.raises(lab.HubLifecycleLabError, match="identity"):
        lab.load_plan(output)


def test_plan_rejects_rehashed_legacy_five_app_contract(tmp_path: Path) -> None:
    candidate, bundle_root = _release(tmp_path)
    output = tmp_path / "hub-lifecycle-plan.json"
    lab.build_plan(
        base_url="https://echo-nas.example:8443/",
        catalog=_catalog(),
        candidate_index=candidate,
        bundle_root=bundle_root,
        output=output,
        docker=_LifecycleDocker({}),
    )
    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["apps"].remove("qbittorrent")
    del changed["catalog"]["apps"]["qbittorrent"]
    changed["baseline"]["checkedVolumeCount"] = len(lab._storage_volume_names(changed["catalog"]))
    identity = {
        key: value for key, value in changed.items() if key not in {"planId", "confirmation"}
    }
    changed["planId"] = hashlib.sha256(lab._canonical(identity)).hexdigest()
    changed["confirmation"] = f"RUN ECHO HUB LIFECYCLE {changed['planId']}"
    output.chmod(0o600)
    output.write_bytes(lab._canonical(changed))
    output.chmod(0o400)

    with pytest.raises(lab.HubLifecycleLabError, match="catalog snapshot"):
        lab.load_plan(output)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["apps"][0].__setitem__("installable", False),
        lambda value: value["apps"][0]["installation"].__setitem__("installed", True),
        lambda value: value.__setitem__("runtime", {"available": False, "error": "down"}),
    ],
)
def test_plan_refuses_blocked_installed_or_unavailable_catalogs(
    tmp_path: Path,
    mutation,
) -> None:
    catalog = _catalog()
    mutation(catalog)

    with pytest.raises(lab.HubLifecycleLabError, match="must be available|not ready"):
        lab._catalog_snapshot(catalog)


class _LifecycleDocker:
    def __init__(self, contracts: dict[str, Any]) -> None:
        self.contracts = contracts
        self.installed: set[str] = set()
        self.stopped: set[str] = set()
        self.privileged: set[str] = set()
        self.optional_volume_exists = False
        self.runtime_image = IMAGE_REFERENCE
        self.network_internal_override: bool | None = None
        self.wrong_mount_source: set[str] = set()

    def __call__(self, arguments) -> Any:
        command = tuple(arguments)
        if command[0] == "volume-inspect-optional":
            return [{}] if self.optional_volume_exists else []
        if command[:2] == ("network", "inspect"):
            docker_name = command[2]
            for app_id, contract in self.contracts.items():
                for definition in contract["networks"]:
                    logical_name = definition["name"]
                    if docker_name == f"echo-hub-{app_id}-{logical_name}-testplan1234":
                        return [
                            {
                                "Id": hashlib.sha256(docker_name.encode()).hexdigest(),
                                "Internal": (
                                    definition["internal"]
                                    if self.network_internal_override is None
                                    else self.network_internal_override
                                ),
                                "Labels": {
                                    "sh.echo.hub.bundle-app-id": app_id,
                                    "sh.echo.hub.bundle-network": logical_name,
                                },
                            }
                        ]
            raise lab.HubLifecycleLabError("missing test network")
        if command[:2] == ("volume", "inspect"):
            name = command[2]
            app_id = next(app for app in lab.APPS if name.startswith(f"echo-hub-{app}-"))
            logical_name = name.removeprefix(f"echo-hub-{app_id}-")
            if self.contracts[app_id]["kind"] == "package":
                labels = {
                    "sh.echo.hub.managed": "true",
                    "sh.echo.hub.app-id": app_id,
                    "sh.echo.hub.volume-name": logical_name,
                }
            elif logical_name.startswith("secrets-"):
                labels = {
                    "sh.echo.hub.managed": "true",
                    "sh.echo.hub.bundle-app-id": app_id,
                    "sh.echo.hub.bundle-volume": logical_name.removeprefix("secrets-"),
                    "sh.echo.hub.bundle-volume-role": "secrets",
                }
            else:
                labels = {
                    "sh.echo.hub.managed": "true",
                    "sh.echo.hub.bundle-app-id": app_id,
                    "sh.echo.hub.bundle-volume": logical_name,
                    "sh.echo.hub.bundle-volume-role": "data",
                }
            return [
                {
                    "Name": name,
                    "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
                    "Labels": labels,
                }
            ]
        if command[0] in {"inspect", "inspect-optional"}:
            name = command[1]
            if name in {"echo-os", "echo-docker-control", "echo-lan-discovery"}:
                labels = {"sh.echo.control-protected": "true"}
                if name == "echo-os":
                    labels["sh.echo.hub.nas-provider"] = "true"
                elif name == "echo-docker-control":
                    labels["sh.echo.hub.data-copy-provider"] = "true"
                else:
                    labels["sh.echo.hub.lan-discovery-provider"] = "true"
                container_id = {
                    "echo-os": "a" * 64,
                    "echo-docker-control": "b" * 64,
                    "echo-lan-discovery": "c" * 64,
                }[name]
                discovery = name == "echo-lan-discovery"
                return [
                    {
                        "Id": container_id,
                        "Config": {
                            "Image": self.runtime_image,
                            "Labels": labels,
                            "User": "65534:65534" if discovery else "0:0",
                        },
                        "State": {
                            "Running": True,
                            **({"Health": {"Status": "healthy"}} if discovery else {}),
                        },
                        "HostConfig": (
                            {
                                "NetworkMode": "host",
                                "Privileged": False,
                                "CapDrop": ["ALL"],
                                "SecurityOpt": ["no-new-privileges:true"],
                                "ReadonlyRootfs": True,
                                "PidsLimit": 32,
                                "Memory": 64 * 1024 * 1024,
                                "Tmpfs": {
                                    "/tmp": "rw,noexec,nosuid,nodev,size=16777216",
                                },
                            }
                            if discovery
                            else {}
                        ),
                        "Mounts": (
                            [
                                {
                                    "Type": "bind",
                                    "Source": "/srv/echo-nas",
                                    "Destination": "/data/nas",
                                    "RW": True,
                                }
                            ]
                            if name == "echo-os"
                            else []
                        ),
                    }
                ]
            resolved = self._resolve(name)
            if resolved is None:
                if command[0] == "inspect-optional":
                    return []
                raise lab.HubLifecycleLabError("missing test container")
            app_id, service = resolved
            if app_id not in self.installed:
                if command[0] == "inspect-optional":
                    return []
                raise lab.HubLifecycleLabError("missing test container")
            return [self._container(app_id, service, name)]
        raise AssertionError(command)

    def _resolve(self, name: str) -> tuple[str, dict[str, Any]] | None:
        for app_id, contract in self.contracts.items():
            for service in contract["services"]:
                expected = lab._expected_container_name(
                    app_id,
                    service["id"],
                    service["public"],
                )
                if name == expected:
                    return app_id, service
        return None

    def _container(
        self,
        app_id: str,
        service: dict[str, Any],
        name: str,
    ) -> dict[str, Any]:
        health = {"Status": "healthy"} if service["healthcheck"] else None
        mounts = [
            {
                "Type": mount_type,
                "Source": source,
                "Destination": destination,
                "RW": rw,
            }
            for mount_type, source, destination, rw in lab._expected_mounts(
                app_id=app_id,
                service=service,
                contract=self.contracts[app_id],
                nas_source="/srv/echo-nas",
            )
        ]
        if name in self.wrong_mount_source and mounts:
            mounts[0]["Source"] = "/srv/attacker-controlled"
        labels = {
            "sh.echo.hub.managed": "true",
            (
                "sh.echo.hub.app-id"
                if self.contracts[app_id]["kind"] == "package"
                else "sh.echo.hub.bundle-app-id"
            ): app_id,
        }
        if self.contracts[app_id]["kind"] == "bundle":
            labels["sh.echo.hub.bundle-service"] = service["id"]
            if service["public"]:
                labels["sh.echo.hub.app-id"] = app_id
            else:
                labels["sh.echo.hide"] = "1"
        running = app_id not in self.stopped
        state: dict[str, Any] = {
            "Running": running,
            "Status": "running" if running else "exited",
            "OOMKilled": False,
            "ExitCode": 0,
        }
        if health is not None:
            state["Health"] = health
        runtime = service["runtime"]
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
        }
        host: dict[str, Any] = {
            "Privileged": name in self.privileged,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Init": True,
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "PidsLimit": 512 if runtime is None else runtime["pids"],
            "PortBindings": (
                {}
                if service["networkMode"] == "host"
                else {
                    f"{port['container']}/{port['protocol']}": [
                        {"HostIp": "0.0.0.0", "HostPort": str(port["host"])}
                    ]
                    for port in service["ports"]
                }
            ),
        }
        if service["networkMode"] == "host":
            host["NetworkMode"] = "host"
        if runtime is not None:
            host.update(
                {
                    "CapAdd": cap_add[runtime["profile"]],
                    "Memory": runtime["memoryMiB"] * 1024 * 1024,
                    "ShmSize": runtime["shmSizeMiB"] * 1024 * 1024,
                    "ReadonlyRootfs": runtime["readOnlyRootfs"],
                }
            )
        networks = (
            {"host": {}}
            if service["networkMode"] == "host"
            else {
                f"echo-hub-{app_id}-{logical_name}-testplan1234": {}
                for logical_name in service["networks"]
            }
        )
        return {
            "Id": hashlib.sha256(name.encode()).hexdigest(),
            "Config": {"Image": service["image"], "Labels": labels},
            "HostConfig": host,
            "State": state,
            "Mounts": mounts,
            "NetworkSettings": {"Networks": networks},
        }


def test_installation_inspection_rejects_privilege_and_records_retained_volumes() -> None:
    snapshot = lab._catalog_snapshot(_catalog())
    docker = _LifecycleDocker(snapshot["apps"])
    docker.installed.add("immich")

    result = lab.inspect_installation("immich", snapshot["apps"]["immich"], docker)

    assert set(result["services"]) == {
        "cache",
        "database",
        "machine-learning",
        "server",
    }
    assert set(result["volumes"]) == {
        "database",
        "model-cache",
        "secrets-database",
        "secrets-server",
    }
    server_data = next(
        mount for mount in result["services"]["server"]["mounts"] if mount["destination"] == "/data"
    )
    assert server_data["sourceSha256"] == hashlib.sha256(b"/srv/echo-nas/photos/immich").hexdigest()

    docker.privileged.add("echo-hub-immich")
    with pytest.raises(lab.HubLifecycleLabError, match="unsafe runtime profile"):
        lab.inspect_installation("immich", snapshot["apps"]["immich"], docker)

    docker.privileged.clear()
    docker.network_internal_override = False
    with pytest.raises(lab.HubLifecycleLabError, match="network isolation"):
        lab.inspect_installation("immich", snapshot["apps"]["immich"], docker)

    docker.network_internal_override = None
    docker.wrong_mount_source.add("echo-hub-immich")
    with pytest.raises(lab.HubLifecycleLabError, match="violates its runtime contract"):
        lab.inspect_installation("immich", snapshot["apps"]["immich"], docker)


def test_physical_baseline_rejects_wrong_release_or_retained_volume() -> None:
    snapshot = lab._catalog_snapshot(_catalog())
    docker = _LifecycleDocker(snapshot["apps"])
    docker.runtime_image = f"ghcr.io/echo-os/echo-os@sha256:{'0' * 64}"
    with pytest.raises(lab.HubLifecycleLabError, match="reviewed release candidate"):
        lab._running_candidate(IMAGE_REFERENCE, docker)

    docker.runtime_image = IMAGE_REFERENCE
    docker.optional_volume_exists = True
    with pytest.raises(lab.HubLifecycleLabError, match="fresh appliance"):
        lab._fresh_storage(snapshot, docker)


def test_public_endpoint_probe_retries_server_failure_and_retains_only_a_bounded_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_body = b"private-onboarding-page" * 4096
    responses = [
        (503, b"starting", "text/plain; charset=utf-8"),
        (302, private_body, "text/html; charset=utf-8"),
    ]

    class _Response:
        def __init__(self, status: int, body: bytes, content_type: str) -> None:
            self.status = status
            self.body = body
            self.content_type = content_type

        def read(self, maximum: int) -> bytes:
            return self.body[:maximum]

        def getheader(self, name: str) -> str | None:
            return self.content_type if name == "Content-Type" else None

    class _Connection:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert (host, port, timeout) == ("127.0.0.1", 3008, 5)

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            assert (method, path) == ("GET", "/")
            assert headers["Host"] == "127.0.0.1:3008"

        def getresponse(self) -> _Response:
            status, body, content_type = responses.pop(0)
            return _Response(status, body, content_type)

        def close(self) -> None:
            pass

    monkeypatch.setattr(lab.http.client, "HTTPConnection", _Connection)
    monkeypatch.setattr(lab.time, "sleep", lambda _seconds: None)
    endpoint = {"scheme": "http", "host": "127.0.0.1", "port": 3008, "path": "/"}

    observed = lab._public_endpoint_probe(endpoint)

    assert observed["status"] == 302
    assert observed["mediaType"] == "text/html"
    assert observed["attempts"] == 2
    assert observed["sampleBytes"] == lab.PUBLIC_ENDPOINT_SAMPLE_BYTES
    assert observed["sampleTruncated"] is True
    assert "private-onboarding-page" not in json.dumps(observed)
    assert lab._validate_public_endpoint_evidence(endpoint, observed) == observed

    rejected = {**observed, "status": 503}
    with pytest.raises(lab.HubLifecycleLabError, match="endpoint evidence"):
        lab._validate_public_endpoint_evidence(endpoint, rejected)


class _LifecycleApi:
    def __init__(self, catalog: dict[str, Any], docker: _LifecycleDocker) -> None:
        self.catalog = catalog
        self.docker = docker
        self.install_counts = {app_id: 0 for app_id in lab.APPS}
        self.control_counts = {
            (operation, app_id): 0 for operation in lab.CONTROL_OPERATIONS for app_id in lab.APPS
        }
        self.plan_ids: dict[tuple[str, str], str] = {}
        self.approvals: set[str] = set()
        self.pending_operations: dict[str, tuple[str, str, str]] = {}
        self.fail_control: tuple[str, str] | None = None

    @staticmethod
    def _operation_status(
        *,
        operation_id: str,
        operation: str,
        app_id: str,
        plan_id: str,
        catalog_digest: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        completed = status in {"succeeded", "failed", "interrupted"}
        return {
            "schema": "echo.hub.operation.v1",
            "operationId": operation_id,
            "operation": operation,
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "status": status,
            "createdAt": "2026-08-29T00:00:00+00:00",
            "updatedAt": "2026-08-29T00:00:01+00:00" if completed else "2026-08-29T00:00:00+00:00",
            "startedAt": "2026-08-29T00:00:00+00:00" if completed else None,
            "finishedAt": "2026-08-29T00:00:01+00:00" if completed else None,
            "error": error,
            "warning": None,
            "progress": {
                "schema": "echo.hub.progress.v1",
                "stage": "completed"
                if status == "succeeded"
                else "failed"
                if completed
                else "queued",
                "step": "finished"
                if status == "succeeded"
                else "operation-failed"
                if completed
                else "waiting",
                "completed": 1 if completed else None,
                "total": 1 if completed else None,
                "unit": "services" if completed else None,
                "item": None,
                "items": None,
                "sequence": 2 if completed else 0,
            },
            "credentialsAvailable": False,
            "result": result,
        }

    def __call__(
        self,
        method: str,
        url: str,
        payload,
        token: str | None,
        headers,
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        path = url.split("/api", 1)[-1]
        if path == "/auth/local/login":
            assert payload == {"username": "admin", "password": "correct-password"}
            return 200, {"success": True, "access_token": "device-token"}
        assert token == "device-token"
        if path == "/appliance/hub/catalog":
            return 200, copy.deepcopy(self.catalog)
        if path.startswith("/appliance/hub/apps/"):
            app_id = path.rsplit("/", 1)[-1]
            assert app_id in self.docker.installed
            running = app_id not in self.docker.stopped
            app = next(item for item in self.catalog["apps"] if item["id"] == app_id)
            definitions = (
                app["bundle"]["services"]
                if isinstance(app.get("bundle"), dict)
                else [{"id": "app", "role": "app"}]
            )
            public_service = (
                app["bundle"]["publicService"] if isinstance(app.get("bundle"), dict) else "app"
            )
            services = [
                {
                    "id": service["id"],
                    "role": service["role"],
                    "public": service["id"] == public_service,
                    "state": "running" if running else "exited",
                    "health": "healthy",
                    "restartCount": 0,
                    "oomKilled": False,
                    "exitCode": 0,
                    "cpuPercent": 1.5 if running else None,
                    "memoryUsageBytes": 128 * 1024**2 if running else None,
                    "memoryLimitBytes": 1024 * 1024**2 if running else None,
                    "pids": 12 if running else None,
                }
                for service in definitions
            ]
            return 200, {
                "app": {"id": app_id, "installation": {"installed": True}},
                "appRuntime": {
                    "schema": "echo.hub.runtime.v1",
                    "status": "healthy" if running else "stopped",
                    "summary": {
                        "serviceCount": len(services),
                        "runningServices": len(services) if running else 0,
                        "healthyServices": len(services) if running else 0,
                        "restartCount": 0,
                        "cpuPercent": len(services) * 1.5 if running else None,
                        "memoryUsageBytes": len(services) * 128 * 1024**2 if running else None,
                        "memoryLimitBytes": len(services) * 1024 * 1024**2 if running else None,
                        "pids": len(services) * 12 if running else None,
                    },
                    "services": services,
                },
                "diagnostics": {
                    "schema": "echo.hub.diagnostics.v1",
                    "status": "ok" if running else "stopped",
                    "incidents": [],
                },
            }
        if path == "/appliance/approvals":
            plan_id = payload["target"]
            self.approvals.add(plan_id)
            return 200, {"approvalToken": f"approval-{plan_id}"}
        if path.startswith("/appliance/hub/operations/"):
            operation_id = path.rsplit("/", 1)[-1]
            operation, app_id, plan_id = self.pending_operations.pop(operation_id)
            if self.fail_control == (operation, app_id):
                return 200, self._operation_status(
                    operation_id=operation_id,
                    operation=operation,
                    app_id=app_id,
                    plan_id=plan_id,
                    catalog_digest=self.catalog["digest"],
                    status="failed",
                    error={
                        "code": "OPERATION_FAILED",
                        "message": "The Hub operation did not complete",
                        "recoveryAction": "Refresh the app state before retrying",
                    },
                )
            if operation == "stop":
                self.docker.stopped.add(app_id)
            else:
                self.docker.stopped.discard(app_id)
            self.control_counts[(operation, app_id)] += 1
            app = next(item for item in self.catalog["apps"] if item["id"] == app_id)
            definitions = (
                app["bundle"]["services"]
                if isinstance(app.get("bundle"), dict)
                else [{"id": "app", "public": True}]
            )
            public_service = (
                app["bundle"]["publicService"] if isinstance(app.get("bundle"), dict) else "app"
            )
            public_name = lab._expected_container_name(app_id, public_service, True)
            result = {
                "schema": f"echo.hub.{operation}-result.v1",
                "appId": app_id,
                "planId": plan_id,
                "catalogDigest": self.catalog["digest"],
                "containerId": hashlib.sha256(public_name.encode()).hexdigest()[:12],
                "state": "stopped" if operation == "stop" else "running",
                "serviceCount": len(definitions),
                "dataVolumesRetained": True,
                "nasDataRetained": True,
                "rollback": {"previousRunningStateRestoredOnFailure": True},
            }
            return 200, self._operation_status(
                operation_id=operation_id,
                operation=operation,
                app_id=app_id,
                plan_id=plan_id,
                catalog_digest=self.catalog["digest"],
                status="succeeded",
                result=result,
            )
        for operation in ("stop", "start", "restart"):
            plan_path = f"/appliance/hub/plans/{operation}"
            if path == plan_path:
                app_id = payload["appId"]
                app = next(item for item in self.catalog["apps"] if item["id"] == app_id)
                definitions = (
                    app["bundle"]["services"]
                    if isinstance(app.get("bundle"), dict)
                    else [{"id": "app"}]
                )
                service_order = [service["id"] for service in definitions]
                plan_id = hashlib.sha256(f"{operation}:{app_id}".encode()).hexdigest()
                self.plan_ids[(operation, app_id)] = plan_id
                return 200, {
                    "schema": f"echo.hub.{operation}-plan.v1",
                    "operation": operation,
                    "ready": True,
                    "planId": plan_id,
                    "approvalAction": f"hub.app.{operation}",
                    "approvalTarget": plan_id,
                    "desired": {
                        "appId": app_id,
                        "catalogDigest": self.catalog["digest"],
                        "state": "stopped" if operation == "stop" else "running",
                        "serviceOrder": service_order,
                        "dataVolumesRetained": True,
                        "nasDataRetained": True,
                    },
                }
            if path == f"{plan_path}/queue":
                app_id = payload["appId"]
                plan_id = self.plan_ids[(operation, app_id)]
                assert payload["planId"] == plan_id
                assert headers[lab.APPROVAL_HEADER] == f"approval-{plan_id}"
                assert headers[lab.INTENT_HEADER].startswith(f"physical.hub.{operation}.{app_id}")
                assert plan_id in self.approvals
                operation_id = hashlib.sha256(
                    f"operation:{operation}:{app_id}".encode()
                ).hexdigest()[:32]
                self.pending_operations[operation_id] = (operation, app_id, plan_id)
                return 202, self._operation_status(
                    operation_id=operation_id,
                    operation=operation,
                    app_id=app_id,
                    plan_id=plan_id,
                    catalog_digest=self.catalog["digest"],
                    status="queued",
                )
        for operation in ("install", "uninstall"):
            plan_path = f"/appliance/hub/plans/{operation}"
            if path == plan_path:
                app_id = payload["appId"]
                plan_id = hashlib.sha256(f"{operation}:{app_id}".encode()).hexdigest()
                self.plan_ids[(operation, app_id)] = plan_id
                return 200, {"ready": True, "planId": plan_id}
            if path == f"{plan_path}/apply":
                app_id = payload["appId"]
                plan_id = self.plan_ids[(operation, app_id)]
                assert payload["planId"] == plan_id
                assert headers[lab.APPROVAL_HEADER] == f"approval-{plan_id}"
                assert headers[lab.INTENT_HEADER].startswith(f"physical.hub.{operation}.{app_id}")
                assert plan_id in self.approvals
                if operation == "install":
                    self.docker.installed.add(app_id)
                    self.docker.stopped.discard(app_id)
                    self.install_counts[app_id] += 1
                    revealed = (
                        {"admin-password": "PaperlessSecret202608290"}
                        if app_id in {"nextcloud", "qbittorrent", "syncthing", "paperless-ngx"}
                        and self.install_counts[app_id] == 1
                        else {}
                    )
                    return 200, {
                        "schema": "echo.hub.install-result.v1",
                        "appId": app_id,
                        "state": "running",
                        "revealedSecrets": revealed,
                    }
                self.docker.installed.remove(app_id)
                self.docker.stopped.discard(app_id)
                return 200, {
                    "schema": "echo.hub.uninstall-result.v1",
                    "appId": app_id,
                    "state": "not-installed",
                    "dataVolumesRetained": True,
                    "nasDataRetained": True,
                }
        raise AssertionError((method, path, payload, timeout))


def test_run_executes_two_realistic_cycles_without_leaking_revealed_secrets(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    candidate, bundle_root = _release(tmp_path)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    plan_path = evidence_root / "plan.json"
    plan = lab.build_plan(
        base_url="http://127.0.0.1:8000",
        catalog=catalog,
        candidate_index=candidate,
        bundle_root=bundle_root,
        output=plan_path,
        docker=_LifecycleDocker({}),
    )
    docker = _LifecycleDocker(plan["catalog"]["apps"])
    api = _LifecycleApi(catalog, docker)
    result_path = evidence_root / "result.json"
    private_secret_path = private_root / lab.PAPERLESS_PRIVATE_SECRET_NAME
    endpoint_calls: list[int] = []

    def endpoint_probe(endpoint: dict[str, Any]) -> dict[str, Any]:
        endpoint_calls.append(endpoint["port"])
        sample = f"ready:{endpoint['port']}:{len(endpoint_calls)}".encode()
        return {
            **endpoint,
            "status": 302 if endpoint["port"] == 8081 else 200,
            "mediaType": "text/html",
            "sampleBytes": len(sample),
            "sampleSha256": hashlib.sha256(sample).hexdigest(),
            "sampleTruncated": False,
            "attempts": 1,
        }

    result = lab.run_plan(
        plan_path=plan_path,
        confirmation=plan["confirmation"],
        password="correct-password",
        output=result_path,
        request=api,
        docker=docker,
        endpoint_probe=endpoint_probe,
        private_paperless_secret_output=private_secret_path,
    )

    assert result["allPassed"] is True
    assert result["finalState"] == "not-installed-data-retained"
    assert docker.installed == set()
    assert all(count == 2 for count in api.install_counts.values())
    assert len(endpoint_calls) == len(lab.APPS) * 4
    assert set(endpoint_calls) == {
        value["endpoint"]["port"] for value in plan["catalog"]["apps"].values()
    }
    assert all(
        record["publicEndpoint"]["status"] in {200, 302}
        for cycle in (result["firstInstall"], result["reinstall"])
        for record in cycle.values()
    )
    assert all(
        record["runtimeHealth"]["status"] == "healthy"
        and record["runtimeHealth"]["serviceCount"] == record["runtimeHealth"]["runningServices"]
        for cycle in (result["firstInstall"], result["reinstall"])
        for record in cycle.values()
    )
    assert all(count == 1 for count in api.control_counts.values())
    assert all(
        record["lifecycleControl"]["stop"]["runtime"]["status"] == "stopped"
        and record["lifecycleControl"]["stop"]["runtime"]["runningServices"] == 0
        and record["lifecycleControl"]["start"]["installation"] == record["installation"]
        and record["lifecycleControl"]["restart"]["installation"] == record["installation"]
        and all(
            record["lifecycleControl"][operation]["operation"]["result"]["dataVolumesRetained"]
            is True
            for operation in ("stop", "start", "restart")
        )
        for record in result["firstInstall"].values()
    )
    assert result["firstInstall"]["nextcloud"]["revealedSecretNames"] == ["admin-password"]
    assert result["reinstall"]["nextcloud"]["revealedSecretNames"] == []
    assert set(result["firstInstall"]["open-webui"]["installation"]["services"]) == {
        "cache",
        "app",
    }
    assert set(result["firstInstall"]["open-webui"]["installation"]["volumes"]) == {
        "data",
        "secrets-app",
    }
    assert result["firstInstall"]["open-webui"]["revealedSecretNames"] == []
    assert result["reinstall"]["open-webui"]["revealedSecretNames"] == []
    assert result["firstInstall"]["qbittorrent"]["revealedSecretNames"] == ["admin-password"]
    assert result["reinstall"]["qbittorrent"]["revealedSecretNames"] == []
    qbittorrent_install = result["firstInstall"]["qbittorrent"]["installation"]
    assert set(qbittorrent_install["services"]) == {"app"}
    assert set(qbittorrent_install["volumes"]) == {
        "config",
        "secrets-app",
    }
    assert result["firstInstall"]["syncthing"]["revealedSecretNames"] == ["admin-password"]
    assert result["reinstall"]["syncthing"]["revealedSecretNames"] == []
    syncthing_install = result["firstInstall"]["syncthing"]["installation"]
    assert set(syncthing_install["services"]) == {"app"}
    assert set(syncthing_install["volumes"]) == {"config", "secrets-app"}
    assert result["firstInstall"]["paperless-ngx"]["revealedSecretNames"] == ["admin-password"]
    assert result["reinstall"]["paperless-ngx"]["revealedSecretNames"] == []
    paperless_install = result["firstInstall"]["paperless-ngx"]["installation"]
    assert set(paperless_install["services"]) == {
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    }
    assert set(paperless_install["volumes"]) == {
        "database",
        "cache",
        "data",
        "secrets-database",
        "secrets-app",
    }
    assert "PaperlessSecret202608290" not in result_path.read_text(encoding="utf-8")
    private_secret = json.loads(private_secret_path.read_text(encoding="utf-8"))
    assert private_secret == {
        "schemaVersion": 1,
        "kind": lab.PAPERLESS_PRIVATE_SECRET_KIND,
        "appId": "paperless-ngx",
        "secretName": "admin-password",
        "hubLifecyclePlanId": plan["planId"],
        "releaseCandidate": plan["releaseCandidate"],
        "password": "PaperlessSecret202608290",
    }
    assert stat.S_IMODE(private_secret_path.stat().st_mode) == 0o400
    with pytest.raises(lab.HubLifecycleLabError, match="outside public evidence"):
        lab._write_private_paperless_secret(
            evidence_root / lab.PAPERLESS_PRIVATE_SECRET_NAME,
            plan=plan,
            password="PaperlessSecret202608290",
            public_directories=(evidence_root,),
        )
    broad_private_root = tmp_path / "broad-private"
    broad_private_root.mkdir(mode=0o755)
    with pytest.raises(lab.HubLifecycleLabError, match="mode 0700"):
        lab._write_private_paperless_secret(
            broad_private_root / lab.PAPERLESS_PRIVATE_SECRET_NAME,
            plan=plan,
            password="PaperlessSecret202608290",
            public_directories=(evidence_root,),
        )
    assert stat.S_IMODE(result_path.stat().st_mode) == 0o444
    assert lab.verify_result(plan_path=plan_path, result_path=result_path, docker=docker) == result

    rehashed_control = copy.deepcopy(result)
    rehashed_control["firstInstall"]["immich"]["lifecycleControl"]["stop"]["operation"]["result"][
        "dataVolumesRetained"
    ] = False
    rehashed_control.pop("resultId")
    rehashed_control["resultId"] = hashlib.sha256(lab._canonical(rehashed_control)).hexdigest()
    with pytest.raises(lab.HubLifecycleLabError, match="stop result"):
        lab._validate_result_value(plan, rehashed_control)

    changed = json.loads(result_path.read_text(encoding="utf-8"))
    changed["firstInstall"]["immich"]["installation"]["services"]["server"]["running"] = False
    result_path.chmod(0o600)
    result_path.write_text(json.dumps(changed), encoding="utf-8")
    result_path.chmod(0o444)
    with pytest.raises(lab.HubLifecycleLabError, match="identity"):
        lab.verify_result(plan_path=plan_path, result_path=result_path, docker=docker)

    with pytest.raises(lab.HubLifecycleLabError, match="confirmation"):
        lab.run_plan(
            plan_path=plan_path,
            confirmation="RUN SOMETHING ELSE",
            password="correct-password",
            output=tmp_path / "bad-result.json",
            request=api,
            docker=docker,
            endpoint_probe=endpoint_probe,
        )


def test_failed_lifecycle_removes_the_new_private_paperless_handoff(tmp_path: Path) -> None:
    catalog = _catalog()
    candidate, bundle_root = _release(tmp_path)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    plan_path = evidence_root / "plan.json"
    plan = lab.build_plan(
        base_url="http://127.0.0.1:8000",
        catalog=catalog,
        candidate_index=candidate,
        bundle_root=bundle_root,
        output=plan_path,
        docker=_LifecycleDocker({}),
    )
    docker = _LifecycleDocker(plan["catalog"]["apps"])
    api = _LifecycleApi(catalog, docker)
    secret_path = private_root / lab.PAPERLESS_PRIVATE_SECRET_NAME

    def endpoint_probe(endpoint: dict[str, Any]) -> dict[str, Any]:
        if endpoint["port"] == 8123:
            raise lab.HubLifecycleLabError("forced failure after Paperless handoff")
        sample = str(endpoint["port"]).encode()
        return {
            **endpoint,
            "status": 200,
            "mediaType": "text/html",
            "sampleBytes": len(sample),
            "sampleSha256": hashlib.sha256(sample).hexdigest(),
            "sampleTruncated": False,
            "attempts": 1,
        }

    with pytest.raises(lab.HubLifecycleLabError, match="forced failure"):
        lab.run_plan(
            plan_path=plan_path,
            confirmation=plan["confirmation"],
            password="correct-password",
            output=evidence_root / "result.json",
            request=api,
            docker=docker,
            endpoint_probe=endpoint_probe,
            private_paperless_secret_output=secret_path,
        )

    assert not secret_path.exists()
    assert docker.installed == set()


def test_failed_background_restart_fails_closed_and_uninstalls_every_app(tmp_path: Path) -> None:
    catalog = _catalog()
    candidate, bundle_root = _release(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan = lab.build_plan(
        base_url="http://127.0.0.1:8000",
        catalog=catalog,
        candidate_index=candidate,
        bundle_root=bundle_root,
        output=plan_path,
        docker=_LifecycleDocker({}),
    )
    docker = _LifecycleDocker(plan["catalog"]["apps"])
    api = _LifecycleApi(catalog, docker)
    api.fail_control = ("restart", "immich")

    def endpoint_probe(endpoint: dict[str, Any]) -> dict[str, Any]:
        sample = str(endpoint["port"]).encode()
        return {
            **endpoint,
            "status": 200,
            "mediaType": "text/html",
            "sampleBytes": len(sample),
            "sampleSha256": hashlib.sha256(sample).hexdigest(),
            "sampleTruncated": False,
            "attempts": 1,
        }

    output = tmp_path / "result.json"
    with pytest.raises(
        lab.HubLifecycleLabError,
        match="restart operation for immich failed",
    ):
        lab.run_plan(
            plan_path=plan_path,
            confirmation=plan["confirmation"],
            password="correct-password",
            output=output,
            request=api,
            docker=docker,
            endpoint_probe=endpoint_probe,
        )

    assert docker.installed == set()
    assert docker.stopped == set()
    assert not output.exists()
