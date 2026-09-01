from __future__ import annotations

import os
from pathlib import Path

_DENIED_WRITE_BASENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".bashrc",
        ".zshrc",
        ".profile",
        ".bash_profile",
        ".zprofile",
        ".netrc",
        ".pgpass",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".anthropic_oauth.json",
        ".credentials.json",
        "authorized_keys",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "ssh_config",
    }
)

_DENIED_WRITE_HOME_SUBDIRS: frozenset[str] = frozenset(
    {
        ".ssh",
        ".aws",
        ".gcp",
        ".kube",
        ".docker",
        ".gnupg",
    }
)

_DENIED_WRITE_ABS_PREFIXES: tuple[str, ...] = (
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/ssh/",
    "/root/.ssh/",
    "/proc/self/environ",
    "/sys/class/",
    "/var/log/auth",
)


class FileWriteVerdict:
    __slots__ = ("allow", "path", "reason")

    def __init__(self, allow: bool, path: str, reason: str = "") -> None:
        self.allow = allow
        self.path = path
        self.reason = reason

    def __repr__(self) -> str:
        status = "ALLOW" if self.allow else f"DENY({self.reason})"
        return f"FileWriteVerdict({status} {self.path})"

    def __bool__(self) -> bool:
        return self.allow


def check_file_write(path: str | Path) -> FileWriteVerdict:
    p_in = str(path)
    if not p_in:
        return FileWriteVerdict(False, p_in, "empty_path")

    try:
        resolved = Path(p_in).resolve()
    except (OSError, RuntimeError) as exc:
        return FileWriteVerdict(False, p_in, f"resolve_error: {exc}")

    resolved_str = str(resolved)
    resolved_norm = resolved_str.replace("\\", "/").lower()

    basename = resolved.name.lower()
    if basename in _DENIED_WRITE_BASENAMES:
        return FileWriteVerdict(False, p_in, f"denied_basename: {basename}")

    # Denylist prefixes name canonical Unix locations (/etc/...), but on
    # macOS /etc, /var and /tmp are symlinks into /private, so the
    # resolved path loses the /etc prefix. Match the realpath (with and
    # without a leading /private) plus the pre-resolution input — extra
    # candidates can only add denials, never relax them.
    candidates = [resolved_norm]
    if resolved_norm.startswith("/private/"):
        candidates.append(resolved_norm[len("/private") :])
    input_norm = os.path.normpath(p_in).replace("\\", "/").lower()
    if input_norm.startswith("/"):
        candidates.append(input_norm)

    for prefix in _DENIED_WRITE_ABS_PREFIXES:
        pl = prefix.lower()
        if any(cand.startswith(pl) for cand in candidates):
            return FileWriteVerdict(False, p_in, f"denied_abs_prefix: {prefix}")

    try:
        home = Path(os.path.expanduser("~")).resolve()
    except (OSError, RuntimeError):
        home = None

    if home is not None:
        try:
            rel = resolved.relative_to(home)
        except ValueError:
            rel = None
        if rel is not None:
            rel_str = str(rel).replace("\\", "/").lower()
            parts = rel_str.split("/")
            if parts and parts[0] in _DENIED_WRITE_HOME_SUBDIRS:
                return FileWriteVerdict(
                    False,
                    p_in,
                    f"denied_home_subdir: ~/{parts[0]}",
                )

    return FileWriteVerdict(True, p_in)


def is_safe_write(path: str | Path) -> bool:
    return check_file_write(path).allow


def denied_basenames() -> frozenset[str]:
    return _DENIED_WRITE_BASENAMES


def denied_home_subdirs() -> frozenset[str]:
    return _DENIED_WRITE_HOME_SUBDIRS
