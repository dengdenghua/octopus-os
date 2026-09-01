"""Local-model cookbook: recommend which model to run on THIS machine.

echo already has the plumbing to *serve* local models (``ollama_router``) and a
coarse "can this box do local at all?" check (``ai_mode``). What was missing — and
what Odysseus's hwfit cookbook does — is the middle layer: detect the hardware,
then for a curated model catalog work out what *fits* in memory and roughly how
*fast* it would generate, so the user gets a ranked "install this" list instead of
guessing.

Scoped on purpose:
- Detection is stdlib + ``nvidia-smi`` / ``system_profiler`` only (no new deps).
- The catalog + bandwidth tables are a hand-curated 2026 snapshot — data that
  goes stale as new chips / models ship; treat them as a starting point, not gospel.
- Throughput is a roofline estimate (generation is memory-bandwidth bound:
  tokens/s ≈ bandwidth ÷ active-weight-bytes), not a benchmark.

The fit math is pure and unit-tested; only ``detect_hardware`` and the ollama
calls touch the outside world.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass

# ── quantization → bytes per parameter (incl. typical GGUF overhead) ──────────
QUANT_BYTES: dict[str, float] = {
    "f16": 2.0,
    "q8_0": 1.06,
    "q6_K": 0.82,
    "q5_K_M": 0.69,
    "q4_K_M": 0.56,  # ollama's default pull for most tags
    "q4_0": 0.50,
    "q3_K_M": 0.43,
    "q2_K": 0.33,
}
DEFAULT_QUANT = "q4_K_M"

# Per-token generation reads the active weights plus KV-cache + runtime scratch.
# A flat ~0.9 GB headroom approximates context/KV/runtime for the small-to-mid
# models in the catalog (good enough for a fit verdict, not a profiler).
_OVERHEAD_GB = 0.9


@dataclass
class Hardware:
    backend: str  # "cuda" | "metal" | "cpu"
    gpu_name: str | None
    vram_gb: float  # usable model-weight budget (VRAM, or unified RAM share)
    ram_gb: float
    bandwidth_gbps: float | None  # memory bandwidth for the tps roofline; None if unknown
    unified_memory: bool
    note: str | None = None


@dataclass
class ModelSpec:
    tag: str  # the ollama pull tag, e.g. "qwen2.5:7b" or "hf.co/<repo>:Q4_K_M"
    label: str
    params_b: float  # total params, billions
    family: str
    arch_rank: int  # rough capability/recency tier (higher = newer/stronger)
    active_params_b: float | None = None  # for MoE; defaults to params_b (dense)
    weight_gb: float | None = None  # measured GGUF size (live catalog); overrides the estimate

    def active(self) -> float:
        return self.active_params_b or self.params_b


@dataclass
class Recommendation:
    tag: str
    label: str
    params_b: float
    quant: str
    est_mem_gb: float
    fits: bool
    verdict: str  # "fits" | "tight" | "offload" | "too_big"
    est_tokens_per_s: float | None
    installed: bool
    score: float
    family: str


# ── curated catalog · 2026 snapshot (dense models, ollama tags) ───────────────
def default_catalog() -> list[ModelSpec]:
    return [
        ModelSpec("qwen2.5:0.5b", "Qwen2.5 0.5B", 0.5, "qwen", 8),
        ModelSpec("llama3.2:1b", "Llama 3.2 1B", 1.2, "llama", 7),
        ModelSpec("qwen2.5:1.5b", "Qwen2.5 1.5B", 1.5, "qwen", 8),
        ModelSpec("qwen2.5-coder:1.5b", "Qwen2.5-Coder 1.5B", 1.5, "qwen-coder", 8),
        ModelSpec("gemma2:2b", "Gemma 2 2B", 2.6, "gemma", 7),
        ModelSpec("llama3.2:3b", "Llama 3.2 3B", 3.2, "llama", 7),
        ModelSpec("qwen2.5:3b", "Qwen2.5 3B", 3.1, "qwen", 8),
        ModelSpec("phi3.5:3.8b", "Phi-3.5 Mini", 3.8, "phi", 6),
        ModelSpec("mistral:7b", "Mistral 7B", 7.2, "mistral", 5),
        ModelSpec("qwen2.5:7b", "Qwen2.5 7B", 7.6, "qwen", 8),
        ModelSpec("qwen2.5-coder:7b", "Qwen2.5-Coder 7B", 7.6, "qwen-coder", 9),
        ModelSpec("llama3.1:8b", "Llama 3.1 8B", 8.0, "llama", 7),
        ModelSpec("deepseek-r1:8b", "DeepSeek-R1 8B (distill)", 8.0, "deepseek-r1", 8),
        ModelSpec("gemma2:9b", "Gemma 2 9B", 9.2, "gemma", 7),
        ModelSpec("qwen2.5:14b", "Qwen2.5 14B", 14.8, "qwen", 8),
        ModelSpec("qwen2.5-coder:14b", "Qwen2.5-Coder 14B", 14.8, "qwen-coder", 9),
        ModelSpec("deepseek-r1:14b", "DeepSeek-R1 14B (distill)", 14.8, "deepseek-r1", 8),
        ModelSpec("gemma2:27b", "Gemma 2 27B", 27.2, "gemma", 7),
        ModelSpec("qwen2.5:32b", "Qwen2.5 32B", 32.8, "qwen", 8),
        ModelSpec("qwen2.5-coder:32b", "Qwen2.5-Coder 32B", 32.8, "qwen-coder", 9),
        ModelSpec("deepseek-r1:32b", "DeepSeek-R1 32B (distill)", 32.8, "deepseek-r1", 8),
        ModelSpec("llama3.1:70b", "Llama 3.1 70B", 70.6, "llama", 7),
        ModelSpec("qwen2.5:72b", "Qwen2.5 72B", 72.7, "qwen", 8),
    ]


# ── memory-bandwidth tables (GB/s) for the tps roofline ───────────────────────
_APPLE_BW: list[tuple[str, float]] = [
    ("m1 ultra", 800),
    ("m1 max", 400),
    ("m1 pro", 200),
    ("m1", 68),
    ("m2 ultra", 800),
    ("m2 max", 400),
    ("m2 pro", 200),
    ("m2", 100),
    ("m3 ultra", 800),
    ("m3 max", 400),
    ("m3 pro", 150),
    ("m3", 100),
    ("m4 max", 546),
    ("m4 pro", 273),
    ("m4", 120),
]
_NVIDIA_BW: list[tuple[str, float]] = [
    ("h100", 3350),
    ("a100", 1935),
    ("5090", 1792),
    ("4090", 1008),
    ("3090", 936),
    ("4080", 717),
    ("3080", 760),
    ("4070", 504),
    ("3070", 448),
    ("3060", 360),
    ("4060", 272),
    ("a6000", 768),
]


def _match_bandwidth(name: str, table: list[tuple[str, float]]) -> float | None:
    low = name.lower()
    for key, bw in table:
        if key in low:
            return bw
    return None


# ── detection (touches the OS; best-effort, never raises) ─────────────────────
def _run(cmd: list[str], timeout: float = 2.5) -> str | None:
    try:
        r = subprocess.run(  # noqa: S603 — resolved argv, shell=False
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _ram_gb() -> float:
    try:
        import psutil  # type: ignore[import-untyped]

        return psutil.virtual_memory().total / (1024**3)
    except Exception:  # noqa: BLE001 — fall back to platform probes
        pass
    if platform.system() == "Darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        if out and out.isdigit():
            return int(out) / (1024**3)
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024**2)
    except OSError:  # expected · non-Linux or unreadable, falls through to the unknown-size default
        pass
    return 0.0


def _detect_nvidia() -> tuple[float, str | None]:
    """Total VRAM (GB) summed across NVIDIA GPUs + a representative name."""
    if not shutil.which("nvidia-smi"):
        return 0.0, None
    out = _run(["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"])
    if not out:
        return 0.0, None
    total_mb = 0.0
    name = None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].replace(".", "", 1).isdigit():
            total_mb += float(parts[0])
            name = name or parts[1]
    return total_mb / 1024.0, name


def _detect_apple() -> tuple[str | None, float | None]:
    """Apple Silicon chip name + memory bandwidth, or (None, None)."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return None, None
    out = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or ""
    chip = out.strip() or "Apple Silicon"
    bw = _match_bandwidth(chip, _APPLE_BW)
    return chip, bw


