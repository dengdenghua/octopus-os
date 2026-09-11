"""Security and routing contracts for the native OS storage broker."""

from __future__ import annotations

import ast
import json
import os
import socket
import threading
from pathlib import Path

import pytest

from appliance import native_storage
from appliance.native_storage_broker import (
    APPLY_OPERATION_TARGETS,
    HEALTH_SCHEMA,
    MAX_MESSAGE_BYTES,
    OPERATION_TARGETS,
    PLAN_OPERATION_TARGETS,
    READ_OPERATION_TARGETS,
    SCHEMA,
    SYSTEM_OPERATION_TARGETS,
    NativeStorageBrokerClient,
    NativeStorageBrokerError,
    NativeStorageBrokerServer,
    _resolve_operation,
    _strict_json_loads,
    dispatch_request,
    operation_for_callable,
)
from appliance.native_storage_routes import (
    _apply_native_write,
    _plan_native_write,
    _read_native_storage,
)


def _request(operation: str) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "requestId": "a" * 32,
        "operation": operation,
        "desired": {"schema": "echo.test.desired.v1"},
        "planId": "plan-123",
    }


def test_broker_allowlist_contains_only_paired_plan_and_apply_operations() -> None:
    assert len(APPLY_OPERATION_TARGETS) == 40
    assert len(PLAN_OPERATION_TARGETS) == 40
    assert len(READ_OPERATION_TARGETS) == 2
    assert len(SYSTEM_OPERATION_TARGETS) == 1
    assert len(OPERATION_TARGETS) == 83
    assert set(APPLY_OPERATION_TARGETS).isdisjoint(PLAN_OPERATION_TARGETS)
    assert set(READ_OPERATION_TARGETS).isdisjoint(
        {*APPLY_OPERATION_TARGETS, *PLAN_OPERATION_TARGETS}
    )
    assert all(
        operation.endswith(
            (
                ".apply_policy",
                ".apply_lock",
                ".apply_config",
                ".apply_time_machine",
                ".apply_smart_self_test",
                ".apply_snapshot",
                ".apply_snapshot_delete",
                ".apply_snapshot_restore_copy",
            )
        )
        or ".apply_" in operation
        for operation in APPLY_OPERATION_TARGETS
    )
    assert all(".plan_" in operation for operation in PLAN_OPERATION_TARGETS)
    assert set(READ_OPERATION_TARGETS) == {
        "native_storage.sharing_overview",
        "native_storage.share_privileges",
    }
    assert SYSTEM_OPERATION_TARGETS == {
        "native_hub_firewall.sync": ("appliance.native_hub_firewall", "sync")
    }
    assert all(module.startswith("appliance.") for module, _function in OPERATION_TARGETS.values())
    assert all(not function.startswith("_") for _module, function in OPERATION_TARGETS.values())


def test_broker_allowlist_exactly_covers_every_routed_storage_apply() -> None:
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "appliance/native_storage_routes.py").read_text(encoding="utf-8"))
    aliases = {
        "apply_policy": "ups_shutdown_policy.apply_policy",
        "apply_smart_self_test": "native_smart.apply_smart_self_test",
    }
    routed = {"native_storage.apply_shared_folder"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "apply_fn":
                continue
            expression = ast.unparse(keyword.value)
            routed.add(aliases.get(expression, expression))

    assert routed == set(APPLY_OPERATION_TARGETS)


def test_broker_allowlist_exactly_covers_every_routed_storage_plan() -> None:
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "appliance/native_storage_routes.py").read_text(encoding="utf-8"))
    aliases = {
        "plan_policy": "ups_shutdown_policy.plan_policy",
        "plan_smart_self_test": "native_smart.plan_smart_self_test",
    }
    routed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "run_in_threadpool" or len(node.args) < 2:
            continue
        if ast.unparse(node.args[0]) != "_plan_native_write":
            continue
        expression = ast.unparse(node.args[1])
        if expression != "plan_fn":
            routed.add(aliases.get(expression, expression))

    assert routed == set(PLAN_OPERATION_TARGETS)


