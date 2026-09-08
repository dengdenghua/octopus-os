"""Real synthetic files and TaskSupervisor for approved provider operations."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from appliance.data_access import DataAccessScope, DataPathRule
from appliance.files.manager import FileManager
from appliance.files.organization import FileOrganizationService, OrganizationError
from appliance.files.organization_plan import seal_record
from runtime.platform.process.task_supervisor import TaskSupervisor


class Policy:
    def __init__(self, root):
        self.root = root
        self.permission = "readWrite"

    def scope_for_actor(self, actor):
        return DataAccessScope(
            actor=actor,
            operator=False,
            root=self.root,
            rules=(DataPathRule(("Invoices",), self.permission),),
        )


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "nas"
    folder = root / "Invoices"
    folder.mkdir(parents=True)
    policy = Policy(root)
    state = tmp_path / "state"
    state.mkdir()
    tasks = TaskSupervisor.from_path(state / "tasks.json", holder_id="test-provider")
    service = FileOrganizationService(
        FileManager(root), state, data_access=policy, supervisor=tasks
    )
    return service, folder, policy, state, tasks


def invoice(folder: Path, name="one.txt", date="2026-09-05"):
    path = folder / name
    path.write_text(f"Invoice\nInvoice Date: {date}\nTotal: CNY 42.00\n", encoding="utf-8")
    return path


def test_real_plan_move_receipt_reopen_undo_and_original_retry(fixture):
    service, folder, policy, state, tasks = fixture
    source = invoice(folder)
    expected = source.read_bytes()
    plan = service.create_plan("local:alice", "Invoices")
    assert plan["ready"] is True
    assert source.read_bytes() == expected
    with pytest.raises(OrganizationError, match="尚未开始") as missing:
        service.get_result("local:alice", plan["planId"])
    assert missing.value.status == 404
    result = service.apply("local:alice", plan["planId"])
    target = folder / "2026/09/one.txt"
    assert result["state"] == "completed"
    assert result["counts"]["moved"] == 1
    assert target.read_bytes() == expected and not source.exists()
    assert result["results"][0]["actualPath"] == "Invoices/2026/09/one.txt"
    assert str(tasks.store.get(result["taskId"]).status) == "completed"
    reopened = FileOrganizationService(service.manager, state, data_access=policy, supervisor=tasks)
    assert reopened.get_result("local:alice", plan["planId"])["state"] == "completed"
    assert reopened.apply("local:alice", plan["planId"])["counts"]["moved"] == 1
    undo = reopened.create_undo_plan("local:alice", plan["planId"])
    assert undo["approval"] == {"action": "files.organize.undo", "target": undo["planId"]}
    assert reopened.apply("local:alice", undo["planId"])["state"] == "completed"
    assert source.read_bytes() == expected and not target.exists()
    original_retry = reopened.apply("local:alice", plan["planId"])
    assert original_retry["results"][0]["actualPath"] == "Invoices/one.txt"
    assert source.read_bytes() == expected and not target.exists()


def test_changed_file_conflict_preserves_independent_success_and_retry(fixture):
    service, folder, _, _, _ = fixture
    changed = invoice(folder, "a.txt")
    invoice(folder, "b.txt")
    plan = service.create_plan("local:alice", "Invoices")
    changed.write_text("later user edit", encoding="utf-8")
    result = service.apply("local:alice", plan["planId"])
    assert result["state"] == "partial"
    assert result["counts"]["moved"] == 1
    assert result["counts"]["conflicts"] == 1
    target = folder / "2026/09/b.txt"
    identity = target.stat().st_ino
    retry = service.apply("local:alice", plan["planId"])
    assert retry["counts"]["moved"] == 1
    assert target.stat().st_ino == identity
    assert changed.read_text() == "later user edit"


def test_owner_scope_and_plan_tamper_fail_before_file_write(fixture):
    service, folder, policy, _, _ = fixture
    source = invoice(folder)
    plan = service.create_plan("local:alice", "Invoices")
    with pytest.raises(OrganizationError) as denied:
        service.get_plan("local:bob", plan["planId"])
    assert denied.value.status == 403
    policy.permission = "read"
    result = service.apply("local:alice", plan["planId"])
    assert result["counts"]["moved"] == 0
    assert source.exists()
    policy.permission = "none"
    with pytest.raises(OrganizationError) as revoked:
        service.get_result("local:alice", plan["planId"])
    assert revoked.value.status == 403
    policy.permission = "readWrite"
    record = service.store.load(plan["planId"])
    record["plan"]["entries"][0]["target"] = "Invoices/changed.txt"
    service.store.save(record)
    with pytest.raises(OrganizationError) as tampered:
        service.apply("local:alice", plan["planId"])
    assert tampered.value.error == "plan_changed"
    assert source.exists()


def test_undo_does_not_overwrite_later_original_or_edited_target(fixture):
    service, folder, _, _, _ = fixture
    first = invoice(folder, "a.txt")
    invoice(folder, "b.txt")
    plan = service.create_plan("local:alice", "Invoices")
    service.apply("local:alice", plan["planId"])
    first.write_text("new original", encoding="utf-8")
    target = folder / "2026/09/b.txt"
    target.write_text("edited target", encoding="utf-8")
    undo = service.create_undo_plan("local:alice", plan["planId"])
    assert undo["ready"] is False
    assert undo["summary"]["conflicts"] == 2
    with pytest.raises(OrganizationError):
        service.apply("local:alice", undo["planId"])
    assert first.read_text() == "new original"
    assert target.read_text() == "edited target"


def test_expired_first_execution_rejected(fixture):
    service, folder, _, _, _ = fixture
    source = invoice(folder)
    plan = service.create_plan("local:alice", "Invoices")
    record = service.store.load(plan["planId"])
    record["createdAtEpoch"], record["expiresAtEpoch"] = 1, 901
    old = seal_record(record)
    service.store.save(old)
    with pytest.raises(OrganizationError) as expired:
        service.apply("local:alice", old["plan"]["planId"])
    assert expired.value.error == "plan_expired"
    assert source.exists()


def test_cancel_waits_for_current_move_and_preserves_it(fixture, monkeypatch):
    service, folder, _, _, _ = fixture
    invoice(folder, "a.txt")
    invoice(folder, "b.txt")
    plan = service.create_plan("local:alice", "Invoices")
    entered, release = threading.Event(), threading.Event()
    original_move = service._move

    def pause(*args):
        result = original_move(*args)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(service, "_move", pause)
    results, errors = [], []

    def run():
        try:
            results.append(service.apply("local:alice", plan["planId"]))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(10)
    try:
        service.cancel("local:alice", plan["planId"])
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert results[0]["state"] == "cancelled"
    assert results[0]["counts"]["moved"] == 1
    assert results[0]["counts"]["pending"] == 1
    assert len(list((folder / "2026/09").glob("*.txt"))) == 1


def test_post_commit_receipt_failure_never_reports_not_moved(fixture, monkeypatch):
    service, folder, _, _, _ = fixture
    invoice(folder)
    plan = service.create_plan("local:alice", "Invoices")
    save = service.store.save

    def fail_after_commit(record):
        if any(row["committed"] is True for row in record.get("result", {}).get("results", [])):
            raise OSError("synthetic receipt storage full")
        save(record)

    monkeypatch.setattr(service.store, "save", fail_after_commit)
    result = service.apply("local:alice", plan["planId"])
    assert result["state"] == "uncertain"
    assert result["receiptRecorded"] is False
    assert result["results"][0]["committed"] is True
    assert (folder / "2026/09/one.txt").exists()


def test_reopening_reconciles_commit_when_receipt_was_not_saved(fixture, monkeypatch):
    service, folder, policy, state, tasks = fixture
    invoice(folder)
    plan = service.create_plan("local:alice", "Invoices")
    original_save = service.store.save

    def fail_commit(record):
        if any(row["committed"] is True for row in record.get("result", {}).get("results", [])):
            raise OSError("synthetic post-publication receipt failure")
        original_save(record)

    monkeypatch.setattr(service.store, "save", fail_commit)
    assert service.apply("local:alice", plan["planId"])["state"] == "uncertain"
    reopened = FileOrganizationService(service.manager, state, data_access=policy, supervisor=tasks)
    result = reopened.get_result("local:alice", plan["planId"])
    assert result["state"] == "partial" and result["finalizationPending"] is True
    assert str(tasks.store.get(result["taskId"]).status) == "failed"
    assert result["results"][0]["committed"] is True
    persisted = reopened.store.load(plan["planId"])
    assert persisted["result"]["results"][0]["committed"] is True

    def no_more_moves(*args, **kwargs):
        raise AssertionError("already committed file must not be moved on finalization")

    monkeypatch.setattr(reopened, "_move", no_more_moves)
    finalized = reopened.apply("local:alice", plan["planId"])
    assert finalized["state"] == "completed" and finalized["finalizationPending"] is False
    assert str(tasks.store.get(result["taskId"]).status) == "completed"
    assert reopened.create_undo_plan("local:alice", plan["planId"])["ready"] is True


def test_audit_failure_after_file_commit_remains_visible(fixture):
    service, folder, _, _, tasks = fixture
    invoice(folder)

    class Audit:
        def record(self, **kwargs):
            if kwargs["outcome"] != "attempted":
                raise OSError("synthetic audit storage full")

    service.audit = Audit()
    plan = service.create_plan("local:alice", "Invoices")
    result = service.apply("local:alice", plan["planId"])
    assert result["state"] == "partial" and result["auditRecorded"] is False
    assert result["finalizationPending"] is True
    assert result["results"][0]["committed"] is True
    assert str(tasks.store.get(result["taskId"]).status) == "failed"
    assert service.get_result("local:alice", plan["planId"])["state"] == "partial"


def test_directory_identity_change_stops_before_touching_replacement(fixture):
    service, folder, _, _, _ = fixture
    original = invoice(folder)
    plan = service.create_plan("local:alice", "Invoices")
    moved_folder = folder.with_name("OldInvoices")
    folder.rename(moved_folder)
    folder.mkdir()
    replacement = invoice(folder)
    with pytest.raises(OrganizationError) as changed:
        service.apply("local:alice", plan["planId"])
    assert changed.value.error == "root_changed"
    assert replacement.exists() and (moved_folder / original.name).exists()


def test_recent_plan_discovery_filters_owner_and_current_scope(fixture):
    service, folder, policy, _, _ = fixture
    invoice(folder)
    alice = service.create_plan("local:alice", "Invoices")
    bob = service.create_plan("local:bob", "Invoices")
    assert [row["planId"] for row in service.list_plans("local:alice", "Invoices")["plans"]] == [
        alice["planId"]
    ]
    assert [row["planId"] for row in service.list_plans("local:bob", "Invoices")["plans"]] == [
        bob["planId"]
    ]
    policy.permission = "none"
    with pytest.raises(OrganizationError) as revoked:
        service.list_plans("local:alice", "Invoices")
    assert revoked.value.status == 403


def _download_fixture(fixture):
    service, folder, *_ = fixture
    source = invoice(folder, "原件 2026.txt")
    data = source.read_bytes()
    plan = service.create_plan("local:alice", "Invoices")
    result = service.apply("local:alice", plan["planId"])
    row = result["results"][0]
    return plan, row, source, service.manager.root / row["actualPath"], data


def test_original_download_verifies_bytes_and_both_plans_follow_completed_undo(fixture):
    service, *_ = fixture
    plan, row, source, target, data = _download_fixture(fixture)
    item = service.read_original("local:alice", plan["planId"], row["entryId"])
    assert item.filename == source.name and item.data == data
    assert isinstance(item.data, bytes) and item.sha256 == hashlib.sha256(data).hexdigest()
    undo = service.create_undo_plan("local:alice", plan["planId"])
    restored = service.apply("local:alice", undo["planId"])
    undo_id = restored["results"][0]["entryId"]
    assert len(row["entryId"]) == 24 and len(undo_id) == 64
    assert service.read_original("local:alice", plan["planId"], row["entryId"]).data == data
    assert service.read_original("local:alice", undo["planId"], undo_id).data == data
    assert source.read_bytes() == data and not target.exists()


@pytest.mark.parametrize("change", ["content", "mtime", "oversize"])
def test_original_download_rechecks_changes_after_result_was_read(fixture, change):
    service, *_ = fixture
    plan, row, _, target, data = _download_fixture(fixture)
    assert service.get_result("local:alice", plan["planId"])["results"][0]["actualPath"]
    before = target.stat()
    if change == "content":
        target.write_bytes(b"x" * len(data))
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "mtime":
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
    else:
        with target.open("ab") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(OrganizationError) as changed:
        service.read_original("local:alice", plan["planId"], row["entryId"])
    assert changed.value.status == 409 and changed.value.error == "original_unavailable"


def test_original_download_rejects_replacement_between_internal_readback_and_byte_read(
    fixture, monkeypatch
):
    import appliance.files.organization as provider

    service, *_ = fixture
    plan, row, _, target, data = _download_fixture(fixture)
    read = provider.read_file_snapshot
    before = target.stat()

    def replaced(*args, **kwargs):
        target.write_bytes(b"z" * len(data))
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        return read(*args, **kwargs)

    monkeypatch.setattr(provider, "read_file_snapshot", replaced)
    with pytest.raises(OrganizationError) as changed:
        service.read_original("local:alice", plan["planId"], row["entryId"])
    assert changed.value.status == 409


def test_original_download_rejects_link_replacement(fixture, tmp_path):
    service, *_ = fixture
    plan, row, _, target, _ = _download_fixture(fixture)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"unrelated synthetic content")
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit synthetic links")
    with pytest.raises(OrganizationError) as linked:
        service.read_original("local:alice", plan["planId"], row["entryId"])
    assert linked.value.status == 409
    assert outside.read_bytes() == b"unrelated synthetic content"


def test_original_download_rejects_selected_directory_identity_replacement(fixture):
    service, folder, *_ = fixture
    plan, row, _, target, data = _download_fixture(fixture)
    old = folder.with_name("OldInvoices")
    folder.rename(old)
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    with pytest.raises(OrganizationError) as replaced:
        service.read_original("local:alice", plan["planId"], row["entryId"])
    assert replaced.value.status == 409


@pytest.mark.parametrize("when", ["before", "after_bytes"])
def test_original_download_uses_fresh_read_scope_and_rejects_other_owner(
    fixture, monkeypatch, when
):
    import appliance.files.organization as provider

    service, _, policy, *_ = fixture
    plan, row, _, _, _ = _download_fixture(fixture)
    with pytest.raises(OrganizationError) as other:
        service.read_original("local:bob", plan["planId"], row["entryId"])
    assert other.value.status == 403
    if when == "before":
        policy.permission = "none"
    else:
        read = provider.read_file_snapshot

        def revoke_after_read(*args, **kwargs):
            result = read(*args, **kwargs)
            policy.permission = "none"
            return result

        monkeypatch.setattr(provider, "read_file_snapshot", revoke_after_read)
    with pytest.raises(OrganizationError) as revoked:
        service.read_original("local:alice", plan["planId"], row["entryId"])
    assert revoked.value.status == 403


def test_original_download_requires_a_result_and_an_exact_known_entry(fixture):
    service, folder, *_ = fixture
    source = invoice(folder)
    plan = service.create_plan("local:alice", "Invoices")
    with pytest.raises(OrganizationError) as no_result:
        service.read_original("local:alice", plan["planId"], plan["entries"][0]["entryId"])
    assert no_result.value.status == 409
    service.apply("local:alice", plan["planId"])
    for entry_id in ("0" * 24, "../../outside", "A" * 24):
        with pytest.raises(OrganizationError) as unknown:
            service.read_original("local:alice", plan["planId"], entry_id)
        assert unknown.value.status == 409
    assert not source.exists()


_CRASH_PROVIDER = r"""
import os
import sys
from pathlib import Path
from appliance.files.manager import FileManager
from appliance.files.organization import FileOrganizationService
from appliance.files import organization_io
from runtime.platform.process.task_supervisor import TaskSupervisor
state, root, plan_id, phase = sys.argv[1:]
supervisor = TaskSupervisor.from_path(Path(state) / 'tasks.json', holder_id='crashing-provider')
service = FileOrganizationService(FileManager(Path(root)), Path(state), supervisor=supervisor)
original = organization_io._rename_noreplace
renames = 0
def crash(*args, **kwargs):
    global renames
    if phase == 'before' and renames == 0:
        os._exit(71)
    original(*args, **kwargs)
    renames += 1
    if phase == 'captured' and renames == 1:
        os._exit(72)
    if phase == 'published' and renames == 2:
        os._exit(73)
