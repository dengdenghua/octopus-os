#!/usr/bin/env python3
"""Provision and reboot-verify an Echo-managed two-disk Btrfs RAID1 volume."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import storage_provisioning_lab as common
except ModuleNotFoundError:
    import operations_systemd as systemd
    import storage_provisioning_lab as common

SCHEMA_VERSION = 1
PHASES = ("provision", "reboot-verify")
PHASE_OUTPUTS = {
    "provision": "btrfs-provisioning.log",
    "reboot-verify": "btrfs-provisioning-reboot.log",
}
BTRFS_DESIRED_SCHEMA = "echo.omv.btrfs-raid1-desired.v1"
BTRFS_PLAN_SCHEMA = "echo.omv.btrfs-raid1-plan.v1"
FS_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class BtrfsProvisioningLabError(RuntimeError):
    """The destructive Btrfs provisioning lab cannot proceed safely."""


@dataclass(frozen=True)
class LabTools:
    btrfs: Path = Path("/usr/bin/btrfs")
    blkid: Path = Path("/usr/sbin/blkid")
    findmnt: Path = Path("/usr/bin/findmnt")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")


DEFAULT_TOOLS = LabTools()
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
HttpCaller = common.HttpCaller
CandidateLoader = common.CandidateLoader
BundleLoader = common.BundleLoader
PlatformProbe = Callable[[Path, LabTools, CommandRunner], Mapping[str, str]]
HostVerifier = Callable[
    [str, str, Sequence[str], LabTools, CommandRunner], Mapping[str, Any]
]
ProbeWriter = Callable[[Path, int], Mapping[str, Any]]
ProbeReader = Callable[[Path, Mapping[str, Any], int], Mapping[str, Any]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=900,
        env={
            **os.environ,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        },
    )


def _validated_tools(tools: LabTools, trusted_uid: int) -> None:
    for tool in (tools.btrfs, tools.blkid, tools.findmnt, tools.dpkg_query):
        systemd._safe_regular(
            tool,
            f"Btrfs provisioning tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )


def _platform(path: Path, tools: LabTools, runner: CommandRunner) -> Mapping[str, str]:
    common_tools = common.LabTools(dpkg_query=tools.dpkg_query)
    return common._platform(path, common_tools, runner)


def _bundle_identity(
    root: Path, candidate: Mapping[str, str], trusted_uid: int
) -> Mapping[str, Any]:
    identity = dict(common._bundle_identity(root, candidate, trusted_uid))
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
        raise BtrfsProvisioningLabError("operations bundle manifest is not strict JSON") from exc
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    tool = systemd._safe_regular(
        root / "btrfs_provisioning_lab.py",
        "candidate bundle Btrfs provisioning lab",
        maximum=16 * 1024 * 1024,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o755,
    )
    if (
        not isinstance(artifact, dict)
        or not isinstance(artifact.get("entrypoints"), dict)
        or artifact["entrypoints"].get("btrfsProvisioningLab")
        != "./btrfs_provisioning_lab.py plan|run"
        or not isinstance(files, dict)
        or files.get("btrfs_provisioning_lab.py")
        != {"sha256": common._sha256(tool), "size": len(tool), "mode": "0755"}
    ):
        raise BtrfsProvisioningLabError("Btrfs provisioning lab is not bound into the bundle")
    identity["btrfsProvisioningLabSha256"] = common._sha256(tool)
    return identity


def _desired(name: str, devices: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": BTRFS_DESIRED_SCHEMA,
        "name": name,
        "devices": sorted(devices),
        "dataLossConfirmed": True,
    }


def _validate_plan(plan: Mapping[str, Any], desired: Mapping[str, Any]) -> dict[str, Any]:
    devices = plan.get("devices")
    safety = plan.get("safety")
    if (
        plan.get("schema") != BTRFS_PLAN_SCHEMA
        or plan.get("operation") != "createAndMount"
        or plan.get("desired") != desired
        or plan.get("mountpoint") != f"/data/{desired['name']}"
        or not isinstance(plan.get("planId"), str)
        or common.SHA256.fullmatch(str(plan["planId"])) is None
        or not isinstance(devices, list)
        or sorted(str(item.get("devicefile")) for item in devices if isinstance(item, dict))
        != desired["devices"]
        or plan.get("requiresApproval") is not True
        or not isinstance(safety, dict)
        or safety.get("destructive") is not True
        or safety.get("dataProfile") != "raid1"
        or safety.get("metadataProfile") != "raid1"
        or safety.get("force") is not False
    ):
        raise BtrfsProvisioningLabError("appliance returned an invalid Btrfs RAID1 plan")
    return dict(plan)


def _command_output(
    command: list[str], runner: CommandRunner, label: str
) -> str:
    completed = runner(command)
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8")) > 1024 * 1024
        or len(completed.stderr.encode("utf-8")) > 1024 * 1024
    ):
        raise BtrfsProvisioningLabError(f"{label} verification failed")
    return completed.stdout


def _verify_host(
    mountpoint: str,
    filesystem_uuid: str,
    devices: Sequence[str],
    tools: LabTools,
    runner: CommandRunner,
) -> Mapping[str, Any]:
    member_uuids: set[str] = set()
    for device in devices:
        fields = common._export_fields(
            _command_output(
                [str(tools.blkid), "--probe", "--output", "export", device],
                runner,
                "Btrfs member identity",
            )
        )
        if fields.get("TYPE") != "btrfs" or FS_UUID.fullmatch(fields.get("UUID", "")) is None:
            raise BtrfsProvisioningLabError("Btrfs member identity drifted")
        member_uuids.add(fields["UUID"])
    if member_uuids != {filesystem_uuid}:
        raise BtrfsProvisioningLabError("Btrfs members do not retain one filesystem UUID")
    mount = _command_output(
        [
            str(tools.findmnt),
            "--noheadings",
            "--raw",
            "--output",
            "SOURCE,FSTYPE,OPTIONS,TARGET",
            "--mountpoint",
            mountpoint,
        ],
        runner,
        "Btrfs mount",
    ).strip()
    parts = mount.split(None, 3)
    if len(parts) != 4:
        raise BtrfsProvisioningLabError("Btrfs mount identity is incomplete")
    source, fstype, options, actual_mountpoint = parts
    option_set = set(options.split(","))
    if (
        source not in {*devices, f"UUID={filesystem_uuid}"}
        or fstype != "btrfs"
        or "rw" not in option_set
        or "ro" in option_set
        or actual_mountpoint != mountpoint
    ):
        raise BtrfsProvisioningLabError("Btrfs mount is not the planned writable filesystem")
    profile_output = _command_output(
        [str(tools.btrfs), "filesystem", "df", "--raw", mountpoint],
        runner,
        "Btrfs RAID profiles",
    )
    profiles: dict[str, set[str]] = {"Data": set(), "Metadata": set()}
    for line in profile_output.splitlines():
        match = re.match(r"^(Data|Metadata),\s*([^:]+):", line.strip())
        if match:
            profiles[match.group(1)].add(match.group(2).casefold())
    if profiles != {"Data": {"raid1"}, "Metadata": {"raid1"}}:
        raise BtrfsProvisioningLabError("Btrfs data or metadata profile is not RAID1")
    show = _command_output(
        [str(tools.btrfs), "filesystem", "show", "--raw", mountpoint],
        runner,
        "Btrfs topology",
    )
    uuid_match = re.search(r"\buuid:\s*([0-9a-f-]+)\s*$", show, re.MULTILINE)
    total_match = re.search(r"^\s*Total devices\s+(\d+)\b", show, re.MULTILINE)
    if (
        uuid_match is None
        or uuid_match.group(1) != filesystem_uuid
        or total_match is None
        or total_match.group(1) != "2"
        or "Some devices missing" in show
    ):
        raise BtrfsProvisioningLabError("Btrfs topology is incomplete or drifted")
    return {
        "filesystemUuid": filesystem_uuid,
        "mountpoint": mountpoint,
        "devices": sorted(devices),
        "dataProfile": "raid1",
        "metadataProfile": "raid1",
        "totalDevices": 2,
        "activeDevices": 2,
        "readOnly": False,
    }


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    devices: Sequence[str],
    volume_name: str,
    evidence_directory: Path,
    base_url: str,
    output: Path,
    password_env: str = "ECHO_ADMIN_PASSWORD",
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    http_caller: HttpCaller = common._http_call,
    candidate_loader: CandidateLoader = common._candidate_identity,
    bundle_loader: BundleLoader = _bundle_identity,
    platform_probe: PlatformProbe = _platform,
    tool_validator: Callable[[LabTools, int], None] = _validated_tools,
    boot_id_reader: Callable[[], str] = common._boot_id,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise BtrfsProvisioningLabError("Btrfs provisioning plan requires Linux root")
    if (
        len(devices) != 2
        or len(set(devices)) != 2
        or any(common.SAFE_WHOLE_DISK.fullmatch(device) is None for device in devices)
        or common.PORTABLE_VOLUME_NAME.fullmatch(volume_name) is None
    ):
        raise BtrfsProvisioningLabError("two safe whole disks and a portable volume name are required")
    origin = common._loopback_origin(base_url)
    tool_validator(tools, trusted_uid)
    evidence_root = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence_root, "Btrfs provisioning evidence directory", trusted_uid=trusted_uid
    )
    candidate = dict(candidate_loader(candidate_index, trusted_uid))
    bundle = dict(bundle_loader(bundle_root.resolve(strict=True), candidate, trusted_uid))
    platform = dict(platform_probe(os_release, tools, runner))
    password = common._password(password_env)
    token = common._login(http_caller, origin, password)
    candidates = common._request(
        http_caller,
        origin,
        "GET",
        "/api/appliance/omv/volumes/btrfs-raid1/candidates",
        expected=200,
        token=token,
    )
    identities = common._validate_candidates(candidates.get("devices"), devices)
    desired = _desired(volume_name, devices)
    api_plan = _validate_plan(
        common._request(
            http_caller,
            origin,
            "POST",
            "/api/appliance/omv/volumes/btrfs-raid1/plan",
            expected=200,
            payload=desired,
            token=token,
        ),
        desired,
    )
    material: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.btrfs-provisioning-physical-lab-plan",
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "platform": platform,
        "appliance": {"baseUrl": origin},
        "selection": {
            "devices": common._bound_candidates(identities),
            "desired": desired,
            "apiPlanId": api_plan["planId"],
            "mountpoint": api_plan["mountpoint"],
        },
        "evidenceDirectory": str(evidence_root),
        "baselineBootId": boot_id_reader(),
        "phases": list(PHASES),
        "retention": "retainForManualInspectionAndCleanup",
    }
    material["planId"] = common._sha256(common._canonical(material))
    material["confirmations"] = {
        phase: f"RUN ECHO BTRFS PROVISIONING LAB {phase} {material['planId']}"
        for phase in PHASES
    }
    common._write_new(output, material, trusted_uid, 0o400)
    return material


def _validate_plan_shape(plan: Mapping[str, Any], phase: str, confirmation: str) -> None:
    unsigned = dict(plan)
    plan_id = unsigned.pop("planId", None)
    unsigned.pop("confirmations", None)
    confirmations = plan.get("confirmations")
    if (
        phase not in PHASES
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.btrfs-provisioning-physical-lab-plan"
        or plan.get("phases") != list(PHASES)
        or plan.get("retention") != "retainForManualInspectionAndCleanup"
        or not isinstance(plan_id, str)
        or plan_id != common._sha256(common._canonical(unsigned))
        or not isinstance(confirmations, dict)
        or confirmations.get(phase) != confirmation
        or not isinstance(plan.get("selection"), dict)
    ):
        raise BtrfsProvisioningLabError("Btrfs provisioning plan or confirmation is invalid")


def _rebind(
    plan: Mapping[str, Any],
    *,
    candidate_index: Path,
    bundle_root: Path,
    tools: LabTools,
    runner: CommandRunner,
    candidate_loader: CandidateLoader,
    bundle_loader: BundleLoader,
    platform_probe: PlatformProbe,
    trusted_uid: int,
    os_release: Path,
) -> None:
    candidate = dict(candidate_loader(candidate_index, trusted_uid))
    bundle = dict(bundle_loader(bundle_root.resolve(strict=True), candidate, trusted_uid))
    platform = dict(platform_probe(os_release, tools, runner))
    if (
        candidate != plan.get("releaseCandidate")
        or bundle != plan.get("operationsBundle")
        or platform != plan.get("platform")
    ):
        raise BtrfsProvisioningLabError("candidate, bundle, or platform changed after planning")


def _provision(
    plan: Mapping[str, Any],
    *,
    password: str,
    http_caller: HttpCaller,
    tools: LabTools,
    runner: CommandRunner,
    host_verifier: HostVerifier,
    probe_writer: ProbeWriter,
    trusted_uid: int,
) -> dict[str, Any]:
    origin = str(plan["appliance"]["baseUrl"])
    selection = dict(plan["selection"])
    desired = dict(selection["desired"])
    devices = list(desired["devices"])
    token = common._login(http_caller, origin, password)
    candidates = common._request(
        http_caller,
        origin,
        "GET",
        "/api/appliance/omv/volumes/btrfs-raid1/candidates",
        expected=200,
        token=token,
    )
    rebound = common._bound_candidates(common._validate_candidates(candidates.get("devices"), devices))
    if rebound != selection["devices"]:
        raise BtrfsProvisioningLabError("selected disk identities changed after planning")
    api_plan = _validate_plan(
        common._request(
            http_caller,
            origin,
            "POST",
            "/api/appliance/omv/volumes/btrfs-raid1/plan",
            expected=200,
            payload=desired,
            token=token,
        ),
        desired,
    )
    if api_plan["planId"] != selection["apiPlanId"]:
        raise BtrfsProvisioningLabError("Btrfs RAID1 plan became stale before approval")
    mutation_started = False
    try:
        approval = common._approve(
            http_caller,
            origin,
            token,
            password,
            "omv.btrfs-raid1.create",
            api_plan["planId"],
        )
        mutation_started = True
        result = common._request(
            http_caller,
            origin,
            "POST",
            "/api/appliance/omv/volumes/btrfs-raid1/apply",
            expected=200,
            payload={"desired": desired, "planId": api_plan["planId"]},
            token=token,
            headers={"X-Echo-Approval": approval},
        )
        filesystem = result.get("filesystem")
        if (
            result.get("applied") is not True
            or result.get("verified") is not True
            or not isinstance(filesystem, dict)
            or filesystem.get("type") != "btrfs"
            or filesystem.get("devices") != devices
            or filesystem.get("mountpoint") != selection["mountpoint"]
            or filesystem.get("dataProfile") != "raid1"
            or filesystem.get("metadataProfile") != "raid1"
            or not isinstance(filesystem.get("uuid"), str)
            or FS_UUID.fullmatch(str(filesystem["uuid"])) is None
        ):
            raise BtrfsProvisioningLabError("Btrfs RAID1 apply result is invalid")
        host = dict(
            host_verifier(
                selection["mountpoint"],
                filesystem["uuid"],
                devices,
                tools,
                runner,
            )
        )
        probe = dict(probe_writer(Path(selection["mountpoint"]), trusted_uid))
    except Exception as exc:
        if mutation_started:
            raise BtrfsProvisioningLabError(
                "Btrfs provisioning failed after mutation began; preserve the host for inspection"
            ) from exc
        raise
    return {
        "apiPlanId": api_plan["planId"],
        "filesystem": dict(filesystem),
        "host": host,
        "probe": probe,
        "retainedForManualCleanup": True,
    }


def _prior_provision(root: Path, plan_id: str, trusted_uid: int) -> dict[str, Any]:
    value = common._read_json(
        root / PHASE_OUTPUTS["provision"], "Btrfs provisioning evidence", trusted_uid, 0o444
    )
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "echo.btrfs-provisioning-physical-lab-evidence"
        or value.get("planId") != plan_id
        or value.get("phase") != "provision"
        or value.get("passed") is not True
        or not isinstance(value.get("details"), dict)
    ):
        raise BtrfsProvisioningLabError("prior Btrfs provisioning evidence is invalid")
    return dict(value["details"])


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    candidate_index: Path,
    bundle_root: Path,
    password_env: str = "ECHO_ADMIN_PASSWORD",
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    http_caller: HttpCaller = common._http_call,
    candidate_loader: CandidateLoader = common._candidate_identity,
    bundle_loader: BundleLoader = _bundle_identity,
    platform_probe: PlatformProbe = _platform,
    tool_validator: Callable[[LabTools, int], None] = _validated_tools,
    boot_id_reader: Callable[[], str] = common._boot_id,
    host_verifier: HostVerifier = _verify_host,
    probe_writer: ProbeWriter = common._write_probe,
    probe_reader: ProbeReader = common._read_probe,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise BtrfsProvisioningLabError("Btrfs provisioning phase requires Linux root")
    plan = common._read_plan(plan_path, trusted_uid)
    _validate_plan_shape(plan, phase, confirmation)
    tool_validator(tools, trusted_uid)
    _rebind(
        plan,
        candidate_index=candidate_index,
        bundle_root=bundle_root,
        tools=tools,
        runner=runner,
        candidate_loader=candidate_loader,
        bundle_loader=bundle_loader,
        platform_probe=platform_probe,
        trusted_uid=trusted_uid,
        os_release=os_release,
    )
    root = Path(str(plan["evidenceDirectory"])).resolve(strict=True)
    systemd._assert_owned_directory(
        root, "Btrfs provisioning evidence directory", trusted_uid=trusted_uid
    )
    output = root / PHASE_OUTPUTS[phase]
    if output.exists() or output.is_symlink():
        raise BtrfsProvisioningLabError("Btrfs provisioning phase evidence already exists")
    current_boot = boot_id_reader()
    if phase == "provision":
        if current_boot != plan["baselineBootId"]:
            raise BtrfsProvisioningLabError("host rebooted after Btrfs provisioning was planned")
        details = _provision(
            plan,
            password=common._password(password_env),
            http_caller=http_caller,
            tools=tools,
            runner=runner,
            host_verifier=host_verifier,
            probe_writer=probe_writer,
            trusted_uid=trusted_uid,
        )
    else:
        prior = _prior_provision(root, str(plan["planId"]), trusted_uid)
        if current_boot == plan["baselineBootId"]:
            raise BtrfsProvisioningLabError("Btrfs reboot verification requires a new kernel boot")
        filesystem = prior.get("filesystem")
        if not isinstance(filesystem, dict):
            raise BtrfsProvisioningLabError("Btrfs provisioning evidence lacks filesystem identity")
        selection = dict(plan["selection"])
        host = dict(
            host_verifier(
                selection["mountpoint"],
                str(filesystem.get("uuid")),
                list(selection["desired"]["devices"]),
                tools,
                runner,
            )
        )
        probe = dict(
            probe_reader(
                Path(selection["mountpoint"]), dict(prior.get("probe") or {}), trusted_uid
            )
        )
        details = {
            "previousBootId": plan["baselineBootId"],
            "currentBootId": current_boot,
            "host": host,
            "probe": probe,
            "mountedAfterReboot": True,
            "dataPreserved": True,
            "retainedForManualCleanup": True,
        }
    common._write_new(
        output,
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.btrfs-provisioning-physical-lab-evidence",
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
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--device", type=str, action="append", required=True)
    plan.add_argument("--volume-name", required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    plan.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--phase", choices=PHASES, required=True)
    run.add_argument("--confirm", required=True)
    run.add_argument("--candidate-index", type=Path, required=True)
    run.add_argument("--bundle-root", type=Path, required=True)
    run.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            report = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                devices=args.device,
                volume_name=args.volume_name,
                evidence_directory=args.evidence_directory,
                base_url=args.base_url,
                output=args.output,
                password_env=args.password_env,
            )
            print(f"plan={report['planId']} phases={len(report['phases'])}")
            for phase in PHASES:
                print(f"{phase}: {report['confirmations'][phase]}")
            return 0
        report = run_phase(
            plan_path=args.plan,
            phase=args.phase,
            confirmation=args.confirm,
            candidate_index=args.candidate_index,
            bundle_root=args.bundle_root,
            password_env=args.password_env,
        )
    except (
        OSError,
        ValueError,
        common.StorageProvisioningLabError,
        BtrfsProvisioningLabError,
    ) as exc:
        print(f"Btrfs provisioning lab failed: {exc}", file=sys.stderr)
        return 1
    print(f"phase={report['phase']} plan={report['planId']} output={report['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
