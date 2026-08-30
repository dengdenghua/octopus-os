"""Live local-model catalog from the HuggingFace Hub (GGUF), with offline fallback.

The hand-curated catalog in ``hwfit`` goes stale. This pulls the currently
*trending* GGUF repos from the Hub API, reads each one's real per-quant file
size, and turns them into ``ModelSpec``s the existing fit engine ranks — so the
recommendations track the latest popular models instead of a frozen snapshot.

Pullable without any tag mapping: ollama can fetch GGUF repos directly via
``hf.co/<repo>:<quant>``, so the recommendation's ``tag`` is exactly that.

Resilience is the whole point:
- serve-stale-while-revalidate: a cold call returns ``None`` (caller falls back
  to ``hwfit.default_catalog()``) and kicks a background refresh; later calls get
  the live list. Stale cache is served while a refresh runs.
- a disk cache survives restarts; any network / parse failure degrades silently
  to the static catalog. No new dependency (httpx is core).
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from pathlib import Path

from runtime.sensing.model_router.hwfit import ModelSpec

_HF_API = "https://huggingface.co/api"
_CACHE_TTL_S = 6 * 3600.0
_DISK_TTL_S = 24 * 3600.0
_LIST_LIMIT = 50  # raw repos to consider before dedup
_SHORTLIST = 14  # how many to enrich with real file sizes (1 tree call each)
_MIN_DOWNLOADS = 300  # cut long-tail noise

# Preferred quant when a repo ships several (q4_K_M is ollama's default sweet spot).
_QUANT_PREF = ["Q4_K_M", "Q4_K_S", "Q4_0", "Q5_K_M", "Q6_K", "Q8_0", "Q3_K_M", "Q2_K"]
_QUANT_RE = re.compile(r"(IQ?\d(?:_[A-Z0-9]+)*K?(?:_[A-Z])?|Q\d_[A-Z0-9_]+|Q\d_\d|F16|BF16)", re.I)
_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?![a-z])")

# Repos that ship GGUF but aren't chat LLMs (or need a different runner).
_EXCLUDE_RE = re.compile(
    r"embed|rerank|bge[-_]|gte[-_]|e5[-_]|whisper|[-_]tts|[-_]stt|"
    r"clip|stable[-_]?diffusion|flux|[-_]vae|sd[-_]?xl|wan2|musicgen",
    re.I,
)

_FAMILIES = [
    ("qwen", "qwen"),
    ("llama", "llama"),
    ("gemma", "gemma"),
    ("mistral", "mistral"),
    ("mixtral", "mixtral"),
    ("phi", "phi"),
    ("deepseek", "deepseek"),
    ("yi", "yi"),
    ("command-r", "command-r"),
    ("granite", "granite"),
    ("smollm", "smollm"),
    ("falcon", "falcon"),
    ("internlm", "internlm"),
    ("glm", "glm"),
    ("olmo", "olmo"),
]

_lock = threading.Lock()
_cache: dict[str, object] = {"ts": 0.0, "specs": None}
_refreshing = False
_disk_loaded = False


def _disk_path() -> Path:
    return Path.home() / ".echo" / "cookbook_hf_cache.json"


# ── parsing helpers (pure / unit-tested) ──────────────────────────────────────
def _parse_params_b(name: str) -> float:
    """Largest plausible "<n>B" in the repo name → billions (0 if none)."""
    vals = [float(m) for m in _PARAM_RE.findall(name or "")]
    # Drop absurd matches (e.g. a "100B" in a dataset name); keep <= 2000B.
    vals = [v for v in vals if 0.1 <= v <= 2000]
    return max(vals) if vals else 0.0


def _family_of(name: str) -> str:
    low = (name or "").lower()
    for key, fam in _FAMILIES:
        if key in low:
            return fam
    return "other"


def _quant_of(filename: str) -> str | None:
    m = _QUANT_RE.search(filename or "")
    return m.group(1).upper() if m else None


def _base_key(repo: str) -> str:
    """Normalize a repo to a base-model key so 5 quantizers of the same model
    collapse to one row."""
    name = repo.split("/")[-1].lower()
    name = re.sub(r"[-_.]?gguf$", "", name)
    name = _QUANT_RE.sub("", name)
    return re.sub(r"[-_.]+", "-", name).strip("-")


def _label_of(repo: str) -> str:
    name = repo.split("/")[-1]
    name = re.sub(r"[-_.]?GGUF$", "", name, flags=re.I)
    return name.strip("-_. ") or repo


def _pick_quant(quants: dict[str, float]) -> str | None:
    for q in _QUANT_PREF:
        if q in quants:
            return q
    return next(iter(quants), None)


# ── HTTP (best-effort; never raises) ──────────────────────────────────────────
def _get_json(url: str, timeout: float):
    try:
        import httpx

        with httpx.Client(timeout=timeout, headers={"User-Agent": "echo-agent-cookbook"}) as c:
            r = c.get(url)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def _list_gguf_models() -> list[dict]:
    """Most-downloaded GGUF repos. Downloads (not trendingScore) is the quality
    signal for "what should I actually run" — battle-tested + still tracks new
    models as they climb. trendingScore surfaces too many experimental finetunes."""
    data = _get_json(
        f"{_HF_API}/models?filter=gguf&sort=downloads&direction=-1&limit={_LIST_LIMIT}",
        timeout=8.0,
    )
    return data if isinstance(data, list) else []


_SPLIT_RE = re.compile(r"-\d{3,5}-of-\d{3,5}", re.I)


def _repo_quant_sizes(repo: str) -> dict[str, float]:
    """Map quant → main-weight GGUF size (GB) from the repo file tree.

    A repo often holds several .gguf for one quant: split parts
    (``…-00001-of-00003`` → sum), plus small extras like vision projectors. So
    we *sum* split parts but otherwise take the *largest* file per quant, never
    the naive total (which a 1 GB projector would corrupt). Empty on failure."""
    data = _get_json(f"{_HF_API}/models/{repo}/tree/main?recursive=true", timeout=6.0)
    if not isinstance(data, list):
        return {}
    singles: dict[str, float] = {}
    parts: dict[str, float] = {}
    for item in data:
        path = item.get("path", "")
        if not path.lower().endswith(".gguf"):
            continue
        size = item.get("size") or (item.get("lfs") or {}).get("size")
        quant = _quant_of(path)
        if not quant or not isinstance(size, (int, float)) or size <= 0:
            continue
        gb = round(float(size) / (1024**3), 2)
        if _SPLIT_RE.search(path):
            parts[quant] = round(parts.get(quant, 0.0) + gb, 2)
        else:
            singles[quant] = max(singles.get(quant, 0.0), gb)
    return {q: (parts.get(q) or singles.get(q, 0.0)) for q in set(singles) | set(parts)}


# ── build live ModelSpecs ─────────────────────────────────────────────────────
def _build_specs() -> list[ModelSpec]:
    raw = _list_gguf_models()
    if not raw:
        return []
    # Dedup to one repo per base model, keeping the most-downloaded.
    best: dict[str, dict] = {}
    for m in raw:
        repo = m.get("id") or m.get("modelId")
        if not repo or _EXCLUDE_RE.search(repo):
            continue  # skip embedding / rerank / TTS / vision-only GGUF repos
        downloads = int(m.get("downloads") or 0)
        if downloads < _MIN_DOWNLOADS:
            continue
        key = _base_key(repo)
        if key not in best or downloads > int(best[key].get("downloads") or 0):
            best[key] = m
    # Rank by downloads (HF already sorted), cap the shortlist we enrich.
    ordered = sorted(
        best.values(),
        key=lambda m: (m.get("downloads") or 0, m.get("trendingScore") or 0),
        reverse=True,
    )[:_SHORTLIST]

    specs: list[ModelSpec] = []
    for rank, m in enumerate(ordered):
        repo = m.get("id") or m.get("modelId")
        params = _parse_params_b(repo)
        sizes = _repo_quant_sizes(repo)
        quant = _pick_quant(sizes)
        if quant is None:
            continue
        weight_gb: float | None = sizes[quant]
        # Sanity: a real file smaller than even q2 (≈0.33 B/param) is a projector
        # or partial, not the model — distrust it and fall back to the estimate.
        if params and weight_gb and weight_gb < params * 0.30:
            weight_gb = None
        if not weight_gb and not params:
            continue  # no trustworthy way to size this repo → skip
        # arch_rank from trending position (top of the live list = strongest signal).
        arch_rank = max(1, 10 - rank // 2)
        specs.append(
            ModelSpec(
                tag=f"hf.co/{repo}:{quant}",
                label=_label_of(repo),
                params_b=params,
                family=_family_of(repo),
                arch_rank=arch_rank,
                weight_gb=weight_gb,
            )
        )
    return specs


# ── disk cache ────────────────────────────────────────────────────────────────
def _load_disk() -> None:
    global _disk_loaded
    _disk_loaded = True
    with contextlib.suppress(Exception):
        raw = json.loads(_disk_path().read_text(encoding="utf-8"))
        if time.time() - float(raw.get("ts", 0)) < _DISK_TTL_S:
            specs = [ModelSpec(**s) for s in raw.get("specs", [])]
            if specs:
                with _lock:
                    _cache["specs"] = specs
                    _cache["ts"] = float(raw["ts"])


def _write_disk(specs: list[ModelSpec]) -> None:
    with contextlib.suppress(Exception):
        p = _disk_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": time.time(), "specs": [vars(s) for s in specs]}
        p.write_text(json.dumps(payload), encoding="utf-8")


def _refresh_worker() -> None:
    global _refreshing
    try:
        specs = _build_specs()
        if specs:
            with _lock:
                _cache["specs"] = specs
                _cache["ts"] = time.time()
            _write_disk(specs)
    finally:
        with _lock:
            _refreshing = False


def _maybe_refresh() -> None:
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True
    threading.Thread(target=_refresh_worker, name="cookbook-hf-refresh", daemon=True).start()


def dynamic_catalog() -> list[ModelSpec] | None:
    """Live ModelSpecs if cached/fresh, else None (caller falls back to static).

    Serve-stale-while-revalidate: returns whatever is cached immediately and
    kicks a background refresh when the cache is stale; a cold start returns
    None and triggers the first fetch."""
    if not _disk_loaded:
        _load_disk()
    now = time.time()
    with _lock:
        specs = _cache["specs"]
        ts = float(_cache["ts"])
    if specs is not None and (now - ts) < _CACHE_TTL_S:
        return specs  # type: ignore[return-value]
    _maybe_refresh()
    return specs  # type: ignore[return-value]
