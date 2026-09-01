from __future__ import annotations

import base64
import copy
import hashlib
import json
import socket
import stat
import struct
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from deploy.appliance import hub_lifecycle_lab as hub_lab
from deploy.appliance import lan_discovery_functional_lab as lab
from tests.appliance.test_hub_lifecycle_lab import _catalog, _LifecycleDocker, _release

NAS_DEVICE_ID = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH"
COMPANION_DEVICE_ID = "1111111-2222222-3333333-4444444-5555555-6666666-7777777-8888888"
CONTROL_ENTITY = "switch.echo_physical_lab"


def _installed_catalog() -> dict[str, Any]:
    catalog = copy.deepcopy(_catalog())
    for app in catalog["apps"]:
        if app["id"] in lab.APP_IDS:
            app["installation"]["installed"] = True
            app["installable"] = False
            app["installBlockers"] = ["PORT_IN_USE", "ALREADY_INSTALLED"]
    return catalog


def _docker(catalog: dict[str, Any]) -> _LifecycleDocker:
    snapshot = hub_lab._catalog_snapshot(catalog, expected_installed=lab.APP_IDS)
    docker = _LifecycleDocker(snapshot["apps"])
    docker.installed.update(lab.APP_IDS)
    return docker


def _plan(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    candidate, bundle_root = _release(tmp_path)
    catalog = _installed_catalog()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    path = evidence / lab.PLAN_NAME
    value = lab.build_plan(
        syncthing_base_url="http://127.0.0.1:3007",
        home_assistant_base_url="http://127.0.0.1:8123",
        catalog=catalog,
        candidate_index=candidate,
        bundle_root=bundle_root,
        output=path,
        docker=_docker(catalog),
    )
    return value, path


class _SyncthingApi:
    def __init__(self, local_id: str, peer_id: str, address: str) -> None:
        self.local_id = local_id
        self.peer_id = peer_id
        self.address = address

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        maximum: int,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        del maximum, timeout
        assert method == "GET" and body is None
        assert headers["Authorization"].startswith("Basic ")
        path = urlsplit(url).path
        if path == "/rest/system/status":
            value = {
                "myID": self.local_id,
                "discoveryStatus": {"IPv4 local": {"error": None}},
            }
        elif path == "/rest/config/devices":
            value = [{"deviceID": self.peer_id, "addresses": ["dynamic"]}]
        elif path == "/rest/system/discovery":
            value = {self.peer_id: [self.address]}
        elif path == "/rest/system/connections":
            value = {
                "connections": {
                    self.peer_id: {
                        "address": self.address,
                        "clientVersion": "v2.0.10",
                        "connected": True,
                        "inBytesTotal": 1024,
                        "isLocal": True,
                        "outBytesTotal": 2048,
                        "type": "tcp-client",
                    }
                }
            }
        else:
            raise AssertionError(path)
        return 200, {"content-type": "application/json"}, json.dumps(value).encode()


class _HomeAssistantApi:
    def __init__(self) -> None:
        self.state = "off"
        self.calls: list[str] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        maximum: int,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        del maximum, timeout
        assert headers["Authorization"] == "Bearer ha-token"
        path = urlsplit(url).path
        if method == "GET" and path == f"/api/states/{CONTROL_ENTITY}":
            value: Any = {"entity_id": CONTROL_ENTITY, "state": self.state, "attributes": {}}
        elif method == "POST" and path in {
            "/api/services/switch/turn_on",
            "/api/services/switch/turn_off",
        }:
            assert json.loads(body or b"") == {"entity_id": CONTROL_ENTITY}
            self.state = path.rsplit("_", 1)[1]
            self.calls.append(self.state)
            value = []
        else:
            raise AssertionError((method, path))
        return 200, {"content-type": "application/json"}, json.dumps(value).encode()


def _home_assistant_ws(
    _base_url: str,
    token: str,
    messages: list[dict[str, Any]],
) -> list[Any]:
    assert token == "ha-token"
    assert messages == [
        {"type": "config_entries/get"},
        {"type": "config/entity_registry/get", "entity_id": CONTROL_ENTITY},
    ]
    return [
        [
            {
                "entry_id": "entry-zeroconf",
                "domain": "matter",
                "source": "zeroconf",
                "state": "loaded",
            },
            {
                "entry_id": "entry-ssdp",
                "domain": "hue",
                "source": "ssdp",
                "state": "loaded",
            },
        ],
        {"entity_id": CONTROL_ENTITY, "config_entry_id": "entry-zeroconf"},
    ]


def _evidence(tmp_path: Path) -> tuple[dict[str, Any], Path, dict[str, Path]]:
    plan, plan_path = _plan(tmp_path)
    paths = {
        "nas": tmp_path / lab.SYNCTHING_NAS_NAME,
        "companion": tmp_path / lab.SYNCTHING_COMPANION_NAME,
        "homeAssistant": tmp_path / lab.HOME_ASSISTANT_NAME,
        "result": tmp_path / lab.RESULT_NAME,
    }
    lab.run_syncthing_probe(
        plan_path=plan_path,
        role="nas",
        username="admin",
        password="syncthing-password",
        confirmation=plan["confirmation"],
        output=paths["nas"],
        request=_SyncthingApi(NAS_DEVICE_ID, COMPANION_DEVICE_ID, "192.168.50.22:22000"),
        machine_identity="machine-nas",
        observed_at_unix=1_700_000_000,
    )
    lab.run_syncthing_probe(
        plan_path=plan_path,
        role="companion",
        username="admin",
        password="syncthing-password",
        confirmation=plan["confirmation"],
        output=paths["companion"],
        request=_SyncthingApi(COMPANION_DEVICE_ID, NAS_DEVICE_ID, "192.168.50.10:22000"),
        machine_identity="machine-companion",
        observed_at_unix=1_700_000_000,
    )
    api = _HomeAssistantApi()
    lab.run_home_assistant_probe(
        plan_path=plan_path,
        token="ha-token",
        control_entity_id=CONTROL_ENTITY,
        confirmation=plan["confirmation"],
        output=paths["homeAssistant"],
        request=api,
        websocket_query=_home_assistant_ws,
        clock=lambda: 100.0,
        sleeper=lambda _seconds: None,
        observed_at_unix=1_700_000_000,
    )
    assert api.calls == ["on", "off"] and api.state == "off"
    result = lab.verify_evidence(
        plan_path=plan_path,
        syncthing_nas_path=paths["nas"],
        syncthing_companion_path=paths["companion"],
        home_assistant_path=paths["homeAssistant"],
        output=paths["result"],
        now=1_700_000_000,
    )
    return result, plan_path, paths


def test_plan_binds_candidate_installed_apps_and_candidate_tool(tmp_path: Path) -> None:
    plan, path = _plan(tmp_path)

    assert plan["workflow"] == lab.WORKFLOW
    assert set(plan["installations"]) == set(lab.APP_IDS)
    assert plan["catalog"]["apps"]["syncthing"]["providers"] == ["lan-discovery"]
    assert plan["catalog"]["apps"]["home-assistant"]["services"][0]["networkMode"] == "host"
    assert plan["operationsBundle"]["lanDiscoveryLabSha256"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert lab.load_plan(path) == plan


def test_running_tool_must_match_candidate_bound_bytes_and_mode(tmp_path: Path) -> None:
    plan, _path = _plan(tmp_path)

    assert lab._verify_local_tool(plan) == {
        "sha256": plan["operationsBundle"]["lanDiscoveryLabSha256"],
        "size": plan["operationsBundle"]["lanDiscoveryLabSize"],
    }
    altered = tmp_path / "altered-lan-discovery-functional-lab.py"
    altered.write_bytes(Path(lab.__file__).read_bytes() + b"\n# altered copy\n")
    altered.chmod(0o755)
    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="release candidate"):
        lab._verify_local_tool(plan, altered)

    wrong_mode = tmp_path / "wrong-mode-lan-discovery-functional-lab.py"
    wrong_mode.write_bytes(Path(lab.__file__).read_bytes())
    wrong_mode.chmod(0o644)
    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="release candidate"):
        lab._verify_local_tool(plan, wrong_mode)


