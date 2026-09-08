from __future__ import annotations

import os
import sys

import pytest

from appliance.files import organization_directories as directories
from appliance.files.organization_io import OrganizationIOError

pytestmark = pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"), reason="Windows/Linux primitives"
)


def test_pin_root_and_nested_identity(tmp_path):
    (tmp_path / "share").mkdir()
    snapshot = directories.require_directory(tmp_path, "share")
    assert snapshot["path"] == tmp_path / "share"
    assert snapshot["identity"]["ino"] == (tmp_path / "share").stat().st_ino
    with directories.pinned_directory(tmp_path, "share", expected=snapshot["identity"]):
        assert directories.require_directory(tmp_path)["path"] == tmp_path


def test_changed_root_identity_does_not_create(tmp_path):
    old = directories.require_directory(tmp_path)["identity"]
    with pytest.raises(OrganizationIOError, match="directory_identity_changed"):
        directories.ensure_parent_directories(
            tmp_path,
            "2026/09/report.pdf",
            authorize=lambda _: None,
            expected_root={**old, "ino": old["ino"] + 1},
        )
    assert not (tmp_path / "2026").exists()


def test_create_authorizes_existing_and_new_parents_and_is_repeatable(tmp_path):
    (tmp_path / "share").mkdir()
    seen = []
    assert directories.ensure_parent_directories(
        tmp_path,
        "share/2026/09/report.pdf",
        authorize=seen.append,
    ) == ["share/2026", "share/2026/09"]
    assert seen == ["share", "share/2026", "share/2026/09"]
    assert (
        directories.ensure_parent_directories(
            tmp_path,
            "share/2026/09/report.pdf",
            authorize=lambda _: None,
        )
        == []
    )
    assert not (tmp_path / "share/2026/09/report.pdf").exists()


def test_denial_does_not_create_denied_child_and_reports_preceding_creation(tmp_path):
    def authorize(relative):
        if relative == "2026/09":
            raise PermissionError("synthetic denial")

    with pytest.raises(PermissionError) as caught:
        directories.ensure_parent_directories(tmp_path, "2026/09/a.pdf", authorize=authorize)
    assert caught.value.created_directories == ["2026"]
    assert (tmp_path / "2026").is_dir()
    assert not (tmp_path / "2026/09").exists()


@pytest.mark.parametrize(
    "relative", ["../outside/a", "/absolute/a", "a//b", "a/../b", "a:x/b", "a\\b/c"]
)
def test_invalid_relative_rejected_before_authorization(tmp_path, relative):
    seen = []
    with pytest.raises(OrganizationIOError):
        directories.ensure_parent_directories(tmp_path, relative, authorize=seen.append)
    assert seen == []


def test_file_as_parent_is_not_replaced(tmp_path):
    (tmp_path / "2026").write_bytes(b"external")
    with pytest.raises(OSError):
        directories.ensure_parent_directories(tmp_path, "2026/09/a.pdf", authorize=lambda _: None)
    assert (tmp_path / "2026").read_bytes() == b"external"


def test_link_ancestor_is_rejected_without_creating_in_target(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / "alias").symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        directories.ensure_parent_directories(tmp_path, "alias/09/a.pdf", authorize=lambda _: None)
    assert list(target.iterdir()) == []
    with pytest.raises(OSError):
        directories.require_directory(tmp_path / "alias")


@pytest.mark.skipif(os.name != "nt", reason="real Windows ACL and sharing")
def test_windows_parent_pin_blocks_replacement_and_preserves_inheritance(tmp_path):
    from tests.appliance.windows_acl_assertions import read_windows_acl as acl

    parent = tmp_path / "share"
    parent.mkdir()
    before = acl(parent)
    with directories.pinned_directory(tmp_path, "share"):
        with pytest.raises(PermissionError):
            parent.rename(tmp_path / "renamed")
        directories.ensure_parent_directories(
            tmp_path, "share/2026/a.pdf", authorize=lambda _: None
        )
    assert acl(parent) == before
    control = parent / "control"
    control.mkdir()
    assert acl(parent / "2026") == acl(control)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux dirfd and mode")
def test_linux_creation_uses_dirfd_and_retains_existing_permissions(tmp_path, monkeypatch):
    parent = tmp_path / "share"
    parent.mkdir(mode=0o750)
    original = os.mkdir
    seen = []

    def mkdir(path, mode=0o777, *, dir_fd=None):
        seen.append((path, dir_fd))
        return original(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", mkdir)
    directories.ensure_parent_directories(tmp_path, "share/2026/a.pdf", authorize=lambda _: None)
    assert seen and all(fd is not None for _, fd in seen)
    assert parent.stat().st_mode & 0o777 == 0o750
