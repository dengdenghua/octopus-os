"""Build the root-owned per-user bind-mount jails used by Echo WebDAV."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from appliance.auth import ADMIN_USERNAME, normalized_accounts, read_auth_store
from appliance.native_webdav import MANIFEST_PATH, MANIFEST_SCHEMA, SFTP_PORT

RUNTIME_ROOT = MANIFEST_PATH.parent
JAIL_ROOT = RUNTIME_ROOT / "jails"
GATEWAY_ROOT = Path("/var/lib/echo-os/webdav-gateway")
GATEWAY_KEY = GATEWAY_ROOT / "id_ed25519"
AUTHORIZED_KEYS = GATEWAY_ROOT / "authorized_keys"
KNOWN_HOSTS = GATEWAY_ROOT / "known_hosts"
HOST_PUBLIC_KEY = Path("/etc/ssh/ssh_host_ed25519_key.pub")
LOCK_PATH = Path("/run/lock/echo-webdav-jails.lock")
AUTH_PATH = Path("/var/lib/echo-agent/appliance-auth.json")
SHARE_LIMIT = 256
_POSIX_USER = re.compile(r"[a-z][a-z0-9_-]{0,31}")

if os.name == "posix":
    import pwd
else:  # pragma: no cover - production jail management is Linux-only
    pwd = None  # type: ignore[assignment]


class JailSyncError(RuntimeError):
    """The chroot view could not be built and verified safely."""


def _run(*args: str, timeout: float = 30.0) -> str:
    try:
        completed = subprocess.run(
            list(args), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise JailSyncError(f"failed to execute {args[0]}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise JailSyncError(f"{args[0]} failed: {detail}")
    return completed.stdout or ""


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chown(path, 0, 0, follow_symlinks=False)
        path.chmod(mode, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _safe_directory(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise JailSyncError(f"unsafe WebDAV directory: {path}")
    os.chown(path, 0, 0, follow_symlinks=False)
    path.chmod(mode, follow_symlinks=False)


def _provision_gateway_identity() -> None:
    # sshd checks the centralized public key after dropping to the target
    # account, so users need traverse-only access to this root-owned directory.
    # The gateway private key itself remains root:root 0400.
    _safe_directory(GATEWAY_ROOT, 0o711)
    if GATEWAY_KEY.exists() != GATEWAY_KEY.with_suffix(".pub").exists():
        raise JailSyncError("WebDAV gateway key pair is incomplete")
    if not GATEWAY_KEY.exists():
        temporary_root = Path(tempfile.mkdtemp(prefix=".webdav-key.", dir=GATEWAY_ROOT))
        temporary_key = temporary_root / "id_ed25519"
        try:
            _run(
                "/usr/bin/ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "echo-webdav-gateway",
                "-f",
                str(temporary_key),
            )
            _atomic_write(GATEWAY_KEY, temporary_key.read_bytes(), 0o400)
            _atomic_write(
                GATEWAY_KEY.with_suffix(".pub"),
                temporary_key.with_suffix(".pub").read_bytes(),
                0o644,
            )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    public_line = GATEWAY_KEY.with_suffix(".pub").read_text(encoding="ascii").strip()
    fields = public_line.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise JailSyncError("WebDAV gateway public key is invalid")
    _atomic_write(AUTHORIZED_KEYS, f"{fields[0]} {fields[1]}\n".encode("ascii"), 0o644)

    if not HOST_PUBLIC_KEY.exists():
        _run("/usr/bin/ssh-keygen", "-A")
    host_line = HOST_PUBLIC_KEY.read_text(encoding="ascii").strip().split()
    if len(host_line) < 2 or host_line[0] != "ssh-ed25519":
        raise JailSyncError("dedicated SFTP host key is invalid")
    known = f"[127.0.0.1]:{SFTP_PORT} {host_line[0]} {host_line[1]}\n"
    _atomic_write(KNOWN_HOSTS, known.encode("ascii"), 0o644)


def _effective_read_access(username: str, path: Path) -> bool:
    """Ask the kernel as the target identity instead of reimplementing ACLs."""

    if pwd is None:
        raise JailSyncError("WebDAV access checks require Linux")
    account = pwd.getpwnam(username)
    pid = os.fork()
    if pid == 0:  # pragma: no branch - child exits immediately
        try:
            os.initgroups(username, account.pw_gid)
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
            allowed = os.access(path, os.R_OK | os.X_OK, effective_ids=True)
        except BaseException:
            allowed = False
        os._exit(0 if allowed else 1)
    _, status = os.waitpid(pid, 0)
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def _published_names(shares: list[dict[str, Any]]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for share in shares:
        counts[share["name"]] = counts.get(share["name"], 0) + 1
    return {
        share["uuid"]: (
            share["name"]
            if counts[share["name"]] == 1
            else f"{share['name']}--{share['uuid'].split('-', 1)[0]}"
        )
        for share in shares
    }


def build_desired(
    accounts: dict[str, dict[str, Any]],
    shares: list[dict[str, Any]],
    *,
    posix_lookup: Callable[[str], Any] | None = None,
    access_check: Callable[[str, Path], bool] = _effective_read_access,
) -> dict[str, dict[str, Any]]:
    """Return validated login-to-chroot mappings, including private sources."""

    if len(shares) > SHARE_LIMIT:
        raise JailSyncError("too many registered shares for the WebDAV gateway")
    if posix_lookup is None:
        if pwd is None:
            raise JailSyncError("WebDAV POSIX account lookup requires Linux")
        posix_lookup = pwd.getpwnam
    names = _published_names(shares)
    desired: dict[str, dict[str, Any]] = {}
    used_posix: set[str] = set()
    for login, account in sorted(accounts.items()):
        if account.get("active") is not True:
            continue
        posix_user = "echo" if login == ADMIN_USERNAME else account.get("omv_username")
        if not isinstance(posix_user, str) or posix_user in used_posix:
            raise JailSyncError("WebDAV account mapping is invalid or duplicated")
        try:
            host_account = posix_lookup(posix_user)
        except KeyError as exc:
            raise JailSyncError(f"WebDAV POSIX account is missing: {posix_user}") from exc
        if int(host_account.pw_uid) < 1000 or not str(host_account.pw_dir).startswith("/home/"):
            raise JailSyncError("WebDAV POSIX account is outside the managed user range")
        visible = []
        for share in shares:
            source = Path(share["source"])
            if access_check(posix_user, source):
                visible.append(
                    {
                        "name": names[share["uuid"]],
                        "uuid": share["uuid"],
                        "source": str(source),
                    }
                )
        if visible:
            desired[login] = {"posixUser": posix_user, "shares": visible}
            used_posix.add(posix_user)
    return desired


def _source_state() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    from appliance import native_storage, native_webdav_control

    if not native_webdav_control.publication_enabled(strict=True):
        return {}, []

    accounts = normalized_accounts(read_auth_store(AUTH_PATH))
    shares: list[dict[str, Any]] = []
    with native_storage._registry_transaction():  # noqa: SLF001 - same native trust domain
        for entry in native_storage._registry_load(strict=True):  # noqa: SLF001
            share_uuid = native_storage._registered_uuid(entry, strict=True)  # noqa: SLF001
            name = native_storage._registered_relative_name(entry, strict=True)  # noqa: SLF001
            source = native_storage._native_folder_path(entry)  # noqa: SLF001
            assert share_uuid is not None and name is not None
            if any(character in name for character in ("/", "\0")) or name in {".", ".."}:
                raise JailSyncError("registered share has an unsafe WebDAV name")
            shares.append({"uuid": share_uuid, "name": name, "source": str(source)})
    identities = [item["uuid"] for item in shares]
    if len(identities) != len(set(identities)):
        raise JailSyncError("registered shares contain duplicate identities")
    return accounts, shares


def _manifest_payload(desired: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "accounts": {
            login: {
                "posixUser": record["posixUser"],
                "shares": [
                    {"name": share["name"], "uuid": share["uuid"]} for share in record["shares"]
                ],
            }
            for login, record in sorted(desired.items())
        },
    }


def _load_previous() -> dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": MANIFEST_SCHEMA, "accounts": {}}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JailSyncError("existing WebDAV jail manifest is unreadable") from exc


def _target(posix_user: str, published_name: str) -> Path:
    if _POSIX_USER.fullmatch(posix_user) is None:
        raise JailSyncError("WebDAV bind target has an unsafe POSIX user")
    if (
        not published_name
        or len(published_name) > 160
        or published_name in {".", ".."}
        or any(character in published_name for character in ("/", "\\", "\0"))
    ):
        raise JailSyncError("WebDAV bind target has an unsafe share name")
    return JAIL_ROOT / posix_user / "shares" / published_name


def _is_mountpoint(target: Path) -> bool:
    try:
        completed = subprocess.run(
            ["/usr/bin/mountpoint", "-q", "--", str(target)],
            capture_output=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise JailSyncError("mountpoint inspection failed") from exc
    # util-linux mountpoint(1) uses 32 for a valid path that is not mounted.
    if completed.returncode not in {0, 32}:
        raise JailSyncError("mountpoint inspection returned an unexpected status")
    return completed.returncode == 0


def _unmount(target: Path) -> None:
    if _is_mountpoint(target):
        _run("/usr/bin/umount", "--", str(target), timeout=60.0)
    if _is_mountpoint(target):
        raise JailSyncError(f"WebDAV bind mount remained active: {target}")


def _previous_targets(payload: dict[str, Any]) -> set[Path]:
    result: set[Path] = set()
    raw_accounts = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(raw_accounts, dict):
        raise JailSyncError("existing WebDAV jail manifest has an invalid shape")
    for record in raw_accounts.values():
        if not isinstance(record, dict):
            raise JailSyncError("existing WebDAV jail manifest has an invalid account")
        user = record.get("posixUser")
        shares = record.get("shares")
        if not isinstance(user, str) or not isinstance(shares, list):
            raise JailSyncError("existing WebDAV jail manifest has an invalid mapping")
        for share in shares:
            if not isinstance(share, dict) or not isinstance(share.get("name"), str):
                raise JailSyncError("existing WebDAV jail manifest has an invalid share")
            result.add(_target(user, share["name"]))
    return result


def sync_jails() -> bool:
    """Converge bind mounts and atomically publish the matching auth manifest."""

    if os.name != "posix" or os.geteuid() != 0:
        raise JailSyncError("WebDAV jail synchronization requires Linux root")
    _safe_directory(RUNTIME_ROOT, 0o755)
    _safe_directory(JAIL_ROOT, 0o755)
    _safe_directory(LOCK_PATH.parent, 0o755)
    _provision_gateway_identity()

    import fcntl

    lock_descriptor = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        accounts, shares = _source_state()
        desired = build_desired(accounts, shares)
        payload = _manifest_payload(desired)
        previous = _load_previous()
        wanted_targets = {
            _target(record["posixUser"], share["name"])
            for record in desired.values()
            for share in record["shares"]
        }
        for target in sorted(_previous_targets(previous) - wanted_targets, reverse=True):
            _unmount(target)
            with contextlib.suppress(OSError):
                target.rmdir()

        for record in desired.values():
            jail = JAIL_ROOT / record["posixUser"]
            shares_root = jail / "shares"
            _safe_directory(jail, 0o755)
            _safe_directory(shares_root, 0o755)
            for share in record["shares"]:
                source = Path(share["source"])
                target = _target(record["posixUser"], share["name"])
                mounted = target.exists() and _is_mountpoint(target)
                if mounted:
                    try:
                        matches = os.path.samefile(source, target)
                    except OSError:
                        matches = False
                    if not matches:
                        _unmount(target)
                        mounted = False
                if not mounted:
                    # Never chmod/chown an active bind target: those operations
                    # affect the source share and can collapse its POSIX ACL
                    # mask. Only normalize the empty mountpoint underneath.
                    _safe_directory(target, 0o755)
                    _run("/usr/bin/mount", "--bind", "--", str(source), str(target), timeout=60.0)
                if not _is_mountpoint(target) or not os.path.samefile(source, target):
                    raise JailSyncError(f"WebDAV bind mount was not verified: {target}")

        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        current = MANIFEST_PATH.read_text(encoding="utf-8") if MANIFEST_PATH.exists() else None
        changed = current != rendered
        if changed:
            _atomic_write(MANIFEST_PATH, rendered.encode("utf-8"), 0o644)
        return changed
    finally:
        os.close(lock_descriptor)


def clean_jails() -> None:
    previous = _load_previous()
    for target in sorted(_previous_targets(previous), reverse=True):
        _unmount(target)
    with contextlib.suppress(FileNotFoundError):
        MANIFEST_PATH.unlink()


def refresh() -> bool:
    changed = sync_jails()
    if changed:
        _run("/usr/bin/systemctl", "try-restart", "echo-webdav.service", timeout=90.0)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage Echo WebDAV per-user chroot views")
    parser.add_argument("command", choices=("sync", "refresh", "clean"))
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            changed = sync_jails()
        elif args.command == "refresh":
            changed = refresh()
        else:
            clean_jails()
            changed = True
    except (JailSyncError, OSError, ValueError) as exc:
        print(f"Echo WebDAV jail synchronization failed: {exc}", file=sys.stderr)
        return 1
    print(f"ECHO_WEBDAV_JAILS_READY changed={str(changed).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
