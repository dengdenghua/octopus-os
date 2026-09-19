from pathlib import Path

import yaml

from deploy.rk3576.probe import evaluate


def test_probe_requires_vendor_board_arch_os_and_resources():
    good = (
        "aarch64",
        {"ID": "debian", "VERSION_ID": "12"},
        "rockchip,rk3576-evb1-v10\0rockchip,rk3576\0",
        4_000_000,
        True,
        True,
    )
    assert evaluate(*good)["prerequisites_met"]
    assert not evaluate(*good)["hardware_validated"]
    for index, value in [
        (0, "x86_64"),
        (1, {"ID": "ubuntu"}),
        (2, "rockchip,rk3588"),
        (3, 1_000_000),
        (4, False),
        (5, False),
    ]:
        bad = list(good)
        bad[index] = value
        assert not evaluate(*bad)["prerequisites_met"]


def test_overlay_does_not_grant_hub_device_or_host_storage_access():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "deploy/rk3576/compose.yaml").read_text())
    service = compose["services"]["echo"]
    assert service["platform"] == "linux/arm64"
    assert service["ports"] == ["127.0.0.1:18000:8000"]
    assert service["volumes"] == ["echo-state:/data"]
    assert not service.get("privileged")
    assert not service.get("devices")
    assert service.get("network_mode") != "host"
