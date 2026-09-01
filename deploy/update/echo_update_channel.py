#!/usr/bin/env python3
"""Fetch one signed Echo OS update bundle into an immutable local cache."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

MAX_CHANNEL_CONFIG_BYTES = 2048
MAX_KEYRING_BYTES = 16 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
GPG_TIMEOUT_SECONDS = 30
MAX_CACHED_BUNDLES = 2
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HOST = re.compile(r"^[0-9A-Za-z.-]+$")


class ChannelError(RuntimeError):
    pass


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def read_bounded_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ChannelError(f"{label} is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
            raise ChannelError(f"{label} is empty, oversized or unsafe")
        raw = bytearray()
        while len(raw) <= maximum:
            block = os.read(descriptor, min(DOWNLOAD_CHUNK_BYTES, maximum + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(descriptor)
        if (
            len(raw) > maximum
            or len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise ChannelError(f"{label} changed while reading or exceeds its bound")
        return bytes(raw)
    finally:
        os.close(descriptor)


def load_channel_url(config: Path) -> str:
    raw = read_bounded_regular(config, MAX_CHANNEL_CONFIG_BYTES, "update channel config")
    try:
        text = raw.decode("ascii")
    except UnicodeError as error:
        raise ChannelError("update channel config must be ASCII") from error
    if text.endswith("\n"):
        text = text[:-1]
    if not text or any(character.isspace() for character in text):
        raise ChannelError("update channel config must contain one whitespace-free URL")
    if "%" in text or "\\" in text:
        raise ChannelError("update channel URL cannot contain escapes or backslashes")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise ChannelError("update channel URL is malformed") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or HOST.fullmatch(parsed.hostname) is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
        or not parsed.path.startswith("/")
        or parsed.path in {"", "/"}
        or "//" in parsed.path
        or any(part in {"", ".", ".."} for part in parsed.path.strip("/").split("/"))
    ):
        raise ChannelError("update channel must be one credential-free HTTPS directory URL")
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


def artifact_url(channel: str, name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ChannelError("update artifact name is unsafe")
    return f"{channel}/{quote(name, safe='')}"


def create_opener():  # noqa: ANN201
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return build_opener(RejectRedirects(), HTTPSHandler(context=context))


def download(url: str, destination: Path, maximum: int, expected_sha256: str | None = None) -> str:
    if maximum <= 0 or (expected_sha256 is not None and SHA256.fullmatch(expected_sha256) is None):
        raise ChannelError("download bound or expected digest is invalid")
    if destination.exists() or destination.is_symlink():
        raise ChannelError("download destination must be one new file")
    request = Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
            "User-Agent": "Echo-OS-Update/1",
        },
        method="GET",
    )
    descriptor = os.open(
        destination,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC,
        0o600,
    )
    digest = hashlib.sha256()
    total = 0
    try:
        try:
            response = create_opener().open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise ChannelError("update channel request failed") from error
        with response:
            if response.getcode() != 200 or response.geturl() != url:
                raise ChannelError("update channel redirected or returned a non-success status")
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            if encoding not in {"", "identity"}:
                raise ChannelError("update channel returned transformed content")
            length_header = response.headers.get("Content-Length")
            if length_header is not None and (
                not length_header.isdecimal() or not 1 <= int(length_header) <= maximum
            ):
                raise ChannelError("update channel response length is outside its bound")
            while True:
                block = response.read(min(DOWNLOAD_CHUNK_BYTES, maximum + 1 - total))
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    raise ChannelError("update channel response exceeds its bound")
                digest.update(block)
                offset = 0
                while offset < len(block):
                    written = os.write(descriptor, block[offset:])
                    if written <= 0:
                        raise ChannelError("cannot write update cache file")
                    offset += written
            if total == 0:
                raise ChannelError("update channel returned an empty response")
            if length_header is not None and total != int(length_header):
                raise ChannelError("update channel response length did not match its header")
        actual = digest.hexdigest()
        if expected_sha256 is not None and actual != expected_sha256:
            raise ChannelError("downloaded update artifact has the wrong SHA-256")
        os.fsync(descriptor)
        return actual
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)


def load_verifier(path: Path) -> ModuleType:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ChannelError("update bundle verifier must be an absolute regular file")
    specification = importlib.util.spec_from_file_location("echo_update_bundle_verifier", path)
    if specification is None or specification.loader is None:
        raise ChannelError("cannot load update bundle verifier")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    for name in ("parse_manifest", "verify_bundle_identity", "PAYLOAD_LIMITS"):
        if not hasattr(module, name):
            raise ChannelError("update bundle verifier lacks the channel interface")
    return module


def verify_detached_signature(gpgv: Path, keyring: Path, signature: Path, manifest: Path) -> None:
    if (
        not gpgv.is_absolute()
        or gpgv.is_symlink()
        or not gpgv.is_file()
        or not os.access(gpgv, os.X_OK)
    ):
        raise ChannelError("gpgv must be one absolute executable regular file")
    read_bounded_regular(keyring, MAX_KEYRING_BYTES, "update keyring")
    try:
        completed = subprocess.run(
            (str(gpgv), "--keyring", str(keyring), str(signature), str(manifest)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GPG_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ChannelError("cannot run update signature verification") from error
    if completed.returncode != 0:
        raise ChannelError("update channel manifest signature is not trusted")


def validate_cache_root(cache: Path, expected_uid: int = 0) -> Path:
    if not cache.is_absolute() or cache.is_symlink():
        raise ChannelError("update cache must be an absolute non-symlink directory")
    try:
        cache = cache.resolve(strict=True)
    except OSError as error:
        raise ChannelError("update cache is unavailable") from error
    metadata = cache.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ChannelError("update cache must be private and owned by the updater")
    return cache


def remove_staging(cache: Path, staging: Path) -> None:
    try:
        staging.relative_to(cache)
    except ValueError as error:
        raise ChannelError("refusing to remove a staging directory outside the update cache") from error
    if not staging.name.startswith(".incoming-") or staging.is_symlink():
        raise ChannelError("refusing to remove an unsafe update staging directory")
    if staging.exists():
        os.chmod(staging, 0o700)
        shutil.rmtree(staging)


def clean_abandoned_staging(cache: Path) -> None:
    removed = False
    for child in cache.iterdir():
        if not child.name.startswith(".incoming-"):
            continue
        if child.is_symlink() or not child.is_dir():
            raise ChannelError("update cache contains an unsafe staging entry")
        remove_staging(cache, child)
        removed = True
    if removed:
        fsync_directory(cache)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def vacuum_cache(cache: Path, protected: Path, maximum: int = MAX_CACHED_BUNDLES) -> None:
    """Retain the authenticated target plus the newest bounded history."""
    if maximum < 1 or protected.parent != cache or VERSION.fullmatch(protected.name) is None:
        raise ChannelError("update cache retention request is invalid")
    candidates: list[tuple[int, str, Path]] = []
    for child in cache.iterdir():
        if VERSION.fullmatch(child.name) is None:
            continue
        metadata = child.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != cache.stat().st_uid
            or stat.S_IMODE(metadata.st_mode) != 0o555
        ):
            raise ChannelError("update cache contains an unsafe version directory")
        candidates.append((metadata.st_mtime_ns, child.name, child))

    retained = {protected}
    for _mtime, _name, child in sorted(candidates, reverse=True):
        if len(retained) >= maximum:
            break
        retained.add(child)
    removed = False
    for _mtime, _name, child in candidates:
        if child in retained:
            continue
        # All candidates are direct cache children and were checked without
        # following symlinks above.  The cache itself is private/root-owned.
        os.chmod(child, 0o700)
        shutil.rmtree(child)
        removed = True
    if removed:
        fsync_directory(cache)


def fetch_bundle(
    channel: str,
    cache_input: Path,
    keyring: Path,
    verifier_path: Path,
    gpgv: Path,
    *,
    expected_uid: int = 0,
    downloader: Callable[[str, Path, int, str | None], str] = download,
    signature_verifier: Callable[[Path, Path, Path, Path], None] = verify_detached_signature,
) -> dict[str, str]:
    cache = validate_cache_root(cache_input, expected_uid)
    verifier = load_verifier(verifier_path)
    clean_abandoned_staging(cache)
    staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=cache))
    os.chmod(staging, 0o700)
    published = False
    try:
        manifest = staging / "SHA256SUMS"
        signature = staging / "SHA256SUMS.gpg"
        manifest_sha256 = downloader(
            artifact_url(channel, manifest.name), manifest, verifier.MAX_MANIFEST_SIZE, None
        )
        downloader(
            artifact_url(channel, signature.name), signature, verifier.MAX_SIGNATURE_SIZE, None
        )
        signature_verifier(gpgv, keyring, signature, manifest)
        manifest_raw = read_bounded_regular(
            manifest, verifier.MAX_MANIFEST_SIZE, "authenticated update manifest"
        )
        manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
        entries, version = verifier.parse_manifest(manifest_raw)
        if VERSION.fullmatch(version) is None:
            raise ChannelError("authenticated update version is invalid")
        target = cache / version
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir():
                raise ChannelError("update cache version path is unsafe")
            existing = verifier.verify_bundle_identity(target)
            existing_manifest = read_bounded_regular(
                target / "SHA256SUMS", verifier.MAX_MANIFEST_SIZE, "cached update manifest"
            )
            if (
                existing.get("version") != version
                or hashlib.sha256(existing_manifest).hexdigest() != manifest_sha256
            ):
                raise ChannelError("update channel attempted to replace an immutable cached version")
            vacuum_cache(cache, target)
            return {
                "version": version,
                "bundle": str(target),
                "manifest_sha256": manifest_sha256,
            }
        for kind in sorted(entries):
            name, expected_digest = entries[kind]
            downloader(
                artifact_url(channel, name),
                staging / name,
                verifier.PAYLOAD_LIMITS[kind],
                expected_digest,
            )
        identity = verifier.verify_bundle_identity(staging)
        if identity.get("version") != version:
            raise ChannelError("downloaded update bundle changed version during verification")

        for child in staging.iterdir():
            if not child.is_file() or child.is_symlink():
                raise ChannelError("update staging contains an unsafe entry")
            os.chmod(child, 0o444)
        fsync_directory(staging)
        os.chmod(staging, 0o555)
        os.rename(staging, target)
        published = True
        fsync_directory(cache)
        vacuum_cache(cache, target)
        return {
            "version": version,
            "bundle": str(target),
            "manifest_sha256": manifest_sha256,
        }
    except (ChannelError, OSError, UnicodeError, ValueError) as error:
        if isinstance(error, ChannelError):
            raise
        raise ChannelError(f"update channel rejected: {error}") from error
    finally:
        if not published and staging.exists():
            remove_staging(cache, staging)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-config", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--keyring", type=Path, required=True)
    parser.add_argument("--verifier", type=Path, required=True)
    parser.add_argument("--gpgv", type=Path, required=True)
    parser.add_argument("--machine", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        channel = load_channel_url(args.channel_config)
        result = fetch_bundle(
            channel,
            args.cache_root,
            args.keyring,
            args.verifier,
            args.gpgv,
        )
    except (ChannelError, OSError, UnicodeError) as error:
        print(f"Echo OS update channel failed: {error}", file=sys.stderr)
        return 1
    if args.machine:
        print(result["version"], result["bundle"], result["manifest_sha256"], sep="\t")
    else:
        print(
            f"ECHO_UPDATE_CHANNEL_READY version={result['version']} "
            f"manifest={result['manifest_sha256']} cache=verified"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
