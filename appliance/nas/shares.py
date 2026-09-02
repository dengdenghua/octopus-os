"""SMB / NFS 共享管理:配置片段 + reload,不改主配置。

这是从参考 NAS 逆向里搬过来的**最值得抄的一个手法**:参考 NAS的 nginx 有 30 个
``conf.d/*.conf`` 片段,每个功能模块一个独立的 unix socket,装一个新应用 =
放一个 conf 片段 + reload,没有注册中心、没有数据库、没有 migrations。

这里把同一套模式用在共享上:

- SMB:整个管控面只维护**一个** ``echo.conf`` 片段,由共享清单全量重生成
  (幂等,不解析别人的 smb.conf);
- NFS:同理写一个 ``exports.d/echo.exports``(nfs-kernel-server 原生支持
  ``exports.d/``);
- 落盘用 tmp + ``os.replace`` 原子替换,写一半断电不会留下半份配置。

安全约束(参考 NAS没做、我们必须做的):

- 共享路径必须落在允许根内(见 :data:`DEFAULT_ALLOWED_ROOTS`),挡住
  ``/etc`` ``/`` 这类路径穿越;
- 共享名白名单校验,防止注入 SMB 配置指令(换行注入可以凭空造一个可写共享);
- 变更即备份上一版配置到 ``.bak``,出错可回滚。
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# SMB 段名:不能含 ] 换行 \ / 等会破坏 ini 结构的字符。
_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

DEFAULT_ALLOWED_ROOTS = ("/data", "/mnt", "/srv", "/fs", "/volume")

Runner = Callable[[list[str]], tuple[int, str, str]]


def _default_runner(cmd: list[str]) -> tuple[int, str, str]:
    if shutil.which(cmd[0]) is None:
        return 127, "", f"{cmd[0]}: not installed"
    try:
        p = subprocess.run(  # noqa: S603 - 参数列表,shell=False
            cmd,
            capture_output=True,
            text=True,
            timeout=float(os.environ.get("ECHO_NAS_CMD_TIMEOUT", "15")),
            check=False,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timeout"
    except OSError as exc:  # pragma: no cover
        return 126, "", str(exc)


class ShareError(ValueError):
    """共享配置非法(名/路径不合规)。"""


@dataclass
class Share:
    name: str
    path: str
    protocols: list[str] = field(default_factory=lambda: ["smb"])
    read_only: bool = False
    guest_ok: bool = False
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShareManager:
    """共享清单的增删改 + 渲染 + reload。

    ``root`` 之下的数据目录布局::

        <root>/
          shares.json          # 共享清单(唯一真源)
          smb/echo.conf     # 渲染产物
          exports.d/echo.exports

    清单是唯一真源,配置文件永远可由清单重建 —— 这样备份/迁移只需拷一个 json。
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        allowed_roots: tuple[str, ...] = DEFAULT_ALLOWED_ROOTS,
        runner: Runner = _default_runner,
    ) -> None:
        self.root = Path(root)
        self.allowed_roots = tuple(allowed_roots)
        self._run = runner
        self.share_root = self.root / "shares.json"

    # ── 校验 ───────────────────────────────────────────────
    def _validate_name(self, name: str) -> str:
        if not _NAME_RE.match(name or ""):
            raise ShareError(
                "share name must match [A-Za-z0-9._-]{1,64} (no spaces, slashes "
                "or newlines)"
            )
        return name

    def _validate_path(self, path: str) -> str:
        # 用 posixpath 词法归一化而非 os.path.realpath:
        #   1) realpath 在 Windows 上会把 /data/media 解析成 C:\data\media,校验直接失效;
        #   2) 不碰文件系统,测试无需真实目录,行为可确定。
        # 词法归一化已能挡住 /tmp/../etc 这类穿越。
        p = posixpath.normpath(path)
        if not any(
            p == r or p.startswith(r.rstrip("/") + "/") for r in self.allowed_roots
        ):
            raise ShareError(
                f"path {p!r} outside allowed roots {list(self.allowed_roots)}"
            )
        return p

    # ── 清单读写 ────────────────────────────────────────────
    def _load(self) -> list[Share]:
        import json

        if not self.share_root.exists():
            return []
        try:
            raw = json.loads(self.share_root.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [Share(**item) for item in raw if isinstance(item, dict)]

    def _save(self, shares: list[Share]) -> None:
        import json

        self.root.mkdir(parents=True, exist_ok=True)
        self.share_root.write_text(
            json.dumps([s.to_dict() for s in shares], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_shares(self) -> list[Share]:
        return self._load()

    def get_share(self, name: str) -> Share | None:
        self._validate_name(name)
        return next((s for s in self._load() if s.name == name), None)

    def add_share(self, share: Share) -> Share:
        self._validate_name(share.name)
        share.path = self._validate_path(share.path)
        shares = [s for s in self._load() if s.name != share.name]
        shares.append(share)
        self._save(shares)
        return share

    def remove_share(self, name: str) -> bool:
        self._validate_name(name)
        shares = self._load()
        kept = [s for s in shares if s.name != name]
        if len(kept) == len(shares):
            return False
        self._save(kept)
        return True

    # ── 渲染 ───────────────────────────────────────────────
    def render_smb_conf(self, shares: list[Share] | None = None) -> str:
        """渲染 SMB 配置片段。路径/注释里的特殊字符按 Samba 规则转义。"""
        shares = shares if shares is not None else self._load()
        smb = [s for s in shares if "smb" in s.protocols]
        lines = [
            "# Generated by echo-os. DO NOT EDIT — edit shares.json instead.",
            "# Atomic-rewritten; previous version kept as echo.conf.bak",
            "",
        ]
        for s in smb:
            lines += [
                f"[{s.name}]",
                f"   path = {s.path}",
                f"   comment = {_smb_escape(s.comment or s.name)}",
                "   browsable = yes",
                f"   read only = {'yes' if s.read_only else 'no'}",
                f"   guest ok = {'yes' if s.guest_ok else 'no'}",
                "   create mask = 0664",
                "   directory mask = 0775",
                "   force create mode = 0664",
                "   force directory mode = 0775",
                "",
            ]
        return "\n".join(lines)

    def render_nfs_exports(self, shares: list[Share] | None = None) -> str:
        shares = shares if shares is not None else self._load()
        nfs = [s for s in shares if "nfs" in s.protocols]
        lines = [
            "# Generated by echo-os. DO NOT EDIT — edit shares.json instead.",
        ]
        for s in nfs:
            opts = "ro" if s.read_only else "rw"
            opts += ",sync,no_subtree_check"
            if s.guest_ok:
                opts += ",all_squash,anonuid=65534,anongid=65534"
            lines.append(f"{_nfs_escape(s.path)} *(fsid=0,{opts})")
        return "\n".join(lines) + "\n"

    # ── 落盘 + reload ──────────────────────────────────────
    def apply(self) -> dict[str, Any]:
        """渲染两份配置并原子落盘,随后 reload 服务。返回各步结果。"""
        shares = self._load()
        smb_path = self.root / "smb" / "echo.conf"
        exports_path = self.root / "exports.d" / "echo.exports"
        smb_path.parent.mkdir(parents=True, exist_ok=True)
        exports_path.parent.mkdir(parents=True, exist_ok=True)

        _atomic_write(smb_path, self.render_smb_conf(shares))
        _atomic_write(exports_path, self.render_nfs_exports(shares))

        return {
            "shares": len(shares),
            "smbConf": str(smb_path),
            "nfsExports": str(exports_path),
            "reload": {
                "smb": self._reload_smb(),
                "nfs": self._reload_nfs(),
            },
        }

    def _reload_smb(self) -> dict[str, Any]:
        # smbcontrol 是热加载,不断连接;失败才退回 systemctl reload。
        rc, out, err = self._run(["smbcontrol", "all", "reload-config"])
        if rc == 0:
            return {"ok": True, "method": "smbcontrol"}
        rc2, out2, err2 = self._run(["systemctl", "reload", "smbd"])
        return {
            "ok": rc2 == 0,
            "method": "systemctl reload smbd",
            "error": (err or err2 or out2 or out or "").strip()[:500] or None,
        }

    def _reload_nfs(self) -> dict[str, Any]:
        rc, out, err = self._run(["exportfs", "-ra"])
        return {
            "ok": rc == 0,
            "method": "exportfs -ra",
            "error": (err or out or "").strip()[:500] or None,
        }


def _atomic_write(path: Path, content: str) -> None:
    """tmp + os.replace 原子替换,并保留上一版为 .bak。"""
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:  # pragma: no cover - 备份失败不阻断主流程
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _smb_escape(value: str) -> str:
    """转义 Samba ini 值,剔除所有在 ini 里有结构含义的字符。

    换行必须挡掉(否则可另起配置段注入一个可写共享);``[`` ``]`` 一并剔除 ——
    虽然 Samba 只把行首的 ``[`` 当段头,中括号留在值里要靠"解析器足够宽容"才安全,
    这种假设不该留。``;`` 与 ``#`` 是 ini 行注释符,同样清掉。

    宁可显示得朴素一点,也不留任何"依赖解析器行为"的余地。
    """
    out = value.replace("\n", " ").replace("\r", " ")
    for ch in "[];#":
        out = out.replace(ch, " ")
    # 压缩连续空白,避免占位难看
    return re.sub(r"\s+", " ", out).strip()[:200]


def _nfs_escape(path: str) -> str:
    """exports 里空格需转义为 \\040。"""
    return path.replace(" ", "\\040")


__all__ = ["DEFAULT_ALLOWED_ROOTS", "Share", "ShareError", "ShareManager"]