def test_plan_generation_starts_with_running_tool_self_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, bundle_root = _release(tmp_path)
    catalog = _installed_catalog()
    output = tmp_path / lab.PLAN_NAME

    def reject(_plan: dict[str, Any], _tool_path: Path | None = None) -> dict[str, Any]:
        raise lab.LanDiscoveryFunctionalLabError("simulated altered running tool")

    monkeypatch.setattr(lab, "_verify_local_tool", reject)
    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="altered running tool"):
        lab.build_plan(
            syncthing_base_url="http://127.0.0.1:3007",
            home_assistant_base_url="http://127.0.0.1:8123",
            catalog=catalog,
            candidate_index=candidate,
            bundle_root=bundle_root,
            output=output,
            docker=_docker(catalog),
        )
    assert not output.exists()


def test_credentials_command_writes_candidate_bound_private_files_without_stdout_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan, plan_path = _plan(tmp_path)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    output = private / lab.NAS_CREDENTIAL_NAME
    password = "syncthing-private-password"
    token = "home-assistant-private-token-" + "x" * 32
    monkeypatch.setenv("SYNCTHING_ADMIN_PASSWORD", password)
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", token)
    monkeypatch.setenv("HOME_ASSISTANT_CONTROL_ENTITY", CONTROL_ENTITY)

    assert (
        lab.main(
            [
                "credentials",
                "--plan",
                str(plan_path),
                "--role",
                "nas",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    public_output = capsys.readouterr()
    assert "LAN_DISCOVERY_FUNCTIONAL_LAB_OK" in public_output.out
    assert password not in public_output.out + public_output.err
    assert token not in public_output.out + public_output.err
    assert CONTROL_ENTITY not in public_output.out + public_output.err
    assert stat.S_IMODE(output.stat().st_mode) == 0o400
    credentials = lab._private_credentials(output, plan, "nas", plan_path=plan_path)
    assert credentials == {
        "syncthingUsername": "admin",
        "syncthingPassword": password,
        "homeAssistantToken": token,
        "controlEntityId": CONTROL_ENTITY,
    }
    public_copy = plan_path.parent / lab.NAS_CREDENTIAL_NAME
    public_copy.write_bytes(output.read_bytes())
    public_copy.chmod(0o400)
    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="owner-only"):
        lab._private_credentials(
            public_copy,
            plan,
            "nas",
            plan_path=plan_path,
        )


def test_credentials_refuse_public_evidence_directory_overwrite_and_role_confusion(
    tmp_path: Path,
) -> None:
    _plan_value, plan_path = _plan(tmp_path)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    companion = private / lab.COMPANION_CREDENTIAL_NAME

    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="separate owner-only"):
        lab.create_private_credentials(
            plan_path=plan_path,
            role="nas",
            syncthing_username="admin",
            syncthing_password="syncthing-private-password",
            home_assistant_token="t" * 64,
            control_entity_id=CONTROL_ENTITY,
            output=plan_path.parent / lab.NAS_CREDENTIAL_NAME,
        )
    nested_public = plan_path.parent / "nested-public"
    nested_public.mkdir(mode=0o700)
    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="separate owner-only"):
        lab.create_private_credentials(
            plan_path=plan_path,
            role="nas",
            syncthing_username="admin",
            syncthing_password="syncthing-private-password",
            home_assistant_token="t" * 64,
            control_entity_id=CONTROL_ENTITY,
            output=nested_public / lab.NAS_CREDENTIAL_NAME,
        )
    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="cannot contain"):
        lab.create_private_credentials(
            plan_path=plan_path,
            role="companion",
            syncthing_username="admin",
            syncthing_password="syncthing-private-password",
            home_assistant_token="t" * 64,
            control_entity_id=CONTROL_ENTITY,
            output=companion,
        )
    lab.create_private_credentials(
        plan_path=plan_path,
        role="companion",
        syncthing_username="admin",
        syncthing_password="syncthing-private-password",
        output=companion,
    )
    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="already exists"):
        lab.create_private_credentials(
            plan_path=plan_path,
            role="companion",
            syncthing_username="admin",
            syncthing_password="syncthing-private-password",
            output=companion,
        )


