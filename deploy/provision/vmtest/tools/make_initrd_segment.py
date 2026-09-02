#!/usr/bin/env python3
"""往 d-i 的 initrd.gz 末尾追加一段 gzip 压缩的 newc cpio。

原理:Linux 内核 initramfs 解包器支持多个"压缩 cpio 段"首尾拼接
(这正是 EFI 微码 pre-sale 段的机制,也是参考 NAS initrd 多段结构的成因)。
利用它,无需重打包 ISO 就能把 preseed / 载荷送进安装器环境 ——
QEMU 直接 -kernel vmlinuz -initrd initrd-appended.gz 即可验证。

用法:
  python make_initrd_segment.py <原initrd.gz> <输出initrd.gz> <载荷根目录>...

载荷根目录内的每个文件按其相对路径进入 cpio(POSIX 分隔符)。
"""
import gzip
import os
import struct
import sys
from pathlib import Path


def newc_header(ino: int, mode: int, namesize: int, filesize: int) -> bytes:
    # newc 格式:6 字节魔数 + 13 个 8 位十六进制字段,共 110 字节
    fields = [ino, mode, 0, 0, 1, 0, filesize, 0, 0, 0, 0, namesize, 0]
    return b"070701" + b"".join(b"%08X" % f for f in fields)


def pad4(buf: bytearray) -> None:
    while len(buf) % 4:
        buf.append(0)


def build_cpio(root: Path) -> bytes:
    buf = bytearray()
    ino = 300  # 避开主 initrd 已用的 inode 编号,从任意高位起
    files = sorted(root.rglob("*"))
    if not files:
        sys.exit(f"载荷目录为空: {root}")
    for p in files:
        rel = p.relative_to(root).as_posix()
        ino += 1
        name = rel.encode("utf-8") + b"\x00"
        if p.is_dir():
            hdr = newc_header(ino, 0o40755, len(name), 0)
            buf += hdr + name
            pad4(buf)
            continue
        if not p.is_file():
            print(f"  跳过非常规文件: {rel}")
            continue
        data = p.read_bytes()
        hdr = newc_header(ino, 0o100644, len(name), len(data))
        buf += hdr + name
        pad4(buf)
        buf += data
        pad4(buf)
        print(f"  + /{rel}  ({len(data)} B)")
    # 结尾必须叫 TRAILER!!!,内核据此知道本段结束
    name = b"TRAILER!!!\x00"
    buf += newc_header(0, 0, len(name), 0) + name
    pad4(buf)
    return bytes(buf)


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    payload_roots = [Path(a) for a in sys.argv[3:]]

    out = bytearray(src.read_bytes())
    print(f"主 initrd: {src} ({len(out)} B)")
    for root in payload_roots:
        seg = gzip.compress(build_cpio(root), mtime=0)
        out += seg
        print(f"追加段: {root} -> {len(seg)} B (gzip)")
    Path(dst).write_bytes(bytes(out))
    print(f"输出: {dst} ({len(out)} B)")


if __name__ == "__main__":
    main()
