"""Public contracts for the Codex App Server stdio client.

The module deliberately models only the stable transport envelope.  App Server
turn and item payloads evolve quickly, so method payloads remain JSON objects
and are validated at the framing boundary instead of being copied into a
second, soon-stale schema hierarchy here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
RequestId: TypeAlias = int | str


class CodexAppServerError(RuntimeError):
    """Base error raised by the local App Server integration."""


class ConfigurationError(CodexAppServerError, ValueError):
    """Client configuration is unsafe or internally inconsistent."""


class TransportClosedError(CodexAppServerError):
    """The App Server process or its stdio transport closed unexpectedly."""


class ProtocolError(CodexAppServerError):
    """The peer sent a malformed or ambiguous protocol message."""


class MessageTooLargeError(ProtocolError):
    """A JSONL frame exceeded the configured byte ceiling."""


class BackpressureError(CodexAppServerError):
    """A bounded client queue filled because its consumer fell behind."""


class RequestTimeoutError(CodexAppServerError, TimeoutError):
    """A JSON-RPC request did not receive a response before its deadline."""


class RemoteError(CodexAppServerError):
    """An error response returned by App Server."""

    def __init__(self, code: int, message: str, data: JsonValue = None) -> None:
        super().__init__(f"App Server error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True, slots=True)
class Notification:
    """One server-to-client notification from the App Server event stream."""

    method: str
    params: JsonObject


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """A server-initiated approval request awaiting an Echo decision."""

    request_id: RequestId
    method: str
    params: JsonObject


ApprovalResponse: TypeAlias = Mapping[str, Any]
ApprovalHandlerResult: TypeAlias = ApprovalResponse | Awaitable[ApprovalResponse]
ApprovalHandler: TypeAlias = Callable[[ApprovalRequest], ApprovalHandlerResult]


class AsyncByteReader(Protocol):
    async def readline(self) -> bytes: ...

    async def read(self, n: int = -1) -> bytes: ...


class AsyncByteWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


class AppServerProcess(Protocol):
    """Minimum subprocess surface, intentionally easy to fake in tests."""

    pid: int
    stdin: AsyncByteWriter | None
    stdout: AsyncByteReader | None
    stderr: AsyncByteReader | None

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProcessLaunch:
    """Sanitized launch request passed to an injectable process factory."""

    argv: tuple[str, ...]
    cwd: str | None
    env: dict[str, str] = field(repr=False)
    stream_limit: int


ProcessFactory: TypeAlias = Callable[[ProcessLaunch], Awaitable[AppServerProcess]]


@dataclass(frozen=True, slots=True)
class CodexProviderProfile:
    """Server-resolved model provider injected into an isolated Codex home.

    The browser never constructs this object.  It is derived from Echo'
    trusted model catalog after tenant/role resolution, then handed directly
    to the sidecar security layer.  It never carries an upstream credential.
    The optional repr-hidden bearer is a short-lived, in-memory capability for
    an Echo-owned loopback proxy and is revoked with its exact turn.
    """

    provider_id: str
    name: str
    base_url: str
    model: str
    wire_api: Literal["responses"] = "responses"
    requires_openai_auth: bool = False
    auth_env_key: Literal["ECHO_CODEX_PROXY_TOKEN"] | None = None
    scoped_bearer_token: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        import re
        from urllib.parse import urlsplit

        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.provider_id):
            raise ConfigurationError("provider_id must be a lowercase Codex provider identifier")
        if self.provider_id in {"openai", "ollama", "lmstudio"}:
            raise ConfigurationError("custom provider_id cannot use a reserved Codex provider id")
        for field_name in ("name", "base_url", "model"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ConfigurationError(f"{field_name} must be a non-empty, NUL-free string")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("base_url must be an absolute http(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ConfigurationError("base_url must not contain embedded credentials")
        if self.auth_env_key not in {None, "ECHO_CODEX_PROXY_TOKEN"}:
            raise ConfigurationError("scoped provider auth env key is not server-managed")
        if (self.auth_env_key is None) != (self.scoped_bearer_token is None):
            raise ConfigurationError("scoped provider authentication must be complete")
        if self.scoped_bearer_token is not None:
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}:
                raise ConfigurationError("scoped provider tokens may target only loopback HTTP")
            try:
                port = parsed.port
            except ValueError:
                port = None
            if port is None:
                raise ConfigurationError(
                    "scoped provider loopback URL must include an explicit port"
                )
            if parsed.path.rstrip("/") != "/v1" or parsed.query or parsed.fragment:
                raise ConfigurationError(
                    "scoped provider loopback URL must target the /v1 API root"
                )
            token = self.scoped_bearer_token
            if not 32 <= len(token) <= 512 or any(
                char.isspace() or char == "\x00" for char in token
            ):
                raise ConfigurationError("scoped provider token must be a bounded opaque value")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(self, "model", self.model.strip())


DEFAULT_ENV_ALLOWLIST = frozenset(
    {
        "APPDATA",
        "CODEX_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "LOGNAME",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "WINDIR",
    }
)


@dataclass(frozen=True, slots=True)
class CodexAppServerConfig:
    """Security and resource limits for one App Server subprocess.

    Environment inheritance is allowlist-only.  To intentionally pass a
    credential such as ``OPENAI_API_KEY``, the caller must add that exact name
    to ``env_allowlist`` and provide it through ``env_overrides`` (or the
    selected source environment).  This explicit seam prevents an unrelated
    host secret from silently becoming visible to executed commands.
    """

    command: tuple[str, ...] = ("codex", "app-server", "--listen", "stdio://")
    cwd: str | None = None
    env_allowlist: frozenset[str] = DEFAULT_ENV_ALLOWLIST
    env_overrides: Mapping[str, str] = field(default_factory=dict, repr=False)
    source_environment: Mapping[str, str] | None = field(default=None, repr=False)
    client_name: str = "echo_agent"
    client_title: str = "Echo Agent"
    client_version: str = "0.2.0"
    experimental_api: bool = False
    opt_out_notification_methods: tuple[str, ...] = ()
    request_timeout_s: float = 30.0
    initialize_timeout_s: float = 10.0
    approval_timeout_s: float = 300.0
    close_grace_s: float = 1.0
    terminate_grace_s: float = 1.0
    kill_wait_s: float = 1.0
    notification_queue_size: int = 512
    approval_queue_size: int = 16
    max_pending_requests: int = 128
    max_message_bytes: int = 4 * 1024 * 1024
    max_method_chars: int = 256
    max_json_depth: int = 64
    stderr_tail_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not self.command or any(
            not isinstance(part, str) or not part or "\x00" in part for part in self.command
        ):
            raise ConfigurationError("command must contain non-empty, NUL-free argv entries")
        if self.cwd is not None and (not isinstance(self.cwd, str) or "\x00" in self.cwd):
            raise ConfigurationError("cwd must not contain NUL")
        if not self.client_name or not self.client_title or not self.client_version:
            raise ConfigurationError("client metadata fields must be non-empty")
        if any(not isinstance(key, str) or not key for key in self.env_allowlist):
            raise ConfigurationError("environment allowlist names must be non-empty strings")
        for env_key, env_value in self.env_overrides.items():
            if (
                not isinstance(env_key, str)
                or not isinstance(env_value, str)
                or "\x00" in env_value
            ):
                raise ConfigurationError("environment overrides must be NUL-free string pairs")
        unknown_env = set(self.env_overrides).difference(self.env_allowlist)
        if unknown_env:
            names = ", ".join(sorted(unknown_env))
            raise ConfigurationError(f"environment override is not allowlisted: {names}")
        positive_floats = {
            "request_timeout_s": self.request_timeout_s,
            "initialize_timeout_s": self.initialize_timeout_s,
            "approval_timeout_s": self.approval_timeout_s,
            "close_grace_s": self.close_grace_s,
            "terminate_grace_s": self.terminate_grace_s,
            "kill_wait_s": self.kill_wait_s,
        }
        for name, float_value in positive_floats.items():
            if float_value <= 0:
                raise ConfigurationError(f"{name} must be positive")
        positive_ints = {
            "notification_queue_size": self.notification_queue_size,
            "approval_queue_size": self.approval_queue_size,
            "max_pending_requests": self.max_pending_requests,
            "max_message_bytes": self.max_message_bytes,
            "max_method_chars": self.max_method_chars,
            "max_json_depth": self.max_json_depth,
            "stderr_tail_bytes": self.stderr_tail_bytes,
        }
        for name, int_value in positive_ints.items():
            if int_value <= 0:
                raise ConfigurationError(f"{name} must be positive")


__all__ = [
    "ApprovalHandler",
    "ApprovalRequest",
    "AppServerProcess",
    "BackpressureError",
    "CodexAppServerConfig",
    "CodexAppServerError",
    "CodexProviderProfile",
    "ConfigurationError",
    "DEFAULT_ENV_ALLOWLIST",
    "JsonObject",
    "JsonValue",
    "MessageTooLargeError",
    "Notification",
    "ProcessFactory",
    "ProcessLaunch",
    "ProtocolError",
    "RemoteError",
    "RequestTimeoutError",
    "TransportClosedError",
]
