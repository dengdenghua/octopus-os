"""echo-runtime · materializer(capability-plane.md §B「落地」半边)。

把下载的资产落到产品**现有磁盘布局**:prompt-skill → ``<skills_dir>/<slug>/SKILL.md``,
之后产品自己的 loader(``register_market_skills``)按现有逻辑接管(prompt handler、enabled 闸)。

**安全分水岭**:只落地 ``kind=data``(prompt_pack 等声明式资产);``kind=code``(带执行器的技能/插件)
只能作广告——执行代码永远留在产品本地、不过线(三种 skill kind 决策)。
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import tarfile
import tempfile
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FuturesTimeoutError,
)
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO

from .client import DEFAULT_BASE, AssetPayload, RegistryClient

# 冷启动(空目录、97 个技能全要同步)串行拉取实测 ~150s——每个技能是独立 HTTP 往返(部分还要
# 再拉一次 bundle),线程池并发把它压到并发 N 批。httpx 同步调用天然线程安全,无需上 asyncio。
_DEFAULT_WORKERS = 16

# Full bundles are compressed on the wire (and independently capped by
# RegistryClient), so their expanded size needs its own limits.  The bundled
# catalog's largest normal skill is currently well below these values (~230
# members / ~6 MiB), leaving room for richer reference packs without allowing
# a small gzip body to expand without bound.
DEFAULT_BUNDLE_MAX_MEMBERS = 1_024
DEFAULT_BUNDLE_MAX_MEMBER_BYTES = 32 * 1024 * 1024
DEFAULT_BUNDLE_MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
_BUNDLE_COPY_CHUNK_BYTES = 64 * 1024

# 可安全落地的类型:type=skill 资产 = SKILL.md prompt-pack —— body 被产品当 **prompt 注入**、
# 从不作为代码执行,故落地安全(registry 把 skill 粗标 kind=code 是为将来签名/沙箱策略,
# 不代表 body 是可执行码)。真正可执行的(plugin 等集成)默认不落地,需 allow_code 显式放开。
SAFE_TYPES = {"skill"}
_SAFE_SKILL_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _is_prompt_pack(p: AssetPayload) -> bool:
    return p.type in SAFE_TYPES


def _skill_md(p: AssetPayload) -> str:
    """registry 的 skill body 无 frontmatter → 用信封 name/description 重建,落成 agent 现有格式。"""
    name = (p.name or p.slug).strip()
    desc = " ".join((p.description or "").split())  # 压成单行,贴 SKILL.md frontmatter
    return f"---\nname: {name}\ndescription: {desc}\nsource: registry\n---\n\n{p.body.strip()}\n"


def _safe_skill_slug(p: AssetPayload) -> str:
    prefix, sep, slug = p.id.partition("/")
    if prefix != "skill" or sep != "/" or "/" in slug:
        raise ValueError(f"unsafe skill id from registry payload: {p.id!r}")
    if not _SAFE_SKILL_SLUG_RE.fullmatch(slug):
        raise ValueError(f"unsafe skill slug from registry payload: {slug!r}")
    return slug


def _verify_bundle_checksum(p: AssetPayload, data: bytes) -> None:
    expected = p.bundle.checksum if p.bundle else None
    if not expected:
        return
    expected = expected.removeprefix("sha256:")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"bundle checksum mismatch for {p.id}: expected {expected} got {actual}")


def _verify_body_checksum(p: AssetPayload) -> None:
    expected = p.content.checksum if p.content else None
    if not expected:
        return
    expected = expected.removeprefix("sha256:")
    actual = hashlib.sha256(p.body.encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError(f"checksum mismatch for {p.id}: expected {expected} got {actual}")


def _ensure_safe_skill_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"skill dir must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"skill dir must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    _ensure_safe_skill_dir(path.parent)
    tmp: Path | None = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as f:
        tmp = Path(f.name)
        f.write(content)
        f.flush()
    try:
        tmp.replace(path)
    except Exception:
        if tmp is not None and tmp.exists():
            tmp.unlink()
        raise


def _positive_bundle_limit(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _safe_bundle_member_parts(member: tarfile.TarInfo, slug: str) -> tuple[str, ...]:
    """Return a portable relative path for one already-decoded tar header."""

    name = member.name
    posix_path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in posix_path.parts
    ):
        raise ValueError(f"unsafe path in bundle: {name}")
    parts = posix_path.parts
    if not parts or parts[0] != slug:
        raise ValueError(f"bundle member outside skill dir {slug!r}: {name}")
    if parts == (slug,) and not member.isdir():
        raise ValueError(f"skill root must be a directory in bundle: {name}")
    return parts


def _copy_bounded_member(
    source: IO[bytes],
    target: IO[bytes],
    *,
    member: tarfile.TarInfo,
    max_member_bytes: int,
    max_extracted_bytes: int,
    extracted_before: int,
) -> int:
    """Copy one regular member without ever writing a byte past a limit."""

    copied = 0
    while True:
        # The +1 probe detects a source that yields more data than its tar
        # header declared.  tarfile's normal ExFileObject stops at size, but
        # keeping this independent check protects future/custom readers too.
        read_size = min(
            _BUNDLE_COPY_CHUNK_BYTES,
            member.size - copied + 1,
            max_member_bytes - copied + 1,
            max_extracted_bytes - extracted_before - copied + 1,
        )
        chunk = source.read(max(1, read_size))
        if not chunk:
            break
        next_copied = copied + len(chunk)
        if next_copied > max_member_bytes:
            raise ValueError(
                f"actual size for bundle member {member.name!r} exceeds "
                f"{max_member_bytes} byte limit"
            )
        if extracted_before + next_copied > max_extracted_bytes:
            raise ValueError(
                "cumulative actual bundle size exceeds "
                f"{max_extracted_bytes} byte limit at {member.name!r}"
            )
        if next_copied > member.size:
            raise ValueError(
                f"actual size for bundle member {member.name!r} exceeds declared size {member.size}"
            )
        target.write(chunk)
        copied = next_copied
    if copied != member.size:
        raise ValueError(
            f"bundle member size mismatch for {member.name!r}: declared {member.size}, got {copied}"
        )
    return copied


def _extract_skill_bundle(
    tar: tarfile.TarFile,
    skills_dir: Path,
    slug: str,
    *,
    max_members: int,
    max_member_bytes: int,
    max_extracted_bytes: int,
) -> Path:
    """Stream a bounded full-bundle into staging, then atomically replace it."""

    skills_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{slug}.", dir=skills_dir) as tmp_name:
        tmp_root = Path(tmp_name)
        seen_paths: set[tuple[str, ...]] = set()
        member_count = 0
        declared_total = 0
        extracted_total = 0
        has_skill_md = False

        # Iterate instead of getmembers()/extractall(): stop parsing at the
        # member cap and stop decompression before an expanded-byte cap is
        # crossed.  All partial output remains confined to the temp directory.
        for member in tar:
            member_count += 1
            if member_count > max_members:
                raise ValueError(f"bundle exceeds {max_members} member limit")
            parts = _safe_bundle_member_parts(member, slug)
            if parts in seen_paths:
                raise ValueError(f"duplicate path in bundle: {member.name}")
            seen_paths.add(parts)

            if member.issym() or member.islnk():
                raise ValueError(f"link not allowed in bundle: {member.name}")
            if member.issparse():
                raise ValueError(f"sparse file not allowed in bundle: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"unsupported file type in bundle: {member.name}")
            if not isinstance(member.size, int) or member.size < 0:
                raise ValueError(f"invalid declared size in bundle: {member.name}")

            target = tmp_root.joinpath(*parts)
            if member.isdir():
                if member.size != 0:
                    raise ValueError(f"directory has non-zero size in bundle: {member.name}")
                target.mkdir(parents=True, exist_ok=True)
                continue

            if member.size > max_member_bytes:
                raise ValueError(
                    f"declared size for bundle member {member.name!r} exceeds "
                    f"{max_member_bytes} byte limit ({member.size})"
                )
            declared_total += member.size
            if declared_total > max_extracted_bytes:
                raise ValueError(
                    "cumulative declared bundle size exceeds "
                    f"{max_extracted_bytes} byte limit at {member.name!r}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError(f"conflicting path in bundle: {member.name}")
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"unable to read bundle member: {member.name}")
            with source, target.open("xb") as output:
                extracted_total += _copy_bounded_member(
                    source,
                    output,
                    member=member,
                    max_member_bytes=max_member_bytes,
                    max_extracted_bytes=max_extracted_bytes,
                    extracted_before=extracted_total,
                )
            if parts == (slug, "SKILL.md"):
                has_skill_md = True

        if not has_skill_md:
            raise ValueError(f"bundle missing required file: {slug}/SKILL.md")
        staged = tmp_root / slug
        md = staged / "SKILL.md"
        if not md.is_file():
            raise ValueError(f"bundle missing required file after extraction: {slug}/SKILL.md")
        dest = skills_dir / slug
        backup = tmp_root / f"{slug}.previous"
        if dest.exists():
            if dest.is_dir() and not dest.is_symlink():
                dest.rename(backup)
            else:
                dest.rename(backup)
        try:
            staged.rename(dest)
        except Exception:
            if backup.exists() and not dest.exists():
                backup.rename(dest)
            raise
        if backup.exists():
            if backup.is_dir() and not backup.is_symlink():
                shutil.rmtree(backup)
            else:
                backup.unlink()
    return skills_dir / slug / "SKILL.md"


def materialize_skill(
    p: AssetPayload,
    skills_dir: Path,
    *,
    client: RegistryClient | None = None,
    max_bundle_members: int = DEFAULT_BUNDLE_MAX_MEMBERS,
    max_bundle_member_bytes: int = DEFAULT_BUNDLE_MAX_MEMBER_BYTES,
    max_bundle_extracted_bytes: int = DEFAULT_BUNDLE_MAX_EXTRACTED_BYTES,
) -> Path:
    """落地一个技能到 ``<skills_dir>/<slug>/``。**有 full-bundle 则取整目录 tar.gz 解压**(带
    scripts/refs/requirements);否则只写 ``SKILL.md``(body-only)。返回 SKILL.md 路径。"""
    skills_dir = Path(skills_dir)
    slug = _safe_skill_slug(p)
    if p.bundle and p.bundle.ref:
        max_bundle_members = _positive_bundle_limit(max_bundle_members, name="max_bundle_members")
        max_bundle_member_bytes = _positive_bundle_limit(
            max_bundle_member_bytes, name="max_bundle_member_bytes"
        )
        max_bundle_extracted_bytes = _positive_bundle_limit(
            max_bundle_extracted_bytes, name="max_bundle_extracted_bytes"
        )
        c = client or RegistryClient(DEFAULT_BASE)
        data = c.fetch_bundle(p.id, expected_size=p.bundle.size)
        _verify_bundle_checksum(p, data)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            return _extract_skill_bundle(
                tar,
                skills_dir,
                slug,
                max_members=max_bundle_members,
                max_member_bytes=max_bundle_member_bytes,
                max_extracted_bytes=max_bundle_extracted_bytes,
            )
    dest = skills_dir / slug
    md = dest / "SKILL.md"
    _verify_body_checksum(p)
    _atomic_write_text(md, _skill_md(p))
    return md


def _sync_one(
    slug: str, skills_dir: Path, client: RegistryClient, allow_code: bool
) -> tuple[str, str | None, str | None]:
    """拉取 + 落地单个技能。返回 (slug, ok_path_or_None, skip_or_error_reason_or_None)。"""
    asset_id = slug if "/" in slug else f"skill/{slug}"
    try:
        p = client.fetch(asset_id)
        if not _is_prompt_pack(p) and not allow_code:
            return (
                slug,
                None,
                f"type={p.type or '?'}/kind={p.kind or '?'}:可执行资产默认不落地(--allow-code 放开)",
            )
        md = materialize_skill(p, skills_dir, client=client)
        return slug, str(md), None
    except Exception as exc:  # noqa: BLE001 — 单个坏不影响整批
        return slug, None, f"__error__:{exc}"


def sync_skills(
    slugs: list[str],
    skills_dir: Path | str,
    *,
    base_url: str = DEFAULT_BASE,
    allow_code: bool = False,
    max_workers: int = _DEFAULT_WORKERS,
    request_timeout_s: float | None = None,
    total_timeout_s: float | None = None,
    client: RegistryClient | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """拉取 + 校验 + 落地一批技能(**并发**,httpx 同步调用线程安全)。

    ``request_timeout_s`` and ``total_timeout_s`` only narrow best-effort
    callers such as packaged-catalog startup refreshes.  ``None`` preserves
    :class:`RegistryClient`'s historical timeout and the synchronous
    CLI/bootstrap contract.  On a total deadline, completed items are returned,
    queued work is cancelled, and every unfinished slug is surfaced in
    ``errors``; already-running requests finish behind their shorter request
    timeout without extending the caller's startup path.

    返回 (ok, skipped, errors),各元素 (slug, info)。"""
    registry_client = client or (
        RegistryClient(base_url)
        if request_timeout_s is None
        else RegistryClient(base_url, timeout=request_timeout_s)
    )
    skills_dir = Path(skills_dir)
    ok: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    if not slugs:
        return ok, skipped, errors

    pool = ThreadPoolExecutor(max_workers=min(max_workers, len(slugs)))
    futures: dict[Future[tuple[str, str | None, str | None]], str] = {
        pool.submit(_sync_one, slug, skills_dir, registry_client, allow_code): slug
        for slug in slugs
    }
    collected: set[Future[tuple[str, str | None, str | None]]] = set()
    deadline_expired = False

    def _collect(future: Future[tuple[str, str | None, str | None]]) -> None:
        slug, path, reason = future.result()
        collected.add(future)
        if path:
            ok.append((slug, path))
        elif reason and reason.startswith("__error__:"):
            errors.append((slug, reason.removeprefix("__error__:")))
        elif reason:
            skipped.append((slug, reason))

    try:
        for future in as_completed(futures, timeout=total_timeout_s):
            _collect(future)
    except FuturesTimeoutError:
        deadline_expired = True
        # Harvest results that crossed the finish line with the timeout before
        # labelling the remainder.  This is non-blocking and keeps result loss
        # at the deadline edge deterministic.
        for future in futures:
            if future not in collected and future.done() and not future.cancelled():
                _collect(future)
        timeout_label = max(0.0, float(total_timeout_s or 0.0))
        for future, slug in futures.items():
            if future in collected:
                continue
            future.cancel()
            errors.append((slug, f"refresh deadline exceeded after {timeout_label:.3f}s"))
    finally:
        pool.shutdown(wait=not deadline_expired, cancel_futures=deadline_expired)
    return ok, skipped, errors
