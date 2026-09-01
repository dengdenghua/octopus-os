from pathlib import Path

import pytest

from benchmarks.validate_result_manifest import validate_manifest

pytestmark = pytest.mark.slow


def test_echo_latest_k3_manifest_does_not_claim_missing_evidence():
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="manifest is not authoritative: evidence_missing"):
        validate_manifest(root / "benchmarks/echo-k3-latest.json")
