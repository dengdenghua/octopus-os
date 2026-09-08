from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from appliance.files import organization_store as module
from appliance.files.organization_store import OrganizationStore, StateLockError
from appliance.state_lock import StateDirectoryLock


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / "private" / "organizations")


@pytest.fixture
def record(tmp_path):
    return {
        "schema": "echo.files.organization-state.v1",
        "owner": "local:alice",
        "taskId": "synthetic-task",
        "workspacePath": str(tmp_path / "share"),
        "plan": {"planId": "a" * 64},
        "result": {"state": "prepared", "text": "合成票据"},
    }


def test_private_roundtrip_survives_new_instance_and_caller_mutation(store, record):
    expected = copy.deepcopy(record)
    store.save(record)
    record["result"]["text"] = "caller mutation"
    loaded = OrganizationStore(store.directory).load(expected["plan"]["planId"])
    assert loaded == expected
    loaded["result"]["state"] = "another mutation"
    assert store.load(expected["plan"]["planId"]) == expected
    if os.name == "nt":
        from tests.appliance.windows_acl_assertions import assert_private_windows_acl

        assert_private_windows_acl(store.directory)
        assert_private_windows_acl(store.directory / ("a" * 64 + ".json"))
    else:
        assert store.directory.stat().st_mode & 0o777 == 0o700
        assert (store.directory / ("a" * 64 + ".json")).stat().st_mode & 0o777 == 0o600


def test_outer_lease_nested_save_and_load_share_one_real_lock(store, record):
    with store.lease() as outer:
        assert isinstance(outer, StateDirectoryLock)
        with store.lease() as inner:
            assert inner is outer
            store.save(record)
            assert store.load("a" * 64) == record
        with pytest.raises(StateLockError):
            StateDirectoryLock.acquire(store.directory, exclusive=True)
    with StateDirectoryLock.acquire(store.directory, exclusive=True):
        pass


def test_competing_thread_or_instance_cannot_enter_writer_lease(store, record):
    competitor = OrganizationStore(store.directory)
    with store.lease(), ThreadPoolExecutor(max_workers=1) as pool:
        for candidate in (store, competitor):
            future = pool.submit(candidate.save, record)
            with pytest.raises(StateLockError):
                future.result(timeout=5)
    competitor.save(record)


def test_real_child_is_excluded_then_can_publish_after_release(store, record):
    script = """
import json, pathlib, sys
from appliance.files.organization_store import OrganizationStore, StateLockError
store=OrganizationStore(pathlib.Path(sys.argv[1]))
try:
    store.save(json.loads(sys.argv[2]))
except StateLockError:
    raise SystemExit(73)
"""
    args = [sys.executable, "-c", script, str(store.directory), json.dumps(record)]
    with store.lease():
        child = subprocess.run(args, capture_output=True, timeout=15)
        assert child.returncode == 73, child.stderr.decode(errors="replace")
    subprocess.run(args, capture_output=True, check=True, timeout=15)
    assert store.load("a" * 64) == record


def test_result_read_does_not_require_running_worker_lease(store, record):
    store.save(record)
    with store.lease(), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(store.load, "a" * 64).result(timeout=5) == record


