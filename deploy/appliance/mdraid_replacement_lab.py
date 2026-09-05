#!/usr/bin/env python3
"""Exercise failed-member replacement through the Echo HTTP control plane.

Run only on a disposable Debian 13 + OMV 8 lab host after
``storage_provisioning_lab.py`` has completed both phases.  The first phase
fails and removes one original member, then uses the real candidate/plan/
approval/apply API to add a third blank disk.  Later phases prove rebuild and
post-reboot data persistence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import storage_provisioning_lab as provisioning
    from deploy.appliance import storage_recovery_lab as recovery
except ModuleNotFoundError:
    import operations_systemd as systemd
    import storage_provisioning_lab as provisioning
    import storage_recovery_lab as recovery

SCHEMA_VERSION = 1
PHASES = ("repair", "rebuild", "reboot-verify")
PHASE_OUTPUTS = {
    "repair": "mdraid-replacement-repair.log",
    "rebuild": "mdraid-replacement-rebuild.log",
    "reboot-verify": "mdraid-replacement-reboot.log",
}
DESIRED_SCHEMA = "echo.omv.mdraid1-replace-desired.v1"
PLAN_SCHEMA = "echo.omv.mdraid1-replace-plan.v1"

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
ArrayProbe = Callable[[Path], Mapping[str, Any]]
DeviceVerifier = Callable[
    [Path, Sequence[Path], Path, recovery.LabTools, CommandRunner], Mapping[str, Any]
]
Sleeper = Callable[[float], None]


class MdRaidReplacementLabError(RuntimeError):
    """The three-disk replacement experiment cannot proceed safely."""


def _validated_tools(tools: recovery.LabTools, trusted_uid: int) -> None:
    provisioning._validated_tools(tools, trusted_uid)
    systemd._safe_regular(
        tools.lsblk,
        "md RAID replacement tool lsblk",
        maximum=64 * 1024 * 1024,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o755,
    )


def _read_evidence(path: Path, label: str, trusted_uid: int) -> dict[str, Any]:
    try:
        return provisioning._read_json(path, label, trusted_uid, 0o444)
    except provisioning.StorageProvisioningLabError as exc:
        raise MdRaidReplacementLabError(str(exc)) from exc


def _provisioning_state(plan_path: Path, trusted_uid: int) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        plan = provisioning._read_plan(plan_path, trusted_uid)
        confirmation = plan.get("confirmations", {}).get("reboot-verify")
        provisioning._validate_plan_shape(plan, "reboot-verify", confirmation)
        root = Path(str(plan["evidenceDirectory"])).resolve(strict=True)
        provision = provisioning._prior_provision(root, str(plan["planId"]), trusted_uid)
    except (OSError, KeyError, TypeError, provisioning.StorageProvisioningLabError) as exc:
        raise MdRaidReplacementLabError("completed provisioning evidence is required") from exc
    reboot = _read_evidence(
        root / provisioning.PHASE_OUTPUTS["reboot-verify"],
        "storage provisioning reboot evidence",
        trusted_uid,
    )
    if (
        set(reboot) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
        or reboot.get("schemaVersion") != provisioning.SCHEMA_VERSION
        or reboot.get("kind") != "echo.storage-provisioning-physical-lab-evidence"
        or reboot.get("planId") != plan["planId"]
        or reboot.get("phase") != "reboot-verify"
        or reboot.get("passed") is not True
        or not isinstance(reboot.get("details"), dict)
        or reboot["details"].get("dataPreserved") is not True
    ):
        raise MdRaidReplacementLabError("provisioning reboot evidence is invalid")
    array = provision.get("array")
    filesystem = provision.get("filesystem")
    probe = provision.get("probe")
    if not all(isinstance(item, dict) for item in (array, filesystem, probe)):
        raise MdRaidReplacementLabError("provisioning evidence lacks storage identities")
    return plan, provision


def _bundle_identity(root: Path, candidate: Mapping[str, str], trusted_uid: int) -> dict[str, Any]:
    try:
        base = dict(provisioning._bundle_identity(root, candidate, trusted_uid))
    except provisioning.StorageProvisioningLabError as exc:
        raise MdRaidReplacementLabError(str(exc)) from exc
    manifest_raw = systemd._safe_regular(
        root / "bundle-manifest.json",
        "operations bundle manifest",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    try:
        manifest = json.loads(
            manifest_raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MdRaidReplacementLabError("operations bundle manifest is not strict JSON") from exc
    if not isinstance(manifest, dict):
        raise MdRaidReplacementLabError("operations bundle manifest is invalid")
    tool = systemd._safe_regular(
        root / "mdraid_replacement_lab.py",
        "candidate md RAID replacement lab",
        maximum=16 * 1024 * 1024,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o755,
    )
    record = manifest.get("files", {}).get("mdraid_replacement_lab.py")
    entrypoint = manifest.get("artifact", {}).get("entrypoints", {}).get("mdraidReplacementLab")
    if (
        record
        != {
            "sha256": provisioning._sha256(tool),
            "size": len(tool),
            "mode": "0755",
        }
        or entrypoint != "./mdraid_replacement_lab.py plan|run"
    ):
        raise MdRaidReplacementLabError("replacement lab bytes are not bundle-bound")
    return {**base, "mdraidReplacementLabSha256": provisioning._sha256(tool)}


def _single_candidate(value: Any, selected: str) -> dict[str, Any]:
    if not isinstance(value, list):
        raise MdRaidReplacementLabError("replacement disk candidate response is invalid")
    matches = [
        item for item in value if isinstance(item, dict) and item.get("devicefile") == selected
    ]
    if len(matches) != 1:
        raise MdRaidReplacementLabError("replacement disk is not one unique blank API candidate")
    item = matches[0]
    if set(item) != {"devicefile", "sizeBytes", "serial", "wwn", "model"}:
        raise MdRaidReplacementLabError("replacement disk candidate identity is invalid")
    size = item.get("sizeBytes")
    serial = item.get("serial")
    wwn = item.get("wwn")
    if (
        provisioning.SAFE_WHOLE_DISK.fullmatch(selected) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not provisioning.MIN_DISK_BYTES <= size <= provisioning.MAX_DISK_BYTES
        or not ((isinstance(serial, str) and serial) or (isinstance(wwn, str) and wwn))
        or any(
            len(identity) > 512 or any(character < " " for character in identity)
            for identity in (serial, wwn)
            if isinstance(identity, str)
        )
    ):
        raise MdRaidReplacementLabError("replacement disk candidate is unsafe")
    return provisioning._bound_candidates([item])[0]


def _replacement_candidate(
    value: Any,
    *,
    name: str,
    array_uuid: str,
    target: str,
    survivor: str,
    replacement: str,
) -> dict[str, Any]:
    if not isinstance(value, list):
        raise MdRaidReplacementLabError("RAID replacement candidate response is invalid")
    matches = [
        item
        for item in value
        if isinstance(item, dict)
        and item.get("array", {}).get("name") == name
        and item.get("array", {}).get("uuid") == array_uuid
        and item.get("array", {}).get("devicefile") == target
    ]
    if len(matches) != 1:
        raise MdRaidReplacementLabError("degraded array is not one replacement candidate")
    item = matches[0]
    devices = item.get("replacementDevices")
    if (
        item.get("survivingMember", {}).get("devicefile") != survivor
        or item.get("failedMember") is not None
        or isinstance(item.get("missingSlot"), bool)
        or item.get("missingSlot") not in {0, 1}
        or isinstance(item.get("minimumReplacementBytes"), bool)
        or not isinstance(item.get("minimumReplacementBytes"), int)
        or item["minimumReplacementBytes"] <= 0
    ):
        raise MdRaidReplacementLabError("degraded array replacement topology is invalid")
    _single_candidate(devices, replacement)
    return dict(item)


def _validate_api_plan(value: Mapping[str, Any], desired: Mapping[str, Any]) -> dict[str, Any]:
    safety = value.get("safety")
    if (
        value.get("schema") != PLAN_SCHEMA
        or value.get("operation") != "replaceFailedMember"
        or value.get("desired") != desired
        or not isinstance(value.get("planId"), str)
        or provisioning.SHA256.fullmatch(str(value["planId"])) is None
        or value.get("requiresApproval") is not True
        or not isinstance(safety, dict)
        or safety.get("force") is not False
        or safety.get("marksHealthyMemberFaulty") is not False
        or value.get("replacement", {}).get("devicefile") != desired["replacementDevice"]
    ):
        raise MdRaidReplacementLabError("appliance returned an unsafe replacement plan")
    return dict(value)


def _write_new(path: Path, value: Mapping[str, Any], trusted_uid: int, mode: int) -> None:
    try:
        provisioning._write_new(path, value, trusted_uid, mode)
    except provisioning.StorageProvisioningLabError as exc:
        raise MdRaidReplacementLabError(str(exc)) from exc


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
    tools: recovery.LabTools = recovery.DEFAULT_TOOLS,
    runner: CommandRunner = recovery._run,
    http_caller: provisioning.HttpCaller = provisioning._http_call,
    candidate_loader: provisioning.CandidateLoader = provisioning._candidate_identity,
    bundle_loader: provisioning.BundleLoader = _bundle_identity,
    platform_probe: provisioning.PlatformProbe = provisioning._platform,
    device_verifier: DeviceVerifier = recovery._device_inventory,
    host_verifier: provisioning.HostVerifier = provisioning._verify_host,
    probe_reader: provisioning.ProbeReader = provisioning._read_probe,
    boot_id_reader: provisioning.BootIdReader = provisioning._boot_id,
    tool_validator: provisioning.ToolValidator = _validated_tools,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise MdRaidReplacementLabError("md RAID replacement plan requires Linux root")
    provisioning_state, provision = _provisioning_state(provisioning_plan, trusted_uid)
    selection = dict(provisioning_state["selection"])
    original = list(selection["mdDesired"]["devices"])
    if (
        len(original) != 2
        or sacrificial_device not in original
        or replacement_device in original
        or provisioning.SAFE_WHOLE_DISK.fullmatch(replacement_device) is None
    ):
        raise MdRaidReplacementLabError(
            "one original member and one distinct third disk are required"
        )
    tool_validator(tools, trusted_uid)
    candidate = dict(candidate_loader(candidate_index, trusted_uid))
    bundle = dict(bundle_loader(bundle_root.resolve(strict=True), candidate, trusted_uid))
    platform = dict(platform_probe(os_release, tools, runner))
    if (
        candidate != provisioning_state["releaseCandidate"]
        or platform != provisioning_state["platform"]
        or bundle.get("artifactId") != provisioning_state["operationsBundle"].get("artifactId")
    ):
        raise MdRaidReplacementLabError("replacement lab does not match the provisioned candidate")
    password = provisioning._password(password_env)
    origin = str(provisioning_state["appliance"]["baseUrl"])
    token = provisioning._login(http_caller, origin, password)
    response = provisioning._request(
        http_caller,
        origin,
        "GET",
        "/api/appliance/omv/arrays/mdraid1/candidates",
        expected=200,
        token=token,
    )
    replacement = _single_candidate(response.get("devices"), replacement_device)
    array = dict(provision["array"])
    filesystem = dict(provision["filesystem"])
    probe = dict(provision["probe"])
    host_verifier(
        selection["arrayTarget"],
        array["uuid"],
        selection["mountpoint"],
        filesystem["uuid"],
        original,
        tools,
        runner,
    )
    probe_reader(Path(selection["mountpoint"]), probe, trusted_uid)
    evidence_root = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence_root, "replacement lab evidence directory", trusted_uid=trusted_uid
    )
    devices = dict(
        device_verifier(
            Path(selection["arrayTarget"]),
            [Path(path) for path in (*original, replacement_device)],
            Path(selection["mountpoint"]),
            tools,
            runner,
        )
    )
    material: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.mdraid-replacement-lab-plan",
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "platform": platform,
        "provisioningPlanId": provisioning_state["planId"],
        "appliance": {"baseUrl": origin},
        "array": {
            "name": selection["mdDesired"]["name"],
            "devicefile": selection["arrayTarget"],
            "uuid": array["uuid"],
            "filesystemUuid": filesystem["uuid"],
            "mountpoint": selection["mountpoint"],
            "originalMembers": original,
            "sacrificialMember": sacrificial_device,
            "survivingMember": next(path for path in original if path != sacrificial_device),
            "replacement": replacement,
        },
        "deviceBinding": devices,
        "probe": probe,
        "evidenceDirectory": str(evidence_root),
        "baselineBootId": boot_id_reader(),
        "phases": list(PHASES),
    }
    material["planId"] = provisioning._sha256(provisioning._canonical(material))
    material["confirmations"] = {
        phase: f"RUN ECHO MDRAID REPLACEMENT LAB {phase} {material['planId']}" for phase in PHASES
    }
    _write_new(output, material, trusted_uid, 0o400)
    return material


def _validate_plan(plan: Mapping[str, Any], phase: str, confirmation: str) -> None:
    unsigned = dict(plan)
    plan_id = unsigned.pop("planId", None)
    confirmations = unsigned.pop("confirmations", None)
    if (
        phase not in PHASES
        or set(plan)
        != {
            "schemaVersion",
            "kind",
            "releaseCandidate",
            "operationsBundle",
            "platform",
            "provisioningPlanId",
            "appliance",
            "array",
            "deviceBinding",
            "probe",
            "evidenceDirectory",
            "baselineBootId",
            "phases",
            "planId",
            "confirmations",
        }
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.mdraid-replacement-lab-plan"
        or plan.get("phases") != list(PHASES)
        or not isinstance(plan_id, str)
        or plan_id != provisioning._sha256(provisioning._canonical(unsigned))
        or not isinstance(confirmations, dict)
        or confirmations.get(phase) != confirmation
    ):
        raise MdRaidReplacementLabError("replacement lab plan or confirmation is invalid")


def _prior(root: Path, plan_id: str, phases: Sequence[str], trusted_uid: int) -> None:
    for name in phases:
        value = _read_evidence(root / PHASE_OUTPUTS[name], f"prior {name} evidence", trusted_uid)
        if (
            set(value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
            or value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "echo.mdraid-replacement-lab-evidence"
            or value.get("planId") != plan_id
            or value.get("phase") != name
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise MdRaidReplacementLabError("replacement lab evidence sequence is invalid")


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    candidate_index: Path,
    bundle_root: Path,
    password_env: str = "ECHO_ADMIN_PASSWORD",
    wait_seconds: int = 86400,
    tools: recovery.LabTools = recovery.DEFAULT_TOOLS,
    runner: CommandRunner = recovery._run,
    http_caller: provisioning.HttpCaller = provisioning._http_call,
    candidate_loader: provisioning.CandidateLoader = provisioning._candidate_identity,
    bundle_loader: provisioning.BundleLoader = _bundle_identity,
    platform_probe: provisioning.PlatformProbe = provisioning._platform,
    device_verifier: DeviceVerifier = recovery._device_inventory,
    array_probe: ArrayProbe | None = None,
    host_verifier: provisioning.HostVerifier = provisioning._verify_host,
    probe_reader: provisioning.ProbeReader = provisioning._read_probe,
    boot_id_reader: provisioning.BootIdReader = provisioning._boot_id,
    sleeper: Sleeper = time.sleep,
    tool_validator: provisioning.ToolValidator = _validated_tools,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux" or not 0 <= wait_seconds <= 86400:
        raise MdRaidReplacementLabError("replacement phase requires Linux root and a safe wait")
    plan = provisioning._read_plan(plan_path, trusted_uid)
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
        raise MdRaidReplacementLabError("candidate, bundle, or platform changed after planning")
    root = Path(str(plan["evidenceDirectory"])).resolve(strict=True)
    systemd._assert_owned_directory(root, "replacement evidence", trusted_uid=trusted_uid)
    index = PHASES.index(phase)
    _prior(root, str(plan["planId"]), PHASES[:index], trusted_uid)
    if any((root / PHASE_OUTPUTS[name]).exists() for name in PHASES[index:]):
        raise MdRaidReplacementLabError("replacement evidence sequence is stale")
    output = root / PHASE_OUTPUTS[phase]
    if output.exists() or output.is_symlink():
        raise MdRaidReplacementLabError("replacement phase evidence already exists")
    array = dict(plan["array"])
    original = list(array["originalMembers"])
    replacement_path = str(array["replacement"]["devicefile"])
    current_binding = dict(
        device_verifier(
            Path(array["devicefile"]),
            [Path(path) for path in (*original, replacement_path)],
            Path(array["mountpoint"]),
            tools,
            runner,
        )
    )
    if current_binding != plan["deviceBinding"]:
        raise MdRaidReplacementLabError("one of the three bound disk identities changed")

    def probe() -> dict[str, Any]:
        return dict(probe_reader(Path(array["mountpoint"]), dict(plan["probe"]), trusted_uid))

    array_status = array_probe or (lambda path: recovery._array_status(path, tools, runner))
    current_boot = boot_id_reader()
    if phase == "repair":
        if current_boot != plan["baselineBootId"]:
            raise MdRaidReplacementLabError("host rebooted after replacement was planned")
        host_verifier(
            array["devicefile"],
            array["uuid"],
            array["mountpoint"],
            array["filesystemUuid"],
            original,
            tools,
            runner,
        )
        before_probe = probe()
        try:
            for operation in ("--fail", "--remove"):
                completed = runner(
                    [
                        str(tools.mdadm),
                        "--manage",
                        array["devicefile"],
                        operation,
                        array["sacrificialMember"],
                    ]
                )
                if completed.returncode != 0:
                    raise MdRaidReplacementLabError(f"mdadm {operation} rejected the bound member")
            password = provisioning._password(password_env)
            origin = str(plan["appliance"]["baseUrl"])
            token = provisioning._login(http_caller, origin, password)
            candidates = provisioning._request(
                http_caller,
                origin,
                "GET",
                "/api/appliance/omv/arrays/mdraid1/replacement-candidates",
                expected=200,
                token=token,
            )
            _replacement_candidate(
                candidates.get("replacements"),
                name=array["name"],
                array_uuid=array["uuid"],
                target=array["devicefile"],
                survivor=array["survivingMember"],
                replacement=replacement_path,
            )
            desired = {
                "schema": DESIRED_SCHEMA,
                "name": array["name"],
                "arrayUuid": array["uuid"],
                "replacementDevice": replacement_path,
                "dataPreserved": True,
            }
            api_plan = _validate_api_plan(
                provisioning._request(
                    http_caller,
                    origin,
                    "POST",
                    "/api/appliance/omv/arrays/mdraid1/replace/plan",
                    expected=200,
                    payload=desired,
                    token=token,
                ),
                desired,
            )
            approval = provisioning._approve(
                http_caller,
                origin,
                token,
                password,
                "omv.mdraid1.replace",
                api_plan["planId"],
            )
            result = provisioning._request(
                http_caller,
                origin,
                "POST",
                "/api/appliance/omv/arrays/mdraid1/replace/apply",
                expected=200,
                payload={"desired": desired, "planId": api_plan["planId"]},
                token=token,
                headers={"X-Echo-Approval": approval},
            )
            if (
                result.get("applied") is not True
                or result.get("verified") is not True
                or result.get("dataPreserved") is not True
                or result.get("maintenanceState") not in {"recovering", "acceptedOrCompleted"}
                or result.get("array", {}).get("devicefile") != array["devicefile"]
                or result.get("array", {}).get("uuid") != array["uuid"]
                or result.get("replacement", {}).get("devicefile") != replacement_path
            ):
                raise MdRaidReplacementLabError("replacement apply result is invalid")
        except Exception as exc:
            raise MdRaidReplacementLabError(
                "replacement failed after degradation; preserve the host for manual inspection"
            ) from exc
        details = {
            "bootId": current_boot,
            "replacementPlanId": api_plan["planId"],
            "degradedMemberRemoved": True,
            "replacementAccepted": True,
            "dataBeforeReplacement": before_probe,
        }
    elif phase == "rebuild":
        repair = _read_evidence(root / PHASE_OUTPUTS["repair"], "repair evidence", trusted_uid)
        if current_boot != repair.get("details", {}).get("bootId"):
            raise MdRaidReplacementLabError("rebuild verification must precede reboot")
        deadline = time.monotonic() + wait_seconds
        state = dict(array_status(Path(array["devicefile"])))
        while state.get("healthy") is not True and time.monotonic() < deadline:
            sleeper(min(5.0, max(0.0, deadline - time.monotonic())))
            state = dict(array_status(Path(array["devicefile"])))
        if state.get("healthy") is not True or state.get("active") != 2:
            raise MdRaidReplacementLabError("RAID1 rebuild did not complete within the bound")
        host = dict(
            host_verifier(
                array["devicefile"],
                array["uuid"],
                array["mountpoint"],
                array["filesystemUuid"],
                [array["survivingMember"], replacement_path],
                tools,
                runner,
            )
        )
        details = {"bootId": current_boot, "array": state, "host": host, "probe": probe()}
    else:
        rebuild = _read_evidence(root / PHASE_OUTPUTS["rebuild"], "rebuild evidence", trusted_uid)
        if current_boot == rebuild.get("details", {}).get("bootId"):
            raise MdRaidReplacementLabError("reboot verification requires a new kernel boot")
        state = dict(array_status(Path(array["devicefile"])))
        if state.get("healthy") is not True or state.get("active") != 2:
            raise MdRaidReplacementLabError("replacement RAID1 is not healthy after reboot")
        host = dict(
            host_verifier(
                array["devicefile"],
                array["uuid"],
                array["mountpoint"],
                array["filesystemUuid"],
                [array["survivingMember"], replacement_path],
                tools,
                runner,
            )
        )
        details = {
            "previousBootId": rebuild["details"]["bootId"],
            "currentBootId": current_boot,
            "array": state,
            "host": host,
            "probe": probe(),
            "dataPreserved": True,
        }
    _write_new(
        output,
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.mdraid-replacement-lab-evidence",
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
    except (OSError, ValueError, MdRaidReplacementLabError) as exc:
        print(f"md RAID replacement lab failed: {exc}", file=sys.stderr)
        return 1
    print(f"phase={result['phase']} plan={result['planId']} output={result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
