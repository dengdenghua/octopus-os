#!/usr/bin/env python3
"""Verify upstream runs and OIDC provenance for one Echo release candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_GH_OUTPUT = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
SHA1 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RELEASE_TAG = re.compile(r"^echo-appliance-v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
RUN_SPECS = {
    "osImage": {
        "workflow": ".github/workflows/os-image.yml",
        "headBranch": "os-main",
        "sourceRef": "refs/heads/os-main",
        "runnerPolicy": "dedicated-self-hosted",
        "denySelfHosted": False,
    },
    "abUpdate": {
        "workflow": ".github/workflows/ab-update-smoke.yml",
        "headBranch": "os-main",
        "sourceRef": "refs/heads/os-main",
        "runnerPolicy": "dedicated-self-hosted",
        "denySelfHosted": False,
    },
    "realOmvX86": {
        "workflow": ".github/workflows/omv-real-x86.yml",
        "headBranch": "os-main",
        "sourceRef": "refs/heads/os-main",
        "runnerPolicy": "github-hosted-only",
        "denySelfHosted": True,
    },
    "appliance": {
        "workflow": ".github/workflows/appliance-release.yml",
        "headBranch": None,
        "sourceRef": None,
        "runnerPolicy": "github-hosted-only",
        "denySelfHosted": True,
    },
}
ARTIFACT_SPECS = {
    "osImageManifest": ("osImage", "osImageManifest"),
    "osImageSignature": ("osImage", "osImageSignature"),
    "osImageKeyring": ("osImage", "osImageKeyring"),
    "abManifest": ("abUpdate", "abManifest"),
    "abSignature": ("abUpdate", "abSignature"),
    "abKeyring": ("abUpdate", "abKeyring"),
    "omvEvidence": ("realOmvX86", "omvEvidence"),
    "omvVerification": ("realOmvX86", "omvVerification"),
    "omvPlugin": ("realOmvX86", "omvPlugin"),
    "applianceManifest": ("appliance", "applianceManifest"),
}


class CandidatePreflightError(RuntimeError):
    """The selected runs cannot form one trusted release candidate."""


def _run_gh(argv: list[str], timeout: int = 60) -> str:
    environment = os.environ.copy()
    environment.update({"GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"})
    try:
        completed = subprocess.run(  # nosec B603
            ["gh", *argv],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CandidatePreflightError("GitHub verification command is unavailable") from exc
    if (
        completed.returncode != 0
        or not completed.stdout
        or len(completed.stdout.encode("utf-8", "replace")) > MAX_GH_OUTPUT
    ):
        raise CandidatePreflightError("GitHub verification command failed closed")
    return completed.stdout


def _strict_json(raw: str, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CandidatePreflightError(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise CandidatePreflightError(f"{label} is not valid JSON") from exc


def _hash_regular(path: Path, label: str) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CandidatePreflightError(f"{label} is unavailable") from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_ARTIFACT_BYTES
        ):
            raise CandidatePreflightError(f"{label} is empty, oversized or unsafe")
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise CandidatePreflightError(f"{label} ended while hashing")
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise CandidatePreflightError(f"{label} changed while hashing")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _run_metadata(
    repository: str,
    run_id: int,
    *,
    expected_workflow: str,
    expected_sha: str,
    expected_branch: str | None,
    allowed_events: set[str],
    gh_runner: Callable[[list[str], int], str],
) -> dict[str, Any]:
    raw = gh_runner(["api", f"repos/{repository}/actions/runs/{run_id}"], 30)
    value = _strict_json(raw, "workflow run metadata")
    run_repository = value.get("repository") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("id") != run_id
        or value.get("status") != "completed"
        or value.get("conclusion") != "success"
        or value.get("path") != expected_workflow
        or value.get("head_sha") != expected_sha
        or (expected_branch is not None and value.get("head_branch") != expected_branch)
        or value.get("event") not in allowed_events
        or not isinstance(value.get("run_attempt"), int)
        or isinstance(value.get("run_attempt"), bool)
        or value["run_attempt"] < 1
        or not isinstance(run_repository, dict)
        or run_repository.get("full_name") != repository
        or value.get("html_url") != f"https://github.com/{repository}/actions/runs/{run_id}"
    ):
        raise CandidatePreflightError(
            f"run {run_id} is not one successful {expected_workflow} execution"
        )
    head_branch = value.get("head_branch")
    if head_branch is not None and (
        not isinstance(head_branch, str) or not 1 <= len(head_branch) <= 255
    ):
        raise CandidatePreflightError(f"run {run_id} has an invalid head branch")
    return {
        "id": run_id,
        "attempt": value["run_attempt"],
        "workflow": expected_workflow,
        "event": value["event"],
        "headBranch": head_branch,
        "htmlUrl": value["html_url"],
    }


def _resolve_release_tag(
    repository: str,
    release_tag: str,
    *,
    gh_runner: Callable[[list[str], int], str],
) -> str:
    raw = gh_runner(["api", f"repos/{repository}/git/ref/tags/{release_tag}"], 30)
    value = _strict_json(raw, "release tag reference")
    if not isinstance(value, dict):
        raise CandidatePreflightError("release tag reference is invalid")
    obj = value.get("object")
    if value.get("ref") != f"refs/tags/{release_tag}" or not isinstance(obj, dict):
        raise CandidatePreflightError("release tag reference is invalid")
    for _depth in range(5):
        object_type = obj.get("type")
        object_sha = obj.get("sha")
        if not isinstance(object_sha, str) or SHA1.fullmatch(object_sha) is None:
            raise CandidatePreflightError("release tag object has an invalid Git identity")
        if object_type == "commit":
            return object_sha
        if object_type != "tag":
            raise CandidatePreflightError("release tag does not resolve to a commit")
        raw = gh_runner(["api", f"repos/{repository}/git/tags/{object_sha}"], 30)
        tag = _strict_json(raw, "annotated release tag")
        obj = tag.get("object") if isinstance(tag, dict) else None
        if not isinstance(obj, dict):
            raise CandidatePreflightError("annotated release tag is invalid")
    raise CandidatePreflightError("release tag annotation depth exceeds the safe bound")


def inspect_candidate(
    *,
    repository: str,
    source_revision: str,
    release_tag: str,
    run_ids: Mapping[str, int],
    artifacts: Mapping[str, Path],
    gh_runner: Callable[[list[str], int], str] = _run_gh,
) -> dict[str, Any]:
    if REPOSITORY.fullmatch(repository) is None:
        raise CandidatePreflightError("repository must be one owner/name identity")
    if SHA1.fullmatch(source_revision) is None:
        raise CandidatePreflightError("source revision must be one full lowercase Git SHA")
    if RELEASE_TAG.fullmatch(release_tag) is None:
        raise CandidatePreflightError("release tag is invalid")
    if set(run_ids) != set(RUN_SPECS) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in run_ids.values()
    ):
        raise CandidatePreflightError("all four positive workflow run IDs are required")
    if len(set(run_ids.values())) != len(run_ids):
        raise CandidatePreflightError("workflow run IDs must be distinct")
    if set(artifacts) != set(ARTIFACT_SPECS):
        raise CandidatePreflightError("the candidate artifact set is incomplete")

    runs: dict[str, dict[str, Any]] = {}
    for name, spec in RUN_SPECS.items():
        expected_branch = None if name == "appliance" else str(spec["headBranch"])
        runs[name] = _run_metadata(
            repository,
            run_ids[name],
            expected_workflow=str(spec["workflow"]),
            expected_sha=source_revision,
            expected_branch=expected_branch,
            allowed_events={"push"} if name == "appliance" else {"push", "workflow_dispatch"},
            gh_runner=gh_runner,
        )
    tag_revision = _resolve_release_tag(repository, release_tag, gh_runner=gh_runner)
    if tag_revision != source_revision:
        raise CandidatePreflightError("release tag resolves to another OS commit")

    attestations: dict[str, dict[str, Any]] = {}
    for artifact_name, (run_name, _argument_name) in ARTIFACT_SPECS.items():
        path = artifacts[artifact_name]
        sha256 = _hash_regular(path, artifact_name)
        workflow = str(RUN_SPECS[run_name]["workflow"])
        source_ref = (
            f"refs/tags/{release_tag}"
            if run_name == "appliance"
            else str(RUN_SPECS[run_name]["sourceRef"])
        )
        signer_workflow = f"github.com/{repository}/{workflow}"
        verification_argv = [
            "attestation",
            "verify",
            str(path),
            "--repo",
            repository,
            "--signer-workflow",
            signer_workflow,
            "--source-digest",
            source_revision,
            "--source-ref",
            source_ref,
        ]
        if bool(RUN_SPECS[run_name]["denySelfHosted"]):
            verification_argv.append("--deny-self-hosted-runners")
        verification_argv.extend(["--format", "json"])
        output = gh_runner(verification_argv, 90)
        verification = _strict_json(output, f"{artifact_name} attestation result")
        if not isinstance(verification, list) or not 1 <= len(verification) <= 30:
            raise CandidatePreflightError(
                f"{artifact_name} has no bounded verified provenance result"
            )
        attestations[artifact_name] = {
            "sha256": sha256,
            "signerWorkflow": signer_workflow,
            "sourceRef": source_ref,
            "runnerPolicy": RUN_SPECS[run_name]["runnerPolicy"],
            "verificationCount": len(verification),
        }

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.delivery-release-candidate-preflight",
        "ready": True,
        "repository": repository,
        "sourceRevision": source_revision,
        "releaseTag": release_tag,
        "releaseTagRevision": tag_revision,
        "runs": runs,
        "attestations": attestations,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["reportId"] = hashlib.sha256(canonical).hexdigest()
    return payload


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.name in {"", ".", ".."} or path.parent.is_symlink():
        raise CandidatePreflightError("output path is unsafe")
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise CandidatePreflightError("output must be a new path")
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise CandidatePreflightError("output must remain a new path") from exc
        temporary.unlink()
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--os-image-run-id", type=int, required=True)
    parser.add_argument("--ab-update-run-id", type=int, required=True)
    parser.add_argument("--real-omv-x86-run-id", type=int, required=True)
    parser.add_argument("--appliance-run-id", type=int, required=True)
    parser.add_argument("--os-image-manifest", type=Path, required=True)
    parser.add_argument("--os-image-signature", type=Path, required=True)
    parser.add_argument("--os-image-keyring", type=Path, required=True)
    parser.add_argument("--ab-manifest", type=Path, required=True)
    parser.add_argument("--ab-signature", type=Path, required=True)
    parser.add_argument("--ab-keyring", type=Path, required=True)
    parser.add_argument("--omv-evidence", type=Path, required=True)
    parser.add_argument("--omv-verification", type=Path, required=True)
    parser.add_argument("--omv-plugin", type=Path, required=True)
    parser.add_argument("--appliance-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    gh_runner: Callable[[list[str], int], str] = _run_gh,
) -> int:
    args = _parser().parse_args(argv)
    try:
        report = inspect_candidate(
            repository=args.repository,
            source_revision=args.source_revision,
            release_tag=args.release_tag,
            run_ids={
                "osImage": args.os_image_run_id,
                "abUpdate": args.ab_update_run_id,
                "realOmvX86": args.real_omv_x86_run_id,
                "appliance": args.appliance_run_id,
            },
            artifacts={
                "osImageManifest": args.os_image_manifest,
                "osImageSignature": args.os_image_signature,
                "osImageKeyring": args.os_image_keyring,
                "abManifest": args.ab_manifest,
                "abSignature": args.ab_signature,
                "abKeyring": args.ab_keyring,
                "omvEvidence": args.omv_evidence,
                "omvVerification": args.omv_verification,
                "omvPlugin": args.omv_plugin,
                "applianceManifest": args.appliance_manifest,
            },
            gh_runner=gh_runner,
        )
        _write_new(args.output, report)
    except (CandidatePreflightError, OSError) as exc:
        print(f"Echo release candidate preflight failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_RELEASE_CANDIDATE_PREFLIGHT_OK "
        f"os={report['sourceRevision']} tag={report['releaseTag']} "
        f"runs={len(report['runs'])} attestations={len(report['attestations'])} "
        f"report={report['reportId']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
