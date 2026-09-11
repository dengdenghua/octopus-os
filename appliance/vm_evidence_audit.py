"""Strict local-VM evidence audit used before an Echo OS source freeze."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
INDEX_KIND = "echo.local-vm-evidence-index"
MAX_INDEX_BYTES = 256 * 1024
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 64
_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,126}\.json$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


class VmEvidenceAuditError(ValueError):
    """The audit index itself is malformed or unsafe."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise VmEvidenceAuditError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_json(path: Path, *, maximum: int, label: str) -> tuple[bytes, Any]:
    if path.is_symlink() or not path.is_file():
        raise VmEvidenceAuditError(f"{label} must be a regular file")
    data = path.read_bytes()
    if not data or len(data) > maximum:
        raise VmEvidenceAuditError(f"{label} has an invalid size")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VmEvidenceAuditError(f"{label} is not strict UTF-8 JSON") from exc
    return data, value


def _field(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split("."):
        if not part or not isinstance(current, dict) or part not in current:
            raise KeyError(dotted)
        current = current[part]
    return current


def _contains_windows_path(value: Any) -> bool:
    if isinstance(value, str):
        return _WINDOWS_ABSOLUTE_PATH.match(value) is not None
    if isinstance(value, list):
        return any(_contains_windows_path(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_windows_path(item) for item in value.values())
    return False


def _validated_index(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "kind", "entries"}:
        raise VmEvidenceAuditError("evidence index has an invalid top-level schema")
    if value["schemaVersion"] != SCHEMA_VERSION or value["kind"] != INDEX_KIND:
        raise VmEvidenceAuditError("evidence index identity is unsupported")
    entries = value["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise VmEvidenceAuditError("evidence index entry count is invalid")
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "name",
            "sha256",
            "discriminator",
            "bindings",
            "assertions",
        }:
            raise VmEvidenceAuditError("evidence index entry schema is invalid")
        name = entry["name"]
        digest = entry["sha256"]
        discriminator = entry["discriminator"]
        bindings = entry["bindings"]
        assertions = entry["assertions"]
        if (
            not isinstance(name, str)
            or _NAME.fullmatch(name) is None
            or Path(name).name != name
            or name in names
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise VmEvidenceAuditError("evidence index name or digest is invalid")
        if (
            not isinstance(discriminator, dict)
            or set(discriminator) != {"field", "value"}
            or not all(isinstance(item, str) and item for item in discriminator.values())
        ):
            raise VmEvidenceAuditError("evidence discriminator is invalid")
        if not isinstance(bindings, list) or len(bindings) > 16:
            raise VmEvidenceAuditError("evidence bindings are invalid")
        if not isinstance(assertions, list) or not 1 <= len(assertions) <= 64:
            raise VmEvidenceAuditError("evidence assertions are invalid")
        for assertion in assertions:
            if (
                not isinstance(assertion, dict)
                or set(assertion) != {"field", "value"}
                or not isinstance(assertion["field"], str)
                or not assertion["field"]
                or not isinstance(assertion["value"], (str, int, float, bool))
            ):
                raise VmEvidenceAuditError("evidence assertion schema is invalid")
        for binding in bindings:
            if (
                not isinstance(binding, dict)
                or set(binding) != {"field", "target", "targetField"}
                or not all(isinstance(item, str) and item for item in binding.values())
            ):
                raise VmEvidenceAuditError("evidence binding schema is invalid")
        names.add(name)
    for entry in entries:
        for binding in entry["bindings"]:
            if binding["target"] not in names:
                raise VmEvidenceAuditError("evidence binding target is not indexed")
    return entries


def audit_vm_evidence(index_path: Path, evidence_root: Path) -> dict[str, Any]:
    _index_data, raw_index = _read_json(
        index_path.expanduser().resolve(), maximum=MAX_INDEX_BYTES, label="evidence index"
    )
    entries = _validated_index(raw_index)
    root = evidence_root.expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise VmEvidenceAuditError("evidence root must be a regular directory")

    indexed = {entry["name"]: entry for entry in entries}
    payloads: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    for entry in entries:
        name = entry["name"]
        checks = {"regularFile": False, "sha256": False, "json": False, "privacy": False}
        try:
            data, payload = _read_json(root / name, maximum=MAX_EVIDENCE_BYTES, label=name)
            checks["regularFile"] = True
            checks["sha256"] = hmac.compare_digest(
                hashlib.sha256(data).hexdigest(), entry["sha256"]
            )
            discriminator = entry["discriminator"]
            checks["json"] = _field(payload, discriminator["field"]) == discriminator["value"]
            checks["privacy"] = not _contains_windows_path(payload)
            payloads[name] = payload
        except (OSError, KeyError, VmEvidenceAuditError):
            pass
        for code, passed in checks.items():
            if not passed:
                blockers.append(f"{name}:{code}")
        results.append({"name": name, "checks": checks, "assertions": [], "bindings": []})

    result_by_name = {result["name"]: result for result in results}
    for entry in entries:
        payload = payloads.get(entry["name"])
        for assertion in entry["assertions"]:
            passed = False
            try:
                actual = _field(payload, assertion["field"])
                expected = assertion["value"]
                passed = type(actual) is type(expected) and actual == expected
            except (KeyError, TypeError):
                pass
            result_by_name[entry["name"]]["assertions"].append(
                {**assertion, "passed": passed}
            )
            if not passed:
                blockers.append(f"{entry['name']}:assertion:{assertion['field']}")

    for entry in entries:
        source = payloads.get(entry["name"])
        for binding in entry["bindings"]:
            passed = False
            target_entry = indexed[binding["target"]]
            target = payloads.get(binding["target"])
            try:
                actual = _field(source, binding["field"])
                expected = (
                    target_entry["sha256"]
                    if binding["targetField"] == "$sha256"
                    else _field(target, binding["targetField"])
                )
                passed = actual == expected
            except (KeyError, TypeError):
                pass
            binding_result = {
                "field": binding["field"],
                "target": binding["target"],
                "targetField": binding["targetField"],
                "passed": passed,
            }
            result_by_name[entry["name"]]["bindings"].append(binding_result)
            if not passed:
                blockers.append(f"{entry['name']}:binding:{binding['field']}")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.local-vm-evidence-audit",
        "ready": not blockers,
        "entries": results,
        "blockers": blockers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_vm_evidence(args.index, args.evidence_root)
    except VmEvidenceAuditError as exc:
        print(f"Echo VM evidence audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
