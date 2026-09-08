"""Synthetic files only: exact-object, no-overwrite organization moves."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

from appliance.files import organization_io as io

pytestmark = pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"), reason="Windows/Linux primitives"
)


@pytest.fixture
def library(tmp_path):
    root = tmp_path.resolve()
    (root / "2026" / "09").mkdir(parents=True)
    (root / "invoice.pdf").write_bytes(b"%PDF synthetic invoice\r\n" + bytes(range(256)) * 700)
    return root


def _snapshot(root):
    return io.snapshot_file(root, "invoice.pdf")


def _move(root, expected, **kwargs):
    return io.move_file(
        root,
        "invoice.pdf",
        "2026/09/invoice.pdf",
        expected=expected,
        pending=kwargs.get("pending", ".echo-organize-fixture.pending"),
    )


def _inspect(root, expected):
    return io.inspect_move(
        root,
        "invoice.pdf",
        "2026/09/invoice.pdf",
        expected=expected,
        pending=".echo-organize-fixture.pending",
    )


@pytest.mark.parametrize("phase", ["source", "pending", "target", "recreated", "unknown"])
def test_inspect_reports_three_path_evidence_without_moving(library, monkeypatch, phase):
    expected = _snapshot(library)
    source = library / "invoice.pdf"
    pending = library / ".echo-organize-fixture.pending"
    target = library / "2026/09/invoice.pdf"
    if phase == "pending":
        source.rename(pending)
    elif phase in {"target", "recreated", "unknown"}:
        assert _move(library, expected)["committed"] is True
        if phase == "recreated":
            source.write_bytes(b"independent new source")
        elif phase == "unknown":
            target.write_bytes(b"external edit")
    before = {str(p): p.read_bytes() for p in (source, pending, target) if p.exists()}

    def must_not_rename(*args, **kwargs):
        pytest.fail("read-only inspection attempted a rename")

    monkeypatch.setattr(io, "_rename_noreplace", must_not_rename)
    result = _inspect(library, expected)
    expected_states = {
        "source": ("pending", False, "source_ready"),
        "pending": ("pending", False, "captured_pending_ready"),
        "target": ("moved", True, "already_moved"),
        "recreated": ("conflict", True, "other_path_present_after_move"),
        "unknown": ("uncertain", None, "target_changed_or_unrelated"),
    }
    assert (result["status"], result["committed"], result["reason"]) == expected_states[phase]
    assert {str(p): p.read_bytes() for p in (source, pending, target) if p.exists()} == before


def test_inspect_does_not_create_missing_destination_parents(library):
    expected = _snapshot(library)
    (library / "2026/09").rmdir()
    (library / "2026").rmdir()
    assert _inspect(library, expected)["reason"] == "source_ready"
    assert not (library / "2026").exists()


@pytest.mark.parametrize("path_name", ["invoice.pdf", "2026/09/invoice.pdf"])
def test_oversized_replacement_is_not_read_or_moved(library, monkeypatch, path_name):
    expected = _snapshot(library)
    changed = library / path_name
    changed.write_bytes(b"larger replacement" * (expected["size"] // 10 + 1))
    original = io._named_snapshot

    def bounded(parents, path, *, max_bytes, collect=False):
        assert path != changed, "oversized metadata must be rejected before opening its bytes"
        assert max_bytes == expected["size"]
        return original(parents, path, max_bytes=max_bytes, collect=collect)

    monkeypatch.setattr(io, "_named_snapshot", bounded)
    assert _inspect(library, expected)["status"] == "conflict"
    assert _move(library, expected)["status"] == "conflict"
    assert changed.exists()


def test_every_move_recheck_has_original_size_bound(library, monkeypatch):
    expected = _snapshot(library)
    snapshot = io._snapshot_stream
    limits = []

    def bounded(stream, *, max_bytes, collect=False):
        limits.append(max_bytes)
        return snapshot(stream, max_bytes=max_bytes, collect=collect)

    monkeypatch.setattr(io, "_snapshot_stream", bounded)
    assert _move(library, expected)["status"] == "moved"
    assert len(limits) >= 5
    assert set(limits) == {expected["size"]}


def test_snapshot_and_bounded_read_use_identical_exact_binary_bytes(library):
    raw = (library / "invoice.pdf").read_bytes()
    snapshot = _snapshot(library)
    data, read_snapshot = io.read_file_snapshot(library, "invoice.pdf", max_bytes=len(raw))
    assert data == raw
    assert read_snapshot == snapshot
    assert snapshot["sha256"] == hashlib.sha256(raw).hexdigest()
    assert snapshot["size"] == len(raw)
    assert snapshot["identity"]["ino"] == (library / "invoice.pdf").stat().st_ino


@pytest.mark.parametrize("operation", ["snapshot", "read"])
def test_snapshot_size_limit_refuses_without_changing_file(library, operation):
    before = (library / "invoice.pdf").read_bytes()
    call = io.snapshot_file if operation == "snapshot" else io.read_file_snapshot
    with pytest.raises(io.OrganizationIOError, match="file_too_large"):
        call(library, "invoice.pdf", max_bytes=10)
    assert (library / "invoice.pdf").read_bytes() == before


def test_snapshot_digest_reads_bounded_chunks_and_retains_caller_handle(library):
    class Reader:
        def __init__(self, stream):
            self.stream = stream
            self.reads = 0

        def fileno(self):
            return self.stream.fileno()

        def seek(self, offset):
            return self.stream.seek(offset)

        def read(self, size):
            assert size == 64 * 1024
            self.reads += 1
            return self.stream.read(size)

    with (library / "invoice.pdf").open("rb") as stream:
        reader = Reader(stream)
        snapshot = io._snapshot_stream(reader, max_bytes=None)[1]
        assert not stream.closed
    assert reader.reads >= 4
    assert snapshot == _snapshot(library)


def test_move_preserves_binary_identity_and_repeated_call_does_not_move_again(library):
    expected = _snapshot(library)
    raw = (library / "invoice.pdf").read_bytes()
    first = _move(library, expected)
    assert first == {
        "status": "moved",
        "committed": True,
        "reason": "moved",
        "recoveryPaths": ["2026/09/invoice.pdf"],
    }
    assert not (library / "invoice.pdf").exists()
    assert (library / "2026/09/invoice.pdf").read_bytes() == raw
    assert io.snapshot_file(library, "2026/09/invoice.pdf") == expected
    assert _move(library, expected)["reason"] == "already_moved"


def test_inverse_move_restores_only_recorded_object(library):
    expected = _snapshot(library)
    assert _move(library, expected)["status"] == "moved"
    result = io.move_file(
        library,
        "2026/09/invoice.pdf",
        "invoice.pdf",
        expected=expected,
        pending="2026/09/.echo-organize-undo.pending",
    )
    assert result["status"] == "moved"
    assert _snapshot(library) == expected


def test_inverse_move_does_not_overwrite_recreated_original(library):
    expected = _snapshot(library)
    assert _move(library, expected)["status"] == "moved"
    (library / "invoice.pdf").write_bytes(b"new independent invoice")
    result = io.move_file(
        library,
        "2026/09/invoice.pdf",
        "invoice.pdf",
        expected=expected,
        pending="2026/09/.echo-organize-undo.pending",
    )
    assert result["status"] == "conflict"
    assert result["committed"] is False
    assert (library / "invoice.pdf").read_bytes() == b"new independent invoice"
    assert io.snapshot_file(library, "2026/09/invoice.pdf") == expected
    replay = _move(library, expected)
    assert replay["status"] == "conflict"
    assert replay["committed"] is True  # the original forward move is still observed


def test_inverse_move_refuses_modified_organized_file(library):
    expected = _snapshot(library)
    assert _move(library, expected)["status"] == "moved"
    (library / "2026/09/invoice.pdf").write_bytes(b"edited after organization")
    result = io.move_file(
        library,
        "2026/09/invoice.pdf",
        "invoice.pdf",
        expected=expected,
        pending="2026/09/.echo-organize-undo.pending",
    )
    assert result["reason"] == "source_changed"
    assert not (library / "invoice.pdf").exists()
    assert (library / "2026/09/invoice.pdf").read_bytes() == b"edited after organization"


@pytest.mark.parametrize("conflict", ["file", "directory", "same_bytes_other_inode"])
def test_existing_target_never_overwritten(library, conflict):
    expected = _snapshot(library)
    target = library / "2026/09/invoice.pdf"
    if conflict == "directory":
        target.mkdir()
    else:
        target.write_bytes(
            (library / "invoice.pdf").read_bytes()
            if conflict == "same_bytes_other_inode"
            else b"external"
        )
    result = _move(library, expected)
    assert result["status"] == "conflict"
    assert result["committed"] is False
    assert result["reason"] == "target_exists"
    assert _snapshot(library) == expected
    assert (
        target.is_dir()
        if conflict == "directory"
        else target.read_bytes()
        == (
            (library / "invoice.pdf").read_bytes()
            if conflict == "same_bytes_other_inode"
            else b"external"
        )
    )


@pytest.mark.parametrize("change", ["content", "identity"])
def test_changed_source_is_not_moved(library, change):
    expected = _snapshot(library)
    source = library / "invoice.pdf"
    if change == "content":
        source.write_bytes(b"external edit")
    else:
        replacement = library / "replacement.pdf"
        replacement.write_bytes(source.read_bytes())
        os.replace(replacement, source)
    result = _move(library, expected)
    assert result["reason"] == "source_changed"
    assert source.exists()
    assert not (library / "2026/09/invoice.pdf").exists()


def test_unrelated_pending_never_overwritten(library):
    expected = _snapshot(library)
    pending = library / ".echo-organize-fixture.pending"
    pending.write_bytes(b"independent pending")
    assert _move(library, expected)["reason"] == "pending_conflict"
    assert pending.read_bytes() == b"independent pending"
    assert _snapshot(library) == expected


def test_missing_source_without_receipt_evidence_is_uncertain(library):
    expected = _snapshot(library)
    (library / "invoice.pdf").rename(library / "outside-plan.pdf")
    result = _move(library, expected)
    assert result["status"] == "uncertain"
    assert result["committed"] is None


@pytest.mark.parametrize(
    "path",
    [
        "../invoice.pdf",
        "/invoice.pdf",
        "a//b",
        "a/./b",
        "invoice.pdf:stream",
        "a\\b",
        "NUL",
        "invoice.pdf.",
    ],
)
def test_invalid_relative_paths_are_rejected(library, path):
    with pytest.raises(io.OrganizationIOError, match="invalid_path"):
        io.snapshot_file(library, path)
    result = io.move_file(
        library, "invoice.pdf", path, expected=_snapshot(library), pending=".pending"
    )
    assert result["status"] == "conflict"
    assert result["reason"] == "invalid_path"


def test_pending_must_be_distinct_and_share_source_parent(library):
    expected = _snapshot(library)
    assert _move(library, expected, pending="invoice.pdf")["reason"] == "invalid_move_paths"
    assert _move(library, expected, pending="2026/.pending")["reason"] == "invalid_move_paths"


def test_missing_parent_is_not_created(library):
    result = io.move_file(
        library,
        "invoice.pdf",
        "missing/invoice.pdf",
        expected=_snapshot(library),
        pending=".pending",
    )
    assert result["status"] != "moved"
    assert not (library / "missing").exists()
    assert (library / "invoice.pdf").is_file()


def test_hardlinks_are_rejected(library):
    os.link(library / "invoice.pdf", library / "alias.pdf")
    with pytest.raises(io.OrganizationIOError, match="not_regular_single_link_file"):
        _snapshot(library)


def test_parent_and_file_symlinks_are_not_followed(library):
    outside = library.parent / (library.name + "-outside")
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"synthetic outside")
    try:
        (library / "alias").symlink_to(outside, target_is_directory=True)
        (library / "alias.pdf").symlink_to(outside / "secret.pdf")
    except OSError as exc:
        pytest.skip(f"host cannot create test symlinks: {type(exc).__name__}")
    for path in ("alias/secret.pdf", "alias.pdf"):
        with pytest.raises(OSError):
            io.snapshot_file(library, path)
    assert (outside / "secret.pdf").read_bytes() == b"synthetic outside"


@pytest.mark.parametrize("phase", ["capture", "publish"])
def test_exception_after_successful_syscall_is_reconciled_from_files(library, monkeypatch, phase):
    expected = _snapshot(library)
    rename = io._rename_noreplace

    def interrupted(parents, source, target, *, stream):
        rename(parents, source, target, stream=stream)
        if (target.name.startswith(".echo-organize")) == (phase == "capture"):
            raise OSError("synthetic exception after successful rename")

    monkeypatch.setattr(io, "_rename_noreplace", interrupted)
    result = _move(library, expected)
    if phase == "publish":
        assert result["status"] == "uncertain"
        assert result["committed"] is True
    else:
        assert result["committed"] is False
        assert io.snapshot_file(library, ".echo-organize-fixture.pending") == expected
    monkeypatch.setattr(io, "_rename_noreplace", rename)
    assert _move(library, expected)["status"] == "moved"


def test_target_created_at_publish_boundary_preserves_both_files(library, monkeypatch):
    expected = _snapshot(library)
    rename = io._rename_noreplace

    def race(parents, source, target, *, stream):
        if target.name == "invoice.pdf":
            target.write_bytes(b"external winner")
        rename(parents, source, target, stream=stream)

    monkeypatch.setattr(io, "_rename_noreplace", race)
    result = _move(library, expected)
    assert result["status"] == "conflict"
    assert result["committed"] is False
    assert (library / "2026/09/invoice.pdf").read_bytes() == b"external winner"
    assert io.snapshot_file(library, ".echo-organize-fixture.pending") == expected


@pytest.mark.parametrize("change", ["edit", "remove"])
def test_external_change_after_verified_publish_does_not_erase_commit(library, monkeypatch, change):
    expected = _snapshot(library)
    observe = io._observe
    calls = 0

    def changed_before_readback(parents, path, *, max_bytes):
        nonlocal calls
        calls += 1
        if calls == 4:  # three initial observations; the exclusive handle has now closed
            target = library / "2026/09/invoice.pdf"
            if change == "edit":
                target.write_bytes(b"external edit after completed publish")
            else:
                target.rename(library / "externally-moved.pdf")
        return observe(parents, path, max_bytes=max_bytes)

    monkeypatch.setattr(io, "_observe", changed_before_readback)
    result = _move(library, expected)
    assert result["status"] == "uncertain"
    assert result["committed"] is True
    assert result["reason"] == "target_changed_after_publish"
    monkeypatch.setattr(io, "_observe", observe)
    recovered = _move(library, expected)
    assert recovered["status"] == "uncertain"
    assert recovered["committed"] is None  # a fresh caller lacks that historical fact
    assert not (library / "invoice.pdf").exists()
    if change == "edit":
        assert (
            library / "2026/09/invoice.pdf"
        ).read_bytes() == b"external edit after completed publish"
    else:
        assert io.snapshot_file(library, "externally-moved.pdf") == expected


@pytest.mark.parametrize("code", [errno.EXDEV, errno.ENOSYS, errno.EIO])
def test_rename_failure_does_not_fall_back_to_copy_delete(library, monkeypatch, code):
    expected = _snapshot(library)

    def fail(*_args, **_kwargs):
        raise OSError(code, "synthetic rename failure")

    monkeypatch.setattr(io, "_rename_noreplace", fail)
    result = _move(library, expected)
    assert result["status"] == "conflict"
    assert result["committed"] is False
    assert _snapshot(library) == expected
    assert not (library / ".echo-organize-fixture.pending").exists()


@pytest.mark.parametrize("phase", ["before", "capture", "publish"])
def test_real_child_process_exit_recovers_exact_prepared_move(library, phase):
    expected = _snapshot(library)
    receipt = library / "prepared.json"
    receipt.write_text(json.dumps(expected), encoding="utf-8")
    code = """
