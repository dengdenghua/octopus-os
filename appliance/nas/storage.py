"""NAS 存储底座:磁盘 / 池 / 数据集的探测与管理。

设计取舍(直接来自参考 NAS 逆向的结论,见 docs/P3_provision_BASE_PLAN.md):

1. **不自研文件系统。** 参考 NAS自研了 ``trimafs2.ko.xz`` 挂 ``/fs`` 做统一命名空间,
   代价是每跟一个内核版本都要重新适配。这里改为 **ZFS dataset + bind mount**,
   零内核代码,覆盖九成场景。
2. **只读探测与写操作分离。** 探测无副作用、可高频轮询;建池/销毁/扩容属破坏性
   操作,走写路径并留给上层接审批门(参考 NAS的教训:管控面一旦能随便删池,
   事故成本不可控)。
3. **工具缺失即优雅降级。** zfs / mdadm / smartctl 任一不在 PATH,对应能力返回
   ``available: false``,绝不拖垮整个管控面 —— 与 app_registry 对 Docker 的处理一致。

所有外部命令走 :func:`_run`(``shell=False`` + 参数列表 + 超时),设备名经
:data:`_DEV_RE` 白名单校验,用户输入不可能落到 shell。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

# 设备名白名单:只允许 sd*/nvme* 的 basename,挡掉 ../ 与任意路径。
_DEV_RE = re.compile(r"^(sd[a-z]+|nvme\d+n\d+|md\d+|vd[a-z]+)$")
_POOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
_DATASET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-/]{0,255}$")

Runner = Callable[[list[str]], tuple[int, str, str]]


def _default_runner(cmd: list[str]) -> tuple[int, str, str]:
    """执行外部命令。命令不存在时返回 (127, "", "not found")。"""
    if shutil.which(cmd[0]) is None:
        return 127, "", f"{cmd[0]}: not installed"
    try:
        p = subprocess.run(  # noqa: S603 - 参数列表固定,shell=False
            cmd,
            capture_output=True,
            text=True,
            timeout=float(os.environ.get("ECHO_NAS_CMD_TIMEOUT", "15")),
            check=False,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timeout"
    except OSError as exc:  # pragma: no cover - 环境相关
        return 126, "", str(exc)


def _validate(name: str, pattern: re.Pattern[str], kind: str) -> str:
    if not pattern.match(name):
        raise ValueError(f"invalid {kind}: {name!r}")
    return name


@dataclass
class Partition:
    name: str
    size: int
    fstype: str | None
    mountpoint: str | None
    uuid: str | None


@dataclass
class Disk:
    name: str
    path: str
    size: int
    model: str
    serial: str
    removable: bool
    partitions: list[Partition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["partitions"] = [asdict(p) for p in self.partitions]
        return d


@dataclass
class Dataset:
    name: str
    used: int
    avail: int
    mountpoint: str | None


@dataclass
class Pool:
    name: str
    health: str
    size: int
    allocated: int
    free: int
    datasets: list[Dataset] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["datasets"] = [asdict(x) for x in self.datasets]
        return d


class StorageInspector:
    """只读存储探测。可在无 ZFS / 无 smartctl 的环境构造,调用时降级。"""

    def __init__(self, runner: Runner = _default_runner) -> None:
        self._run = runner

    # ── 能力探测 ────────────────────────────────────────────
    def capabilities(self) -> dict[str, bool]:
        return {
            "zfs": shutil.which("zpool") is not None
            and shutil.which("zfs") is not None,
            "mdadm": shutil.which("mdadm") is not None,
            "smart": shutil.which("smartctl") is not None,
            "lvm": shutil.which("lvs") is not None,
        }

    # ── 磁盘 ───────────────────────────────────────────────
    def list_disks(self) -> list[Disk]:
        """枚举块设备。lsblk 缺失时返回空列表(不抛)。"""
        rc, out, _ = self._run(
            [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,SIZE,TYPE,MODEL,SERIAL,MOUNTPOINT,FSTYPE,UUID,RM",
            ]
        )
        if rc != 0 or not out.strip():
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []

        def parse(node: dict[str, Any], is_disk: bool) -> Disk | None:
            name = node.get("name") or ""
            size = _to_int(node.get("size"))
            if is_disk:
                path = f"/dev/{name}"
                disk = Disk(
                    name=name,
                    path=path,
                    size=size,
                    model=(node.get("model") or "").strip() or "unknown",
                    serial=(node.get("serial") or "").strip() or "unknown",
                    removable=str(node.get("rm")) in {"1", "True", "true"},
                )
                disk.partitions = [
                    p
                    for c in node.get("children", [])
                    if (p := parse_partition(c))
                ]
                return disk
            return None

        def parse_partition(node: dict[str, Any]) -> Partition | None:
            return Partition(
                name=node.get("name") or "",
                size=_to_int(node.get("size")),
                fstype=node.get("fstype") or None,
                mountpoint=node.get("mountpoint") or None,
                uuid=node.get("uuid") or None,
            )

        disks: list[Disk] = []
        for node in data.get("blockdevices", []):
            if node.get("type") == "disk":
                d = parse(node, True)
                if d:
                    disks.append(d)
        return disks

    # ── ZFS 池 ─────────────────────────────────────────────
    def list_pools(self, with_datasets: bool = True) -> list[Pool]:
        rc, out, _ = self._run(
            ["zpool", "list", "-H", "-p", "-o", "name,health,size,allocated,free"]
        )
        if rc != 0 or not out.strip():
            return []
        pools: list[Pool] = []
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            name, health, size, alloc, free = parts[:5]
            pools.append(
                Pool(
                    name=name,
                    health=health,
                    size=_to_int(size),
                    allocated=_to_int(alloc),
                    free=_to_int(free),
                )
            )
        if with_datasets:
            ds_by_pool = self._datasets_by_pool()
            for p in pools:
                p.datasets = ds_by_pool.get(p.name, [])
        return pools

    def _datasets_by_pool(self) -> dict[str, list[Dataset]]:
        rc, out, _ = self._run(
            [
                "zfs",
                "list",
                "-H",
                "-p",
                "-t",
                "filesystem",
                "-o",
                "name,used,avail,mountpoint",
            ]
        )
        result: dict[str, list[Dataset]] = {}
        if rc != 0 or not out.strip():
            return result
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name, used, avail, mount = parts[:4]
            pool = name.split("/")[0]
            result.setdefault(pool, []).append(
                Dataset(
                    name=name,
                    used=_to_int(used),
                    avail=_to_int(avail),
                    mountpoint=mount if mount != "-" else None,
                )
            )
        return result

    # ── SMART ─────────────────────────────────────────────
    def smart_health(self, device: str) -> dict[str, Any]:
        """读取单盘 SMART 健康度。JSON 解析失败时退化为返回原始文本。"""
        dev = _validate(os.path.basename(device), _DEV_RE, "device")
        rc, out, err = self._run(["smartctl", "-j", "-H", "-A", f"/dev/{dev}"])
        if rc == 127:
            return {"available": False, "device": dev, "error": err}
        if not out.strip():
            return {"available": False, "device": dev, "error": err or "no output"}
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return {"available": True, "device": dev, "raw": out[:4000]}
        return {
            "available": True,
            "device": dev,
            "passed": data.get("smart_status", {}).get("passed"),
            "model": data.get("model_name"),
            "serial": data.get("serial_number"),
            "temperature": (data.get("temperature") or {}).get("current"),
            "powerOnHours": (data.get("power_on_time") or {}).get("hours"),
            # rc 的 bit1 置位表示盘已故障预警 —— smartctl 的惯例,别只看 passed。
            "exitCode": rc,
        }


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "Dataset",
    "Disk",
    "Partition",
    "Pool",
    "Runner",
    "StorageInspector",
]
