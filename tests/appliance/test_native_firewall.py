from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from appliance import native_firewall


@pytest.fixture
def firewall_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, dict[str, Any], list[tuple[str, ...]]]:
    state_path = tmp_path / "state" / "native-firewall.json"
    runtime: dict[str, Any] = {"permanent": set(), "live": set(), "zone": "echo-public"}
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(native_firewall, "STATE_PATH", state_path)

    def command(*args: str, timeout: float = 30.0) -> str:
        calls.append(args)
        permanent = "--permanent" in args
        rules = runtime["permanent" if permanent else "live"]
        if args == ("--get-default-zone",):
            return f"{runtime['zone']}\n"
        if args[-1] == "--list-rich-rules":
            return "".join(f"{rule}\n" for rule in sorted(rules))
        if args[0] == "--permanent" and args[-1].startswith("--add-rich-rule="):
            rules.add(args[-1].split("=", 1)[1])
            return "success\n"
        if args[0] == "--permanent" and args[-1].startswith("--remove-rich-rule="):
            rules.remove(args[-1].split("=", 1)[1])
            return "success\n"
        if args == ("--reload",):
            runtime["live"] = set(runtime["permanent"])
            return "success\n"
        raise AssertionError(args)

    monkeypatch.setattr(native_firewall, "_run", command)
    return state_path, runtime, calls


def test_rules_are_bounded_to_private_samba_and_requested_nfs_clients() -> None:
    state = native_firewall.desired_state(
        smb=True, nfs_clients=["fd12:3456::/64", "192.168.50.0/24"]
    )
    rules = native_firewall.managed_rules(state)

    assert len(rules) == 18
    assert all('source address="' in rule and rule.endswith(" accept") for rule in rules)
    assert sum('service name="samba"' in rule for rule in rules) == 4
    assert sum('port port="3702" protocol="udp"' in rule for rule in rules) == 4
    assert sum('port port="5357" protocol="tcp"' in rule for rule in rules) == 4
    assert sum('source address="192.168.50.0/24"' in rule for rule in rules) == 3
    assert sum('source address="fd12:3456::/64"' in rule for rule in rules) == 3
    assert not any("0.0.0.0/0" in rule or "::/0" in rule for rule in rules)


@pytest.mark.parametrize("client", ["8.8.8.0/24", "192.168.50.1/24", "fe80::/10"])
def test_public_noncanonical_or_link_local_nfs_clients_are_rejected(client: str) -> None:
    with pytest.raises(OSError, match="private|canonical"):
        native_firewall.desired_state(smb=False, nfs_clients=[client])


def test_hub_rules_are_private_and_distinguish_host_from_bridge_networking() -> None:
    state = native_firewall.desired_state(
        smb=False,
        nfs_clients=[],
        hub_forwards=[
            {
                "appId": "home-assistant",
                "hostPort": 8123,
                "containerPort": 8123,
                "protocol": "tcp",
                "mode": "host",
                "address": None,
            },
            {
                "appId": "nextcloud",
                "hostPort": 8081,
                "containerPort": 80,
                "protocol": "tcp",
                "mode": "bridge",
                "address": "172.20.0.4",
            },
        ],
    )
    rules = native_firewall.managed_rules(state)

    assert len(rules) == 6
    assert sum('port port="8123" protocol="tcp" accept' in rule for rule in rules) == 3
    assert (
        sum(
            'forward-port port="8081" protocol="tcp" to-port="80" to-addr="172.20.0.4"' in rule
            for rule in rules
        )
        == 3
    )
    assert all(
        any(f'source address="{network}"' in rule for network in native_firewall._SMB_NETWORKS[:3])
        for rule in rules
    )
    assert not any("0.0.0.0/0" in rule or "::/0" in rule for rule in rules)


@pytest.mark.parametrize("address", ["8.8.8.8", "127.0.0.1", "fc00::2"])
def test_hub_bridge_forward_rejects_non_private_ipv4_address(address: str) -> None:
    with pytest.raises(OSError, match="private IPv4"):
        native_firewall.desired_state(
            smb=False,
            nfs_clients=[],
            hub_forwards=[
                {
                    "appId": "nextcloud",
                    "hostPort": 8081,
                    "containerPort": 80,
                    "protocol": "tcp",
                    "mode": "bridge",
                    "address": address,
                }
            ],
        )


def test_sync_persists_exact_rules_and_cleanly_removes_them(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
) -> None:
    state_path, runtime, _calls = firewall_runtime

    applied = native_firewall.sync(smb=True, nfs_clients=["192.168.50.0/24"])
    assert applied["changed"] is True
    assert runtime["live"] == runtime["permanent"] == set(applied["rules"])
    assert json.loads(state_path.read_text(encoding="utf-8")) == applied["state"]
    assert native_firewall.verify() == {"state": applied["state"], "rules": applied["rules"]}

    repeated = native_firewall.sync(smb=True, nfs_clients=["192.168.50.0/24"])
    assert repeated["changed"] is False

    removed = native_firewall.sync(smb=False, nfs_clients=[])
    assert removed["changed"] is True
    assert runtime["live"] == runtime["permanent"] == set()
    assert not state_path.exists()