import json, os, sys
from pathlib import Path
from appliance.files import organization_io as io
root = Path(sys.argv[1]); phase = sys.argv[2]
expected = json.loads((root/'prepared.json').read_text())
rename = io._rename_noreplace
def crash(parents, source, target, *, stream):
    if phase == 'before': os._exit(73)
    rename(parents, source, target, stream=stream)
    if (target.name.startswith('.echo-organize')) == (phase == 'capture'): os._exit(73)
io._rename_noreplace = crash
io.move_file(root,'invoice.pdf','2026/09/invoice.pdf',expected=expected,pending='.echo-organize-fixture.pending')
raise SystemExit(99)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(library), phase], capture_output=True, timeout=20
    )
    assert child.returncode == 73, child.stderr.decode(errors="replace")
    result = _move(library, expected)
    assert result["status"] == "moved"
    assert io.snapshot_file(library, "2026/09/invoice.pdf") == expected
    assert not (library / "invoice.pdf").exists()
    assert not (library / ".echo-organize-fixture.pending").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive file handles")
def test_windows_same_handle_blocks_real_child_replace_and_write(library, monkeypatch):
    expected = _snapshot(library)
    rename = io._rename_noreplace
    observations = []

    def competing_child(parents, source, target, *, stream):
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import json, os, pathlib, sys
source=pathlib.Path(sys.argv[1]); other=source.parent/'independent.pdf'
other.write_bytes(b'independent')
blocked=[]
for operation in (lambda: source.write_bytes(b'edit'),lambda: os.replace(other,source),source.unlink):
    try: operation()
    except OSError: blocked.append(True)
    else: blocked.append(False)