def test_real_process_exit_releases_state_lock(store):
    code = """
import os, pathlib, sys
from appliance.files.organization_store import OrganizationStore
with OrganizationStore(pathlib.Path(sys.argv[1])).lease():
    os._exit(73)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(store.directory)],
        capture_output=True,
        timeout=15,
    )
    assert child.returncode == 73
    with store.lease():
        pass


@pytest.mark.parametrize("key", ["owner", "taskId", "workspacePath"])
def test_existing_binding_cannot_be_reassigned(store, record, key):
    store.save(record)
    changed = copy.deepcopy(record)
    changed[key] = "different-principal-or-task"
    with pytest.raises(PermissionError, match="binding cannot change"):
        store.save(changed)
    assert store.load("a" * 64) == record


@pytest.mark.parametrize(
    "changed",
    [
        {"schema": "wrong"},
        {"plan": []},
        {"plan": {"planId": "../escape"}},
        {"owner": ""},
        {"taskId": None},
        {"workspacePath": []},
    ],
)
def test_invalid_input_never_creates_receipt(store, record, changed):
    with pytest.raises(ValueError):
        store.save({**record, **changed})
    assert list(store.directory.glob("*.json")) == []


def test_missing_and_corrupt_receipts_are_explicit(store, record):
    with pytest.raises(FileNotFoundError):
        store.load("a" * 64)
    store.save(record)
    path = store.directory / ("a" * 64 + ".json")
    path.write_text('{"schema":"echo.files.organization-state.v1","plan":[]}', encoding="utf-8")
    with pytest.raises(ValueError):
        store.load("a" * 64)
    with pytest.raises(ValueError):
        store.save(record)


@pytest.mark.parametrize("failure", ["fsync", "publish"])
def test_failure_before_publish_preserves_old_receipt_and_removes_temp(
    store, record, monkeypatch, failure
):
    store.save(record)
    changed = {**record, "result": {"state": "running"}}

    def fail(*args):
        raise OSError("synthetic IO failure")

    if failure == "fsync":
        monkeypatch.setattr(module.os, "fsync", fail)
    else:
        monkeypatch.setattr(module, "_replace_upload_metadata", fail)
    with pytest.raises(OSError, match="synthetic IO failure") as caught:
        store.save(changed)
    if failure == "publish":
        assert caught.value.organization_committed is False
    assert store.load("a" * 64) == record
    assert list(store.directory.glob("*.tmp")) == []


def test_publish_then_error_reports_committed_evidence(store, record, monkeypatch):
    publish = module._replace_upload_metadata

    def commit_then_error(source, destination):
        publish(source, destination)
        raise OSError("synthetic post-rename durability error")

    monkeypatch.setattr(module, "_replace_upload_metadata", commit_then_error)
    with pytest.raises(OSError) as caught:
        store.save(record)
    assert caught.value.organization_committed is True
    assert store.load("a" * 64) == record


def test_cleanup_failure_preserves_primary_error_and_private_evidence(store, record, monkeypatch):
    def fail(*args):
        raise OSError("primary synthetic write failure")

    unlink = Path.unlink

    def refuse_staging(path, *args, **kwargs):
        if path.name.endswith(".tmp"):
            raise PermissionError("synthetic cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "fsync", fail)
    monkeypatch.setattr(Path, "unlink", refuse_staging)
    with pytest.raises(OSError, match="primary synthetic write failure") as caught:
        store.save(record)
    assert caught.value.organization_committed is False
    assert caught.value.organization_cleanup_failed is True
    staging = list(store.directory.glob("*.tmp"))
    assert len(staging) == 1
    if os.name == "nt":
        from tests.appliance.windows_acl_assertions import assert_private_windows_acl

        assert_private_windows_acl(staging[0])
    else:
        assert staging[0].stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_receipt_links_are_rejected_without_modifying_target(store, record, tmp_path, kind):
    outside = tmp_path / "external.json"
    outside.write_bytes(b"external fixture")
    path = store.directory / ("a" * 64 + ".json")
    if kind == "symlink":
        path.symlink_to(outside)
    else:
        os.link(outside, path)
    with pytest.raises(OSError):
        store.load("a" * 64)
    with pytest.raises(OSError):
        store.save(record)
    assert outside.read_bytes() == b"external fixture"


def test_state_ancestor_link_is_rejected_before_creation(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "alias").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        OrganizationStore(tmp_path / "alias" / "must-not-exist")
    assert list(outside.iterdir()) == []


def test_size_and_nan_are_rejected_without_replacing_record(store, record, monkeypatch):
    store.save(record)
    with pytest.raises(ValueError):
        store.save({**record, "result": float("nan")})
    monkeypatch.setattr(module, "_MAX_BYTES", 8)
    with pytest.raises(ValueError, match="oversized"):
        store.save(record)
