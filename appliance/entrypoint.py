"""Secure container entrypoint for the Echo OS appliance.

The Agent correctly refuses a network bind when its loaded configuration says
authentication is disabled. Echo OS bootstraps its single-device admin before
starting Agent, injects the same durable credentials into a generated runtime
configuration, and therefore keeps the Agent control plane and appliance APIs
behind one login instead of mounting two unrelated auth systems.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

from appliance.auth import ADMIN_USERNAME, auth_store_path, load_or_bootstrap_auth

DEFAULT_TEMPLATE = Path("/etc/echo/config.example.yaml")
DEFAULT_RUNTIME_CONFIG = Path("/data/echo-agent-config.yaml")
OWNER_MARKER = ".echo-runtime-owner"
AUTH_HASH_ENV = "ECHO_APPLIANCE_ADMIN_PASSWORD_HASH"
AUTH_JWT_ENV = "ECHO_APPLIANCE_JWT_SECRET"
MEMBER_AUTH_HASH_ENV_PREFIX = "ECHO_APPLIANCE_MEMBER_PASSWORD_HASH_"


def _password_hash_environment(username: str) -> str:
    if username == ADMIN_USERNAME:
        return AUTH_HASH_ENV
    suffix = hashlib.sha256(username.encode("utf-8")).hexdigest()[:24].upper()
    return f"{MEMBER_AUTH_HASH_ENV_PREFIX}{suffix}"


def _numeric_identity(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive numeric id") from exc
    if not 1 <= value <= 2_147_483_647:
        raise RuntimeError(f"{name} must be between 1 and 2147483647")
    # 禁止映射到系统低 uid (0=root 已单独处理, 1..99 为系统账户)，避免误用 daemon/nobody
    if value < 100 and value != default:
        raise RuntimeError(f"{name} must be at least 100 (system accounts forbidden)")
    return value


def _validate_trusted_proxy_ips() -> None:
    raw = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
    for value in (part.strip() for part in raw.split(",")):
        if not value:
            continue
        if value == "*":
            raise RuntimeError("FORWARDED_ALLOW_IPS=* is forbidden for Echo appliance")
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise RuntimeError(
                "FORWARDED_ALLOW_IPS entries must be explicit IP addresses or CIDRs"
            ) from exc


def _same_path(left: Path, right: Path) -> bool:
    return os.path.abspath(left) == os.path.abspath(right)


def _prepare_state_ownership(data_root: Path, nas_root: Path, uid: int, gid: int) -> None:
    """Migrate appliance state ownership once, never recursively touching NAS data."""

    data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = data_root / OWNER_MARKER
    expected = f"{uid}:{gid}\n"
    try:
        if (
            marker.read_text() == expected
            and data_root.stat().st_uid == uid
            and data_root.stat().st_gid == gid
        ):
            return
    except OSError:
        pass

    def _reown(path: Path) -> None:
        # 拒绝符号链接，避免通过 /data 下的 symlink 污染宿主
        try:
            if path.is_symlink():
                return
        except OSError:
            return
        os.chown(path, uid, gid, follow_symlinks=False)

    _reown(data_root)
    for current, directories, files in os.walk(data_root, topdown=True, followlinks=False):
        current_path = Path(current)
        # 根本身若为 symlink 则跳过
        if current_path.is_symlink():
            directories[:] = []
            continue
        kept: list[str] = []
        for name in directories:
            candidate = current_path / name
            if (
                candidate.is_symlink()
                or _same_path(candidate, nas_root)
                or os.path.ismount(candidate)
            ):
                continue
            _reown(candidate)
            kept.append(name)
        directories[:] = kept
        for name in files:
            candidate = current_path / name
            if candidate.is_symlink() or _same_path(candidate, marker):
                continue
            _reown(candidate)

    marker.write_text(expected)
    marker.chmod(stat.S_IRUSR | stat.S_IWUSR)
    _reown(marker)


def _drop_container_privileges(data_root: Path) -> None:
    """Prepare bind-mounted state and permanently become the configured Echo uid/gid."""

    if os.geteuid() != 0:
        os.umask(0o077)
        return
    uid = _numeric_identity("ECHO_PUID", 1000)
    gid = _numeric_identity("ECHO_PGID", 1000)
    nas_root = Path(os.environ.get("ECHO_NAS_ROOT", "").strip() or data_root / "nas")
    _prepare_state_ownership(data_root, nas_root, uid, gid)

    # Keep getpwuid()/HOME behavior coherent for Agent libraries after setuid.
    subprocess.run(["groupmod", "-o", "-g", str(gid), "echo"], check=True)
    subprocess.run(["usermod", "-o", "-u", str(uid), "-g", str(gid), "echo"], check=True)
    os.setgroups([gid])
    os.setgid(gid)
    os.setuid(uid)
    if os.geteuid() == 0:
        raise RuntimeError("Echo OS entrypoint failed to drop root privileges")
    os.environ.update(HOME=str(data_root), USER="echo", LOGNAME="echo")
    os.umask(0o077)


def _serve_template(args: list[str]) -> tuple[list[str], Path]:
    remaining: list[str] = []
    template: Path | None = None
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--config":
            if index + 1 >= len(args):
                raise ValueError("--config requires a path")
            template = Path(args[index + 1])
            index += 2
            continue
        if value.startswith("--config="):
            template = Path(value.split("=", 1)[1])
            index += 1
            continue
        remaining.append(value)
        index += 1
    configured = os.environ.get("ECHO_CONFIG_TEMPLATE", "").strip()
    return remaining, template or (Path(configured) if configured else DEFAULT_TEMPLATE)


def prepare_runtime_config(template: Path, output: Path) -> tuple[Path, str | None]:
    try:
        data = yaml.safe_load(template.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Echo OS config template is invalid: {template}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Echo OS config template must contain a YAML mapping: {template}")

    auth_config, generated_password = load_or_bootstrap_auth()
    auth_payload = auth_config.model_dump(mode="json")
    # Agent config loading expands ``$NAME`` references in every string. A
    # bcrypt hash itself contains ``$`` separators and its random salt can
    # accidentally begin with an uppercase sequence, which would otherwise be
    # mistaken for an environment variable and silently corrupt the hash.
    # Reference the secrets once from the generated YAML instead: interpolation
    # is a single pass, so the substituted bcrypt value is never re-expanded.
    jwt_secret = str(auth_payload["jwt_secret"])
    for username, raw_password_hash in auth_payload["users"].items():
        environment_name = _password_hash_environment(str(username))
        os.environ[environment_name] = str(raw_password_hash)
        auth_payload["users"][username] = f"${environment_name}"
    os.environ[AUTH_JWT_ENV] = jwt_secret
    auth_payload["jwt_secret"] = f"${AUTH_JWT_ENV}"
    data["local_auth"] = auth_payload

    # Appliance device identity must be per-device and individually revocable.
    # The upstream personal preset enables a convenience Tentacle listener with
    # one shared token; if it starts first, Echo can only project agent-shared
    # mode and must disable file/photo sync. Keep that listener off so the
    # approval-bound Device Link service owns the single published port.
    tentacle = data.get("tentacle")
    if tentacle is None:
        tentacle = {}
    if not isinstance(tentacle, dict):
        raise RuntimeError("Echo OS config template tentacle section must be a mapping")
    tentacle["enabled"] = False
    data["tentacle"] = tentacle

    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    # 先以 0o600 创建，避免 umask 竞态
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
    temporary.chmod(0o600)
    os.replace(temporary, output)
    output.chmod(0o600)
    # 目录 fsync 保证持久化
    with contextlib.suppress(OSError):
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return output, generated_password


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _validate_trusted_proxy_ips()
    data_root = Path(os.environ.get("ECHO_DATA_DIR", "").strip() or "/data")
    _drop_container_privileges(data_root)
    if not args:
        # NAS clients must reach the service over the LAN; the generated local-auth
        # configuration below is what makes this deliberate all-interface bind safe.
        args = ["serve", "--host", "0.0.0.0", "--port", "8000"]  # nosec B104

    if args[0] == "serve":
        # This is the Echo OS entrypoint, so the device layer is always loaded
        # from the same distribution even outside Docker Compose.
        os.environ.setdefault("ECHO_APPLIANCE", "1")
        os.environ.setdefault("ECHO_APP_EXTENSIONS", "appliance.extension")
        os.environ.setdefault("ECHO_SKILL_EXTENSIONS", "appliance.pm_skills:register_pm_skills")
        # Do this outside Agent's best-effort extension loader so a broken image
        # exits instead of merely logging a warning and serving a mixed runtime.
        from appliance.agent_ui import agent_bundle_status

        bundle = agent_bundle_status()
        if bundle:
            codex_version = str(bundle.get("packaged_codex_version") or "").strip()
            if codex_version:
                os.environ.setdefault("ECHO_PACKAGED_CODEX_VERSION", codex_version)
            print(
                "Echo Agent bundle verified before startup: "
                f"source={bundle['source_id']} version={bundle['version']}",
                file=sys.stderr,
                flush=True,
            )
        serve_args, template = _serve_template(args[1:])
        configured_output = os.environ.get("ECHO_RUNTIME_CONFIG", "").strip()
        output = (
            Path(configured_output)
            if configured_output
            else data_root / DEFAULT_RUNTIME_CONFIG.name
        )
        runtime_config, generated_password = prepare_runtime_config(template, output)
        if generated_password:
            # 仅当显式允许时才打印明文，避免 docker logs 持久化泄露
            if os.environ.get("ECHO_PRINT_GENERATED_PASSWORD", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }:
                print(
                    "Echo OS appliance admin password generated: "
                    f"username={ADMIN_USERNAME} password={generated_password}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    f"Echo OS appliance admin password generated. username={ADMIN_USERNAME} "
                    f"stored in {auth_store_path()}; set ECHO_PRINT_GENERATED_PASSWORD=1 to print it, then rotate immediately.",
                    file=sys.stderr,
                    flush=True,
                )
        args = ["serve", "--config", str(runtime_config), *serve_args]

    # Launch the runtime from this exact Python distribution. This keeps the
    # appliance independent of PATH and proves the Agent is truly embedded.
    os.execv(sys.executable, [sys.executable, "-m", "runtime", *args])
    return 127  # pragma: no cover - execv only returns by raising


if __name__ == "__main__":
    raise SystemExit(main())