organization_io._rename_noreplace = crash
service.apply('local:alice', plan_id)
raise RuntimeError('crash point not reached')
"""


@pytest.mark.parametrize("phase,exit_code", [("before", 71), ("captured", 72), ("published", 73)])
def test_real_process_exit_recovers_without_moving_on_get(fixture, phase, exit_code):
    service, folder, policy, state, tasks = fixture
    original = invoice(folder)
    expected = original.read_bytes()
    plan = service.create_plan("local:alice", "Invoices")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    child = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            _CRASH_PROVIDER,
            str(state),
            str(service.manager.root),
            plan["planId"],
            phase,
        ],
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert child.returncode == exit_code, child.stderr.decode("utf-8", "replace")
    before = {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }
    restarted = FileOrganizationService(
        service.manager, state, data_access=policy, supervisor=tasks
    )
    result = restarted.get_result("local:alice", plan["planId"])
    after = {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert list(after.values()) == [expected]
    row = result["results"][0]
    if phase == "published":
        assert result["state"] == "partial" and row["committed"] is True
        assert result["finalizationPending"] is True and result["executionComplete"] is False
        assert str(tasks.store.get(result["taskId"]).status) == "running"
        assert row["actualPath"] == "Invoices/2026/09/one.txt"
    else:
        assert result["state"] == "failed" and row["committed"] is False
        assert row["reason"] == ("source_ready" if phase == "before" else "captured_pending_ready")
        assert row["actualPath"] == ("Invoices/one.txt" if phase == "before" else None)
    # A dead process can leave a still-valid task lease. A new execution
    # must respect it even though file observation is already recoverable.
    with pytest.raises(OrganizationError) as leased:
        restarted.apply("local:alice", plan["planId"])
    assert leased.value.error == "task_unavailable"
