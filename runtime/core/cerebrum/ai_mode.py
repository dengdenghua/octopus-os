"""AI Mode — Marvis-style two-mode wrapper over the 3-tier router.

Ordinary users don't reason about "local / value / performance".
They reason about:

  * **效率模式 (efficiency)** — let the router pick: local for cheap
    chitchat, cloud for serious work. Best speed × quality tradeoff
    for most users. (Default.)

  * **隐私模式 (privacy)** — never leave the machine. All turns route
    to the local model regardless of complexity. Files stay local.
    Slower / weaker on hard tasks, but zero data exits the host.

This module is the user-facing knob. Internally it sits **above**
``turn_complexity``: AI mode maps to a routing override that the
classifier honors.

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

    Always returns a valid mode — corrupted files / unknown values
    fall through to the default.
    """
    env_val = os.environ.get("ECHO_AI_MODE")
    if env_val and env_val.strip().lower() in _VALID_MODES:
        return env_val.strip().lower()  # type: ignore[return-value]

    p = _state_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            mode = data.get("mode")
            if isinstance(mode, str) and mode.lower() in _VALID_MODES:
                return mode.lower()  # type: ignore[return-value]
        except Exception:  # noqa: BLE001 — corrupted file → default
            pass
    return _DEFAULT_MODE


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
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {"mode": canonical, "set_at": time.time()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        _log.warning("ai_mode persist failed: %s", exc)
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
    """Whether a local model server appears available.

    Looks for:
      * ``ECHO_MODEL_LOCAL`` env (explicit operator config)
      * ``ollama`` on PATH + a local server on :11434
      * LM Studio default port 1234
    """
    if os.environ.get("ECHO_MODEL_LOCAL"):
        return True, "ECHO_MODEL_LOCAL configured"

    if shutil.which("ollama"):
        try:
            r = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if r.returncode == 0 and r.stdout.strip().count("\n") >= 1:
                return True, "ollama detected with at least one model"
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001 — ollama check is best-effort
            pass

    # Best-effort port probe — LM Studio / vLLM default
    try:
        import socket

        for port in (11434, 1234, 8000):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            try:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return True, f"local server on port {port}"
            finally:
                sock.close()
    except OSError:  # noqa: BLE001 — port probe is best-effort
        pass

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
    try:
        # psutil is in pyproject deps; fall back to platform-specific if missing
        import psutil  # type: ignore[import-untyped]

        return psutil.virtual_memory().total / (1024**3)
    except ImportError:  # noqa: BLE001 — RAM check is best-effort, fall back to /proc
        pass
    try:
        # Linux fallback
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024**2)
    except OSError:  # noqa: BLE001 — RAM check is best-effort
        pass
    return 0.0


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
    cloud = _detect_cloud_reachable()

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

    Privacy mode pins everything to ``local`` — the router will then
    escalate up if no local model is configured (so we never silently
    ship data to cloud when the user asked for privacy).

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
