from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlannerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["static", "llm"] = "static"
    model: str = "mock/planner"  # Implementation note.
    mock_response: str | None = None  # Implementation note.
    anthropic_api_key: str | None = None  # Implementation note.
    base_url: str | None = None
    max_nodes: int = Field(default=10, gt=0, le=50)


# ─── Budget defaults · single source of truth ─────────────────────────
# These constants are the ONE authoritative home for the default budget
# values. Other modules (react_loop, pause_control, cli_run, presets)
# reference these instead of re-hardcoding the same numbers.
BUDGET_DEFAULT_MAX_TOKENS = 100_000
BUDGET_DEFAULT_MAX_USD = 1.00
BUDGET_DEFAULT_MAX_LATENCY_MS = 600_000


class BudgetConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_tokens: int = Field(default=BUDGET_DEFAULT_MAX_TOKENS, gt=0)
    max_usd: float = Field(default=BUDGET_DEFAULT_MAX_USD, gt=0.0)
    max_latency_ms: int = Field(default=BUDGET_DEFAULT_MAX_LATENCY_MS, gt=0)
    # Wall-clock ceiling (seconds) for one hidden model-thinking iteration.
    # 10..900, default 120. Controlled only by this config (no env override).
    model_iteration_timeout_s: float = Field(default=120.0, ge=10.0, le=900.0)
    # Forced-convergence max_tokens cap for normal (non research/swarm) mode.
    # Default 2000; research/swarm convergence stays fixed at 5000.
    convergence_max_tokens: int = Field(default=2000, gt=0)
    # Elastic budget: pause on the hard USD spend ceiling when the threshold is
    # reached. Cumulative tokens are accounting telemetry by default because a
    # multi-step task resends prompt tokens on every model call; treating that
    # cumulative counter as context pressure prematurely interrupts long jobs.
    budget_auto_pause: bool = False
    # Legacy/strict accounting mode. Only when BOTH flags are true may the
    # cumulative token counter auto-pause a task. Current context pressure is
    # tracked separately and handled by compaction.
    cumulative_token_auto_pause: bool = False


class ImmunityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # SECURITY: keep this in EXACT sync with the TrustEngine in-process
    # default (runtime/safety/auth/trust_engine.py). ``mcp://*`` is
    # deliberately absent — a malicious MCP server must not be trusted
    # out of the box; operators whitelist specific ``mcp://<server>/*``
    # entries in config (see config.example.yaml). Previously this
    # default DID include ``mcp://*`` while TrustEngine's did not, so
    # the yaml-driven path silently trusted every MCP server — the
    # opposite of the documented posture.
    trusted_sources: list[str] = Field(default_factory=lambda: ["skill://public/*"])
    self_whitelist: list[str] = Field(
        default_factory=lambda: [
            "cerebrum",
            "ganglia",
            "arms/*",
            "react_loop",
            "react_arm",
            "intel_collector/*",
        ]
    )
    unknown_policy: Literal["quarantine", "reject", "allow"] = "quarantine"
    # Memory tier (antibody memory). ``attack_memory_path`` enables
    # persistence across restarts; None keeps antibodies in-process.
    attack_memory_path: str | None = None
    attack_threshold: int = 3
    attack_window_seconds: int = 3600
    # Adaptive tier (behavioural anomaly z-score). Off by default —
    # needs a per-sucker cost-baseline history to be meaningful.
    enable_adaptive: bool = False
    adaptive_window_size: int = 200
    adaptive_quarantine_threshold: float = 0.7


class IntelSourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    max_results: int = 5
    fetch_top_n: int = 0
    frequency_seconds: int = 3600
    tags: list[str] = Field(default_factory=list)


class MCPServerConfigEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    # stdio transport (local subprocess server)
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http/sse transport (remote/hosted server)
    transport: Literal["stdio", "http", "sse"] = "stdio"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    name_prefix: str | None = None  # Implementation note.
    # Required for a shared/commercial stdio server. The server process is
    # launched inside this operator-selected workspace by the hard process
    # sandbox; it is never inferred from a model-supplied path.
    sandbox_dir: str | None = None