print(json.dumps(blocked))
""",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        observations.append(json.loads(child.stdout))
        rename(parents, source, target, stream=stream)

    monkeypatch.setattr(io, "_rename_noreplace", competing_child)
    assert _move(library, expected)["status"] == "moved"
    assert observations == [[True, True, True], [True, True, True]]
    assert (library / "independent.pdf").read_bytes() == b"independent"


@pytest.mark.skipif(os.name != "nt", reason="independent Windows ACL observation")
def test_windows_move_preserves_original_acl_across_different_parent(library):
    from appliance.windows_state import private_state_directory
    from tests.appliance.windows_acl_assertions import read_windows_acl

    before = read_windows_acl(library / "invoice.pdf")
    # The destination inherits a different protected DACL. Rename must keep
    # the original file's permissions, not create a new file under this ACL.
    with private_state_directory(library / "2026/09", protect=True):
        pass
    expected = _snapshot(library)
    assert _move(library, expected)["status"] == "moved"
    after = read_windows_acl(library / "2026/09/invoice.pdf")
    assert after["owner"] == before["owner"]

    def grants(acl):
        return sorted((rule["sid"], rule["rights"], rule["type"]) for rule in acl["rules"])

    # NTFS can turn inherited ACEs into protected explicit ones on rename.
    # Independent PowerShell verifies equal subjects/rights/allow-deny rules;
    # the provider additionally compares full ACE payloads through its handle.
    assert grants(after) == grants(before)
    assert io.snapshot_file(library, "2026/09/invoice.pdf") == expected


@pytest.mark.skipif(os.name != "nt", reason="real Windows DACL change")
def test_windows_permission_change_since_plan_is_a_conflict(library):
    from appliance.windows_state import open_private_file, private_state_directory

    expected = _snapshot(library)
    with private_state_directory(library, protect=False):
        descriptor = open_private_file(library / "invoice.pdf")
        os.close(descriptor)
    changed = _snapshot(library)
    assert changed["identity"] == expected["identity"]
    assert changed["sha256"] == expected["sha256"]
    assert changed["permissions"] != expected["permissions"]
    result = _move(library, expected)
    assert result["reason"] == "source_changed"
    assert result["committed"] is False
    assert _snapshot(library) == changed


@pytest.mark.skipif(os.name != "nt", reason="Windows readonly attribute and handle access")
def test_windows_readonly_source_is_not_silently_made_writable(library):
    source = library / "invoice.pdf"
    source.chmod(stat.S_IREAD)
    try:
        expected = _snapshot(library)
        result = _move(library, expected)
        # FileRenameInfo may allow readonly rename on a supporting filesystem;
        # either way the original object and readonly attribute must survive.
        relative = "2026/09/invoice.pdf" if result["committed"] else "invoice.pdf"
        assert io.snapshot_file(library, relative) == expected
        assert not ((library / relative).stat().st_mode & stat.S_IWRITE)
    finally:
        for relative in ("invoice.pdf", "2026/09/invoice.pdf", ".echo-organize-fixture.pending"):
            if (library / relative).exists():
                (library / relative).chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="actual Linux renameat2 and POSIX metadata"
)
def test_linux_move_preserves_mode_owner_and_xattrs(library):
    source = library / "invoice.pdf"
    source.chmod(0o640)
    os.setxattr(source, "user.echo-test", b"synthetic")
    expected = _snapshot(library)
    assert _move(library, expected)["status"] == "moved"
    target = library / "2026/09/invoice.pdf"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert io.snapshot_file(library, "2026/09/invoice.pdf") == expected
    assert os.getxattr(target, "user.echo-test") == b"synthetic"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux name-capture race")
def test_linux_changed_name_at_capture_is_preserved_and_not_published(library, monkeypatch):
    expected = _snapshot(library)
    rename = io._rename_noreplace

    def capture_replacement(parents, source, target, *, stream):
        if source.name == "invoice.pdf":
            source.rename(library / "preserved-original.pdf")
            source.write_bytes(b"external replacement")
        rename(parents, source, target, stream=stream)

    monkeypatch.setattr(io, "_rename_noreplace", capture_replacement)
    result = _move(library, expected)
    assert result["status"] == "uncertain"
    assert result["committed"] is None
    assert io.snapshot_file(library, "preserved-original.pdf") == expected
    assert (library / ".echo-organize-fixture.pending").read_bytes() == b"external replacement"
    assert not (library / "2026/09/invoice.pdf").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux final pathname race")
def test_linux_final_name_race_reports_uncertainty_without_claiming_original_committed(
    library, monkeypatch
):
    expected = _snapshot(library)
    rename = io._rename_noreplace

    def replaced_pending(parents, source, target, *, stream):
        if target.name == "invoice.pdf":
            source.rename(library / "preserved-original.pdf")
            source.write_bytes(b"external pending replacement")
        rename(parents, source, target, stream=stream)

    monkeypatch.setattr(io, "_rename_noreplace", replaced_pending)
    result = _move(library, expected)
    assert result["status"] == "uncertain"
    assert result["committed"] is None
    assert io.snapshot_file(library, "preserved-original.pdf") == expected
    assert (library / "2026/09/invoice.pdf").read_bytes() == b"external pending replacement"