def test_operation_id_is_derived_from_the_callable_not_client_input() -> None:
    assert operation_for_callable(native_storage.apply_nfs) == "native_storage.apply_nfs"
    assert operation_for_callable(native_storage.plan_nfs) == "native_storage.plan_nfs"

    def arbitrary_command(_desired, _plan_id):
        return {}

    with pytest.raises(RuntimeError, match="not broker-authorized"):
        operation_for_callable(arbitrary_command)


def test_every_allowlisted_target_resolves_to_its_exact_callable() -> None:
    for operation in OPERATION_TARGETS:
        function = _resolve_operation(operation)
        assert callable(function)
        assert operation_for_callable(function) == operation


def test_dispatch_rejects_unknown_operation_before_execution() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        dispatch_request(_request("native_storage.apply_arbitrary_command"))


def test_health_probe_is_fixed_and_side_effect_free() -> None:
    request = _request("broker.health")
    request["desired"] = {}
    request["planId"] = "probe"
    assert dispatch_request(request) == {
        "schema": HEALTH_SCHEMA,
        "status": "ok",
        "operationCount": len(OPERATION_TARGETS),
    }

    request["desired"] = {"command": "id"}
    with pytest.raises(ValueError, match="health request"):
        dispatch_request(request)


def test_dispatch_passes_only_desired_state_and_plan_id(monkeypatch) -> None:
    observed: list[tuple[dict[str, object], str]] = []

    def apply(desired: dict[str, object], plan_id: str) -> dict[str, object]:
        observed.append((desired, plan_id))
        return {"applied": True, "verified": True}

    monkeypatch.setattr(
        "appliance.native_storage_broker._resolve_operation", lambda _operation: apply
    )

    assert dispatch_request(_request("native_storage.apply_nfs")) == {
        "applied": True,
        "verified": True,
    }
    assert observed == [({"schema": "echo.test.desired.v1"}, "plan-123")]


def test_dispatch_invokes_plan_with_desired_state_only(monkeypatch) -> None:
    observed: list[dict[str, object]] = []

    def plan(desired: dict[str, object]) -> dict[str, object]:
        observed.append(desired)
        return {"operation": "create", "planId": "plan-123"}

    monkeypatch.setattr(
        "appliance.native_storage_broker._resolve_operation", lambda _operation: plan
    )
    request = _request("native_storage.plan_nfs")
    request["planId"] = "preview"

    assert dispatch_request(request) == {"operation": "create", "planId": "plan-123"}
    assert observed == [{"schema": "echo.test.desired.v1"}]

    request["planId"] = "client-selected-plan-id"
    with pytest.raises(ValueError, match="plan request"):
        dispatch_request(request)


def test_dispatch_invokes_hub_firewall_sync_without_caller_arguments(monkeypatch) -> None:
    calls = 0

    def sync() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"changed": True, "rules": []}

    monkeypatch.setattr(
        "appliance.native_storage_broker._resolve_operation", lambda _operation: sync
    )
    request = _request("native_hub_firewall.sync")
    request["desired"] = {}
    request["planId"] = "sync"

    assert dispatch_request(request) == {"changed": True, "rules": []}
    assert calls == 1

    request["desired"] = {"port": 8080}
    with pytest.raises(ValueError, match="synchronization request"):
        dispatch_request(request)


@pytest.mark.parametrize(
    ("operation", "desired", "expected_argument"),
    [
        ("native_storage.sharing_overview", {}, None),
        (
            "native_storage.share_privileges",
            {"shareUuid": "01234567-89ab-cdef-0123-456789abcdef"},
            "01234567-89ab-cdef-0123-456789abcdef",
        ),
    ],
)
def test_dispatch_invokes_only_the_two_bounded_read_shapes(
    monkeypatch, operation, desired, expected_argument
) -> None:
    observed: list[object] = []

    def read(*args):
        observed.extend(args)
        return {"items": []}

    monkeypatch.setattr(
        "appliance.native_storage_broker._resolve_operation", lambda _operation: read
    )
    request = _request(operation)
    request["desired"] = desired
    request["planId"] = "read"

    assert dispatch_request(request) == {"value": {"items": []}}
    assert observed == ([] if expected_argument is None else [expected_argument])