def test_real_public_api_probes_cross_bind_two_devices_and_restore_control(
    tmp_path: Path,
) -> None:
    result, plan_path, paths = _evidence(tmp_path)

    assert result["allPassed"] is True
    assert result["checks"]["syncthingLanDiscoveryVerified"] is True
    assert result["checks"]["homeAssistantReversibleControlVerified"] is True
    assert result["homeAssistant"]["control"]["restoredState"] == "off"
    assert (
        result["syncthing"]["nas"]["localDeviceSha256"]
        == (result["syncthing"]["companion"]["peerDeviceSha256"])
    )
    assert stat.S_IMODE(paths["result"].stat().st_mode) == 0o444
    plan_raw = plan_path.read_bytes()
    result_raw = paths["result"].read_bytes()
    lab.validate_evidence_bytes(
        plan_raw,
        result_raw,
        expected_candidate=result["releaseCandidate"],
        now=1_700_000_000,
    )
    public = b"".join(path.read_bytes() for path in paths.values())
    assert NAS_DEVICE_ID.encode() not in public
    assert COMPANION_DEVICE_ID.encode() not in public
    assert b"192.168.50" not in public
    assert b"ha-token" not in public
    assert CONTROL_ENTITY.encode() not in public
    assert b"syncthing-password" not in public