def detect_hardware() -> Hardware:
    """Best-effort hardware snapshot. Never raises — worst case is a CPU verdict."""
    ram = round(_ram_gb(), 1)
    vram, gpu = _detect_nvidia()
    if vram > 0:
        return Hardware(
            backend="cuda",
            gpu_name=gpu,
            vram_gb=round(vram, 1),
            ram_gb=ram,
            bandwidth_gbps=_match_bandwidth(gpu or "", _NVIDIA_BW),
            unified_memory=False,
        )
    chip, bw = _detect_apple()
    if chip is not None:
        # Unified memory: weights share RAM with the OS — budget a share, not all.
        return Hardware(
            backend="metal",
            gpu_name=chip,
            vram_gb=round(ram * 0.72, 1),
            ram_gb=ram,
            bandwidth_gbps=bw,
            unified_memory=True,
            note="Apple Silicon unified memory (≈72% budgeted for weights)",
        )
    return Hardware(
        backend="cpu",
        gpu_name=None,
        vram_gb=round(ram * 0.6, 1),
        ram_gb=ram,
        bandwidth_gbps=None,
        unified_memory=False,
        note="No supported GPU detected — CPU inference is slow; prefer ≤7B models",
    )


# ── fit math (pure / unit-tested) ─────────────────────────────────────────────
def estimate_mem_gb(params_b: float, quant: str) -> float:
    """Approx RAM/VRAM (GB) to load + run a model at a given quant."""
    return params_b * QUANT_BYTES.get(quant, QUANT_BYTES[DEFAULT_QUANT]) + _OVERHEAD_GB