def test_nas_and_hub_syncs_preserve_each_others_managed_state(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
) -> None:
    forward = {
        "appId": "nextcloud",
        "hostPort": 8081,
        "containerPort": 80,
        "protocol": "tcp",
        "mode": "bridge",
        "address": "172.20.0.4",
    }

    hub = native_firewall.sync_hub([forward])
    nas = native_firewall.sync(smb=True, nfs_clients=["192.168.50.0/24"])
    assert nas["state"]["hubForwards"] == hub["state"]["hubForwards"] == [forward]

    moved = {**forward, "address": "172.20.0.8"}
    refreshed = native_firewall.sync_hub([moved])
    assert refreshed["state"]["smb"] is True
    assert refreshed["state"]["nfsClients"] == ["192.168.50.0/24"]


def test_dlna_rules_are_fixed_private_ipv4_ports() -> None:
    state = native_firewall.desired_state(smb=False, nfs_clients=[], dlna=True)
    rules = native_firewall.managed_rules(state)

    assert len(rules) == 6
    assert sum('port port="1900" protocol="udp"' in rule for rule in rules) == 3
    assert sum('port port="8200" protocol="tcp"' in rule for rule in rules) == 3
    assert all('family="ipv4"' in rule for rule in rules)
    assert not any("0.0.0.0/0" in rule or "::/0" in rule for rule in rules)


def test_dlna_sync_preserves_nas_and_hub_state(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
) -> None:
    forward = {
        "appId": "nextcloud",
        "hostPort": 8081,
        "containerPort": 80,
        "protocol": "tcp",
        "mode": "bridge",
        "address": "172.20.0.4",
    }
    native_firewall.sync(smb=True, nfs_clients=["192.168.50.0/24"])
    native_firewall.sync_hub([forward])

    enabled = native_firewall.sync_dlna(enabled=True)
    assert enabled["state"]["smb"] is True
    assert enabled["state"]["nfsClients"] == ["192.168.50.0/24"]
    assert enabled["state"]["hubForwards"] == [forward]
    assert native_firewall.verify_dlna(enabled=True) == {
        "state": enabled["state"],
        "rules": enabled["rules"],
    }

    refreshed = native_firewall.sync(smb=False, nfs_clients=[])
    assert refreshed["state"]["dlna"] is True
    with pytest.raises(OSError, match="DLNA state"):
        native_firewall.verify_dlna(enabled=False)


def test_legacy_protocol_state_is_read_as_closed_hub_state(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
) -> None:
    state_path, runtime, _calls = firewall_runtime
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "schema": "echo.native-firewall.v1",
                "zone": "echo-public",
                "smb": False,
                "nfsClients": [],
            }
        ),
        encoding="utf-8",
    )

    result = native_firewall.verify()
    assert result["state"]["schema"] == "echo.native-firewall.v3"
    assert result["state"]["hubForwards"] == []
    assert result["state"]["dlna"] is False
    assert runtime["live"] == set()


def test_v2_state_is_read_as_closed_dlna_state(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
) -> None:
    state_path, runtime, _calls = firewall_runtime
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "schema": "echo.native-firewall.v2",
                "zone": "echo-public",
                "smb": False,
                "nfsClients": [],
                "hubForwards": [],
            }
        ),
        encoding="utf-8",
    )

    result = native_firewall.verify()
    assert result["state"]["schema"] == "echo.native-firewall.v3"
    assert result["state"]["dlna"] is False
    assert runtime["live"] == set()


def test_sync_refuses_untracked_or_missing_rules_before_mutation(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
) -> None:
    _state_path, runtime, calls = firewall_runtime
    runtime["live"].add('rule family="ipv4" source address="10.0.0.0/8" accept')

    with pytest.raises(OSError, match="runtime rich rules differ"):
        native_firewall.sync(smb=True, nfs_clients=[])

    assert not any("--add-rich-rule" in " ".join(call) for call in calls)


def test_sync_rolls_back_rules_and_state_when_state_write_fails(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path, runtime, _calls = firewall_runtime
    original = native_firewall._write_state
    writes = 0

    def fail_once(state: dict[str, Any]) -> None:
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("disk full")
        original(state)

    monkeypatch.setattr(native_firewall, "_write_state", fail_once)
    with pytest.raises(OSError, match="disk full"):
        native_firewall.sync(smb=True, nfs_clients=["10.20.0.0/16"])

    assert runtime["live"] == runtime["permanent"] == set()
    assert not state_path.exists()


def test_verify_rejects_wrong_default_zone(
    firewall_runtime: tuple[Path, dict[str, Any], list[tuple[str, ...]]],
) -> None:
    _state_path, runtime, _calls = firewall_runtime
    runtime["zone"] = "public"
    with pytest.raises(OSError, match="default zone"):
        native_firewall.verify()
