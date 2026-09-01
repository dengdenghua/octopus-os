#!/usr/bin/env python3
"""Bind independently verified Echo delivery evidence to one source identity."""

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
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_PLUGIN_BYTES = 32 * 1024 * 1024
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OPERATIONS_ARTIFACT_ID = re.compile(r"^[0-9a-f]{16}$")
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
SIGNATURE_OK = re.compile(
    r"^ECHO_OS_IMAGE_EVIDENCE_SIGNATURE_OK "
    r"manifest=(?P<manifest>[0-9a-f]{64}) "
    r"signature=(?P<signature>[0-9a-f]{64}) "
    r"keyring=(?P<keyring>[0-9a-f]{64})$"
)
PHYSICAL_GATES = (
    "physical_x86_64_install_and_cold_boot",
    "supported_arm64_hardware_install_and_cold_boot",
    "real_disk_smart_and_raid_degradation_recovery",
    "external_smb_and_nfs_client_interoperability",
    "power_loss_during_update_and_state_restore",
    "recovery_media_bare_metal_restore",
)
REQUIRED_WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/os-image.yml",
    ".github/workflows/ab-update-smoke.yml",
    ".github/workflows/desktop-session-smoke.yml",
    ".github/workflows/omv-real-x86.yml",
    ".github/workflows/appliance-release.yml",
    ".github/workflows/delivery-release-candidate.yml",
)
PREFLIGHT_CHECKS = {
    "repository_layout",
    "git_repository",
    "delivery_branch",
    "source_revision",
    "worktree_clean",
    "required_workflows_tracked",
    "tracking_ref",
    "cached_os_remote",
    "os_origin_identity",
    "embedded_agent_source",
    "github_auth",
    "online_os_remote",
    "online_embedded_agent",
}
CANDIDATE_RUNS = {
    "osImage": ".github/workflows/os-image.yml",
    "abUpdate": ".github/workflows/ab-update-smoke.yml",
    "realOmvX86": ".github/workflows/omv-real-x86.yml",
    "appliance": ".github/workflows/appliance-release.yml",
}
CANDIDATE_RUNNER_POLICIES = {
    "osImage": "dedicated-self-hosted",
    "abUpdate": "dedicated-self-hosted",
    "realOmvX86": "github-hosted-only",
    "appliance": "github-hosted-only",
}
CANDIDATE_ATTESTATIONS = {
    "osImageManifest": ("osImage", "refs/heads/os-main"),
    "osImageSignature": ("osImage", "refs/heads/os-main"),
    "osImageKeyring": ("osImage", "refs/heads/os-main"),
    "abManifest": ("abUpdate", "refs/heads/os-main"),
    "abSignature": ("abUpdate", "refs/heads/os-main"),
    "abKeyring": ("abUpdate", "refs/heads/os-main"),
    "omvEvidence": ("realOmvX86", "refs/heads/os-main"),
    "omvVerification": ("realOmvX86", "refs/heads/os-main"),
    "omvPlugin": ("realOmvX86", "refs/heads/os-main"),
    "applianceManifest": ("appliance", None),
}