def estimate_tps(active_params_b: float, quant: str, bandwidth_gbps: float | None) -> float | None:
    """Roofline generation speed: tokens/s ≈ bandwidth ÷ active-weight-bytes.

    Generation is memory-bandwidth bound — each token streams every *active*
    weight once. Returns None when bandwidth is unknown (e.g. CPU / unlisted GPU)."""
    if not bandwidth_gbps:
        return None
    active_gb = active_params_b * QUANT_BYTES.get(quant, QUANT_BYTES[DEFAULT_QUANT])
    if active_gb <= 0:
        return None
    return round(bandwidth_gbps / active_gb, 1)


def _verdict(mem_gb: float, budget_gb: float) -> str:
    if mem_gb <= budget_gb * 0.85:
        return "fits"
    if mem_gb <= budget_gb:
        return "tight"
    if mem_gb <= budget_gb * 1.5:
        return "offload"  # partial CPU offload — runs, but slower
    return "too_big"


def _score(rec_verdict: str, arch_rank: int, params_b: float, tps: float | None) -> float:
    """Rank: prefer models that fit, then bigger/newer, with a gentle nudge for
    interactive speed. ``offload`` is heavily penalised but not excluded."""
    base = {"fits": 100.0, "tight": 80.0, "offload": 40.0, "too_big": 0.0}[rec_verdict]
    if rec_verdict == "too_big":
        return 0.0
    speed = 0.0
    if tps is not None:
        # Saturating bonus: reward usable interactivity, but cap it so a tiny
        # toy model can't outrank a far more capable one that's still fast enough.
        speed = min(tps, 30.0) / 6.0
    # Capability (size) is weighted ahead of raw speed so a mid-size model beats a
    # 1B that only wins on tokens/s.
    return base + arch_rank * 2.0 + min(params_b, 40.0) * 1.4 + speed


def recommend(
    hardware: Hardware,
    catalog: list[ModelSpec] | None = None,
    *,
    installed: set[str] | None = None,
    quant: str = DEFAULT_QUANT,
    top_k: int = 8,
) -> list[Recommendation]:
    """Rank the catalog for this hardware at ``quant`` (ollama's default pull).

    Returns the runnable models best-first (fits / tight / offload), capped at
    ``top_k``; ``too_big`` ones are dropped from the list but the caller can still
    show them via ``recommend_all`` if it wants to explain the ceiling."""
    installed = installed or set()
    out: list[Recommendation] = []
    for spec in catalog or default_catalog():
        if spec.weight_gb:
            # Live catalog: a measured GGUF size beats the params×bpp estimate.
            mem = round(spec.weight_gb + _OVERHEAD_GB, 1)
            tps = (
                round(hardware.bandwidth_gbps / spec.weight_gb, 1)
                if hardware.bandwidth_gbps
                else None
            )
        else:
            mem = estimate_mem_gb(spec.params_b, quant)
            tps = estimate_tps(spec.active(), quant, hardware.bandwidth_gbps)
        verdict = _verdict(mem, hardware.vram_gb)
        if verdict == "too_big":
            continue
        out.append(
            Recommendation(
                tag=spec.tag,
                label=spec.label,
                params_b=spec.params_b,
                quant=quant,
                est_mem_gb=round(mem, 1),
                fits=verdict in ("fits", "tight"),
                verdict=verdict,
                est_tokens_per_s=tps,
                installed=spec.tag in installed,
                score=round(_score(verdict, spec.arch_rank, spec.params_b, tps), 1),
                family=spec.family,
            )
        )
    out.sort(key=lambda r: r.score, reverse=True)
    return out[:top_k]


