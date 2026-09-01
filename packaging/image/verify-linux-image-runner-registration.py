#!/usr/bin/env python3
"""Verify the registered GitHub runner before it can serve image jobs."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import stat
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

RUNNER_APPLICATION_DIR = Path("/opt/actions-runner")
RUNNER_WORK_ROOT = Path("/srv/echo-os-image-runner")
SYSTEMD_UNIT_ROOT = Path("/etc/systemd/system")
EXPECTED_REPOSITORY = "dengdenghua/echo-os"
HOST_EVIDENCE_NAME = "echo-image-runner-host.json"
HOOK_STARTED = "ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/libexec/echo-os-image-runner-job-hook.sh"
HOOK_COMPLETED = (
    "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/libexec/echo-os-image-runner-job-hook.sh"
)
MAX_CONFIG_BYTES = 64 * 1024
MAX_COMMAND_OUTPUT = 4096
RUNNER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SERVICE_NAME = re.compile(r"^actions\.runner\.[A-Za-z0-9._-]{1,128}\.service$")


class RegistrationError(RuntimeError):
    """The local runner registration is unsafe or targets the wrong scope."""


@dataclass(frozen=True)
class RegistrationFacts:
    repository: str
    agent_id: int
    runner_name: str
    pool_id: int
    work_root: str
    service_name: str
    service_enabled: bool
    hooks_ready: bool
    host_evidence_ready: bool


def _bounded_command(arguments: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(  # nosec B603
            list(arguments),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    encoded = completed.stdout.encode("utf-8", "replace")
    if len(encoded) > MAX_COMMAND_OUTPUT:
        return ""
    return completed.stdout.strip()


def _regular_file(
    path: Path,
    *,
    owner_uid: int,
    private: bool = False,
    max_bytes: int = MAX_CONFIG_BYTES,
) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RegistrationError(f"registered runner file is unavailable: {path.name}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != owner_uid
        or metadata.st_size <= 0
        or metadata.st_size > max_bytes
    ):
        raise RegistrationError(f"registered runner file is unsafe: {path.name}")
    if stat.S_IMODE(metadata.st_mode) & 0o002:
        raise RegistrationError(f"registered runner file is writable by other users: {path.name}")
    if private and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RegistrationError(f"registered runner file is not private: {path.name}")
    return metadata


def _read_text(path: Path, *, owner_uid: int, private: bool = False) -> str:
    _regular_file(path, owner_uid=owner_uid, private=private)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RegistrationError(f"registered runner file is unreadable: {path.name}") from error


def _read_json(path: Path, *, owner_uid: int, private: bool = False) -> dict[str, object]:
    try:
        value = json.loads(_read_text(path, owner_uid=owner_uid, private=private))
    except json.JSONDecodeError as error:
        raise RegistrationError(f"registered runner JSON is invalid: {path.name}") from error
    if not isinstance(value, dict) or len(value) > 64:
        raise RegistrationError(f"registered runner JSON has an invalid shape: {path.name}")
    return value


def _canonical_directory(path: Path, *, owner_uid: int, private: bool) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise RegistrationError("runner directory must be absolute and non-symlink")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.stat()
    except OSError as error:
        raise RegistrationError("runner directory is unavailable") from error
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        resolved != path
        or not path.is_dir()
        or metadata.st_uid != owner_uid
        or mode & 0o022
        or (private and mode & 0o077)
    ):
        raise RegistrationError("runner directory ownership or permissions are unsafe")
    return resolved


def _github_repository_url(value: object, repository: str) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.username is None
        and parsed.password is None
        and port is None
        and parsed.path.rstrip("/") == f"/{repository}"
        and not parsed.query
        and not parsed.fragment
    )


def _actions_server_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and hostname.endswith(".actions.githubusercontent.com")
        and parsed.username is None
        and parsed.password is None
        and port is None
        and not parsed.query
        and not parsed.fragment
    )


def inspect_registration(
    *,
    application_dir: Path,
    work_root: Path,
    systemd_unit_root: Path,
    runner_user: str,
    runner_uid: int,
    unit_owner_uid: int,
    repository: str,
) -> RegistrationFacts:
    application_dir = _canonical_directory(application_dir, owner_uid=runner_uid, private=False)
    work_root = _canonical_directory(work_root, owner_uid=runner_uid, private=True)

    settings = _read_json(application_dir / ".runner", owner_uid=runner_uid, private=True)
    agent_id = settings.get("AgentId")
    pool_id = settings.get("PoolId")
    runner_name = settings.get("AgentName")
    if isinstance(agent_id, bool) or not isinstance(agent_id, int) or agent_id <= 0:
        raise RegistrationError("runner AgentId is invalid")
    if isinstance(pool_id, bool) or not isinstance(pool_id, int) or pool_id <= 0:
        raise RegistrationError("runner PoolId is invalid")
    if not isinstance(runner_name, str) or RUNNER_NAME.fullmatch(runner_name) is None:
        raise RegistrationError("runner AgentName is invalid")
    if not _github_repository_url(settings.get("GitHubUrl"), repository):
        raise RegistrationError("runner is not registered to the reviewed GitHub repository")
    if not _actions_server_url(settings.get("ServerUrl")):
        raise RegistrationError("runner server URL is not GitHub Actions")
    if settings.get("WorkFolder") != str(work_root):
        raise RegistrationError("runner work folder is not the dedicated image work root")
    if settings.get("Ephemeral", False) is not False:
        raise RegistrationError("the reusable image runner must not be ephemeral")
    if settings.get("DisableUpdate", False) is not False:
        raise RegistrationError("the image runner must retain official security updates")
    if settings.get("UseV2Flow") is not True:
        raise RegistrationError("the image runner is not using the current GitHub message flow")

    for credential_name in (".credentials", ".credentials_rsaparams"):
        _regular_file(
            application_dir / credential_name,
            owner_uid=runner_uid,
            private=True,
        )

    environment = _read_text(application_dir / ".env", owner_uid=runner_uid, private=True)
    environment_lines = [line.strip() for line in environment.splitlines()]
    hooks_ready = (
        environment_lines.count(HOOK_STARTED) == 1
        and environment_lines.count(HOOK_COMPLETED) == 1
        and sum(line.startswith("ACTIONS_RUNNER_HOOK_JOB_STARTED=") for line in environment_lines)
        == 1
        and sum(line.startswith("ACTIONS_RUNNER_HOOK_JOB_COMPLETED=") for line in environment_lines)
        == 1
    )
    if not hooks_ready:
        raise RegistrationError("runner cleanup hooks are missing, duplicated or redirected")

    service_file = application_dir / ".service"
    service_name = _read_text(service_file, owner_uid=runner_uid, private=True).strip()
    if SERVICE_NAME.fullmatch(service_name) is None:
        raise RegistrationError("runner systemd service name is invalid")
    service_unit = systemd_unit_root / service_name
    unit = _read_text(service_unit, owner_uid=unit_owner_uid)
    required_unit_lines = {
        f"ExecStart={application_dir}/runsvc.sh",
        f"User={runner_user}",
        f"WorkingDirectory={application_dir}",
        "KillMode=process",
        "KillSignal=SIGTERM",
    }
    if not required_unit_lines.issubset(set(unit.splitlines())):
        raise RegistrationError("runner systemd service does not match the official local layout")
    service_enabled = _bounded_command(("systemctl", "is-enabled", service_name)) == "enabled"
    if not service_enabled:
        raise RegistrationError("runner systemd service is not enabled")

    host_evidence = _read_json(
        work_root / HOST_EVIDENCE_NAME,
        owner_uid=runner_uid,
        private=True,
    )
    host_facts = host_evidence.get("facts")
    host_evidence_ready = (
        host_evidence.get("kind") == "echo-os-image-runner-host-preflight"
        and isinstance(host_evidence.get("marker"), str)
        and str(host_evidence["marker"]).startswith("ECHO_IMAGE_RUNNER_HOST_READY ")
        and isinstance(host_facts, dict)
        and host_facts.get("work_root") == str(work_root)
    )
    if not host_evidence_ready:
        raise RegistrationError("runner registration is not bound to valid host evidence")

    return RegistrationFacts(
        repository=repository,
        agent_id=agent_id,
        runner_name=runner_name,
        pool_id=pool_id,
        work_root=str(work_root),
        service_name=service_name,
        service_enabled=service_enabled,
        hooks_ready=hooks_ready,
        host_evidence_ready=host_evidence_ready,
    )


def success_marker(facts: RegistrationFacts) -> str:
    return (
        "ECHO_IMAGE_RUNNER_REGISTRATION_READY "
        f"repository={facts.repository} runner={facts.runner_name} "
        f"agent-id={facts.agent_id} pool-id={facts.pool_id} "
        f"work={facts.work_root} service={facts.service_name} "
        "enabled=yes hooks=ready host-evidence=ready"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-user", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", arguments.runner_user) is None:
        print("Echo image runner registration rejected: invalid runner user", file=sys.stderr)
        return 2
    try:
        account = pwd.getpwnam(arguments.runner_user)
    except KeyError:
        print("Echo image runner registration rejected: runner user is missing", file=sys.stderr)
        return 2
    if account.pw_uid == 0 or os.geteuid() != account.pw_uid:
        print(
            "Echo image runner registration rejected: run as the non-root runner user",
            file=sys.stderr,
        )
        return 1
    try:
        facts = inspect_registration(
            application_dir=RUNNER_APPLICATION_DIR,
            work_root=RUNNER_WORK_ROOT,
            systemd_unit_root=SYSTEMD_UNIT_ROOT,
            runner_user=arguments.runner_user,
            runner_uid=account.pw_uid,
            unit_owner_uid=0,
            repository=EXPECTED_REPOSITORY,
        )
    except RegistrationError as error:
        print(f"Echo image runner registration rejected: {error}", file=sys.stderr)
        return 1
    print(success_marker(facts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