def test_dispatch_rejects_unbounded_read_arguments() -> None:
    request = _request("native_storage.share_privileges")
    request["planId"] = "read"
    for desired in (
        {},
        {"shareUuid": "not-a-uuid"},
        {"shareUuid": "01234567-89ab-cdef-0123-456789abcdef", "path": "/etc"},
    ):
        request["desired"] = desired
        with pytest.raises(ValueError, match="request|UUID"):
            dispatch_request(request)


@pytest.mark.parametrize(
    "mutation",
    [
        {"extra": True},
        {"schema": "legacy"},
        {"requestId": "not-an-id"},
        {"desired": []},
        {"planId": ""},
    ],
)
def test_dispatch_fails_closed_on_malformed_requests(mutation) -> None:
    request = _request("native_storage.apply_nfs")
    request.update(mutation)
    with pytest.raises(ValueError):
        dispatch_request(request)


@pytest.mark.parametrize(
    "raw, message",
    [
        (b'{"schema":"one","schema":"two"}', "duplicate keys"),
        (b'{"value":NaN}', "non-finite"),
        (b"\xff", "not UTF-8"),
    ],
)
def test_protocol_rejects_ambiguous_json(raw, message) -> None:
    with pytest.raises(ValueError, match=message):
        _strict_json_loads(raw)


