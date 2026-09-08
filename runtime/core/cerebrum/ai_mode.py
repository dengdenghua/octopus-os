"""AI Mode — Marvis-style two-mode wrapper over the 3-tier router.

Ordinary users don't reason about "local / value / performance".
They reason about:

  * **效率模式 (efficiency)** — let the router pick: local for cheap
    chitchat, cloud for serious work. Best speed × quality tradeoff
    for most users. (Default.)

  * **隐私模式 (privacy)** — admit only verified loopback model
    transports and explicitly local tools. Stop when local inference
    is unavailable; never escalate to a cloud provider. This is an
    Agent application policy, not an operating-system firewall.

This module is the canonical policy source. Routing, model transports,
tool execution and the Storage gateway enforce the same setting.

Operator/user setting flow:

  1. UI calls ``GET /api/ai-mode`` → returns ``{mode, recommended,
     device_summary}``
  2. UI shows the two cards; user picks one
  3. UI calls ``POST /api/ai-mode {"mode": "efficiency"}``
  4. Setting persisted in ``data/ai_mode.json``
  5. Every turn reads ``current_ai_mode()`` and applies its override
     before complexity classification picks the tier

Device detection (``detect_recommended_mode``) inspects:

  * Available local models (``ollama list`` etc.)
  * RAM size
  * GPU presence (``nvidia-smi`` etc.)
  * Network reachability of cloud providers
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger("echo.ai_mode")

AIMode = Literal["efficiency", "privacy"]
_VALID_MODES: tuple[AIMode, ...] = ("efficiency", "privacy")
_DEFAULT_MODE: AIMode = "efficiency"


def _state_path() -> Path:
    """Where the AI-mode setting persists.

    Override via ``ECHO_AI_MODE_PATH`` for tests / non-default
    install layouts. Otherwise lives under the data dir (next to
    feature flags / cron).
    """
    explicit = os.environ.get("ECHO_AI_MODE_PATH")
    if explicit:
        return Path(explicit).expanduser()
    try:
        from runtime.platform.process.paths import app_paths

        return app_paths().data_dir / "ai_mode.json"
    except Exception:  # noqa: BLE001 — fall through to a sensible default
        return Path("data") / "ai_mode.json"


def current_ai_mode() -> AIMode:
    """Return the persisted AI mode, defaulting to ``efficiency``.

    Resolution order:
      1. ``ECHO_AI_MODE`` env var (operator override)
      2. ``data/ai_mode.json`` (user setting from UI)
      3. ``"efficiency"`` (default)

    An unreadable or malformed existing policy fails closed to privacy.
    """
    env_val = os.environ.get("ECHO_AI_MODE")
    if env_val is not None:
        return "efficiency" if env_val.strip().lower() == "efficiency" else "privacy"

    p = _state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        mode = data.get("mode") if isinstance(data, dict) else None
        return "efficiency" if mode == "efficiency" else "privacy"
    except FileNotFoundError:
        return _DEFAULT_MODE
    except (OSError, ValueError):
        return "privacy"


def set_ai_mode(mode: str) -> AIMode:
    """Persist the chosen AI mode.

    Returns the canonical (lowercased) mode actually written.
    Raises ``ValueError`` for unknown modes — callers (HTTP handler)
    should surface this as a 400.
    """
    if not isinstance(mode, str):
        raise ValueError(f"mode must be a string, got {type(mode).__name__}")
    canonical = mode.strip().lower()
    if canonical not in _VALID_MODES:
        raise ValueError(
            f"unknown AI mode {mode!r}; expected one of {_VALID_MODES}",
        )
    env_mode = os.environ.get("ECHO_AI_MODE")
    if env_mode is not None and canonical != current_ai_mode():
        raise ValueError("AI 模式已由设备环境配置锁定，无法在界面中修改。")
    from runtime.platform.io import atomic_write_json

    # No backup rotation: readers must never observe a missing policy between
    # two renames. Persistence errors must reach the UI, never false success.
    atomic_write_json(
        _state_path(), {"mode": canonical, "set_at": time.time()}, keep_backup=False
    )
    return canonical  # type: ignore[return-value]


# ── Device capability detection ───────────────────────────────


@dataclass
class DeviceSummary:
    has_local_model: bool
    has_gpu: bool
    ram_gb: float
    cpu_count: int
    cloud_reachable: bool
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_local_model": self.has_local_model,
            "has_gpu": self.has_gpu,
            "ram_gb": round(self.ram_gb, 1),
            "cpu_count": self.cpu_count,
            "cloud_reachable": self.cloud_reachable,
            "notes": list(self.notes),
        }


def _detect_local_model() -> tuple[bool, str | None]:
    """Require a loopback model catalog, not merely an open application port."""
    import urllib.request

    from runtime.safety.privacy import is_loopback_endpoint, private_urlopen
    from runtime.sensing.model_router.custom_model_flags import read_custom_models

    endpoints: list[str] = []
    for entry in (read_custom_models() or {}).values():
        base = entry.get("base_url") if isinstance(entry, dict) else None
        if is_loopback_endpoint(base):
            endpoints.append(f"{base.rstrip('/')}/models")
    endpoints.extend([
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.1:1234/v1/models",
    ])
    for endpoint in list(dict.fromkeys(endpoints))[:8]:
        try:
            with private_urlopen(urllib.request.Request(endpoint), timeout=0.3) as response:
                catalog = json.loads(response.read(64 * 1024))
            if not isinstance(catalog, dict):
                continue
            models = catalog.get("models") or catalog.get("data")
            if isinstance(models, list) and any(
                isinstance(model, dict) and (model.get("id") or model.get("name"))
                for model in models
            ):
                return True, "local model catalog available"
        except (OSError, ValueError):
            continue
    return False, None


def _detect_gpu() -> tuple[bool, str | None]:
    """Best-effort GPU detection."""
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if r.returncode == 0 and "GPU" in r.stdout:
                return True, "NVIDIA GPU detected"
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001 — GPU check is best-effort
            pass

    # Mac unified memory (Apple Silicon)
    if shutil.which("system_profiler"):
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if r.returncode == 0 and ("Apple M" in r.stdout or "Metal" in r.stdout):
                return True, "Apple Silicon GPU detected"
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001 — GPU check is best-effort
            pass

    return False, None


def _detect_ram_gb() -> float:
    """Total RAM in GB. Returns 0 on failure."""
    from runtime.platform.system_memory import total_memory_gb

    return total_memory_gb()


def _detect_cpu_count() -> int:
    return os.cpu_count() or 1


def _detect_cloud_reachable() -> bool:
    """Whether at least one cloud LLM endpoint is reachable.

    Best-effort, short timeout. False positives only matter for the
    UI hint — they never block routing.
    """
    try:
        import socket

        for host in ("api.anthropic.com", "api.openai.com", "open.bigmodel.cn"):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                if sock.connect_ex((host, 443)) == 0:
                    sock.close()
                    return True
                sock.close()
            except OSError:  # noqa: BLE001 — host check is best-effort, try next
                continue
    except OSError:  # noqa: BLE001 — cloud reachability check is best-effort
        pass
    return False


def detect_device_summary() -> DeviceSummary:
    """Run the full detection battery. Each probe is bounded so the
    call totals at most a few seconds even on a misconfigured box."""
    notes: list[str] = []

    has_local, local_note = _detect_local_model()
    if local_note:
        notes.append(local_note)

    has_gpu, gpu_note = _detect_gpu()
    if gpu_note:
        notes.append(gpu_note)

    ram_gb = _detect_ram_gb()
    cpu_count = _detect_cpu_count()
    cloud = False if current_ai_mode() == "privacy" else _detect_cloud_reachable()

    if not cloud:
        notes.append("no cloud LLM reachable")

    return DeviceSummary(
        has_local_model=has_local,
        has_gpu=has_gpu,
        ram_gb=ram_gb,
        cpu_count=cpu_count,
        cloud_reachable=cloud,
        notes=notes,
    )


def recommend_mode(summary: DeviceSummary) -> AIMode:
    """Pick a recommended mode based on device summary.

    Heuristic:
      * No cloud reachable + has local model → privacy (forced)
      * Has local model + GPU + ≥16GB RAM → efficiency (best of both)
      * Has local model + ≥8GB RAM → efficiency (slow but works)
      * Otherwise → efficiency (cloud-only)
    """
    if not summary.cloud_reachable and summary.has_local_model:
        return "privacy"
    return _DEFAULT_MODE


# ── Integration with turn_complexity ──────────────────────────


def apply_ai_mode_override(verdict: str) -> str:
    """Map a complexity verdict through the active AI mode.

    Privacy mode pins to local. Selection and provider transports separately
    enforce that no cloud fallback is permitted.

    Efficiency mode is a no-op pass-through; the 3-tier classifier
    decides per turn.
    """
    mode = current_ai_mode()
    if mode == "privacy":
        return "local"
    return verdict


__all__ = [
    "AIMode",
    "DeviceSummary",
    "apply_ai_mode_override",
    "current_ai_mode",
    "detect_device_summary",
    "recommend_mode",
    "set_ai_mode",
]
