"""Synthetic real-filesystem checks for conditional rollback publication."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from runtime.memory.runtime_state import _file_rollback_io as io


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _restore(target: Path, text: str = "restored\r\n文本\n", **kwargs) -> None:
    options = {
        "expected_sha256": _sha(target.read_bytes()) if target.is_file() else "",
        "hash_mode": "bytes-v1",
        "require_absent": False,
    }
    options.update(kwargs)
    io.atomic_restore_text(target, text, **options)


def _no_staging(parent: Path) -> None:
    assert list(parent.glob(".echo-rollback-*")) == []


def test_existing_replacement_preserves_exact_bytes_and_changes_file_identity(tmp_path):
    target = tmp_path / "文档.txt"
    target.write_bytes(b"current\r\ncontent\n")
    old_identity = target.stat().st_ino
    _restore(target)
    assert target.read_bytes() == "restored\r\n文本\n".encode()
    assert target.stat().st_ino != old_identity
    _no_staging(tmp_path)


def test_absent_publish_is_complete_utf8_and_leaves_no_staging(tmp_path):
    target = tmp_path / "new.txt"
    _restore(target, require_absent=True)
    assert target.read_bytes() == "restored\r\n文本\n".encode()
    _no_staging(tmp_path)


@pytest.mark.parametrize("mode", ["bytes-v1", "text-v1"])
def test_hash_modes_support_old_newline_normalization_without_changing_restore(tmp_path, mode):
    target = tmp_path / "lines.txt"
    target.write_bytes(b"a\r\nb\rc\n")
    expected = b"a\nb\nc\n" if mode == "text-v1" else target.read_bytes()
    _restore(target, "before\r\n", expected_sha256=_sha(expected), hash_mode=mode)
    assert target.read_bytes() == b"before\r\n"


@pytest.mark.parametrize("operation", ["restore", "remove"])
@pytest.mark.parametrize("mode", ["bytes-v1", "text-v1"])
@pytest.mark.parametrize("matches", [True, False])
def test_streamed_file_hash_handles_split_newlines_and_utf8(
    tmp_path, monkeypatch, operation, mode, matches
):
    if operation == "remove" and not io.guarded_remove_supported():
        pytest.skip("requires Windows exclusive file handles")
    chunk_size = 64 * 1024
    raw = b"a" * (chunk_size - 1) + b"\r\n"
    raw += b"b" * (2 * chunk_size - 1 - len(raw)) + "文".encode()
    raw += b"c" * (3 * chunk_size - 1 - len(raw)) + b"\rz\r"
    target = tmp_path / "chunk-boundaries.txt"
    target.write_bytes(raw)
    expected = (
        raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        if mode == "text-v1"
        else raw
    )
    digest = _sha(expected if matches else expected + b"external change")
    stream_digest = io._stream_digest
    reads = []

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size):
            assert size == chunk_size
            reads.append(size)
            return self.stream.read(size)

    def checked_digest(stream, hash_mode):
        result = stream_digest(BoundedReader(stream), hash_mode)
        assert not stream.closed
        return result

    monkeypatch.setattr(io, "_stream_digest", checked_digest)

    def run():
        if operation == "restore":
            _restore(target, "restored\r\n", expected_sha256=digest, hash_mode=mode)
        else:
            io.guarded_remove_created_file(target, expected_sha256=digest, hash_mode=mode)

    if matches:
        run()
        if operation == "restore":
            assert target.read_bytes() == b"restored\r\n"
        else:
            assert not target.exists()
    else:
        with pytest.raises(io.RollbackConflict, match="hash_mismatch"):
            run()
        assert target.read_bytes() == raw
    assert len(reads) >= 5
    _no_staging(tmp_path)


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"expected_sha256": "wrong"}, "hash_mismatch"),
        ({"expected_sha256": ""}, "missing_precondition"),
        ({"require_absent": True}, "existence_mismatch"),
    ],
)
def test_initial_conflicts_leave_existing_file_and_parent_untouched(tmp_path, options, reason):
    target = tmp_path / "document.txt"
    target.write_bytes(b"external content")
    with pytest.raises(io.RollbackConflict) as raised:
        _restore(target, **options)
    assert raised.value.reason == reason
    assert target.read_bytes() == b"external content"
    _no_staging(tmp_path)


def test_deleted_target_does_not_reappear_when_existing_was_required(tmp_path):
    target = tmp_path / "missing.txt"
    with pytest.raises(io.RollbackConflict, match="existence_mismatch"):
        _restore(target, expected_sha256=_sha(b"old"))
    assert not target.exists()
    _no_staging(tmp_path)


def test_missing_parent_is_not_created(tmp_path):
    target = tmp_path / "missing" / "new.txt"
    with pytest.raises(FileNotFoundError):
        _restore(target, require_absent=True)
    assert not target.parent.exists()


def test_directory_is_never_replaced(tmp_path):
    target = tmp_path / "directory"
    target.mkdir()
    with pytest.raises(io.RollbackConflict, match="not_regular_file"):
        _restore(target, expected_sha256=_sha(b""))
    assert target.is_dir()
    _no_staging(tmp_path)


def test_external_edit_while_staging_is_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    write = io._write_staged

    def changed(*args):
        write(*args)
        target.write_bytes(b"changed by another editor")

    monkeypatch.setattr(io, "_write_staged", changed)
    with pytest.raises(io.RollbackConflict, match="hash_mismatch"):
        _restore(target)
    assert target.read_bytes() == b"changed by another editor"
    _no_staging(tmp_path)


def test_same_content_different_inode_is_a_conflict(tmp_path, monkeypatch):
    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    write = io._write_staged

    def changed(*args):
        write(*args)
        replacement = tmp_path / "external.txt"
        replacement.write_bytes(b"current")
        os.replace(replacement, target)

    monkeypatch.setattr(io, "_write_staged", changed)
    with pytest.raises(io.RollbackConflict, match="identity_or_permissions_changed"):
        _restore(target)
    assert target.read_bytes() == b"current"
    _no_staging(tmp_path)


def test_absent_publish_cannot_overwrite_file_created_after_last_check(tmp_path, monkeypatch):
    target = tmp_path / "document.txt"
    publish = io._publish_absent

    def race(staged, destination):
        destination.write_bytes(b"external winner")
        publish(staged, destination)

    monkeypatch.setattr(io, "_publish_absent", race)
    with pytest.raises(io.RollbackConflict, match="existence_mismatch"):
        _restore(target, require_absent=True)
    assert target.read_bytes() == b"external winner"
    _no_staging(tmp_path)


@pytest.mark.parametrize("absent", [False, True])
def test_fsync_failure_propagates_original_error_without_publication(tmp_path, monkeypatch, absent):
    target = tmp_path / "document.txt"
    if not absent:
        target.write_bytes(b"current")
    failure = OSError("synthetic fsync failure")

    def fail(_fd):
        raise failure

    monkeypatch.setattr(io.os, "fsync", fail)
    with pytest.raises(OSError) as raised:
        _restore(target, require_absent=absent)
    assert raised.value is failure
    assert not getattr(failure, "rollback_committed", False)
    assert (not target.exists()) if absent else target.read_bytes() == b"current"
    _no_staging(tmp_path)


def test_replace_failure_propagates_original_error_without_direct_write_fallback(
    tmp_path, monkeypatch
):
    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    failure = PermissionError("synthetic replace failure")

    def fail(*_args):
        raise failure

    monkeypatch.setattr(io, "_replace_existing", fail)
    with pytest.raises(PermissionError) as raised:
        _restore(target)
    assert raised.value is failure
    assert target.read_bytes() == b"current"
    _no_staging(tmp_path)


def test_cleanup_failure_after_publication_is_marked_committed(tmp_path, monkeypatch):
    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    failure = PermissionError("synthetic cleanup failure")

    def fail(*_args):
        raise failure

    monkeypatch.setattr(io, "_cleanup", fail)
    with pytest.raises(PermissionError) as raised:
        _restore(target)
    assert raised.value is failure
    assert failure.rollback_committed is True
    assert target.read_bytes() == "restored\r\n文本\n".encode()
    assert Path(failure.rollback_backup_path).is_relative_to(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="requires actual Win32 filesystem APIs")
def test_windows_real_acl_is_preserved_and_staging_is_private(tmp_path, monkeypatch):
    from tests.appliance.windows_acl_assertions import read_windows_acl

    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    before = read_windows_acl(target)
    create = io._windows_create_private_file
    checked = []

    def inspect(path):
        fd = create(path)
        assert path.parent == tmp_path
        acl = read_windows_acl(path)
        assert acl["protected"] is True
        assert acl["owner"] == acl["current"]
        assert {rule["sid"] for rule in acl["rules"]} == {acl["current"]}
        assert path.stat().st_size == 0  # private at creation, before payload bytes
        checked.append(True)
        return fd

    monkeypatch.setattr(io, "_windows_create_private_file", inspect)
    _restore(target)
    assert checked == [True]
    assert read_windows_acl(target) == before
    _no_staging(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="requires actual Win32 filesystem APIs")
def test_windows_real_readonly_replace_failure_keeps_original(tmp_path):
    target = tmp_path / "readonly.txt"
    target.write_bytes(b"current")
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            _restore(target)
        assert target.read_bytes() == b"current"
        _no_staging(tmp_path)
    finally:
        target.chmod(stat.S_IREAD | stat.S_IWRITE)


def _set_windows_acl(path: Path, sddl: str) -> None:
    api = io._windows_api()
    descriptor = api.c.c_void_p()
    io._win_check(
        api.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            1,
            api.c.byref(descriptor),
            None,
        )
    )
    try:
        io._win_check(api.security.SetFileSecurityW(str(path), 5, descriptor))
    finally:
        api.kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="requires actual Win32 filesystem APIs")
def test_windows_explicit_protected_read_sharing_acl_is_preserved(tmp_path):
    from tests.appliance.windows_acl_assertions import read_windows_acl

    target = tmp_path / "shared.txt"
    target.write_bytes(b"current")
    sid = io._windows_sid()
    _set_windows_acl(target, f"O:{sid}D:P(A;;FA;;;{sid})(A;;FR;;;WD)")
    before = read_windows_acl(target)
    _restore(target)
    assert read_windows_acl(target) == before
    assert before["protected"] is True
    assert any(rule["sid"] == "S-1-1-0" for rule in before["rules"])


@pytest.mark.skipif(os.name != "nt", reason="requires actual Win32 filesystem APIs")
def test_windows_acl_change_during_staging_is_a_conflict(tmp_path, monkeypatch):
    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    write = io._write_staged
    sid = io._windows_sid()

    def changed(*args):
        write(*args)
        _set_windows_acl(target, f"O:{sid}D:P(A;;FA;;;{sid})")

    monkeypatch.setattr(io, "_write_staged", changed)
    with pytest.raises(io.RollbackConflict, match="identity_or_permissions_changed"):
        _restore(target)
    assert target.read_bytes() == b"current"
    _no_staging(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="requires actual Win32 filesystem APIs")
def test_windows_post_commit_acl_failure_is_reported_and_evidence_is_private(tmp_path, monkeypatch):
    from tests.appliance.windows_acl_assertions import read_windows_acl

    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    api = io._windows_api()
    set_security = api.security.SetFileSecurityW

    def fail_published(path, information, descriptor):
        if path == str(target) and information == 7:
            api.c.set_last_error(5)
            return False
        return set_security(path, information, descriptor)

    monkeypatch.setattr(api.security, "SetFileSecurityW", fail_published)
    with pytest.raises(OSError) as raised:
        _restore(target)
    assert raised.value.winerror == 5
    assert raised.value.rollback_committed is True
    assert raised.value.rollback_evidence_private is True
    assert target.read_bytes() == "restored\r\n文本\n".encode()
    backup = Path(raised.value.rollback_backup_path)
    assert backup.read_bytes() == b"current"
    acl = read_windows_acl(backup)
    assert acl["protected"] is True
    assert {rule["sid"] for rule in acl["rules"]} == {acl["current"]}


@pytest.mark.skipif(os.name != "nt", reason="requires actual Win32 filesystem APIs")
@pytest.mark.parametrize("error", [1176, 1177])
def test_windows_uncertain_replace_retains_real_recovery_files(tmp_path, monkeypatch, error):
    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    failure = io._windows_api().c.WinError(error)

    def interrupted(staged, destination, backup):
        # Reproduce the documented moved-original state with real filesystem IO.
        os.replace(destination, backup)
        raise failure

    monkeypatch.setattr(io, "_replace_existing", interrupted)
    with pytest.raises(OSError) as raised:
        _restore(target)
    assert raised.value is failure
    assert failure.rollback_commit_uncertain is True
    assert not getattr(failure, "rollback_committed", False)
    assert not target.exists()
    assert Path(failure.rollback_backup_path).read_bytes() == b"current"
    assert Path(failure.rollback_temporary_path).read_bytes() == "restored\r\n文本\n".encode()


@pytest.mark.skipif(os.name != "posix", reason="requires actual POSIX permissions and xattrs")
def test_posix_mode_owner_group_and_xattrs_are_preserved(tmp_path):
    target = tmp_path / "document.txt"
    target.write_bytes(b"current")
    target.chmod(0o640)
    os.setxattr(target, "user.echo-test", b"synthetic metadata")
    before = target.stat()
    _restore(target)
    after = target.stat()
    assert (stat.S_IMODE(after.st_mode), after.st_uid, after.st_gid) == (
        0o640,
        before.st_uid,
        before.st_gid,
    )
    assert os.getxattr(target, "user.echo-test") == b"synthetic metadata"


@pytest.mark.skipif(os.name != "posix", reason="requires actual POSIX symlinks")
def test_posix_dangling_symlink_is_not_absent(tmp_path):
    target = tmp_path / "link.txt"
    target.symlink_to(tmp_path / "missing.txt")
    with pytest.raises(io.RollbackConflict, match="existence_mismatch"):
        _restore(target, require_absent=True)
    assert target.is_symlink()
    assert not (tmp_path / "missing.txt").exists()


@pytest.mark.parametrize("mode", ["", "future"])
def test_unknown_hash_mode_is_rejected_without_touching_disk(tmp_path, mode):
    with pytest.raises(ValueError, match="hash mode"):
        _restore(tmp_path / "document.txt", require_absent=True, hash_mode=mode)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
@pytest.mark.parametrize("mode", ["bytes-v1", "text-v1"])
def test_guarded_remove_deletes_matching_file_using_handle(tmp_path, mode):
    target = tmp_path / "created.txt"
    target.write_bytes(b"created\r\n")
    expected = b"created\n" if mode == "text-v1" else target.read_bytes()
    io.guarded_remove_created_file(target, expected_sha256=_sha(expected), hash_mode=mode)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_wrong_hash_preserves_file_and_releases_handle(tmp_path):
    target = tmp_path / "created.txt"
    target.write_bytes(b"external content")
    with pytest.raises(io.RollbackConflict, match="hash_mismatch"):
        io.guarded_remove_created_file(target, expected_sha256=_sha(b"old"), hash_mode="bytes-v1")
    assert target.read_bytes() == b"external content"
    target.write_bytes(b"handle was released")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_excludes_late_writer_and_replacement(tmp_path, monkeypatch):
    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    external = tmp_path / "external.txt"
    external.write_bytes(b"independent external document")
    mark = io._mark_guarded_file_deleted
    blocked = []

    def competing_process_window(handle):
        # This is the exact old hash -> unlink window. Real OS sharing rules
        # reject both mutation forms instead of deleting a replacement object.
        for operation in (
            lambda: target.write_bytes(b"external edit"),
            lambda: os.replace(external, target),
        ):
            with pytest.raises(OSError) as raised:
                operation()
            # CRT-backed open() translates sharing violation to EACCES;
            # os.replace retains the native Windows error code.
            assert raised.value.winerror == 32 or raised.value.errno == 13
            blocked.append(True)
        mark(handle)

    monkeypatch.setattr(io, "_mark_guarded_file_deleted", competing_process_window)
    io.guarded_remove_created_file(target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1")
    assert blocked == [True, True]
    assert not target.exists()
    assert external.read_bytes() == b"independent external document"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_refuses_existing_writer_without_changing_content(tmp_path):
    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    with target.open("r+b"), pytest.raises(OSError) as raised:
        io.guarded_remove_created_file(
            target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1"
        )
    assert raised.value.winerror == 32
    assert target.read_bytes() == b"created"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_refuses_file_replaced_before_handle_open(tmp_path, monkeypatch):
    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    external = tmp_path / "replacement.txt"
    external.write_bytes(b"created")  # same hash still does not authorize a different inode
    api = io._windows_api()
    create = api.kernel.CreateFileW

    def replace_before_open(path, *args):
        if path == str(target):
            os.replace(external, target)
        return create(path, *args)

    monkeypatch.setattr(api.kernel, "CreateFileW", replace_before_open)
    with pytest.raises(io.RollbackConflict, match="identity_mismatch"):
        io.guarded_remove_created_file(
            target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1"
        )
    assert target.read_bytes() == b"created"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_readonly_is_not_silently_overridden(tmp_path):
    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            io.guarded_remove_created_file(
                target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1"
            )
        assert target.read_bytes() == b"created"
    finally:
        target.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_disposition_failure_propagates_and_keeps_file(tmp_path, monkeypatch):
    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    failure = OSError("synthetic disposition failure")

    def fail(_handle):
        raise failure

    monkeypatch.setattr(io, "_mark_guarded_file_deleted", fail)
    with pytest.raises(OSError) as raised:
        io.guarded_remove_created_file(
            target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1"
        )
    assert raised.value is failure
    assert not getattr(failure, "rollback_commit_uncertain", False)
    assert target.read_bytes() == b"created"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_close_error_after_delete_mark_is_uncertain(tmp_path, monkeypatch):
    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    close = io._close_guarded_stream
    failure = OSError("synthetic close failure")

    def fail_after_real_close(stream):
        close(stream)
        raise failure

    monkeypatch.setattr(io, "_close_guarded_stream", fail_after_real_close)
    with pytest.raises(OSError) as raised:
        io.guarded_remove_created_file(
            target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1"
        )
    assert raised.value is failure
    assert failure.rollback_commit_uncertain is True
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX must explicitly refuse unsafe unlink-by-path")
def test_guarded_remove_unsupported_platform_does_not_touch_file(tmp_path):
    import errno

    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    with pytest.raises(OSError) as raised:
        io.guarded_remove_created_file(
            target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1"
        )
    assert raised.value.errno == errno.ENOTSUP
    assert target.read_bytes() == b"created"


def test_guarded_remove_capability_matches_actual_platform():
    assert io.guarded_remove_supported() is (os.name == "nt")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exclusive file handles")
def test_guarded_remove_blocks_real_child_process_write_rename_and_delete(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys

    target = tmp_path / "created.txt"
    target.write_bytes(b"created")
    external = tmp_path / "independent.txt"
    external.write_bytes(b"independent external document")
    mark = io._mark_guarded_file_deleted
    result = []

    def child_at_final_boundary(handle):
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import json, os, pathlib, sys
target, external = map(pathlib.Path, sys.argv[1:])
blocked = []
for op in (lambda: target.write_bytes(b'external edit'), lambda: os.replace(external, target), target.unlink):
    try: op()
    except OSError: blocked.append(True)
    else: blocked.append(False)
print(json.dumps(blocked))
""",
                str(target),
                str(external),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        result.extend(json.loads(child.stdout))
        mark(handle)

    monkeypatch.setattr(io, "_mark_guarded_file_deleted", child_at_final_boundary)
    io.guarded_remove_created_file(target, expected_sha256=_sha(b"created"), hash_mode="bytes-v1")
    assert result == [True, True, True]
    assert not target.exists()
    assert external.read_bytes() == b"independent external document"