def test_route_helper_uses_direct_apply_without_broker(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_NATIVE_STORAGE_BROKER_SOCKET", raising=False)
    calls: list[tuple[dict[str, object], str]] = []

    def apply(desired, plan_id):
        calls.append((desired, plan_id))
        return {"applied": True}

    assert _apply_native_write(apply, {"value": 1}, "plan") == {"applied": True}
    assert calls == [({"value": 1}, "plan")]


def test_route_helper_uses_direct_plan_without_broker(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_NATIVE_STORAGE_BROKER_SOCKET", raising=False)
    calls: list[dict[str, object]] = []

    def plan(desired):
        calls.append(desired)
        return {"operation": "create", "planId": "plan"}

    assert _plan_native_write(plan, {"value": 1}) == {
        "operation": "create",
        "planId": "plan",
    }
    assert calls == [{"value": 1}]


def test_route_helper_uses_bounded_direct_read_without_broker(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_NATIVE_STORAGE_BROKER_SOCKET", raising=False)

    assert _read_native_storage(lambda: {"shares": []}, {}) == {"shares": []}
    assert _read_native_storage(
        lambda share_uuid: [{"shareUuid": share_uuid}],
        {"shareUuid": "01234567-89ab-cdef-0123-456789abcdef"},
    ) == [{"shareUuid": "01234567-89ab-cdef-0123-456789abcdef"}]


def test_route_helper_delegates_allowlisted_apply_to_broker(monkeypatch, tmp_path) -> None:
    socket_path = tmp_path / "broker.sock"
    observed: list[tuple[Path, str, dict[str, object], str]] = []

    def call(self, operation, desired, plan_id):
        observed.append((self.path, operation, desired, plan_id))
        return {"applied": True, "verified": True}

    monkeypatch.setenv("ECHO_NATIVE_STORAGE_BROKER_SOCKET", str(socket_path))
    monkeypatch.setattr(NativeStorageBrokerClient, "call", call)

    result = _apply_native_write(native_storage.apply_nfs, {"client": "host"}, "plan")

    assert result == {"applied": True, "verified": True}
    assert observed == [(socket_path, "native_storage.apply_nfs", {"client": "host"}, "plan")]


def test_route_helper_delegates_allowlisted_plan_to_broker(monkeypatch, tmp_path) -> None:
    socket_path = tmp_path / "broker.sock"
    observed: list[tuple[Path, str, dict[str, object]]] = []

    def plan(self, operation, desired):
        observed.append((self.path, operation, desired))
        return {"operation": "create", "planId": "plan"}

    monkeypatch.setenv("ECHO_NATIVE_STORAGE_BROKER_SOCKET", str(socket_path))
    monkeypatch.setattr(NativeStorageBrokerClient, "plan", plan)

    result = _plan_native_write(native_storage.plan_nfs, {"client": "host"})

    assert result == {"operation": "create", "planId": "plan"}
    assert observed == [(socket_path, "native_storage.plan_nfs", {"client": "host"})]


def test_route_helper_delegates_root_owned_read_to_broker(monkeypatch, tmp_path) -> None:
    socket_path = tmp_path / "broker.sock"
    observed: list[tuple[Path, str, dict[str, object]]] = []

    def read(self, operation, desired):
        observed.append((self.path, operation, desired))
        return {"shares": []}

    monkeypatch.setenv("ECHO_NATIVE_STORAGE_BROKER_SOCKET", str(socket_path))
    monkeypatch.setattr(NativeStorageBrokerClient, "read", read)

    result = _read_native_storage(native_storage.sharing_overview, {})

    assert result == {"shares": []}
    assert observed == [(socket_path, "native_storage.sharing_overview", {})]


def test_native_authority_delegates_account_directory_reads_to_broker(
    monkeypatch, tmp_path
) -> None:
    socket_path = tmp_path / "broker.sock"
    observed: list[tuple[Path, str, dict[str, object]]] = []

    def read(self, operation, desired):
        observed.append((self.path, operation, desired))
        return {"users": []} if not desired else []

    monkeypatch.setenv("ECHO_NATIVE_STORAGE_BROKER_SOCKET", str(socket_path))
    monkeypatch.setattr(NativeStorageBrokerClient, "read", read)
    authority = native_storage.NativeStorageAuthority()

    assert authority.sharing_overview() == {"users": []}
    assert authority.share_privileges("01234567-89ab-cdef-0123-456789abcdef") == []
    assert observed == [
        (socket_path, "native_storage.sharing_overview", {}),
        (
            socket_path,
            "native_storage.share_privileges",
            {"shareUuid": "01234567-89ab-cdef-0123-456789abcdef"},
        ),
    ]


def test_client_rejects_non_allowlisted_operation_without_touching_socket(tmp_path) -> None:
    client = NativeStorageBrokerClient(tmp_path / "missing.sock")
    with pytest.raises(NativeStorageBrokerError, match="not broker-authorized"):
        client.call("native_storage.run_command", {}, "plan")
    with pytest.raises(NativeStorageBrokerError, match="apply operation"):
        client.call("native_storage.plan_nfs", {}, "plan")
    with pytest.raises(NativeStorageBrokerError, match="plan operation"):
        client.plan("native_storage.apply_nfs", {})
    with pytest.raises(NativeStorageBrokerError, match="read operation"):
        client.read("native_storage.plan_nfs", {})


def test_client_probe_requires_the_exact_health_contract(monkeypatch, tmp_path) -> None:
    client = NativeStorageBrokerClient(tmp_path / "broker.sock")
    monkeypatch.setattr(
        client,
        "_exchange",
        lambda *_args, **_kwargs: {
            "schema": HEALTH_SCHEMA,
            "status": "ok",
            "operationCount": len(OPERATION_TARGETS) - 1,
        },
    )
    with pytest.raises(NativeStorageBrokerError, match="health result"):
        client.probe()


def test_client_caps_serialized_request_before_touching_socket(tmp_path) -> None:
    client = NativeStorageBrokerClient(tmp_path / "missing.sock")
    desired = {"value": "x" * MAX_MESSAGE_BYTES}
    with pytest.raises(NativeStorageBrokerError, match="too large"):
        client.call("native_storage.apply_nfs", desired, "plan")


def test_client_validates_response_schema_and_request_identity(monkeypatch, tmp_path) -> None:
    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def connect(self, _path):
            pass

        def sendall(self, payload):
            request = json.loads(payload)
            self.response = (
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "requestId": "b" * 32,
                        "ok": True,
                        "result": {"applied": True},
                        "sentFor": request["requestId"],
                    }
                ).encode()
                + b"\n"
            )

        def shutdown(self, _direction):
            pass

        def recv(self, _maximum):
            response, self.response = self.response, b""
            return response

        def close(self):
            pass

    client = NativeStorageBrokerClient(tmp_path / "broker.sock")
    monkeypatch.setattr(client, "_validate_socket", lambda: None)
    monkeypatch.setattr(
        "appliance.native_storage_broker.socket.socket", lambda *_args: FakeSocket()
    )
    monkeypatch.setattr("appliance.native_storage_broker.socket.AF_UNIX", 1, raising=False)

    with pytest.raises(NativeStorageBrokerError, match="identity"):
        client.call("native_storage.apply_nfs", {}, "plan")


def test_systemd_unit_is_unix_only_and_runs_the_broker_as_root() -> None:
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy/agent/echo-native-storage-broker.service").read_text(encoding="utf-8")

    assert "User=root" in unit
    assert "Group=echo" in unit
    assert "RuntimeDirectoryMode=0750" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "AF_INET" not in unit
    assert "--socket /run/echo-storage-broker/broker.sock" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=" not in unit
    assert "ProtectHome=" not in unit
    assert "PrivateTmp=" not in unit
    assert "ReadWritePaths=" not in unit
    assert "ProtectKernelTunables=" not in unit


def test_linux_peer_credential_contract_is_required_by_image_ci() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/os-image.yml").read_text(encoding="utf-8")
    contract = root / "deploy/agent/test_native_storage_broker_linux.py"

    assert "python3 deploy/agent/test_native_storage_broker_linux.py" in workflow
    source = contract.read_text(encoding="utf-8")
    assert 'sys.platform.startswith("linux")' in source
    assert "os.geteuid() != 0" in source
    assert 'hasattr(socket, "SO_PEERCRED")' in source
    assert 'client.plan("native_storage.plan_nfs"' in source
    assert "allowed_uids=frozenset()" in source


def test_protocol_does_not_expose_dynamic_import_or_shell_fields() -> None:
    forbidden = {"module", "function", "command", "argv", "path", "environment"}
    request = _request("native_storage.apply_nfs")
    assert forbidden.isdisjoint(request)
    assert set(request) == {"schema", "requestId", "operation", "desired", "planId"}


@pytest.mark.skipif(
    os.name != "posix"
    or not hasattr(os, "geteuid")
    or os.geteuid() != 0
    or not hasattr(socket, "AF_UNIX"),
    reason="real root-owned Unix socket verification requires a root Linux runner",
)
def test_real_unix_socket_enforces_peer_identity_and_health_contract(tmp_path) -> None:
    socket_path = tmp_path / "broker.sock"
    tmp_path.chmod(0o700)
    server = NativeStorageBrokerServer(socket_path, allowed_uids=frozenset({os.geteuid()}))
    socket_path.chmod(0o660)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert NativeStorageBrokerClient(socket_path, timeout=2.0).probe() == {
            "schema": HEALTH_SCHEMA,
            "status": "ok",
            "operationCount": len(OPERATION_TARGETS),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        socket_path.unlink(missing_ok=True)

    denied = NativeStorageBrokerServer(socket_path, allowed_uids=frozenset())
    socket_path.chmod(0o660)
    denied_thread = threading.Thread(target=denied.serve_forever, daemon=True)
    denied_thread.start()
    try:
        with pytest.raises(NativeStorageBrokerError, match="not authorized"):
            NativeStorageBrokerClient(socket_path, timeout=2.0).probe()
    finally:
        denied.shutdown()
        denied.server_close()
        denied_thread.join(timeout=2.0)
        socket_path.unlink(missing_ok=True)