class SafetyConfig(BaseModel):
    """Typed backing for the ``safety:`` config block.

    Without this, the whole ``safety:`` section was dropped by Pydantic
    (AgentConfig had no field for it), so a typo like ``enabled_llm_judge``
    silently disabled the constitution judge with zero feedback, and a
    judge flag in a ``--config`` file outside cwd was ignored entirely
    (the bootstrap re-read cwd yaml). Reading it off the loaded config
    fixes both.
    """

    model_config = ConfigDict(frozen=True)

    # None = "not set in config" (defer to env var / legacy yaml).
    # True/False = an explicit choice in the loaded --config file.
    enable_llm_judge: bool | None = None
    llm_judge_model: str | None = None
    # Other safety knobs (enable_trust_signal, disabled_guards, …) are
    # still read via their own paths; declared here so a populated
    # ``safety:`` block validates instead of being silently dropped.
    enable_trust_signal: bool | None = None
    # Whether a realtime client may set ``approvalPolicy="never"`` to
    # skip the human approval gate. Default (None / False) is SECURE:
    # the gateway downgrades ``never`` → ``on-request`` so an untrusted
    # client can't silently disable approvals. Operators on a trusted
    # single-user host opt in with ``safety.allow_client_approval_bypass:
    # true``.
    allow_client_approval_bypass: bool | None = None


class ExecutionConfig(BaseModel):
    """Deployment-level execution isolation contract.

    ``local`` preserves the desktop/developer default.  Shared modes are
    consumed by ``serve`` before the runtime is built, so a commercial
    deployment cannot accidentally rely on the legacy soft subprocess path.
    """

    model_config = ConfigDict(frozen=True)

    deployment_mode: Literal["local", "shared", "commercial", "production", "server"] = "local"
    process_sandbox: Literal[
        "auto",
        "soft",
        "direct",
        "off",
        "strict",
        "bwrap",
        "bubblewrap",
        "seatbelt",
        "sandbox-exec",
    ] = "auto"


class LearnConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    learn_from_journal: str | None = None  # Implementation note.
    min_hits: int = 3
    max_rules: int = 30
    learn_memories_from_journal: str | None = None  # Implementation note.
    learn_kg_from_journal: str | None = None  # Implementation note.
    kg_max_triples: int = 15
    rewrite_from_journal: str | None = None  # Implementation note.
    rewrite_min_confidence: float = 0.7
    rewrite_min_severity: Literal["low", "mid", "high"] = "mid"
    assess_recipe_from_journal: str | None = None  # Implementation note.

    rules_persist_path: str | None = None  # Implementation note.
    memories_persist_path: str | None = None  # Implementation note.
    static_rules_persist_path: str | None = None  # Implementation note.

    @field_validator(
        "learn_from_journal",
        "learn_memories_from_journal",
        "learn_kg_from_journal",
        "rewrite_from_journal",
        "assess_recipe_from_journal",
        "rules_persist_path",
        "memories_persist_path",
        "static_rules_persist_path",
    )
    @classmethod
    def _validate_local_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.strip()
        if not path:
            raise ValueError("path must not be empty")
        if any(ord(ch) < 32 for ch in path):
            raise ValueError("path must not contain control characters")
        if "://" in path:
            raise ValueError("path must be a local filesystem path, not a URI")
        try:
            Path(path)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid filesystem path") from exc
        return path


class CredentialPoolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    keys: list[str] = Field(default_factory=list)
    strategy: Literal["round_robin", "least_used", "random"] = "round_robin"
    cooldown_seconds: float = Field(default=60.0, gt=0.0)
    max_retries: int = Field(default=3, gt=0)


class HotCacheConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    path: str = "~/.echo/hot_cache"
    max_words: int = Field(default=500, gt=0)
    ttl_hours: float = Field(default=24.0, gt=0.0)


_CHEAPER_MAP: dict[str, str] = {
    "mimo-v2.5-pro": "mimo-v2-flash",
    "claude-sonnet-4-6": "claude-haiku-4-5-20251001",
    "claude-opus-4-7": "claude-sonnet-4-6",
    "gpt-4o": "gpt-4o-mini",
    "gpt-4-turbo": "gpt-4o-mini",
}


def _pick_cheaper(model: str) -> str:
    return _CHEAPER_MAP.get(model, model)


class EvolveConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy: Literal["inherit", "cheaper_same_provider", "explicit"] = "inherit"
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None

    def resolve(self, planner: PlannerConfig) -> tuple[str, str | None]:
        if self.strategy == "inherit":
            return planner.model, planner.base_url
        if self.strategy == "cheaper_same_provider":
            return _pick_cheaper(planner.model), planner.base_url
        return self.model or planner.model, self.base_url or planner.base_url


