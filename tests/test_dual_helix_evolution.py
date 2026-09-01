from runtime.safety.evolution.dual_helix import build_dual_helix_evidence
from runtime.safety.evolution.proposal_ledger import ProposalRecord, ProposalStatus


def _record(engine: str, kind: str, *, fingerprint: str, ts: str) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=f"{engine}-{kind}-{ts}",
        kind=kind,
        description=kind,
        status=ProposalStatus.PROPOSED,
        proposer="realtime_cerebrum",
        ts=ts,
        model="test-model",
        metadata={
            "engine": engine,
            "goal": "same task",
            "goal_fingerprint": fingerprint,
            "verification_count": 2,
        },
    )


def test_pairs_real_engine_samples_and_scores_decisive_outcomes() -> None:
    report = build_dual_helix_evidence(
        [
            _record("echo", "turn_success", fingerprint="same", ts="2026-01-01T01:00:00"),
            _record("codex", "turn_failure", fingerprint="same", ts="2026-01-01T01:01:00"),
            _record("echo", "turn_success", fingerprint="native-only", ts="2026-01-01T02:00:00"),
        ]
    )

    assert report["schema"] == "echo.dual_helix_evidence.v1"
    assert report["paired_count"] == 1
    assert report["unpaired_count"] == 1
    assert report["echo_wins"] == 1
    assert report["echo_win_rate"] == 1.0
    assert report["pairs"][0]["winner"] == "echo"
    assert report["pairs"][0]["codex"]["outcome"] == "failure"


def test_ignores_legacy_records_without_trusted_engine_metadata() -> None:
    legacy = _record("echo", "turn_success", fingerprint="old", ts="2026-01-01")
    legacy.metadata.pop("engine")
    report = build_dual_helix_evidence([legacy])
    assert report["paired_count"] == 0
    assert report["strands"]["echo"]["samples"] == 0

