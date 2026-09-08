"""Synthetic, read-only planning through real safe file snapshots and extraction."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from appliance.data_access import DataAccessDenied, DataAccessScope, DataPathRule
from appliance.files import organization_plan as plans
from appliance.files.manager import FileManager

INVOICE = "电子发票\n开票日期：2026年09月05日\n价税合计（小写）：CNY 1,234.50\n"


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "nas"
    root.mkdir()
    (root / "receipts").mkdir()
    return FileManager(root)


def write(manager, relative, content=INVOICE):
    path = manager.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def build(manager, **kwargs):
    return plans.build_organization_record(
        manager=manager,
        actor=kwargs.pop("actor", "alice"),
        scope=kwargs.pop("scope", DataAccessScope.unrestricted("alice")),
        path=kwargs.pop("path", "receipts"),
        now=kwargs.pop("now", 1_800_000_000.0),
        **kwargs,
    )


def tree(root):
    return {
        str(path.relative_to(root)): (
            "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
    }


def test_real_text_preview_is_sealed_scoped_and_does_not_modify_files(library):
    source = write(library, "receipts/sub/invoice.txt")
    before = tree(library.root)
    record = build(library)
    plan = record["plan"]
    assert tree(library.root) == before
    assert record["schema"] == "echo.files.organization-state.v1"
    assert record["owner"] == "alice"
    assert record["workspacePath"] == str(library.root / "receipts")
    assert record["rootIdentity"] == {
        "dev": (library.root / "receipts").stat().st_dev,
        "ino": (library.root / "receipts").stat().st_ino,
    }
    UUID(record["taskId"])
    assert plan["schema"] == "echo.files.organize.plan.v1"
    assert plan["scanComplete"] is True and plan["ready"] is True
    assert plan["createdAt"].endswith("Z") and plan["expiresAt"].endswith("Z")
    assert record["expiresAtEpoch"] - record["createdAtEpoch"] == 900
    assert plan["approval"] == {"action": "files.organize.apply", "target": plan["planId"]}
    (item,) = plan["entries"]
    assert item["source"] == "receipts/sub/invoice.txt"
    assert item["target"] == "receipts/2026/09/invoice.txt"
    assert item["date"] == "2026-09-05"
    assert item["amount"] == "1234.50"
    snapshot = record["snapshots"][item["entryId"]]
    assert snapshot["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert snapshot["size"] == source.stat().st_size
    if os.name == "nt":
        assert "permissions" in snapshot
    assert plans.verify_record(record)
    assert str(library.root) not in json.dumps(plan)


def test_empty_root_convention_and_nonce_keep_separate_previews(library):
    write(library, "invoice.txt")
    first = build(library, path="")
    second = build(library, path="")
    assert first["plan"]["path"] == ""
    assert first["plan"]["entries"][0]["target"] == "2026/09/invoice.txt"
    assert first["plan"]["planId"] != second["plan"]["planId"]


@pytest.mark.parametrize(
    "path",
    [
        "/receipts",
        "../receipts",
        "receipts/../receipts",
        "receipts/.",
        "receipts//sub",
        "receipts\\sub",
        "C:/receipts",
        ".",
        " receipts",
        "receipts ",
        "receipts/.echo-trash",
        "receipts/NUL",
    ],
)
def test_selected_path_aliases_and_internal_paths_are_rejected(library, path):
    with pytest.raises(OSError):
        build(library, path=path)


def test_selected_missing_directory_and_file_are_rejected(library):
    write(library, "invoice.txt")
    for path in ("missing", "invoice.txt"):
        with pytest.raises(OSError):
            build(library, path=path)


def test_unknown_and_ambiguous_documents_stay_in_place_without_targets(library):
    write(library, "receipts/unknown.txt", "Meeting date: 2026-09-05")
    write(library, "receipts/ambiguous.txt", INVOICE + "开票日期：2026年08月31日")
    write(library, "receipts/sheet.xlsx", b"synthetic unsupported worksheet")
    record = build(library)
    items = {Path(item["source"]).name: item for item in record["plan"]["entries"]}
    assert items["unknown.txt"]["status"] == "needs_review"
    assert items["ambiguous.txt"]["reason"] == "conflicting_invoice_dates"
    assert items["sheet.xlsx"]["status"] == "unsupported"
    assert all(item["target"] is None for item in items.values())
    assert record["plan"]["scanComplete"] is True
    assert record["plan"]["ready"] is False


def test_existing_organized_and_colliding_files_are_not_silently_overwritten(library):
    write(library, "receipts/2026/09/done.txt")
    write(library, "receipts/sub/done.txt")
    write(library, "receipts/a/duplicate.txt")
    write(library, "receipts/b/duplicate.txt")
    write(library, "receipts/unique.txt")
    before = tree(library.root)
    record = build(library)
    items = {item["source"]: item for item in record["plan"]["entries"]}
    assert items["receipts/2026/09/done.txt"]["status"] == "already_organized"
    assert items["receipts/sub/done.txt"]["reason"] == "target_exists"
    assert items["receipts/a/duplicate.txt"]["reason"] == "target_collision"
    assert items["receipts/b/duplicate.txt"]["reason"] == "target_collision"
    assert items["receipts/unique.txt"]["status"] == "ready"
    assert record["plan"]["ready"] is True
    assert record["plan"]["summary"]["conflicts"] == 3
    assert tree(library.root) == before


def test_parent_file_blocks_target_without_creating_directories(library):
    write(library, "receipts/invoice.txt")
    write(library, "receipts/2026", b"existing parent file")
    record = build(library)
    item = next(
        item for item in record["plan"]["entries"] if item["source"].endswith("invoice.txt")
    )
    assert item["reason"] == "target_parent_not_directory"
    assert (library.root / "receipts/2026").read_bytes() == b"existing parent file"


def test_link_source_and_link_target_never_read_outside_tree(library, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text(INVOICE, encoding="utf-8")
    try:
        (library.root / "receipts/link.txt").symlink_to(secret)
        (library.root / "receipts/2026").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit synthetic symlink creation")
    write(library, "receipts/invoice.txt")
    record = build(library)
    items = {item["source"]: item for item in record["plan"]["entries"]}
    assert items["receipts/link.txt"]["reason"] == "unsafe_source"
    assert items["receipts/invoice.txt"]["reason"] == "unsafe_target"
    assert not any("secret" in item["source"] for item in items.values())
    with pytest.raises(OSError):
        build(library, path="receipts/2026")


def test_internal_uploads_receipts_and_trash_are_not_classified(library):
    write(library, "receipts/.echo-trash/deleted.txt")
    write(library, "receipts/.echo-upload-session.tmp")
    write(library, "receipts/normal.txt")
    record = build(library)
    assert [item["source"] for item in record["plan"]["entries"]] == ["receipts/normal.txt"]


def test_hardlinked_source_is_preserved_and_not_proposed(library):
    source = write(library, "receipts/invoice.txt")
    os.link(source, library.root / "receipts/alias.txt")
    record = build(library)
    assert all(item["reason"] == "unsafe_source" for item in record["plan"]["entries"])
    assert source.stat().st_nlink == 2
    assert record["snapshots"] == {}


def test_different_target_device_is_detected_before_approval(library):
    source = write(library, "receipts/invoice.txt")
    assert (
        plans._target_conflict(
            library.root,
            "receipts/2026/09/invoice.txt",
            source_device=source.stat().st_dev + 1,
        )
        == "cross_device_move"
    )


def test_directory_changed_during_extraction_is_not_reported_as_complete(library, monkeypatch):
    write(library, "receipts/invoice.txt")
    original = plans.extract_invoice_document

    def concurrent_create(data, extension):
        result = original(data, extension)
        write(library, "receipts/arrived-during-scan.txt")
        # Make the metadata boundary deterministic on coarse-timestamp filesystems.
        directory = library.root / "receipts"
        info = directory.stat()
        os.utime(directory, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        return result

    monkeypatch.setattr(plans, "extract_invoice_document", concurrent_create)
    record = build(library)
    assert record["plan"]["scanComplete"] is False
    assert record["plan"]["ready"] is False
    assert "scan_changed" in record["plan"]["blockers"]
    assert (library.root / "receipts/arrived-during-scan.txt").exists()


@pytest.mark.parametrize("limit", ["entries", "depth", "file", "total"])
def test_scan_and_read_limits_disable_apply_instead_of_claiming_full_coverage(
    library, monkeypatch, limit
):
    write(library, "receipts/first.txt")
    write(library, "receipts/sub/second.txt")
    if limit == "entries":
        monkeypatch.setattr(plans, "MAX_SCAN_ENTRIES", 1)
        blocker = "scan_limit"
    elif limit == "depth":
        monkeypatch.setattr(plans, "MAX_SCAN_DEPTH", 0)
        blocker = "depth_limit"
    elif limit == "file":
        monkeypatch.setattr(plans, "MAX_FILE_BYTES", 5)
        blocker = "file_size_limit"
    else:
        monkeypatch.setattr(plans, "MAX_TOTAL_BYTES", len(INVOICE.encode("utf-8")))
        blocker = "total_read_limit"
    record = build(library)
    assert record["plan"]["scanComplete"] is False
    assert record["plan"]["ready"] is False
    assert blocker in record["plan"]["blockers"]


def test_scan_error_preserves_partial_rows_but_forbids_apply(library, monkeypatch):
    write(library, "receipts/good.txt")
    write(library, "receipts/sub/bad.txt")
    real = plans.os.scandir

    def denied(path):
        if os.name == "nt" and str(path).endswith("sub"):
            raise PermissionError("secret synthetic path must not reach public diagnostics")
        if (
            os.name != "nt"
            and os.fstat(path).st_ino == (library.root / "receipts/sub").stat().st_ino
        ):
            raise PermissionError("private")
        return real(path)

    monkeypatch.setattr(plans.os, "scandir", denied)
    record = build(library)
    assert record["plan"]["entries"][0]["source"] == "receipts/good.txt"
    assert record["plan"]["scanComplete"] is False
    assert record["plan"]["blockers"] == ["scan_failed"]
    assert "secret synthetic" not in json.dumps(record)


def test_read_failure_does_not_masquerade_as_unrecognized_invoice(library, monkeypatch):
    write(library, "receipts/invoice.txt")

    def failed(*args, **kwargs):
        raise PermissionError("private path")

    monkeypatch.setattr(plans.file_io, "read_file_snapshot", failed)
    record = build(library)
    assert record["plan"]["entries"][0]["reason"] == "file_read_failed"
    assert record["plan"]["scanComplete"] is False
    assert record["snapshots"] == {}


def test_member_scope_checks_nested_denials_and_individual_write_access(library):
    write(library, "receipts/invoice.txt")
    blocked = DataAccessScope(
        "alice",
        False,
        (
            DataPathRule(("receipts",), "readWrite"),
            DataPathRule(("receipts", "private"), "none"),
        ),
        library.root,
    )
    with pytest.raises(DataAccessDenied):
        build(library, scope=blocked)
    readonly = DataAccessScope("alice", False, (DataPathRule(("receipts",), "read"),), library.root)
    record = build(library, scope=readonly)
    assert record["plan"]["entries"][0]["reason"] == "write_access_denied"
    assert record["plan"]["ready"] is False
    assert record["plan"]["scanComplete"] is True
    with pytest.raises(DataAccessDenied):
        build(library, actor="bob", scope=readonly)


def test_unauthorized_directory_is_rejected_before_filesystem_probe(library, monkeypatch):
    def should_not_probe(*args, **kwargs):
        raise AssertionError("No filesystem observation before read-tree authorization")

    monkeypatch.setattr(plans, "_directory_snapshot", should_not_probe)
    scope = DataAccessScope("alice", False, (), library.root)
    with pytest.raises(DataAccessDenied):
        build(library, scope=scope, path="ungranted")


def test_read_revocation_during_extraction_discards_captured_preview(library, monkeypatch):
    write(library, "receipts/invoice.txt")
    original = plans.extract_invoice_document
    revoked = False

    def extract(data, extension):
        nonlocal revoked
        result = original(data, extension)
        revoked = True
        return result

    original_read = DataAccessScope.require_read

    def require_read(scope, path):
        if revoked:
            raise DataAccessDenied("revoked")
        return original_read(scope, path)

    monkeypatch.setattr(plans, "extract_invoice_document", extract)
    monkeypatch.setattr(DataAccessScope, "require_read", require_read)
    with pytest.raises(DataAccessDenied):
        build(library)


def test_write_revocation_at_final_check_removes_ready_status(library, monkeypatch):
    write(library, "receipts/invoice.txt")
    calls = 0
    original = DataAccessScope.require_write

    def require_write(scope, path):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise DataAccessDenied("revoked")
        return original(scope, path)

    monkeypatch.setattr(DataAccessScope, "require_write", require_write)
    record = build(library)
    assert record["plan"]["entries"][0]["reason"] == "write_access_denied"
    assert record["plan"]["ready"] is False


@pytest.mark.parametrize(
    "changed",
    [
        "owner",
        "workspacePath",
        "rootIdentity",
        "snapshots",
        "createdAtEpoch",
        "expiresAtEpoch",
        "nonce",
        "entries",
        "direction",
        "sourcePlanId",
    ],
)
def test_seal_binds_every_execution_relevant_input(library, changed):
    write(library, "receipts/invoice.txt")
    record = build(library)
    changed_record = copy.deepcopy(record)
    if changed in {"entries", "direction", "sourcePlanId"}:
        if changed == "entries":
            changed_record["plan"]["entries"][0]["target"] = "somewhere/else.txt"
        else:
            changed_record["plan"][changed] = "tampered"
    elif changed in {"createdAtEpoch", "expiresAtEpoch"}:
        changed_record[changed] += 1
    else:
        changed_record[changed] = {} if changed in {"rootIdentity", "snapshots"} else "tampered"
    assert not plans.verify_record(changed_record)
    assert plans.verify_record(record)


def test_sealing_is_copying_and_dynamic_task_results_do_not_change_identity(library):
    write(library, "receipts/invoice.txt")
    record = build(library)
    changed = copy.deepcopy(record)
    changed["taskId"] = "runtime-assigned-task"
    changed["state"] = "running"
    changed["result"] = {"state": "partial"}
    changed["plan"]["result"] = {"state": "partial"}
    assert plans.verify_record(changed)
    changed["plan"]["direction"] = "undo"
    changed["plan"]["approval"]["action"] = "files.organize.undo"
    changed["plan"]["sourcePlanId"] = record["plan"]["planId"]
    before = copy.deepcopy(changed)
    sealed = plans.seal_record(changed)
    assert changed == before
    assert sealed["plan"]["planId"] != record["plan"]["planId"]
    assert plans.verify_record(sealed)


@pytest.mark.parametrize(
    "record",
    [
        None,
        {},
        {"plan": None},
        {"schema": "bad", "plan": {}},
        {"plan": {"planId": "x", "approval": None}},
    ],
)
def test_malformed_record_verification_never_raises(record):
    assert plans.verify_record(record) is False


def test_no_pdf_parser_is_explicitly_reported_without_installing_dependencies(library, monkeypatch):
    from appliance.agent_api import documents

    write(library, "receipts/invoice.pdf", b"%PDF synthetic bytes")
    monkeypatch.setattr(documents.importlib.util, "find_spec", lambda name: None)
    record = build(library)
    (item,) = record["plan"]["entries"]
    assert item["status"] == "needs_review"
    assert item["reason"] == "extraction_unavailable"
    assert record["plan"]["scanComplete"] is True
    assert record["plan"]["ready"] is False