class ReleaseEvidenceError(RuntimeError):
    """The supplied evidence cannot describe one release candidate."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseEvidenceError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
            raise ReleaseEvidenceError(f"{label} is empty, oversized or unsafe")
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise ReleaseEvidenceError(f"{label} changed while reading")
        if len(data) > maximum:
            raise ReleaseEvidenceError(f"{label} exceeds its size bound")
        return bytes(data)
    finally:
        os.close(descriptor)


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, MAX_JSON_BYTES, label)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseEvidenceError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseEvidenceError(f"{label} must be a JSON object")
    return value, raw


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReleaseEvidenceError(f"{label} has an unexpected schema")
    return value


def _github_repository(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseEvidenceError("source repository is not a credential-free GitHub URL")
    repository = parsed.path.strip("/")
    if repository.endswith(".git"):
        repository = repository[:-4]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ReleaseEvidenceError("source repository identity is invalid")
    return repository


def _canonical_evidence_id(value: Mapping[str, Any], label: str) -> str:
    evidence_id = value.get("evidence_id")
    if not isinstance(evidence_id, str) or SHA256.fullmatch(evidence_id) is None:
        raise ReleaseEvidenceError(f"{label} has an invalid evidence ID")
    unsigned = dict(value)
    del unsigned["evidence_id"]
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    if _sha256(canonical) != evidence_id:
        raise ReleaseEvidenceError(f"{label} evidence ID does not match its contents")
    return evidence_id


def _source_identity(value: object, label: str, *, full_manifest: bool = False) -> dict[str, Any]:
    identity_keys = {
        "repository",
        "commit",
        "tree",
        "commit_time",
        "source_date_epoch",
        "manifest_sha256",
    }
    if full_manifest:
        identity_keys |= {"schema", "kind", "dirty"}
    source = _exact(
        value,
        identity_keys,
        label,
    )
    if (
        not isinstance(source["repository"], str)
        or SHA1.fullmatch(str(source["commit"])) is None
        or SHA1.fullmatch(str(source["tree"])) is None
        or SHA256.fullmatch(str(source["manifest_sha256"])) is None
        or not isinstance(source["commit_time"], str)
        or not isinstance(source["source_date_epoch"], int)
        or isinstance(source["source_date_epoch"], bool)
        or (
            full_manifest
            and (
                source["schema"] != 1
                or source["kind"] != "echo-os-source-identity"
                or source["dirty"] is not False
            )
        )
    ):
        raise ReleaseEvidenceError(f"{label} fields are invalid")
    _github_repository(source["repository"])
    return {key: source[key] for key in identity_keys - {"schema", "kind", "dirty"}}


def _validate_preflight(value: Mapping[str, Any]) -> tuple[str, str, dict[str, str]]:
    expected = {
        "schemaVersion",
        "kind",
        "mode",
        "ready",
        "expectedBranch",
        "branch",
        "sourceRevision",
        "osRepository",
        "agentSource",
        "requiredWorkflows",
        "checks",
        "blockers",
    }
    _exact(value, expected, "delivery source preflight")
    revision = value["sourceRevision"]
    repository = value["osRepository"]
    agent = _exact(value["agentSource"], {"repository", "commit"}, "preflight Agent")
    checks = value["checks"]
    if (
        value["schemaVersion"] != 1
        or value["kind"] != "echo.delivery-source-preflight"
        or value["mode"] != "online"
        or value["ready"] is not True
        or value["expectedBranch"] != "os-main"
        or value["branch"] != "os-main"
        or not isinstance(revision, str)
        or SHA1.fullmatch(revision) is None
        or not isinstance(repository, str)
        or value["requiredWorkflows"] != list(REQUIRED_WORKFLOWS)
        or not isinstance(checks, list)
        or not checks
        or value["blockers"] != []
        or not isinstance(agent["repository"], str)
        or not isinstance(agent["commit"], str)
        or SHA1.fullmatch(agent["commit"]) is None
    ):
        raise ReleaseEvidenceError("delivery source preflight is not online-ready")
    codes: set[str] = set()
    for check in checks:
        item = _exact(check, {"code", "status", "detail"}, "preflight check")
        if (
            not isinstance(item["code"], str)
            or not item["code"]
            or item["code"] in codes
            or item["status"] != "passed"
            or not isinstance(item["detail"], str)
            or not item["detail"]
        ):
            raise ReleaseEvidenceError("delivery source preflight contains an invalid check")
        codes.add(item["code"])
    if codes != PREFLIGHT_CHECKS:
        raise ReleaseEvidenceError("delivery source preflight check set is incomplete")
    canonical_repository = _github_repository(repository)
    canonical_agent_repository = _github_repository(agent["repository"])
    if agent["commit"] != revision or canonical_agent_repository != canonical_repository:
        raise ReleaseEvidenceError("embedded Agent source differs from the Echo OS source")
    return (
        revision,
        canonical_repository,
        {
            "repository": canonical_agent_repository,
            "commit": agent["commit"],
        },
    )


def _validate_candidate_preflight(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact(
        value,
        {
            "schemaVersion",
            "kind",
            "ready",
            "repository",
            "sourceRevision",
            "releaseTag",
            "releaseTagRevision",
            "runs",
            "attestations",
            "reportId",
        },
        "release candidate preflight",
    )
    repository = value["repository"]
    revision = value["sourceRevision"]
    release_tag = value["releaseTag"]
    if (
        value["schemaVersion"] != 1
        or value["kind"] != "echo.delivery-release-candidate-preflight"
        or value["ready"] is not True
        or not isinstance(repository, str)
        or _github_repository(repository) != repository
        or not isinstance(revision, str)
        or SHA1.fullmatch(revision) is None
        or not isinstance(release_tag, str)
        or re.fullmatch(
            r"echo-appliance-v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?",
            release_tag,
        )
        is None
        or value["releaseTagRevision"] != revision
    ):
        raise ReleaseEvidenceError("release candidate preflight identity is invalid")
    runs = _exact(value["runs"], set(CANDIDATE_RUNS), "candidate workflow runs")
    run_ids: set[int] = set()
    for name, workflow in CANDIDATE_RUNS.items():
        run = _exact(
            runs[name],
            {"id", "attempt", "workflow", "event", "headBranch", "htmlUrl"},
            f"candidate {name} run",
        )
        head_branch = run["headBranch"]
        expected_branch = None if name == "appliance" else "os-main"
        if expected_branch is None:
            branch_valid = head_branch is None or (
                isinstance(head_branch, str) and 1 <= len(head_branch) <= 255
            )
        else:
            branch_valid = head_branch == expected_branch
        event_valid = (
            run["event"] == "push"
            if name == "appliance"
            else run["event"]
            in {
                "push",
                "workflow_dispatch",
            }
        )
        if (
            not isinstance(run["id"], int)
            or isinstance(run["id"], bool)
            or run["id"] < 1
            or run["id"] in run_ids
            or not isinstance(run["attempt"], int)
            or isinstance(run["attempt"], bool)
            or run["attempt"] < 1
            or run["workflow"] != workflow
            or not event_valid
            or not branch_valid
            or run["htmlUrl"] != f"https://github.com/{repository}/actions/runs/{run['id']}"
        ):
            raise ReleaseEvidenceError(f"candidate {name} run identity is invalid")
        run_ids.add(run["id"])
    attestations = _exact(
        value["attestations"], set(CANDIDATE_ATTESTATIONS), "candidate attestations"
    )
    for name, (run_name, branch_ref) in CANDIDATE_ATTESTATIONS.items():
        record = _exact(
            attestations[name],
            {
                "sha256",
                "signerWorkflow",
                "sourceRef",
                "runnerPolicy",
                "verificationCount",
            },
            f"candidate {name} attestation",
        )
        expected_ref = f"refs/tags/{release_tag}" if branch_ref is None else branch_ref
        expected_signer = f"github.com/{repository}/{CANDIDATE_RUNS[run_name]}"
        if (
            not isinstance(record["sha256"], str)
            or SHA256.fullmatch(record["sha256"]) is None
            or record["signerWorkflow"] != expected_signer
            or record["sourceRef"] != expected_ref
            or record["runnerPolicy"] != CANDIDATE_RUNNER_POLICIES[run_name]
            or not isinstance(record["verificationCount"], int)
            or isinstance(record["verificationCount"], bool)
            or not 1 <= record["verificationCount"] <= 30
        ):
            raise ReleaseEvidenceError(f"candidate {name} attestation is invalid")
    report_id = value["reportId"]
    unsigned = dict(value)
    del unsigned["reportId"]
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    if not isinstance(report_id, str) or report_id != _sha256(canonical):
        raise ReleaseEvidenceError("release candidate preflight report ID is invalid")
    return {
        "repository": repository,
        "sourceRevision": revision,
        "releaseTag": release_tag,
        "releaseTagRevision": value["releaseTagRevision"],
        "runs": runs,
        "attestations": attestations,
        "reportId": report_id,
    }


def _validate_raw(value: Mapping[str, Any]) -> tuple[dict[str, Any], str, str, str]:
    _exact(
        value,
        {
            "schema",
            "image_id",
            "image_version",
            "architecture",
            "os_source",
            "agent_source_id",
            "agent_manifest_sha256",
            "install_bundle",
            "release_trust",
            "installed_image",
            "checks",
            "evidence_id",
        },
        "OS image evidence",
    )
    source = _source_identity(value["os_source"], "OS image source")
    checks = value["checks"]
    agent = value["agent_source_id"]
    agent_manifest = value["agent_manifest_sha256"]
    if (
        value["schema"] != 1
        or value["image_id"] != "echo-os"
        or value["architecture"] != "x86-64"
        or not isinstance(value["image_version"], str)
        or VERSION.fullmatch(value["image_version"]) is None
        or not isinstance(agent, str)
        or SHA1.fullmatch(agent) is None
        or not isinstance(agent_manifest, str)
        or SHA256.fullmatch(agent_manifest) is None
        or not isinstance(checks, dict)
        or len(checks) < 15
    ):
        raise ReleaseEvidenceError("OS image evidence release identity is invalid")
    return source, agent, agent_manifest, _canonical_evidence_id(value, "OS image")


def _validate_ab(value: Mapping[str, Any]) -> tuple[dict[str, Any], str, str, str]:
    _exact(
        value,
        {
            "schema",
            "evidence_kind",
            "architecture",
            "base_version",
            "update_version",
            "os_source",
            "agent",
            "base_image",
            "runner_preflight",
            "operations_systemd_verification",
            "update_bundle",
            "update_keyring",
            "checks",
            "evidence_id",
        },
        "A/B evidence",
    )
    source = _source_identity(value["os_source"], "A/B source", full_manifest=True)
    agent = _exact(value["agent"], {"source_id", "manifest_sha256"}, "A/B Agent")
    runner_preflight = _exact(value["runner_preflight"], {"sha256", "size"}, "A/B runner preflight")
    systemd = _exact(
        value["operations_systemd_verification"],
        {
            "schemaVersion",
            "kind",
            "sourceRevision",
            "os",
            "systemdVersion",
            "units",
            "verified",
            "reportSha256",
        },
        "A/B operations systemd verification",
    )
    systemd_os = _exact(systemd["os"], {"id", "versionId", "codename"}, "A/B systemd OS")
    expected_units = {
        "echo-state-backup.service",
        "echo-state-backup.timer",
        "echo-audit-evidence.service",
        "echo-audit-evidence.timer",
    }
    systemd_units = _exact(systemd["units"], expected_units, "A/B systemd units")
    unit_digests_valid = all(
        isinstance(record, dict)
        and set(record) == {"sha256"}
        and isinstance(record["sha256"], str)
        and SHA256.fullmatch(record["sha256"]) is not None
        for record in systemd_units.values()
    )
    if (
        value["schema"] != 3
        or value["evidence_kind"] != "echo-os-ab-update"
        or value["architecture"] != "x86-64"
        or not isinstance(value["base_version"], str)
        or VERSION.fullmatch(value["base_version"]) is None
        or not isinstance(value["update_version"], str)
        or VERSION.fullmatch(value["update_version"]) is None
        or value["base_version"] == value["update_version"]
        or SHA1.fullmatch(str(agent["source_id"])) is None
        or SHA256.fullmatch(str(agent["manifest_sha256"])) is None
        or SHA256.fullmatch(str(runner_preflight["sha256"])) is None
        or not isinstance(runner_preflight["size"], int)
        or isinstance(runner_preflight["size"], bool)
        or runner_preflight["size"] < 1
        or not isinstance(systemd["schemaVersion"], int)
        or isinstance(systemd["schemaVersion"], bool)
        or systemd["schemaVersion"] != 1
        or systemd["kind"] != "echo.operations-systemd-native-verification"
        or systemd["sourceRevision"] != source["commit"]
        or systemd_os != {"id": "debian", "versionId": "13", "codename": "trixie"}
        or not isinstance(systemd["systemdVersion"], str)
        or not systemd["systemdVersion"].startswith("systemd ")
        or len(systemd["systemdVersion"]) > 256
        or systemd["verified"] is not True
        or not isinstance(systemd["reportSha256"], str)
        or SHA256.fullmatch(systemd["reportSha256"]) is None
        or not unit_digests_valid
        or not isinstance(value["checks"], dict)
        or not value["checks"]
    ):
        raise ReleaseEvidenceError("A/B evidence release identity is invalid")
    return (
        source,
        agent["source_id"],
        agent["manifest_sha256"],
        _canonical_evidence_id(value, "A/B"),
    )


def _validate_omv(
    value: Mapping[str, Any], evidence_raw: bytes, plugin_raw: bytes
) -> tuple[str, str, str]:
    expected = {
        "verified",
        "schemaVersion",
        "sourceRevision",
        "omvVersion",
        "pluginVersion",
        "pluginSha256",
        "evidenceSha256",
        "netplanProbeResult",
        "nfsShareUuid",
        "nfsClientCidr",
        "accountGroupName",
        "accountUserName",
        "accountSmbShareName",
        "accountSmbProtocol",
        "warningCount",
    }
    _exact(value, expected, "OMV verification")
    if (
        value["verified"] is not True
        or value["schemaVersion"] != 6
        or SHA1.fullmatch(str(value["sourceRevision"])) is None
        or value["pluginSha256"] != _sha256(plugin_raw)
        or value["evidenceSha256"] != _sha256(evidence_raw)
        or not isinstance(value["warningCount"], int)
        or isinstance(value["warningCount"], bool)
        or value["warningCount"] < 0
    ):
        raise ReleaseEvidenceError("OMV verification does not bind its artifact bytes")
    return value["sourceRevision"], value["pluginSha256"], value["evidenceSha256"]


def _validate_appliance(
    value: Mapping[str, Any],
) -> tuple[str, str, str, str, str, dict[str, str]]:
    expected = {
        "schemaVersion",
        "kind",
        "createdAt",
        "release",
        "source",
        "agentSource",
        "image",
        "stateSchema",
        "attestations",
        "sboms",
        "pythonDependencies",
        "operationsBundle",
        "upgrade",
        "recovery",
    }
    _exact(value, expected, "appliance release manifest")
    release = _exact(value["release"], {"tag", "version"}, "appliance release")
    source = _exact(value["source"], {"repository", "ref", "commit"}, "appliance source")
    agent = _exact(value["agentSource"], {"repository", "commit"}, "appliance Agent")
    image = _exact(
        value["image"],
        {"name", "indexDigest", "indexFile", "immutableReference", "platformDigests"},
        "appliance image",
    )
    attestations = _exact(
        value["attestations"],
        {
            "buildkitProvenanceMode",
            "buildkitSbom",
            "registryAttestationManifestCount",
            "githubOidcProvenanceRequired",
        },
        "appliance attestations",
    )
    operations = _exact(
        value["operationsBundle"],
        {
            "artifactId",
            "archive",
            "sha256",
            "fileCount",
            "architectures",
            "imageReference",
            "checksum",
            "spdx",
            "verifier",
            "verifyCommand",
            "extractCommand",
            "installCommand",
        },
        "appliance operations bundle",
    )
    if (
        value["schemaVersion"] != 1
        or value["kind"] != "echo-appliance-container-release"
        or not isinstance(release["tag"], str)
        or not isinstance(release["version"], str)
        or release["tag"] != f"echo-appliance-v{release['version']}"
        or source["ref"] != f"refs/tags/{release['tag']}"
        or SHA1.fullmatch(str(source["commit"])) is None
        or SHA1.fullmatch(str(agent["commit"])) is None
        or not isinstance(source["repository"], str)
        or not isinstance(agent["repository"], str)
        or not isinstance(image["indexDigest"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image["indexDigest"]) is None
        or image["immutableReference"] != f"{image['name']}@{image['indexDigest']}"
        or attestations["buildkitProvenanceMode"] != "max"
        or attestations["buildkitSbom"] is not True
        or attestations["githubOidcProvenanceRequired"] is not True
        or not isinstance(attestations["registryAttestationManifestCount"], int)
        or attestations["registryAttestationManifestCount"] < 1
        or not isinstance(operations["artifactId"], str)
        or OPERATIONS_ARTIFACT_ID.fullmatch(operations["artifactId"]) is None
        or not isinstance(operations["sha256"], str)
        or SHA256.fullmatch(operations["sha256"]) is None
        or operations["imageReference"] != image["immutableReference"]
    ):
        raise ReleaseEvidenceError("appliance release identity is invalid")
    canonical_source_repository = _github_repository(source["repository"])
    canonical_agent_repository = _github_repository(agent["repository"])
    if (
        agent["commit"] != source["commit"]
        or canonical_agent_repository != canonical_source_repository
    ):
        raise ReleaseEvidenceError("appliance embedded Agent source differs from Echo OS")
    return (
        source["commit"],
        canonical_source_repository,
        agent["commit"],
        canonical_agent_repository,
        image["immutableReference"],
        {
            "artifactId": operations["artifactId"],
            "sha256": operations["sha256"],
            "imageReference": operations["imageReference"],
        },
    )


def _signature_verifier_path() -> Path:
    candidates = (
        Path(__file__).resolve().parents[2] / "packaging/image/verify-os-image-evidence-release.sh",
        Path(__file__).resolve().with_name("verify-os-image-evidence-release.sh"),
    )
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink() and os.access(candidate, os.X_OK):
            return candidate
    raise ReleaseEvidenceError("released evidence signature verifier is unavailable or unsafe")


def _verify_signature(manifest: Path, signature: Path, keyring: Path) -> dict[str, str]:
    manifest_raw = _read_regular(manifest, MAX_JSON_BYTES, "signed evidence manifest")
    signature_raw = _read_regular(signature, MAX_SIGNATURE_BYTES, "evidence signature")
    keyring_raw = _read_regular(keyring, MAX_SIGNATURE_BYTES, "evidence public keyring")
    verifier = _signature_verifier_path()
    try:
        completed = subprocess.run(  # nosec B603
            [str(verifier), str(manifest), str(signature), str(keyring)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseEvidenceError("released evidence signature verifier failed") from exc
    if completed.returncode != 0 or len(completed.stdout) > 8192:
        raise ReleaseEvidenceError("released evidence signature is invalid")
    matches = [SIGNATURE_OK.fullmatch(line) for line in completed.stdout.splitlines()]
    valid = [match for match in matches if match is not None]
    if len(valid) != 1:
        raise ReleaseEvidenceError("signature verifier did not emit one bounded result")
    result = valid[0].groupdict()
    expected = {
        "manifest": _sha256(manifest_raw),
        "signature": _sha256(signature_raw),
        "keyring": _sha256(keyring_raw),
    }
    if result != expected:
        raise ReleaseEvidenceError("signature verification result does not bind the inputs")
    return {
        "manifestSha256": expected["manifest"],
        "signatureSha256": expected["signature"],
        "keyringSha256": expected["keyring"],
    }


def _validate_signature_record(value: Mapping[str, str], label: str) -> None:
    _exact(
        value,
        {"manifestSha256", "signatureSha256", "keyringSha256"},
        f"{label} signature result",
    )
    if any(
        not isinstance(digest, str) or SHA256.fullmatch(digest) is None for digest in value.values()
    ):
        raise ReleaseEvidenceError(f"{label} signature result contains an invalid digest")


def build_index(
    *,
    preflight: tuple[Mapping[str, Any], bytes],
    candidate_preflight: tuple[Mapping[str, Any], bytes],
    raw_evidence: tuple[Mapping[str, Any], bytes],
    raw_signature: Mapping[str, str],
    ab_evidence: tuple[Mapping[str, Any], bytes],
    ab_signature: Mapping[str, str],
    omv_verification: tuple[Mapping[str, Any], bytes],
    omv_evidence_raw: bytes,
    omv_plugin_raw: bytes,
    appliance: tuple[Mapping[str, Any], bytes],
) -> dict[str, Any]:
    preflight_value, preflight_raw = preflight
    candidate_value, candidate_raw = candidate_preflight
    raw_value, raw_bytes = raw_evidence
    ab_value, ab_bytes = ab_evidence
    omv_value, omv_report_raw = omv_verification
    appliance_value, appliance_raw = appliance

    _validate_signature_record(raw_signature, "OS image")
    _validate_signature_record(ab_signature, "A/B")
    revision, repository, locked_agent = _validate_preflight(preflight_value)
    candidate = _validate_candidate_preflight(candidate_value)
    raw_source, raw_agent, raw_agent_manifest, raw_id = _validate_raw(raw_value)
    ab_source, ab_agent, ab_agent_manifest, ab_id = _validate_ab(ab_value)
    omv_revision, plugin_sha, omv_evidence_sha = _validate_omv(
        omv_value, omv_evidence_raw, omv_plugin_raw
    )
    (
        appliance_revision,
        appliance_repository,
        appliance_agent,
        appliance_agent_repo,
        image,
        operations_bundle,
    ) = _validate_appliance(appliance_value)
    if raw_signature.get("manifestSha256") != _sha256(raw_bytes):
        raise ReleaseEvidenceError("OS image signature result belongs to another manifest")
    if ab_signature.get("manifestSha256") != _sha256(ab_bytes):
        raise ReleaseEvidenceError("A/B signature result belongs to another manifest")
    revisions = {
        revision,
        str(raw_source["commit"]),
        str(ab_source["commit"]),
        omv_revision,
        appliance_revision,
    }
    if len(revisions) != 1:
        raise ReleaseEvidenceError("delivery evidence contains different OS commits")
    if raw_source != ab_source:
        raise ReleaseEvidenceError("raw-image and A/B OS source manifests differ")
    if {
        repository,
        _github_repository(str(raw_source["repository"])),
        appliance_repository,
    } != {repository}:
        raise ReleaseEvidenceError("delivery evidence contains different OS repositories")
    if {locked_agent["commit"], raw_agent, ab_agent, appliance_agent} != {locked_agent["commit"]}:
        raise ReleaseEvidenceError("delivery evidence contains different Agent commits")
    if {locked_agent["repository"], appliance_agent_repo} != {locked_agent["repository"]}:
        raise ReleaseEvidenceError("delivery evidence contains different Agent repositories")
    if raw_agent_manifest != ab_agent_manifest:
        raise ReleaseEvidenceError("raw-image and A/B Agent bundle manifests differ")
    if raw_value["image_version"] != ab_value["base_version"]:
        raise ReleaseEvidenceError("A/B base version differs from the installed raw image")
    if (
        candidate["repository"] != repository
        or candidate["sourceRevision"] != revision
        or candidate["releaseTag"] != appliance_value["release"]["tag"]
    ):
        raise ReleaseEvidenceError("candidate preflight belongs to another release identity")
    attested_digests = {
        "osImageManifest": _sha256(raw_bytes),
        "osImageSignature": raw_signature["signatureSha256"],
        "osImageKeyring": raw_signature["keyringSha256"],
        "abManifest": _sha256(ab_bytes),
        "abSignature": ab_signature["signatureSha256"],
        "abKeyring": ab_signature["keyringSha256"],
        "omvEvidence": omv_evidence_sha,
        "omvVerification": _sha256(omv_report_raw),
        "omvPlugin": plugin_sha,
        "applianceManifest": _sha256(appliance_raw),
    }
    for name, digest in attested_digests.items():
        if candidate["attestations"][name]["sha256"] != digest:
            raise ReleaseEvidenceError(f"candidate provenance belongs to another {name} artifact")

    evidence = {
        "sourcePreflight": {"sha256": _sha256(preflight_raw)},
        "candidatePreflight": {
            "sha256": _sha256(candidate_raw),
            "reportId": candidate["reportId"],
            "runnerPolicies": dict(CANDIDATE_RUNNER_POLICIES),
            "runs": {
                name: {"id": run["id"], "attempt": run["attempt"]}
                for name, run in candidate["runs"].items()
            },
        },
        "osImage": {
            "manifestSha256": _sha256(raw_bytes),
            "signatureSha256": raw_signature["signatureSha256"],
            "keyringSha256": raw_signature["keyringSha256"],
            "evidenceId": raw_id,
            "version": raw_value["image_version"],
        },
        "abUpdate": {
            "manifestSha256": _sha256(ab_bytes),
            "signatureSha256": ab_signature["signatureSha256"],
            "keyringSha256": ab_signature["keyringSha256"],
            "evidenceId": ab_id,
            "baseVersion": ab_value["base_version"],
            "updateVersion": ab_value["update_version"],
        },
        "realOmvX86": {
            "verificationSha256": _sha256(omv_report_raw),
            "evidenceSha256": omv_evidence_sha,
            "pluginSha256": plugin_sha,
        },
        "appliance": {
            "manifestSha256": _sha256(appliance_raw),
            "immutableReference": image,
            "operationsBundle": operations_bundle,
        },
    }
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.delivery-release-evidence-index",
        "source": {
            "repository": repository,
            "commit": revision,
            "agentRepository": locked_agent["repository"],
            "agentCommit": locked_agent["commit"],
            "releaseTag": candidate["releaseTag"],
        },
        "evidence": evidence,
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {
            "complete": False,
            "remainingGates": list(PHYSICAL_GATES),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["indexId"] = _sha256(canonical)
    return payload


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.name in {"", ".", ".."} or path.parent.is_symlink():
        raise ReleaseEvidenceError("output path is unsafe")
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise ReleaseEvidenceError("output must be a new path")
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
            raise ReleaseEvidenceError("output must remain a new path") from exc
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
    parser.add_argument("--source-preflight", type=Path, required=True)
    parser.add_argument("--candidate-preflight", type=Path, required=True)
    parser.add_argument("--os-image-evidence", type=Path, required=True)
    parser.add_argument("--os-image-signature", type=Path, required=True)
    parser.add_argument("--os-image-keyring", type=Path, required=True)
    parser.add_argument("--ab-evidence", type=Path, required=True)
    parser.add_argument("--ab-signature", type=Path, required=True)
    parser.add_argument("--ab-keyring", type=Path, required=True)
    parser.add_argument("--omv-verification", type=Path, required=True)
    parser.add_argument("--omv-evidence", type=Path, required=True)
    parser.add_argument("--omv-plugin-package", type=Path, required=True)
    parser.add_argument("--appliance-release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    signature_verifier: Callable[[Path, Path, Path], dict[str, str]] = _verify_signature,
) -> int:
    args = _parser().parse_args(argv)
    try:
        preflight = _load_json(args.source_preflight, "source preflight")
        candidate_preflight = _load_json(args.candidate_preflight, "release candidate preflight")
        raw = _load_json(args.os_image_evidence, "OS image evidence")
        raw_signature = signature_verifier(
            args.os_image_evidence, args.os_image_signature, args.os_image_keyring
        )
        ab = _load_json(args.ab_evidence, "A/B evidence")
        ab_signature = signature_verifier(args.ab_evidence, args.ab_signature, args.ab_keyring)
        omv = _load_json(args.omv_verification, "OMV verification")
        omv_evidence_raw = _read_regular(args.omv_evidence, MAX_JSON_BYTES, "OMV evidence")
        plugin_raw = _read_regular(args.omv_plugin_package, MAX_PLUGIN_BYTES, "OMV plugin")
        appliance = _load_json(args.appliance_release, "appliance release")
        payload = build_index(
            preflight=preflight,
            candidate_preflight=candidate_preflight,
            raw_evidence=raw,
            raw_signature=raw_signature,
            ab_evidence=ab,
            ab_signature=ab_signature,
            omv_verification=omv,
            omv_evidence_raw=omv_evidence_raw,
            omv_plugin_raw=plugin_raw,
            appliance=appliance,
        )
        _write_new(args.output, payload)
    except (OSError, ReleaseEvidenceError) as exc:
        print(f"Echo release evidence index failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_RELEASE_EVIDENCE_INDEX_OK "
        f"os={payload['source']['commit']} agent={payload['source']['agentCommit']} "
        f"candidate=ready product=physical-gates-required index={payload['indexId']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
