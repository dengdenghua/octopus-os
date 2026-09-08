"""Real Windows ACLs and cross-process lock ownership; no permission/lock doubles."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from appliance.auth import read_auth_store, write_auth_store
from appliance.state_lock import LOCK_FILENAME, StateDirectoryLock, StateLockError
from tests.appliance.windows_acl_assertions import assert_private_windows_acl as _assert_private
from tests.appliance.windows_acl_assertions import read_windows_acl as _acl

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="requires real Windows ACL and file handles"
)


def test_auth_store_is_private_before_secret_write_and_after_atomic_replace(tmp_path, monkeypatch):
    state = tmp_path / "state ' with space 雪"
    target = state / "appliance-auth.json"
    original_dump = json.dump
    observed = []

    def inspect_before_write(payload, handle, **kwargs):
        temporary = next(state.glob(".appliance-auth.json.*.tmp"))
        assert temporary.stat().st_size == 0
        _assert_private(state)
        _assert_private(temporary)
        observed.append(True)
        return original_dump(payload, handle, **kwargs)

    monkeypatch.setattr("appliance.auth.json.dump", inspect_before_write)
    write_auth_store({"jwt_secret": "synthetic-1"}, target)
    assert read_auth_store(target) == {"jwt_secret": "synthetic-1"}
    write_auth_store({"jwt_secret": "synthetic-2"}, target)
    assert read_auth_store(target) == {"jwt_secret": "synthetic-2"}
    assert observed == [True, True]
    _assert_private(target)
    assert not list(state.glob("*.tmp"))


def test_auth_existing_inherited_acl_is_secured_without_changing_parent(tmp_path):
    ancestor_acl = _acl(tmp_path)
    state = tmp_path / "existing"
    state.mkdir()
    target = state / "appliance-auth.json"
    target.write_text('{"jwt_secret":"synthetic-existing"}', encoding="utf-8")
    assert read_auth_store(target)["jwt_secret"] == "synthetic-existing"
    _assert_private(state)
    _assert_private(target)
    assert _acl(tmp_path) == ancestor_acl


def test_write_failure_preserves_old_store_and_removes_private_temporary(tmp_path, monkeypatch):
    target = tmp_path / "state" / "appliance-auth.json"
    write_auth_store({"version": 1}, target)

    def fail_replace(*_args):
        raise OSError("synthetic failed publication")

    monkeypatch.setattr("appliance.auth.os.replace", fail_replace)
    with pytest.raises(OSError, match="failed publication"):
        write_auth_store({"version": 2}, target)
    assert read_auth_store(target) == {"version": 1}
    assert not list(target.parent.glob("*.tmp"))


@pytest.mark.parametrize("hard_link", [False, True])
def test_auth_rejects_file_links_without_reading_or_altering_target(tmp_path, hard_link):
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"sentinel":true}', encoding="utf-8")
    original = _acl(outside)
    link = state / "appliance-auth.json"
    if hard_link:
        os.link(outside, link)
    else:
        link.symlink_to(outside)
    with pytest.raises((OSError, ValueError), match="link"):
        read_auth_store(link)
    with pytest.raises((OSError, ValueError), match="link"):
        write_auth_store({"sentinel": False}, link)
    assert outside.read_text(encoding="utf-8") == '{"sentinel":true}'
    assert _acl(outside) == original


def test_auth_rejects_junction_ancestor_before_changing_external_acl(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    original = _acl(outside)
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        with pytest.raises(OSError, match="link"):
            write_auth_store({"secret": "synthetic"}, junction / "nested" / "appliance-auth.json")
        assert not list(outside.iterdir())
        assert _acl(outside) == original
    finally:
        # os.rmdir removes only this verified test junction, never its target.
        os.rmdir(junction)


def _child(state: Path, exclusive: bool) -> subprocess.Popen:
    code = """
import sys
from appliance.state_lock import StateDirectoryLock
lock = StateDirectoryLock.acquire(sys.argv[1], exclusive=sys.argv[2] == 'exclusive', create=True)
print('locked', flush=True)
sys.stdin.readline()
lock.release()
print('released', flush=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(state), "exclusive" if exclusive else "shared"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "locked"
    return child


@pytest.mark.parametrize("exclusive", [False, True])
@pytest.mark.parametrize("killed", [False, True])
def test_real_process_lock_modes_and_release_after_exit(tmp_path, exclusive, killed):
    state = tmp_path / "state"
    child = _child(state, exclusive)
    try:
        with pytest.raises(StateLockError, match="already in use"):
            StateDirectoryLock.acquire(state, exclusive=True)
        if exclusive:
            with pytest.raises(StateLockError, match="already in use"):
                StateDirectoryLock.acquire(state, exclusive=False)
        else:
            with StateDirectoryLock.acquire(state, exclusive=False):
                pass
        with pytest.raises(PermissionError):
            (state / LOCK_FILENAME).unlink()
        if killed:
            child.kill()
            child.communicate(timeout=10)
        else:
            output, errors = child.communicate("release\n", timeout=10)
            assert child.returncode == 0, errors
            assert output.strip() == "released"
        with StateDirectoryLock.acquire(state, exclusive=True):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_same_process_double_lock_fails_and_release_is_idempotent(tmp_path):
    lock = StateDirectoryLock.acquire(tmp_path, exclusive=True)
    try:
        with pytest.raises(StateLockError, match="already in use"):
            StateDirectoryLock.acquire(tmp_path, exclusive=True)
        assert os.get_inheritable(lock._descriptor) is False
        _assert_private(lock.path)
    finally:
        lock.release()
        lock.release()
    with StateDirectoryLock.acquire(tmp_path, exclusive=True):
        pass


def test_lock_rejects_symlink_and_hard_link_without_altering_target(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("sentinel", encoding="utf-8")
    original = _acl(outside)
    for method in ("symlink", "hardlink"):
        state = tmp_path / method
        state.mkdir()
        path = state / LOCK_FILENAME
        if method == "symlink":
            path.symlink_to(outside)
        else:
            os.link(outside, path)
        with pytest.raises(StateLockError, match="cannot open private state lock"):
            StateDirectoryLock.acquire(state, exclusive=True)
    assert outside.read_text(encoding="utf-8") == "sentinel"
    assert _acl(outside) == original