# Audit R-03: known-weak / example JWT secrets that must never pass schema
# validation, no matter how long they are. Rotated on 2026-08-17 — the old
# dev literal is treated as leaked.
_WEAK_JWT_SECRET_VALUES: frozenset[str] = frozenset(
    {
        "dev-secret-key-32-chars-minimum-required",
        "secret",
        "changeme",
        "change-me",
        "change_me",
        "replaceme",
        "replace-me",
        "replace_me",
        "your-secret",
        "your-secret-key",
        "your_secret",
        "your-secret-here",
        "jwt-secret",
        "jwt_secret",
        "dev-secret",
        "development",
        "development-secret",
        "password",
        "password123",
        "1234567890",
        "0123456789",
    }
)


def validate_jwt_secret(secret: str | None, *, owner: str) -> None:
    """Reject weak/known JWT secrets at startup (audit R-03).

    * ``None`` is fine — callers that require a secret (e.g. oct enabled)
      enforce it separately.
    * Exact weak/example values are rejected even if long enough.
    * Entropy gate: at least 3 of lower/upper/digit/special must be present
      (same rule as ``integrations.local_auth``), so all-lowercase filler
      like the old dev literal cannot slip through a length check.
    """
    if secret is None:
        return
    if secret.strip().lower() in _WEAK_JWT_SECRET_VALUES:
        raise ValueError(
            f"config.{owner}.jwt_secret is a known weak/default value and is "
            "rejected at startup (audit R-03); rotate it and inject a strong "
            "secret via environment"
        )
    has_lower = any(c.islower() for c in secret)
    has_upper = any(c.isupper() for c in secret)
    has_digit = any(c.isdigit() for c in secret)
    has_special = any(not c.isalnum() for c in secret)
    score = sum([has_lower, has_upper, has_digit, has_special])
    if score < 3:
        raise ValueError(
            f"config.{owner}.jwt_secret is too predictable: needs at least 3 of "
            "lowercase, uppercase, digits, special characters"
        )