# ── ollama integration (installed set + pull) ─────────────────────────────────
def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def installed_models() -> set[str]:
    """Tags ollama already has locally (``/api/tags``); empty if ollama is down."""
    try:
        import httpx

        with httpx.Client(timeout=4.0) as client:
            resp = client.get(f"{_ollama_base_url()}/api/tags")
        if resp.status_code != 200:
            return set()
        data = resp.json()
    except Exception:  # noqa: BLE001 — ollama absent → nothing installed
        return set()
    out: set[str] = set()
    for m in data.get("models", []):
        name = m.get("name") or m.get("model")
        if name:
            out.add(name)
            # ollama reports "qwen2.5:7b"; also index the bare tag for matching.
            out.add(re.sub(r"@.*$", "", name))
    return out


def ollama_available() -> bool:
    try:
        import httpx

        with httpx.Client(timeout=3.0) as client:
            client.get(f"{_ollama_base_url()}/api/tags")
        return True
    except Exception:  # noqa: BLE001
        return False


def pull_model(tag: str, *, timeout: float = 3600.0) -> dict[str, object]:
    """Trigger an ollama pull of ``tag`` (blocking; ollama streams progress).

    Returns ``{"status": "ok"|"error", ...}``. The tag is validated against a
    strict allow-list so nothing user-controlled reaches a shell or a surprise
    registry path."""
    if not re.fullmatch(r"[A-Za-z0-9._/:-]{1,120}", tag or ""):
        return {"status": "error", "error": "invalid model tag"}
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{_ollama_base_url()}/api/pull",
                json={"model": tag, "stream": False},
            )
        if resp.status_code != 200:
            return {"status": "error", "error": f"ollama returned {resp.status_code}"}
        return {"status": "ok", "tag": tag}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


# ── background pull tracking (a pull can take minutes — never block the request) ─
_pull_lock = threading.Lock()
_pull_state: dict[str, str] = {}  # tag -> "pulling" | "ok" | "error: ..."


def _pull_worker(tag: str) -> None:
    res = pull_model(tag)
    with _pull_lock:
        _pull_state[tag] = "ok" if res.get("status") == "ok" else f"error: {res.get('error')}"


def start_pull(tag: str) -> dict[str, object]:
    """Kick an ollama pull in the background and return immediately. Poll
    ``cookbook_snapshot()['pulls']`` (or ``installed`` flags) for progress."""
    if not re.fullmatch(r"[A-Za-z0-9._/:-]{1,120}", tag or ""):
        return {"status": "error", "error": "invalid model tag"}
    with _pull_lock:
        if _pull_state.get(tag) == "pulling":
            return {"status": "already_pulling", "tag": tag}
        _pull_state[tag] = "pulling"
    threading.Thread(
        target=_pull_worker, args=(tag,), name=f"ollama-pull-{tag}", daemon=True
    ).start()
    return {"status": "started", "tag": tag}


def pull_states() -> dict[str, str]:
    with _pull_lock:
        return dict(_pull_state)


def cookbook_snapshot() -> dict[str, object]:
    """Everything the UI needs in one call: hardware + ranked recommendations,
    with installed flags, ollama availability, in-flight pulls, and the catalog
    source. Prefers the live HuggingFace catalog; falls back to the static
    snapshot on a cold cache / offline (serve-stale-while-revalidate)."""
    hw = detect_hardware()
    inst = installed_models()
    specs = None
    source = "static"
    try:
        from runtime.sensing.model_router.hf_catalog import dynamic_catalog

        specs = dynamic_catalog()
        if specs:
            source = "huggingface"
    except Exception:  # noqa: BLE001 — live catalog is strictly best-effort
        specs = None
    recs = recommend(hw, specs or default_catalog(), installed=inst)
    return {
        "hardware": asdict(hw),
        "ollama_available": ollama_available(),
        "recommendations": [asdict(r) for r in recs],
        "pulls": pull_states(),
        "source": source,
    }
