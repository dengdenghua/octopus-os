from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "appliance" / "release_candidate_preflight.py"
SPEC = importlib.util.spec_from_file_location("echo_release_candidate_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

REPOSITORY = "dengdenghua/echo-os"
SOURCE_REVISION = "1" * 40
RELEASE_TAG = "echo-appliance-v1.2.3"
RUN_IDS = {"osImage": 101, "abUpdate": 102, "realOmvX86": 103, "appliance": 104}


def _artifacts(tmp_path: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for index, name in enumerate(preflight.ARTIFACT_SPECS, start=1):
        path = tmp_path / name
        path.write_bytes(f"artifact-{index}-{name}".encode())
        artifacts[name] = path
    return artifacts


def _run_payload(run_name: str) -> dict[str, Any]:
    run_id = RUN_IDS[run_name]
    spec = preflight.RUN_SPECS[run_name]
    branch = RELEASE_TAG if run_name == "appliance" else "os-main"
    return {
        "id": run_id,
        "status": "completed",
        "conclusion": "success",
        "path": spec["workflow"],
        "head_sha": SOURCE_REVISION,
        "head_branch": branch,
        "event": "push",
        "run_attempt": 1,
        "repository": {"full_name": REPOSITORY},
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
    }


def _github(payloads: dict[str, dict[str, Any]] | None = None) -> Any:
    runs = payloads or {name: _run_payload(name) for name in preflight.RUN_SPECS}

    def runner(argv: list[str], timeout: int) -> str:
        assert 1 <= timeout <= 90
        if argv[0] == "api":
            if "/git/ref/tags/" in argv[1]:
                return json.dumps(
                    {
                        "ref": f"refs/tags/{RELEASE_TAG}",
                        "object": {"type": "commit", "sha": SOURCE_REVISION},
                    }
                )
            run_id = int(argv[1].rsplit("/", 1)[1])
            run_name = next(name for name, value in RUN_IDS.items() if value == run_id)
            return json.dumps(runs[run_name])
        assert argv[:2] == ["attestation", "verify"]
        artifact_name = Path(argv[2]).name
        run_name = preflight.ARTIFACT_SPECS[artifact_name][0]
        assert ("--deny-self-hosted-runners" in argv) is bool(
            preflight.RUN_SPECS[run_name]["denySelfHosted"]
        )
        assert argv[argv.index("--source-digest") + 1] == SOURCE_REVISION
        assert argv[argv.index("--repo") + 1] == REPOSITORY
        return json.dumps([{"verificationResult": {"verifiedTimestamps": [{}]}}])

    return runner


def _inspect(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    values = {
        "repository": REPOSITORY,
        "source_revision": SOURCE_REVISION,
        "release_tag": RELEASE_TAG,
        "run_ids": RUN_IDS,
        "artifacts": _artifacts(tmp_path),
        "gh_runner": _github(),
    }
    values.update(overrides)
    return preflight.inspect_candidate(**values)


def test_binds_four_successful_runs_and_ten_strict_oidc_verifications(tmp_path: Path) -> None:
    report = _inspect(tmp_path)

    assert report["ready"] is True
    assert report["sourceRevision"] == SOURCE_REVISION
    assert report["releaseTag"] == RELEASE_TAG
    assert report["releaseTagRevision"] == SOURCE_REVISION
    assert {record["id"] for record in report["runs"].values()} == set(RUN_IDS.values())
    assert len(report["attestations"]) == 10
    assert all(record["verificationCount"] == 1 for record in report["attestations"].values())
    assert {record["runnerPolicy"] for record in report["attestations"].values()} == {
        "dedicated-self-hosted",
        "github-hosted-only",
    }
    unsigned = dict(report)
    report_id = unsigned.pop("reportId")
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    assert report_id == hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("conclusion", "failure"),
        ("status", "in_progress"),
        ("path", ".github/workflows/ci.yml"),
        ("head_sha", "2" * 40),
        ("head_branch", "main"),
        ("event", "pull_request"),
        ("run_attempt", 0),
    ],
)
def test_rejects_a_run_that_is_not_the_exact_successful_source_run(
    tmp_path: Path, field: str, value: Any
) -> None:
    payloads = {name: _run_payload(name) for name in preflight.RUN_SPECS}
    payloads["osImage"][field] = value

    with pytest.raises(preflight.CandidatePreflightError, match="not one successful"):
        _inspect(tmp_path, gh_runner=_github(payloads))


def test_rejects_duplicate_run_ids_before_network_access(tmp_path: Path) -> None:
    run_ids = dict(RUN_IDS)
    run_ids["abUpdate"] = run_ids["osImage"]

    with pytest.raises(preflight.CandidatePreflightError, match="distinct"):
        _inspect(tmp_path, run_ids=run_ids)


def test_rejects_a_release_tag_that_resolves_to_another_commit(tmp_path: Path) -> None:
    base = _github()

    def github(argv: list[str], timeout: int) -> str:
        if argv[0] == "api" and "/git/ref/tags/" in argv[1]:
            return json.dumps(
                {
                    "ref": f"refs/tags/{RELEASE_TAG}",
                    "object": {"type": "commit", "sha": "f" * 40},
                }
            )
        return base(argv, timeout)

    with pytest.raises(preflight.CandidatePreflightError, match="another OS commit"):
        _inspect(tmp_path, gh_runner=github)


def test_resolves_a_bounded_annotated_release_tag(tmp_path: Path) -> None:
    base = _github()
    tag_object = "a" * 40

    def github(argv: list[str], timeout: int) -> str:
        if argv[0] == "api" and "/git/ref/tags/" in argv[1]:
            return json.dumps(
                {
                    "ref": f"refs/tags/{RELEASE_TAG}",
                    "object": {"type": "tag", "sha": tag_object},
                }
            )
        if argv[0] == "api" and argv[1].endswith(f"/git/tags/{tag_object}"):
            return json.dumps({"object": {"type": "commit", "sha": SOURCE_REVISION}})
        return base(argv, timeout)

    assert _inspect(tmp_path, gh_runner=github)["releaseTagRevision"] == SOURCE_REVISION


def test_rejects_missing_or_symlinked_candidate_artifacts(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    artifacts["omvPlugin"].unlink()
    artifacts["omvPlugin"].symlink_to(artifacts["omvEvidence"])

    with pytest.raises(preflight.CandidatePreflightError, match="unavailable"):
        _inspect(tmp_path, artifacts=artifacts)


def test_rejects_empty_or_unbounded_attestation_verification(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)

    def github(argv: list[str], timeout: int) -> str:
        if argv[0] == "api":
            if "/git/ref/tags/" in argv[1]:
                return _github()(argv, timeout)
            return _github()(argv, timeout)
        return "[]"

    with pytest.raises(preflight.CandidatePreflightError, match="no bounded verified"):
        _inspect(tmp_path, artifacts=artifacts, gh_runner=github)


def test_rejects_duplicate_keys_in_github_run_metadata(tmp_path: Path) -> None:
    def github(argv: list[str], timeout: int) -> str:
        del timeout
        if argv[0] == "api":
            return '{"id":101,"id":101}'
        return "[{}]"

    with pytest.raises(preflight.CandidatePreflightError, match="duplicate JSON key"):
        _inspect(tmp_path, gh_runner=github)


def test_cli_creates_read_only_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifacts = _artifacts(tmp_path)
    output = tmp_path / "candidate-preflight.json"
    argv = [
        "--repository",
        REPOSITORY,
        "--source-revision",
        SOURCE_REVISION,
        "--release-tag",
        RELEASE_TAG,
        "--os-image-run-id",
        str(RUN_IDS["osImage"]),
        "--ab-update-run-id",
        str(RUN_IDS["abUpdate"]),
        "--real-omv-x86-run-id",
        str(RUN_IDS["realOmvX86"]),
        "--appliance-run-id",
        str(RUN_IDS["appliance"]),
    ]
    argument_names = {
        "osImageManifest": "os-image-manifest",
        "osImageSignature": "os-image-signature",
        "osImageKeyring": "os-image-keyring",
        "abManifest": "ab-manifest",
        "abSignature": "ab-signature",
        "abKeyring": "ab-keyring",
        "omvEvidence": "omv-evidence",
        "omvVerification": "omv-verification",
        "omvPlugin": "omv-plugin",
        "applianceManifest": "appliance-manifest",
    }
    for name, argument in argument_names.items():
        argv.extend([f"--{argument}", str(artifacts[name])])
    argv.extend(["--output", str(output)])

    exit_code = preflight.main(argv, gh_runner=_github())

    assert exit_code == 0
    assert json.loads(output.read_text())["ready"] is True
    assert output.stat().st_mode & 0o777 == 0o444
    assert "runs=4 attestations=10" in capsys.readouterr().out


def test_run_payload_fixture_is_independent() -> None:
    payload = _run_payload("osImage")
    cloned = copy.deepcopy(payload)
    cloned["repository"]["full_name"] = "other/repository"
    assert payload["repository"]["full_name"] == REPOSITORY
