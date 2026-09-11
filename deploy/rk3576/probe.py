#!/usr/bin/env python3
"""Read-only prerequisites for Echo on an existing vendor RK3576 Debian host."""
import json
import platform
import shutil
from pathlib import Path


def evaluate(machine, os_release, compatible, memory_kib, docker, systemd):
    checks = {
        "arm64": machine in ("aarch64", "arm64"),
        "rk3576": "rockchip,rk3576" in compatible.split("\0"),
        "debian12": os_release.get("ID") == "debian" and os_release.get("VERSION_ID") == "12",
        "memory_4g_class": memory_kib >= 3_500_000,
        "docker_cli": docker,
        "systemd": systemd,
    }
    return {"schema": "echo.rk3576.preflight.v1", "checks": checks,
            "prerequisites_met": all(checks.values()),
            "hardware_validated": False,
            "note": "Prerequisites only; Docker daemon, ARM64 image and board load tests remain required."}


def read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeError):
        return ""


def main():
    release = dict(line.split("=", 1) for line in read("/etc/os-release").splitlines() if "=" in line)
    release = {key: value.strip('"') for key, value in release.items()}
    memory = next((int(line.split()[1]) for line in read("/proc/meminfo").splitlines()
                   if line.startswith("MemTotal:")), 0)
    result = evaluate(platform.machine(), release, read("/proc/device-tree/compatible"),
                      memory, bool(shutil.which("docker")), Path("/run/systemd/system").is_dir())
    result["board_model"] = read("/proc/device-tree/model").rstrip("\0")
    result["kernel"] = platform.release()
    result["vendor_devices"] = {path: Path(path).exists() for path in
                                ("/dev/dri", "/dev/rknpu", "/dev/rtc0", "/dev/watchdog")}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["prerequisites_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