class OctConfig(BaseModel):
    """oct 账号网关(echo 自己的,echo-mobile server)。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    base_url: str = "https://api.echo-age.com"
    default_model: str = "qwen3.5-flash"
    request_timeout_seconds: float = Field(default=15.0, ge=1.0, le=600.0)
    llm_timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    mock_mode: bool = False
    jwt_secret: str | None = Field(default=None, min_length=32)
    jwt_expire_seconds: int = Field(default=2_592_000, gt=0)
    jwt_issuer: str = "echo-agent"

    @model_validator(mode="after")
    def _require_secret_when_enabled(self) -> OctConfig:
        # 启用 oct 必须配 jwt_secret:agent 要用它签发自有会话 JWT 并被全局鉴权门校验。
        # 否则登录会回退到"复用网关 JWT"——agent 不持网关密钥、验不过 → 已登录用户被锁死。
        if self.enabled and not self.jwt_secret:
            raise ValueError("config.oct.enabled=true 时必须设置 oct.jwt_secret(≥32 字符)")
        validate_jwt_secret(self.jwt_secret, owner="oct")
        return self


class LocalAuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    allow_any_username: bool = False
    allowed_usernames: list[str] = Field(default_factory=list)
    users: dict[str, str] = Field(default_factory=dict)
    login_max_failures: int = Field(default=5, ge=1, le=100)
    login_ip_max_failures: int = Field(default=20, ge=1, le=1_000)
    login_failure_window_seconds: float = Field(default=300.0, gt=0, le=86_400)
    login_lockout_seconds: float = Field(default=60.0, gt=0, le=86_400)
    login_rate_limit_max_entries: int = Field(default=10_000, ge=1, le=1_000_000)
    jwt_secret: str | None = Field(default=None, min_length=32)
    jwt_expire_seconds: int = Field(default=604_800, gt=0)
    jwt_issuer: str = "echo-agent"
    jwt_audience: str | None = None
    actor_prefix: str = "local:"
    default_roles: list[str] = Field(default_factory=lambda: ["user", "local"])

    @model_validator(mode="after")
    def _reject_weak_jwt_secret(self) -> LocalAuthConfig:
        validate_jwt_secret(self.jwt_secret, owner="local_auth")
        return self

    @property
    def password_required(self) -> bool:
        return bool(self.users)


class CanaryConfig(BaseModel):
    stages: list[str] = Field(default_factory=lambda: ["shadow", "5pct", "25pct", "50pct", "full"])
    sample_sizes: dict[str, int] = Field(
        default_factory=lambda: {
            "shadow": 0,
            "5pct": 5,
            "25pct": 25,
            "50pct": 50,
            "full": 100,
        }
    )
    auto_promote: bool = True
    rollback_on_failure: bool = True
    max_duration_hours: float = 72.0


class DriftConfig(BaseModel):
    check_soul: bool = True
    check_genome: bool = True
    check_score: bool = True
    score_window: int = 10
    score_threshold: float = 0.15
    critical_auto_rollback: bool = True


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Background scheduler worker pool. Default 2 allows two scheduled tasks
    # to run concurrently (e.g., hourly health check + daily backup). Raise
    # to run more periodic ticks in parallel.
    max_workers: int = Field(default=2, ge=1, le=128)


class TentacleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Mobile/cross-device bridge. Enabled by default to preserve the existing
    # serve behavior; deterministic e2e configs can disable it to avoid binding
    # the fixed LAN WebSocket port.
    enabled: bool = True
    ws_port: int = Field(default=8765, ge=1, le=65535)


class ToolEffectsConfig(BaseModel):
    """Durable receipt plane for at-most-once external tool effects."""

    model_config = ConfigDict(frozen=True)

    backend: Literal["auto", "sqlite", "redis"] = "auto"
    sqlite_path: str | None = None
    redis_url: str | None = None
    key_prefix: str = Field(default="echo:tool-effect:", min_length=1)
    connect_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    require_distributed: bool = False

    @model_validator(mode="after")
    def _validate_backend(self) -> ToolEffectsConfig:
        if self.backend == "redis" and not str(self.redis_url or "").strip():
            raise ValueError("tool_effects.redis_url is required for backend=redis")
        if self.backend == "sqlite" and not str(self.sqlite_path or "").strip():
            raise ValueError("tool_effects.sqlite_path is required for backend=sqlite")
        if self.require_distributed and self.backend != "redis":
            raise ValueError("tool_effects.require_distributed=true requires backend=redis")
        if self.sqlite_path is not None:
            path = self.sqlite_path.strip()
            if not path or "://" in path:
                raise ValueError("tool_effects.sqlite_path must be a local path")
        return self


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "echo-agent"
    version_compat: str = "0.2"
    preset: str | None = None

    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    immunity: ImmunityConfig = Field(default_factory=ImmunityConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    learn: LearnConfig = Field(default_factory=LearnConfig)
    credential_pool: CredentialPoolConfig = Field(default_factory=CredentialPoolConfig)
    hot_cache: HotCacheConfig = Field(default_factory=HotCacheConfig)
    evolve: EvolveConfig = Field(default_factory=EvolveConfig)
    canary: CanaryConfig = Field(default_factory=CanaryConfig)
    drift: DriftConfig = Field(default_factory=DriftConfig)
    oct: OctConfig = Field(default_factory=OctConfig)
    local_auth: LocalAuthConfig = Field(default_factory=LocalAuthConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    tentacle: TentacleConfig = Field(default_factory=TentacleConfig)
    tool_effects: ToolEffectsConfig = Field(default_factory=ToolEffectsConfig)

    intel_sources: list[IntelSourceConfig] = Field(default_factory=list)
    mcp_servers: list[MCPServerConfigEntry] = Field(default_factory=list)

    journal_file: str | None = None  # Implementation note.
    # Journal rotation cap in bytes. When the journal exceeds this size, the
    # oldest events are dropped to keep the file bounded. None (default) disables
    # rotation. Recommended: 10-50 MB (10485760-52428800 bytes) for demo/dev.
    # Audit R-04: bounded by default so a long-running journal cannot grow
    # without limit (JSONL rotates at the cap; the in-memory journal uses a
    # ring buffer sized from it). Explicitly set null to opt out.
    journal_max_bytes: int | None = Field(default=50_000_000, ge=1_000_000)
    # Disable external web/browser skill groups while retaining local coding,
    # filesystem, git, shell, quality and desktop tools.
    enable_web_skills: bool = True
    default_arm_id: str = "code_arm"
