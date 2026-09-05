#!/usr/bin/env python3
"""Verify Btrfs RAID1 missing-member replacement on a disposable lab host.

The tool never detaches or wipes a member.  ``plan`` binds a completed Btrfs
provisioning run, its data probe, both original members and one third blank
disk.  The operator must then hot-detach the selected sacrificial member
without rebooting.  Explicit ``run`` phases exercise the real Echo HTTP
plan/approval/apply path, wait for completion, and verify data after reboot.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from deploy.appliance import btrfs_provisioning_lab as provisioning
    from deploy.appliance import operations_systemd as systemd
except ModuleNotFoundError:
    import btrfs_provisioning_lab as provisioning
    import operations_systemd as systemd

common = provisioning.common
SCHEMA_VERSION = 1
PHASES = ("repair", "rebuild", "reboot-verify")
PHASE_OUTPUTS = {
    "repair": "btrfs-replacement-repair.log",
    "rebuild": "btrfs-replacement-rebuild.log",
    "reboot-verify": "btrfs-replacement-reboot.log",
}
DESIRED_SCHEMA = "echo.omv.btrfs-replace-desired.v1"
PLAN_SCHEMA = "echo.omv.btrfs-replace-plan.v1"

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
StatusProbe = Callable[[str, provisioning.LabTools, CommandRunner], Mapping[str, Any]]
Sleeper = Callable[[float], None]


class BtrfsReplacementLabError(RuntimeError):
    """The three-disk Btrfs replacement experiment cannot proceed safely."""


def _read_evidence(path: Path, label: str, trusted_uid: int) -> dict[str, Any]:
    try:
        return common._read_json(path, label, trusted_uid, 0o444)
    except common.StorageProvisioningLabError as exc:
        raise BtrfsReplacementLabError(str(exc)) from exc


def _provisioning_state(plan_path: Path, trusted_uid: int) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        plan = common._read_plan(plan_path, trusted_uid)
        confirmation = plan.get("confirmations", {}).get("reboot-verify")
        provisioning._validate_plan_shape(plan, "reboot-verify", confirmation)
        root = Path(str(plan["evidenceDirectory"])).resolve(strict=True)
        provision = provisioning._prior_provision(root, str(plan["planId"]), trusted_uid)
    except (
        OSError,
        KeyError,
        TypeError,
        common.StorageProvisioningLabError,
        provisioning.BtrfsProvisioningLabError,
    ) as exc:
        raise BtrfsReplacementLabError("completed Btrfs provisioning evidence is required") from exc
    reboot = _read_evidence(
        root / provisioning.PHASE_OUTPUTS["reboot-verify"],
        "Btrfs provisioning reboot evidence",
        trusted_uid,
    )
    if (
        reboot.get("schemaVersion") != provisioning.SCHEMA_VERSION
        or reboot.get("kind") != "echo.btrfs-provisioning-physical-lab-evidence"
        or reboot.get("planId") != plan["planId"]
        or reboot.get("phase") != "reboot-verify"
        or reboot.get("passed") is not True
        or not isinstance(reboot.get("details"), dict)
        or reboot["details"].get("dataPreserved") is not True
    ):
        raise BtrfsReplacementLabError("Btrfs provisioning reboot evidence is invalid")
    filesystem = provision.get("filesystem")
    probe = provision.get("probe")
    if not isinstance(filesystem, dict) or not isinstance(probe, dict):
        raise BtrfsReplacementLabError("provisioning evidence lacks filesystem or probe identity")
    return plan, provision


def _bundle_identity(
    root: Path, candidate: Mapping[str, str], trusted_uid: int
) -> Mapping[str, Any]:
    try:
        identity = dict(provisioning._bundle_identity(root, candidate, trusted_uid))
    except provisioning.BtrfsProvisioningLabError as exc:
        raise BtrfsReplacementLabError(str(exc)) from exc
    raw = systemd._safe_regular(
        root / "bundle-manifest.json",
        "operations bundle manifest",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BtrfsReplacementLabError("operations bundle manifest is not strict JSON") from exc
    tool = systemd._safe_regular(
        root / "btrfs_replacement_lab.py",
        "candidate bundle Btrfs replacement lab",
        maximum=16 * 1024 * 1024,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o755,
    )
    record = manifest.get("files", {}).get("btrfs_replacement_lab.py")
    entrypoint = manifest.get("artifact", {}).get("entrypoints", {}).get(
        "btrfsReplacementLab"
    )
    if record != {
        "sha256": common._sha256(tool),
        "size": len(tool),
        "mode": "0755",
    } or entrypoint != "./btrfs_replacement_lab.py plan|run":
        raise BtrfsReplacementLabError("Btrfs replacement lab bytes are not bundle-bound")
    identity["btrfsReplacementLabSha256"] = common._sha256(tool)
    return identity


def _single_blank(value: Any, selected: str) -> dict[str, Any]:
    if not isinstance(value, list):
        raise BtrfsReplacementLabError("Btrfs blank-disk candidate response is invalid")
    matches = [
        item for item in value if isinstance(item, dict) and item.get("devicefile") == selected
    ]
    if len(matches) != 1:
        raise BtrfsReplacementLabError("replacement is not one unique blank API candidate")
    item = matches[0]
    allowed = {"devicefile", "sizeBytes", "serial", "wwn", "model"}
    if not set(item).issubset(allowed) or not {"devicefile", "sizeBytes", "serial", "wwn"}.issubset(
        item
    ):
        raise BtrfsReplacementLabError("replacement disk candidate identity is invalid")
    size = item.get("sizeBytes")
    serial = item.get("serial")
    wwn = item.get("wwn")
    if (
        common.SAFE_WHOLE_DISK.fullmatch(selected) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not common.MIN_DISK_BYTES <= size <= common.MAX_DISK_BYTES
        or not ((isinstance(serial, str) and serial) or (isinstance(wwn, str) and wwn))
        or any(
            len(identity) > 512 or any(character < " " for character in identity)
            for identity in (serial, wwn)
            if isinstance(identity, str)
        )
    ):
        raise BtrfsReplacementLabError("replacement disk candidate is unsafe")
    return common._bound_candidates([item])[0]


def _replacement_candidate(
    value: Any,
    *,
    filesystem_uuid: str,
    mountpoint: str,
    survivor: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, list):
        raise BtrfsReplacementLabError("Btrfs replacement candidate response is invalid")
    matches = [
        item
        for item in value
        if isinstance(item, dict)
        and item.get("filesystem", {}).get("uuid") == filesystem_uuid
        and item.get("filesystem", {}).get("mountpoint") == mountpoint
    ]
    if len(matches) != 1:
        raise BtrfsReplacementLabError(
            "operator must hot-detach exactly the planned member without rebooting"
        )
    item = matches[0]
    filesystem = item.get("filesystem")
    missing = item.get("missingMember")
    surviving = item.get("survivingMember")
    minimum = item.get("minimumReplacementBytes")
    if (
        not isinstance(filesystem, dict)
        or filesystem.get("readOnly") is not False
        or filesystem.get("totalDevices") != 2
        or filesystem.get("activeDevices") != 1
        or filesystem.get("missingDevices") != 1
        or filesystem.get("dataProfile") != "raid1"
        or filesystem.get("metadataProfile") != "raid1"
        or filesystem.get("deviceErrorCount") != 0
        or not isinstance(missing, dict)
        or missing.get("missing") is not True
        or missing.get("devicefile") is not None
        or isinstance(missing.get("devid"), bool)
        or not isinstance(missing.get("devid"), int)
        or missing["devid"] < 1
        or not isinstance(surviving, dict)
        or surviving.get("missing") is not False
        or surviving.get("writeable") is not True
        or surviving.get("replaceTarget") is not False
        or surviving.get("errorCount") != 0
        or common._bound_candidates([surviving]) != [dict(survivor)]
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or minimum <= 0
        or _single_blank(item.get("replacementDevices"), str(replacement["devicefile"]))
        != dict(replacement)
        or int(replacement["sizeBytes"]) < minimum
    ):
        raise BtrfsReplacementLabError("degraded Btrfs replacement topology is unsafe")
    return dict(item)


def _validate_api_plan(value: Mapping[str, Any], desired: Mapping[str, Any]) -> dict[str, Any]:
    safety = value.get("safety")
    if (
        value.get("schema") != PLAN_SCHEMA
        or value.get("operation") != "replaceMissingMember"
        or value.get("desired") != desired
        or not isinstance(value.get("planId"), str)
        or common.SHA256.fullmatch(str(value["planId"])) is None
        or value.get("requiresApproval") is not True
        or value.get("replacement", {}).get("devicefile") != desired["replacementDevice"]
        or not isinstance(safety, dict)
        or safety.get("force") is not False
        or safety.get("readFromSourceOnly") is not False
        or safety.get("foreground") is not False
        or safety.get("autoResize") is not False
        or safety.get("degradedRemount") is not False
        or safety.get("rollback") != "noneAfterReplacementAccepted"
    ):
        raise BtrfsReplacementLabError("appliance returned an unsafe Btrfs replacement plan")
    return dict(value)


def _replace_status(
    mountpoint: str, tools: provisioning.LabTools, runner: CommandRunner
) -> Mapping[str, Any]:
    completed = runner([str(tools.btrfs), "replace", "status", "-1", mountpoint])
    output = completed.stdout
    normalized = " ".join(output.strip().split())
    lowered = normalized.casefold()
    if completed.returncode != 0 or not normalized or len(output.encode("utf-8")) > 64 * 1024:
        raise BtrfsReplacementLabError("Btrfs replace status verification failed")
    if re.search(r"\b\d+(?:\.\d+)?%\s+done\b", lowered):
        state = "inProgress"
    elif "finished on" in lowered:
        state = "completed"
    elif "canceled on" in lowered or "cancelled on" in lowered or "failed" in lowered:
        state = "failed"
    else:
        raise BtrfsReplacementLabError("Btrfs replace status has an unknown state")
    errors = [
        int(value) for value in re.findall(r"\b(\d+)\s+(?:write|uncorr\. read) errs\b", lowered)
    ]
    return {"state": state, "errors": sum(errors) if errors else None}


def _write_new(path: Path, value: Mapping[str, Any], trusted_uid: int, mode: int) -> None:
    try:
        common._write_new(path, value, trusted_uid, mode)
    except common.StorageProvisioningLabError as exc:
        raise BtrfsReplacementLabError(str(exc)) from exc


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    provisioning_plan: Path,
    sacrificial_device: str,
    replacement_device: str,
    evidence_directory: Path,
    output: Path,
    password_env: str = "ECHO_ADMIN_PASSWORD",
    tools: provisioning.LabTools = provisioning.DEFAULT_TOOLS,
    runner: CommandRunner = provisioning._run,
    http_caller: common.HttpCaller = common._http_call,
    candidate_loader: common.CandidateLoader = common._candidate_identity,
    bundle_loader: common.BundleLoader = _bundle_identity,
    platform_probe: provisioning.PlatformProbe = provisioning._platform,
    host_verifier: provisioning.HostVerifier = provisioning._verify_host,
    probe_reader: provisioning.ProbeReader = common._read_probe,
    boot_id_reader: Callable[[], str] = common._boot_id,
    tool_validator: Callable[[provisioning.LabTools, int], None] = provisioning._validated_tools,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise BtrfsReplacementLabError("Btrfs replacement plan requires Linux root")
    provision_plan, provision = _provisioning_state(provisioning_plan, trusted_uid)
    selection = dict(provision_plan["selection"])
    desired = dict(selection["desired"])
    original = list(desired["devices"])
    if (
        len(original) != 2
        or sacrificial_device not in original
        or replacement_device in original
        or common.SAFE_WHOLE_DISK.fullmatch(replacement_device) is None
    ):
        raise BtrfsReplacementLabError(
            "one original member and one distinct third disk are required"
        )
    tool_validator(tools, trusted_uid)
    candidate = dict(candidate_loader(candidate_index, trusted_uid))
    bundle = dict(bundle_loader(bundle_root.resolve(strict=True), candidate, trusted_uid))
    platform = dict(platform_probe(os_release, tools, runner))
    if (
        candidate != provision_plan["releaseCandidate"]
        or platform != provision_plan["platform"]
        or bundle.get("artifactId") != provision_plan["operationsBundle"].get("artifactId")
    ):
        raise BtrfsReplacementLabError("replacement lab does not match the provisioned candidate")
    password = common._password(password_env)
    origin = str(provision_plan["appliance"]["baseUrl"])
    token = common._login(http_caller, origin, password)
    candidates = common._request(
        http_caller,
        origin,
        "GET",
        "/api/appliance/omv/volumes/btrfs-raid1/candidates",
        expected=200,
        token=token,
    )
    replacement = _single_blank(candidates.get("devices"), replacement_device)
    filesystem = dict(provision["filesystem"])
    probe = dict(provision["probe"])
    host_verifier(selection["mountpoint"], filesystem["uuid"], original, tools, runner)
    probe_reader(Path(selection["mountpoint"]), probe, trusted_uid)
    evidence_root = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence_root, "Btrfs replacement evidence directory", trusted_uid=trusted_uid
    )
    bound_original = {item["devicefile"]: item for item in selection["devices"]}
    survivor_path = next(path for path in original if path != sacrificial_device)
    material: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.btrfs-replacement-lab-plan",
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "platform": platform,
        "provisioningPlanId": provision_plan["planId"],
        "appliance": {"baseUrl": origin},
        "filesystem": {
            "uuid": filesystem["uuid"],
            "mountpoint": selection["mountpoint"],
            "originalMembers": original,
            "sacrificialMember": sacrificial_device,
            "survivingMember": bound_original[survivor_path],
            "replacement": replacement,
        },
        "probe": probe,
        "evidenceDirectory": str(evidence_root),
        "baselineBootId": boot_id_reader(),
        "phases": list(PHASES),
        "operatorPreparation": {
            "action": "hotDetachSacrificialMember",
            "device": sacrificial_device,
            "rebootAllowedBeforeRepair": False,
            "automatedByLab": False,
        },
        "retention": "retainForManualInspectionAndCleanup",
    }
    material["planId"] = common._sha256(common._canonical(material))
    material["confirmations"] = {
        phase: f"RUN ECHO BTRFS REPLACEMENT LAB {phase} {material['planId']}"
        for phase in PHASES
    }
    _write_new(output, material, trusted_uid, 0o400)
    return material


def _validate_plan(plan: Mapping[str, Any], phase: str, confirmation: str) -> None:
    unsigned = dict(plan)
    plan_id = unsigned.pop("planId", None)
    unsigned.pop("confirmations", None)
    confirmations = plan.get("confirmations")
    if (
        phase not in PHASES
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.btrfs-replacement-lab-plan"
        or plan.get("phases") != list(PHASES)
        or plan.get("retention") != "retainForManualInspectionAndCleanup"
        or plan.get("operatorPreparation", {}).get("automatedByLab") is not False
        or plan.get("operatorPreparation", {}).get("rebootAllowedBeforeRepair") is not False
        or not isinstance(plan_id, str)
        or plan_id != common._sha256(common._canonical(unsigned))
        or not isinstance(confirmations, dict)
        or confirmations.get(phase) != confirmation
    ):
        raise BtrfsReplacementLabError("Btrfs replacement plan or confirmation is invalid")


def _prior(root: Path, plan_id: str, phases: Sequence[str], trusted_uid: int) -> None:
    for phase in phases:
        value = _read_evidence(root / PHASE_OUTPUTS[phase], f"prior {phase} evidence", trusted_uid)
        if (
            value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "echo.btrfs-replacement-lab-evidence"
            or value.get("planId") != plan_id
            or value.get("phase") != phase
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise BtrfsReplacementLabError("Btrfs replacement evidence sequence is invalid")


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    candidate_index: Path,
    bundle_root: Path,
    password_env: str = "ECHO_ADMIN_PASSWORD",
    wait_seconds: int = 86400,
    tools: provisioning.LabTools = provisioning.DEFAULT_TOOLS,
    runner: CommandRunner = provisioning._run,
    http_caller: common.HttpCaller = common._http_call,
    candidate_loader: common.CandidateLoader = common._candidate_identity,
    bundle_loader: common.BundleLoader = _bundle_identity,
    platform_probe: provisioning.PlatformProbe = provisioning._platform,
    host_verifier: provisioning.HostVerifier = provisioning._verify_host,
    probe_reader: provisioning.ProbeReader = common._read_probe,
    boot_id_reader: Callable[[], str] = common._boot_id,
    status_probe: StatusProbe = _replace_status,
    sleeper: Sleeper = time.sleep,
    tool_validator: Callable[[provisioning.LabTools, int], None] = provisioning._validated_tools,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux" or not 0 <= wait_seconds <= 86400:
        raise BtrfsReplacementLabError("Btrfs replacement phase requires Linux root and a safe wait")
    plan = common._read_plan(plan_path, trusted_uid)
    _validate_plan(plan, phase, confirmation)
    tool_validator(tools, trusted_uid)
    candidate = dict(candidate_loader(candidate_index, trusted_uid))
    bundle = dict(bundle_loader(bundle_root.resolve(strict=True), candidate, trusted_uid))
    platform = dict(platform_probe(os_release, tools, runner))
    if (
        candidate != plan["releaseCandidate"]
        or bundle != plan["operationsBundle"]
        or platform != plan["platform"]
    ):
        raise BtrfsReplacementLabError("candidate, bundle, or platform changed after planning")
    root = Path(str(plan["evidenceDirectory"])).resolve(strict=True)
    systemd._assert_owned_directory(root, "Btrfs replacement evidence", trusted_uid=trusted_uid)
    index = PHASES.index(phase)
    _prior(root, str(plan["planId"]), PHASES[:index], trusted_uid)
    if any((root / PHASE_OUTPUTS[name]).exists() for name in PHASES[index:]):
        raise BtrfsReplacementLabError("Btrfs replacement evidence sequence is stale")
    output = root / PHASE_OUTPUTS[phase]
    current_boot = boot_id_reader()
    filesystem = dict(plan["filesystem"])
    replacement_path = str(filesystem["replacement"]["devicefile"])

    def probe() -> dict[str, Any]:
        return dict(
            probe_reader(Path(filesystem["mountpoint"]), dict(plan["probe"]), trusted_uid)
        )

    if phase == "repair":
        if current_boot != plan["baselineBootId"]:
            raise BtrfsReplacementLabError(
                "repair requires the planned boot; hot-detach the member without rebooting"
            )
        password = common._password(password_env)
        origin = str(plan["appliance"]["baseUrl"])
        token = common._login(http_caller, origin, password)
        before_probe = probe()
        candidates = common._request(
            http_caller,
            origin,
            "GET",
            "/api/appliance/omv/volumes/btrfs-raid1/replacement-candidates",
            expected=200,
            token=token,
        )
        topology = _replacement_candidate(
            candidates.get("replacements"),
            filesystem_uuid=str(filesystem["uuid"]),
            mountpoint=str(filesystem["mountpoint"]),
            survivor=dict(filesystem["survivingMember"]),
            replacement=dict(filesystem["replacement"]),
        )
        desired = {
            "schema": DESIRED_SCHEMA,
            "filesystemUuid": filesystem["uuid"],
            "missingDevid": topology["missingMember"]["devid"],
            "replacementDevice": replacement_path,
            "dataPreserved": True,
        }
        try:
            api_plan = _validate_api_plan(
                common._request(
                    http_caller,
                    origin,
                    "POST",
                    "/api/appliance/omv/volumes/btrfs-raid1/replace/plan",
                    expected=200,
                    payload=desired,
                    token=token,
                ),
                desired,
            )
            approval = common._approve(
                http_caller,
                origin,
                token,
                password,
                "omv.btrfs-raid1.replace",
                api_plan["planId"],
            )
            result = common._request(
                http_caller,
                origin,
                "POST",
                "/api/appliance/omv/volumes/btrfs-raid1/replace/apply",
                expected=200,
                payload={"desired": desired, "planId": api_plan["planId"]},
                token=token,
                headers={"X-Echo-Approval": approval},
            )
            if (
                result.get("applied") is not True
                or result.get("verified") is not True
                or result.get("dataPreserved") is not True
                or result.get("maintenanceState")
                not in {"replacing", "acceptedOrCompleted"}
                or result.get("filesystem", {}).get("uuid") != filesystem["uuid"]
                or result.get("replacement", {}).get("devicefile") != replacement_path
            ):
                raise BtrfsReplacementLabError("Btrfs replacement apply result is invalid")
        except Exception as exc:
            raise BtrfsReplacementLabError(
                "replacement failed after approval; preserve the host for manual inspection"
            ) from exc
        details = {
            "bootId": current_boot,
            "missingDevid": desired["missingDevid"],
            "replacementPlanId": api_plan["planId"],
            "operatorDetachedMember": True,
            "replacementAccepted": True,
            "maintenanceState": result["maintenanceState"],
            "dataBeforeReplacement": before_probe,
        }
    elif phase == "rebuild":
        repair = _read_evidence(root / PHASE_OUTPUTS["repair"], "repair evidence", trusted_uid)
        if current_boot != repair.get("details", {}).get("bootId"):
            raise BtrfsReplacementLabError("replacement completion must be verified before reboot")
        deadline = time.monotonic() + wait_seconds
        status = dict(status_probe(str(filesystem["mountpoint"]), tools, runner))
        while status.get("state") == "inProgress" and time.monotonic() < deadline:
            sleeper(min(5.0, max(0.0, deadline - time.monotonic())))
            status = dict(status_probe(str(filesystem["mountpoint"]), tools, runner))
        if status.get("state") != "completed" or status.get("errors") != 0:
            raise BtrfsReplacementLabError("Btrfs replacement did not complete cleanly in time")
        host = dict(
            host_verifier(
                str(filesystem["mountpoint"]),
                str(filesystem["uuid"]),
                [str(filesystem["survivingMember"]["devicefile"]), replacement_path],
                tools,
                runner,
            )
        )
        details = {"bootId": current_boot, "status": status, "host": host, "probe": probe()}
    else:
        rebuild = _read_evidence(root / PHASE_OUTPUTS["rebuild"], "rebuild evidence", trusted_uid)
        if current_boot == rebuild.get("details", {}).get("bootId"):
            raise BtrfsReplacementLabError("reboot verification requires a new kernel boot")
        host = dict(
            host_verifier(
                str(filesystem["mountpoint"]),
                str(filesystem["uuid"]),
                [str(filesystem["survivingMember"]["devicefile"]), replacement_path],
                tools,
                runner,
            )
        )
        details = {
            "previousBootId": rebuild["details"]["bootId"],
            "currentBootId": current_boot,
            "host": host,
            "probe": probe(),
            "mountedAfterReboot": True,
            "dataPreserved": True,
            "retainedForManualCleanup": True,
        }
    _write_new(
        output,
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.btrfs-replacement-lab-evidence",
            "planId": plan["planId"],
            "phase": phase,
            "passed": True,
            "details": details,
        },
        trusted_uid,
        0o444,
    )
    return {"phase": phase, "planId": plan["planId"], "output": output.name}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--provisioning-plan", type=Path, required=True)
    plan.add_argument("--sacrificial-device", required=True)
    plan.add_argument("--replacement-device", required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    plan.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--phase", choices=PHASES, required=True)
    run.add_argument("--confirm", required=True)
    run.add_argument("--candidate-index", type=Path, required=True)
    run.add_argument("--bundle-root", type=Path, required=True)
    run.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    run.add_argument("--wait-seconds", type=int, default=86400)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                provisioning_plan=args.provisioning_plan,
                sacrificial_device=args.sacrificial_device,
                replacement_device=args.replacement_device,
                evidence_directory=args.evidence_directory,
                output=args.output,
                password_env=args.password_env,
            )
            print(f"plan={result['planId']} phases={len(PHASES)}")
            print(
                "operator: hot-detach the sacrificial member without rebooting; "
                "the lab will not do this automatically"
            )
            for phase in PHASES:
                print(f"{phase}: {result['confirmations'][phase]}")
            return 0
        result = run_phase(
            plan_path=args.plan,
            phase=args.phase,
            confirmation=args.confirm,
            candidate_index=args.candidate_index,
            bundle_root=args.bundle_root,
            password_env=args.password_env,
            wait_seconds=args.wait_seconds,
        )
    except (
        OSError,
        ValueError,
        BtrfsReplacementLabError,
        common.StorageProvisioningLabError,
        provisioning.BtrfsProvisioningLabError,
    ) as exc:
        print(f"Btrfs replacement lab failed: {exc}", file=sys.stderr)
        return 1
    print(f"phase={result['phase']} plan={result['planId']} output={result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