def test_syncthing_rejects_manual_address_instead_of_dynamic_discovery(tmp_path: Path) -> None:
    plan, plan_path = _plan(tmp_path)
    api = _SyncthingApi(NAS_DEVICE_ID, COMPANION_DEVICE_ID, "192.168.50.22:22000")
    original = api.__call__

    def manual(*args: Any, **kwargs: Any) -> tuple[int, dict[str, str], bytes]:
        status, headers, raw = original(*args, **kwargs)
        if urlsplit(args[1]).path == "/rest/config/devices":
            value = json.loads(raw)
            value[0]["addresses"] = ["tcp://192.168.50.22:22000"]
            raw = json.dumps(value).encode()
        return status, headers, raw

    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="exactly one"):
        lab.run_syncthing_probe(
            plan_path=plan_path,
            role="nas",
            username="admin",
            password="syncthing-password",
            confirmation=plan["confirmation"],
            output=tmp_path / lab.SYNCTHING_NAS_NAME,
            request=manual,
            machine_identity="machine-nas",
        )


def test_verify_rejects_same_machine_or_cross_candidate_probe(tmp_path: Path) -> None:
    _result, plan_path, paths = _evidence(tmp_path)
    companion = json.loads(paths["companion"].read_text())
    nas = json.loads(paths["nas"].read_text())
    companion["details"]["machineIdentitySha256"] = nas["details"]["machineIdentitySha256"]
    paths["companion"].chmod(0o600)
    paths["companion"].write_text(json.dumps(companion) + "\n")
    paths["companion"].chmod(0o444)

    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="two distinct"):
        lab.verify_evidence(
            plan_path=plan_path,
            syncthing_nas_path=paths["nas"],
            syncthing_companion_path=paths["companion"],
            home_assistant_path=paths["homeAssistant"],
            output=tmp_path / "other" / lab.RESULT_NAME,
        )


@pytest.mark.parametrize(
    "observed_at_unix",
    [
        1_700_000_000 - lab.PROBE_MAX_AGE_SECONDS - 1,
        1_700_000_000 + lab.PROBE_FUTURE_SKEW_SECONDS + 1,
        1_700_000_000 - lab.PROBE_MAX_SKEW_SECONDS - 1,
    ],
    ids=["stale", "future", "different-window"],
)
def test_verify_rejects_stale_future_or_cross_window_probe(
    tmp_path: Path,
    observed_at_unix: int,
) -> None:
    _result, plan_path, paths = _evidence(tmp_path)
    probe = json.loads(paths["nas"].read_text())
    probe["observedAtUnix"] = observed_at_unix
    paths["nas"].chmod(0o600)
    paths["nas"].write_bytes(lab._canonical(probe))
    paths["nas"].chmod(0o444)

    with pytest.raises(
        lab.LanDiscoveryFunctionalLabError,
        match="stale, future-dated or from different lab windows",
    ):
        lab.verify_evidence(
            plan_path=plan_path,
            syncthing_nas_path=paths["nas"],
            syncthing_companion_path=paths["companion"],
            home_assistant_path=paths["homeAssistant"],
            output=tmp_path / "rejected" / lab.RESULT_NAME,
            now=1_700_000_000,
        )


