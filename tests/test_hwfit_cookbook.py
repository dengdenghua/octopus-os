"""Local-model cookbook fit math — pure-logic coverage (no hardware / ollama)."""

from __future__ import annotations

from runtime.sensing.model_router import hwfit


def _hw(backend="cuda", vram=24.0, ram=32.0, bw=1008.0):
    return hwfit.Hardware(
        backend=backend,
        gpu_name="RTX 4090",
        vram_gb=vram,
        ram_gb=ram,
        bandwidth_gbps=bw,
        unified_memory=False,
    )


def test_estimate_mem_scales_with_quant() -> None:
    # f16 is heavier than q4_K_M for the same model.
    assert hwfit.estimate_mem_gb(7.0, "f16") > hwfit.estimate_mem_gb(7.0, "q4_K_M")
    # ~7B at q4 lands in a believable 4–6 GB band (weights + overhead).
    mem = hwfit.estimate_mem_gb(7.0, "q4_K_M")
    assert 4.0 < mem < 6.0


def test_tps_is_bandwidth_over_active_bytes() -> None:
    # Roofline: more bandwidth → faster; bigger model → slower.
    fast = hwfit.estimate_tps(7.0, "q4_K_M", 1000.0)
    slow = hwfit.estimate_tps(70.0, "q4_K_M", 1000.0)
    assert fast and slow and fast > slow
    # Unknown bandwidth (CPU / unlisted GPU) → no estimate.
    assert hwfit.estimate_tps(7.0, "q4_K_M", None) is None


def test_verdict_thresholds() -> None:
    assert hwfit._verdict(8.0, 24.0) == "fits"
    assert hwfit._verdict(22.0, 24.0) == "tight"
    assert hwfit._verdict(30.0, 24.0) == "offload"
    assert hwfit._verdict(60.0, 24.0) == "too_big"


def test_recommend_excludes_too_big_and_ranks_fitting() -> None:
    recs = hwfit.recommend(_hw(vram=8.0), top_k=20)
    tags = [r.tag for r in recs]
    # A 72B model can't fit 8 GB → excluded entirely.
    assert "qwen2.5:72b" not in tags
    # Small models fit and are returned.
    assert any(r.params_b <= 8 for r in recs)
    # Sorted by score, descending.
    assert [r.score for r in recs] == sorted((r.score for r in recs), reverse=True)
    # Every returned model is runnable (not too_big).
    assert all(r.verdict != "too_big" for r in recs)


def test_recommend_marks_installed() -> None:
    recs = hwfit.recommend(_hw(), installed={"qwen2.5:7b"}, top_k=30)
    by_tag = {r.tag: r for r in recs}
    assert by_tag["qwen2.5:7b"].installed is True
    assert by_tag["llama3.1:8b"].installed is False


def test_big_gpu_unlocks_larger_models() -> None:
    small = {r.tag for r in hwfit.recommend(_hw(vram=8.0), top_k=30)}
    big = {r.tag for r in hwfit.recommend(_hw(vram=48.0), top_k=30)}
    # A 48 GB card should admit at least one model the 8 GB card can't.
    assert big - small


def test_bandwidth_lookup_matches_known_chips() -> None:
    assert hwfit._match_bandwidth("Apple M3 Max", hwfit._APPLE_BW) == 400
    assert hwfit._match_bandwidth("NVIDIA GeForce RTX 4090", hwfit._NVIDIA_BW) == 1008
    assert hwfit._match_bandwidth("Totally Unknown GPU", hwfit._NVIDIA_BW) is None


def test_start_pull_rejects_bad_tags() -> None:
    assert hwfit.start_pull("bad tag; rm -rf /")["status"] == "error"
    assert hwfit.start_pull("")["status"] == "error"