def test_home_assistant_requires_both_discovery_sources_and_discovered_control(
    tmp_path: Path,
) -> None:
    plan, plan_path = _plan(tmp_path)

    def incomplete(_base_url: str, _token: str, _messages: list[dict[str, Any]]) -> list[Any]:
        return [
            [
                {
                    "entry_id": "entry-zeroconf",
                    "domain": "matter",
                    "source": "zeroconf",
                    "state": "loaded",
                }
            ],
            {"entity_id": CONTROL_ENTITY, "config_entry_id": "entry-manual"},
        ]

    with pytest.raises(lab.LanDiscoveryFunctionalLabError, match="no loaded ssdp"):
        lab.run_home_assistant_probe(
            plan_path=plan_path,
            token="ha-token",
            control_entity_id=CONTROL_ENTITY,
            confirmation=plan["confirmation"],
            output=tmp_path / lab.HOME_ASSISTANT_NAME,
            request=_HomeAssistantApi(),
            websocket_query=incomplete,
        )


def _server_frame(value: Any, *, opcode: int = 1) -> bytes:
    raw = value if isinstance(value, bytes) else json.dumps(value, separators=(",", ":")).encode()
    if len(raw) < 126:
        header = bytes((0x80 | opcode, len(raw)))
    elif len(raw) <= 0xFFFF:
        header = bytes((0x80 | opcode, 126)) + struct.pack("!H", len(raw))
    else:
        header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", len(raw))
    return header + raw


def _client_frame(connection: socket.socket) -> tuple[int, bytes]:
    def exact(size: int) -> bytes:
        value = b""
        while len(value) < size:
            chunk = connection.recv(size - len(value))
            if not chunk:
                raise AssertionError("client frame closed early")
            value += chunk
        return value

    first, second = exact(2)
    assert first & 0x80 and second & 0x80
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", exact(8))[0]
    mask = exact(4)
    raw = exact(length)
    return first & 0x0F, bytes(value ^ mask[index % 4] for index, value in enumerate(raw))


def test_native_home_assistant_websocket_client_authenticates_handles_ping_and_queries(
    tmp_path: Path,
) -> None:
    del tmp_path
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    failures: list[BaseException] = []
    observed: list[dict[str, Any]] = []

    def server() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += connection.recv(1)
                headers = {}
                for line in request.split(b"\r\n")[1:]:
                    name, separator, value = line.partition(b":")
                    if separator:
                        headers[name.decode().casefold()] = value.decode().strip()
                key = headers["sec-websocket-key"]
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
                ).decode()
                connection.sendall(
                    (
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                    ).encode()
                    + _server_frame({"type": "auth_required", "ha_version": "2026.8.3"})
                )
                opcode, raw = _client_frame(connection)
                assert opcode == 1
                assert json.loads(raw) == {"type": "auth", "access_token": "ha-token"}
                connection.sendall(_server_frame(b"echo-ping", opcode=9))
                opcode, raw = _client_frame(connection)
                assert opcode == 10 and raw == b"echo-ping"
                connection.sendall(_server_frame({"type": "auth_ok", "ha_version": "2026.8.3"}))
                for index, result in enumerate(
                    ([{"entry_id": "entry-a"}], {"entity_id": "x"}), start=1
                ):
                    opcode, raw = _client_frame(connection)
                    assert opcode == 1
                    message = json.loads(raw)
                    observed.append(message)
                    connection.sendall(
                        _server_frame(
                            {
                                "id": index,
                                "type": "result",
                                "success": True,
                                "result": result,
                            }
                        )
                    )
        except BaseException as exc:  # pragma: no cover - re-raised in the test thread
            failures.append(exc)
        finally:
            listener.close()

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    results = lab._websocket_query(
        f"http://127.0.0.1:{port}",
        "ha-token",
        [
            {"type": "config_entries/get"},
            {"type": "config/entity_registry/get", "entity_id": "switch.echo"},
        ],
    )
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert not failures
    assert results == [[{"entry_id": "entry-a"}], {"entity_id": "x"}]
    assert observed == [
        {"id": 1, "type": "config_entries/get"},
        {"id": 2, "type": "config/entity_registry/get", "entity_id": "switch.echo"},
    ]
