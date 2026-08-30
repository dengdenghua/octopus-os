"""Tests for ``runtime.sensing.server.mount_backend``.

Coverage:

  * ``LocalMountBackend`` — full read/write/list/stat/mkdir/remove/test_connection
    cycle against a real ``tmp_path`` plus path-whitelist enforcement.
  * ``SftpMountBackend`` — mocked paramiko ``SFTPClient``.
  * ``WebdavMountBackend`` — mocked ``requests``.
  * ``SmbMountBackend`` / ``S3MountBackend`` — graceful-fallback when the
    optional dep is missing.
  * ``MountBackendRegistry`` — routing + instance caching.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.sensing.server.mount_backend import (
    DEFAULT_IGNORED_DIRS,
    BackendUnavailableError,
    DirEntry,
    FileStat,
    LocalMountBackend,
    MountBackend,
    MountBackendRegistry,
    NfsMountBackend,
    S3MountBackend,
    SftpMountBackend,
    SmbMountBackend,
    WebdavMountBackend,
    default_registry,
)


def _make_registry() -> MountBackendRegistry:
    """Fresh registry pre-populated with all six built-in backends.

    ``default_registry`` is a module-level singleton; tests that mutate
    it would leak state across the suite. This helper builds a private
    registry pre-populated the same way so each test starts clean.
    """
    reg = MountBackendRegistry()
    reg.register("local", LocalMountBackend)
    reg.register("sftp", SftpMountBackend)
    reg.register("webdav", WebdavMountBackend)
    reg.register("smb", SmbMountBackend)
    reg.register("nfs", NfsMountBackend)
    reg.register("s3", S3MountBackend)
    return reg


# ═══════════════════════════════════════════════════════════
# LocalMountBackend
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_local_test_connection_true_for_existing_root(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    assert await backend.test_connection() is True


@pytest.mark.asyncio
async def test_local_test_connection_false_for_missing_root(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    backend.root_path = tmp_path / "does-not-exist"
    assert await backend.test_connection() is False


@pytest.mark.asyncio
async def test_local_read_write_roundtrip(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    await backend.write_file("hello.txt", b"hello world")
    data = await backend.read_file("hello.txt")
    assert data == b"hello world"


@pytest.mark.asyncio
async def test_local_write_creates_parent_dirs(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    await backend.write_file("sub/dir/file.txt", b"nested")
    assert (tmp_path / "sub" / "dir" / "file.txt").read_bytes() == b"nested"


@pytest.mark.asyncio
async def test_local_write_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    await backend.write_file("a.txt", b"first")
    # atomic_write_bytes leaves only the target + optional .bak; no .tmp-*.
    leftover = [p.name for p in tmp_path.iterdir() if ".tmp-" in p.name]
    assert leftover == []


@pytest.mark.asyncio
async def test_local_read_file_not_found(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        await backend.read_file("missing.txt")


@pytest.mark.asyncio
async def test_local_read_outside_root_denied(tmp_path: Path, tmp_path_factory) -> None:
    other = tmp_path_factory.mktemp("other")
    (other / "secret.txt").write_bytes(b"secret")
    backend = LocalMountBackend(tmp_path)
    with pytest.raises(PermissionError):
        await backend.read_file(str(other / "secret.txt"))


@pytest.mark.asyncio
async def test_local_write_outside_root_denied(tmp_path: Path, tmp_path_factory) -> None:
    other = tmp_path_factory.mktemp("other")
    backend = LocalMountBackend(tmp_path)
    with pytest.raises(PermissionError):
        await backend.write_file(str(other / "evil.txt"), b"pwned")
    assert not (other / "evil.txt").exists()


@pytest.mark.asyncio
async def test_local_list_dir_depth_1(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"bb")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_bytes(b"ccc")
    backend = LocalMountBackend(tmp_path)
    entries = await backend.list_dir(".", depth=1)
    names = {e.name for e in entries}
    assert {"a.txt", "b.txt", "sub"} == names
    # depth=1 → no nested entries
    assert all("c.txt" not in e.path for e in entries)


@pytest.mark.asyncio
async def test_local_list_dir_depth_2(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_bytes(b"ccc")
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "x.txt").write_bytes(b"x")
    backend = LocalMountBackend(tmp_path)
    entries = await backend.list_dir(".", depth=2)
    paths = {e.path for e in entries}
    assert "a.txt" in paths
    assert "sub" in paths
    assert "sub/c.txt" in paths
    assert "sub/deep" in paths
    # depth=2 → depth-3 entries pruned
    assert "sub/deep/x.txt" not in paths


@pytest.mark.asyncio
async def test_local_list_dir_filters_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_bytes(b"git")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".echo").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "real.txt").write_bytes(b"real")
    backend = LocalMountBackend(tmp_path)
    entries = await backend.list_dir(".", depth=1)
    names = {e.name for e in entries}
    assert "real.txt" in names
    assert ".git" not in names
    assert "node_modules" not in names
    assert ".echo" not in names
    assert "logs" not in names


@pytest.mark.asyncio
async def test_local_list_dir_custom_ignored_set(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "keep").mkdir()
    backend = LocalMountBackend(tmp_path, ignored_dirs=frozenset({"only-this"}))
    entries = await backend.list_dir(".", depth=1)
    names = {e.name for e in entries}
    assert ".git" in names  # default ignore not applied
    assert "keep" in names


@pytest.mark.asyncio
async def test_local_list_dir_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_bytes(b"x")
    backend = LocalMountBackend(tmp_path)
    with pytest.raises(NotADirectoryError):
        await backend.list_dir("file.txt")


@pytest.mark.asyncio
async def test_local_list_dir_missing_path(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        await backend.list_dir("missing")


@pytest.mark.asyncio
async def test_local_stat_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"hello")
    backend = LocalMountBackend(tmp_path)
    st = await backend.stat("a.txt")
    assert isinstance(st, FileStat)
    assert st.is_dir is False
    assert st.size == 5
    assert st.modified > 0
    assert st.path == "a.txt"


@pytest.mark.asyncio
async def test_local_stat_dir(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    backend = LocalMountBackend(tmp_path)
    st = await backend.stat("sub")
    assert st.is_dir is True


@pytest.mark.asyncio
async def test_local_stat_missing(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        await backend.stat("nope")


@pytest.mark.asyncio
async def test_local_mkdir(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    await backend.mkdir("new/dir")
    assert (tmp_path / "new" / "dir").is_dir()


@pytest.mark.asyncio
async def test_local_mkdir_existing_ok(tmp_path: Path) -> None:
    (tmp_path / "exists").mkdir()
    backend = LocalMountBackend(tmp_path)
    # exist_ok=True → idempotent
    await backend.mkdir("exists")


@pytest.mark.asyncio
async def test_local_remove_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"x")
    backend = LocalMountBackend(tmp_path)
    await backend.remove("a.txt")
    assert not (tmp_path / "a.txt").exists()


@pytest.mark.asyncio
async def test_local_remove_directory(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_bytes(b"x")
    backend = LocalMountBackend(tmp_path)
    await backend.remove("sub")
    assert not (tmp_path / "sub").exists()


@pytest.mark.asyncio
async def test_local_remove_missing(tmp_path: Path) -> None:
    backend = LocalMountBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        await backend.remove("nope")


@pytest.mark.asyncio
async def test_local_constructor_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        LocalMountBackend(tmp_path / "does-not-exist")


def test_default_ignored_dirs_contains_required() -> None:
    for name in (".git", "node_modules", ".echo", "logs"):
        assert name in DEFAULT_IGNORED_DIRS


# ═══════════════════════════════════════════════════════════
# SftpMountBackend (mocked paramiko)
# ═══════════════════════════════════════════════════════════


def _make_mock_sftp() -> MagicMock:
    """Build a MagicMock that quacks like paramiko.SFTPClient."""
    sftp = MagicMock()
    # stat(".") for the liveness probe in _ensure_connected.
    sftp.stat.return_value = MagicMock(st_mode=0o040755, st_size=0, st_mtime=time.time())
    return sftp


@pytest.mark.asyncio
async def test_sftp_test_connection_unavailable_when_paramiko_missing(monkeypatch) -> None:
    # Make `import paramiko` raise ImportError inside _ensure_available.
    monkeypatch.setitem(sys.modules, "paramiko", None)
    backend = SftpMountBackend(host="example.com", user="u", password="p")
    assert await backend.test_connection() is False


@pytest.mark.asyncio
async def test_sftp_read_file_uses_sftp_open() -> None:
    backend = SftpMountBackend(host="example.com", user="u", password="p", root_path="/data")
    sftp = _make_mock_sftp()
    file_mock = MagicMock()
    file_mock.read.return_value = b"hello sftp"
    sftp.open.return_value.__enter__.return_value = file_mock
    backend._sftp = sftp
    # Patch the liveness probe to skip reconnect.
    backend._ensure_available = MagicMock()  # type: ignore[assignment]

    data = await backend.read_file("/data/foo.txt")
    assert data == b"hello sftp"
    sftp.open.assert_called_once_with("/data/foo.txt", "rb")


@pytest.mark.asyncio
async def test_sftp_write_file_uses_temp_then_rename() -> None:
    backend = SftpMountBackend(host="example.com", user="u", password="p", root_path="/data")
    sftp = _make_mock_sftp()
    sftp.posix_rename = MagicMock()
    file_mock = MagicMock()
    sftp.open.return_value.__enter__.return_value = file_mock
    backend._sftp = sftp
    backend._ensure_available = MagicMock()  # type: ignore[assignment]

    await backend.write_file("foo.txt", b"payload")
    # Opened for write…
    assert sftp.open.called
    # …and renamed to the final path.
    sftp.posix_rename.assert_called_once()
    args, _ = sftp.posix_rename.call_args
    assert args[1] == "/data/foo.txt"


@pytest.mark.asyncio
async def test_sftp_list_dir_returns_entries() -> None:
    backend = SftpMountBackend(host="example.com", user="u", password="p", root_path="/data")
    sftp = _make_mock_sftp()
    dir_attr = MagicMock()
    dir_attr.filename = "subdir"
    dir_attr.st_mode = 0o040755
    dir_attr.st_size = 0
    dir_attr.st_mtime = 1700000000.0
    file_attr = MagicMock()
    file_attr.filename = "file.txt"
    file_attr.st_mode = 0o100644
    file_attr.st_size = 42
    file_attr.st_mtime = 1700000001.0
    sftp.listdir_attr.return_value = [dir_attr, file_attr]
    backend._sftp = sftp
    backend._ensure_available = MagicMock()  # type: ignore[assignment]

    entries = await backend.list_dir(".", depth=1)
    assert len(entries) == 2
    by_name = {e.name: e for e in entries}
    assert by_name["subdir"].is_dir is True
    assert by_name["file.txt"].is_dir is False
    assert by_name["file.txt"].size == 42
    # Paths are relative to root_path.
    assert by_name["subdir"].path == "subdir"
    assert by_name["file.txt"].path == "file.txt"


@pytest.mark.asyncio
async def test_sftp_stat_returns_filestat() -> None:
    backend = SftpMountBackend(host="example.com", user="u", password="p", root_path="/data")
    sftp = _make_mock_sftp()
    attr = MagicMock()
    attr.st_mode = 0o100644
    attr.st_size = 128
    attr.st_mtime = 1700000000.0
    # stat(".") liveness probe returns a dir; explicit stat(path) returns file.
    sftp.stat.side_effect = [MagicMock(st_mode=0o040755), attr]
    backend._sftp = sftp
    backend._ensure_available = MagicMock()  # type: ignore[assignment]

    st = await backend.stat("/data/foo.txt")
    assert st.is_dir is False
    assert st.size == 128
    assert st.modified == 1700000000.0


@pytest.mark.asyncio
async def test_sftp_mkdir_builds_parents() -> None:
    backend = SftpMountBackend(host="example.com", user="u", password="p", root_path="/data")
    sftp = _make_mock_sftp()
    backend._sftp = sftp
    backend._ensure_available = MagicMock()  # type: ignore[assignment]

    await backend.mkdir("a/b/c")
    created = [call.args[0] for call in sftp.mkdir.call_args_list]
    assert "/data/a" in created
    assert "/data/a/b" in created
    assert "/data/a/b/c" in created


@pytest.mark.asyncio
async def test_sftp_remove_file_first() -> None:
    backend = SftpMountBackend(host="example.com", user="u", password="p", root_path="/data")
    sftp = _make_mock_sftp()
    backend._sftp = sftp
    backend._ensure_available = MagicMock()  # type: ignore[assignment]

    await backend.remove("foo.txt")
    sftp.remove.assert_called_once_with("/data/foo.txt")
    # rmtree path not triggered.
    sftp.listdir_attr.assert_not_called()


@pytest.mark.asyncio
async def test_sftp_remove_directory_recurses() -> None:
    backend = SftpMountBackend(host="example.com", user="u", password="p", root_path="/data")
    sftp = _make_mock_sftp()
    # First remove() raises OSError (not a file) → falls through to rmtree.
    sftp.remove.side_effect = OSError("is a directory")
    inner_attr = MagicMock()
    inner_attr.filename = "child.txt"
    inner_attr.st_mode = 0o100644
    sftp.listdir_attr.return_value = [inner_attr]
    backend._sftp = sftp
    backend._ensure_available = MagicMock()  # type: ignore[assignment]

    await backend.remove("subdir")
    # child removed, then rmdir on parent
    sftp.remove.assert_any_call("/data/subdir/child.txt")
    sftp.rmdir.assert_called_once_with("/data/subdir")


@pytest.mark.asyncio
async def test_sftp_constructor_validates_port() -> None:
    with pytest.raises(ValueError):
        SftpMountBackend(host="h", port=99999)
    with pytest.raises(ValueError):
        SftpMountBackend(host="h", port=0)
    with pytest.raises(ValueError):
        SftpMountBackend(host="", port=22)


# ═══════════════════════════════════════════════════════════
# WebdavMountBackend (mocked requests)
# ═══════════════════════════════════════════════════════════


def _webdav_propfind_response(payloads: list[tuple[str, bool, int, float]]) -> bytes:
    """Build a minimal WebDAV PROPFIND multistatus body.

    Each payload is (href, is_dir, size, mtime_epoch).
    """
    parts = ['<?xml version="1.0" encoding="utf-8"?>', '<D:multistatus xmlns:D="DAV:">']
    from email.utils import formatdate

    for href, is_dir, size, mtime in payloads:
        rt = "<D:collection/>" if is_dir else ""
        parts.append(
            "<D:response>"
            f"<D:href>{href}</D:href>"
            "<D:propstat><D:prop>"
            f"<D:getcontentlength>{size}</D:getcontentlength>"
            f"<D:getlastmodified>{formatdate(mtime, usegmt=True)}</D:getlastmodified>"
            f"<D:resourcetype>{rt}</D:resourcetype>"
            "</D:prop>"
            "<D:status>HTTP/1.1 200 OK</D:status>"
            "</D:propstat>"
            "</D:response>"
        )
    parts.append("</D:multistatus>")
    return "".join(parts).encode("utf-8")


def _install_mock_requests(monkeypatch, response: MagicMock) -> MagicMock:
    """Install a fake ``requests`` module that returns ``response``."""
    fake = MagicMock()
    fake.request.return_value = response
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake


@pytest.mark.asyncio
async def test_webdav_read_file(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b"hello webdav"
    fake = _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(
        base_url="https://dav.example.com/remote.php/dav/files/me",
        username="u",
        password="p",
    )
    data = await backend.read_file("foo.txt")
    assert data == b"hello webdav"
    fake.request.assert_called_once()
    call_args, call_kwargs = fake.request.call_args
    assert call_args[0] == "GET"
    assert "foo.txt" in call_args[1]
    assert call_kwargs["auth"] == ("u", "p")


@pytest.mark.asyncio
async def test_webdav_read_file_raises_on_http_error(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 404
    response.text = "Not Found"
    _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com", username="u", password="p")
    with pytest.raises(Exception, match="404"):
        await backend.read_file("missing.txt")


@pytest.mark.asyncio
async def test_webdav_write_file(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 201
    response.text = ""
    fake = _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com", username="u", password="p")
    await backend.write_file("foo.txt", b"payload")
    methods = [call.args[0] for call in fake.request.call_args_list]
    # At least one MKCOL (parent) + one PUT.
    assert "PUT" in methods


@pytest.mark.asyncio
async def test_webdav_list_dir(monkeypatch) -> None:
    body = _webdav_propfind_response(
        [
            ("/dav/", True, 0, time.time()),  # collection itself (skipped)
            ("/dav/foo.txt", False, 11, time.time()),
            ("/dav/sub", True, 0, time.time()),
        ]
    )
    response = MagicMock()
    response.status_code = 207
    response.content = body
    response.text = ""
    fake = _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com/dav", username="u", password="p")
    entries = await backend.list_dir(".", depth=1)
    names = {e.name for e in entries}
    assert {"foo.txt", "sub"} == names
    by_name = {e.name: e for e in entries}
    assert by_name["sub"].is_dir is True
    assert by_name["foo.txt"].is_dir is False
    assert by_name["foo.txt"].size == 11
    # PROPFIND was used.
    methods = [call.args[0] for call in fake.request.call_args_list]
    assert "PROPFIND" in methods


@pytest.mark.asyncio
async def test_webdav_stat(monkeypatch) -> None:
    body = _webdav_propfind_response(
        [
            ("/dav/foo.txt", False, 42, 1700000000.0),
        ]
    )
    response = MagicMock()
    response.status_code = 207
    response.content = body
    response.text = ""
    _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com/dav", username="u", password="p")
    st = await backend.stat("foo.txt")
    assert st.is_dir is False
    assert st.size == 42
    assert st.modified == 1700000000.0


@pytest.mark.asyncio
async def test_webdav_mkdir(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 201
    response.text = ""
    fake = _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com/dav", username="u", password="p")
    await backend.mkdir("newdir")
    methods = [call.args[0] for call in fake.request.call_args_list]
    assert "MKCOL" in methods


@pytest.mark.asyncio
async def test_webdav_remove(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 204
    response.text = ""
    fake = _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com/dav", username="u", password="p")
    await backend.remove("foo.txt")
    methods = [call.args[0] for call in fake.request.call_args_list]
    assert "DELETE" in methods


@pytest.mark.asyncio
async def test_webdav_remove_accepts_404(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 404
    response.text = ""
    _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com/dav", username="u", password="p")
    # 404 is treated as success (idempotent delete).
    await backend.remove("foo.txt")


@pytest.mark.asyncio
async def test_webdav_test_connection_true(monkeypatch) -> None:
    body = _webdav_propfind_response([("/dav/", True, 0, time.time())])
    response = MagicMock()
    response.status_code = 207
    response.content = body
    response.text = ""
    _install_mock_requests(monkeypatch, response)

    backend = WebdavMountBackend(base_url="https://dav.example.com/dav", username="u", password="p")
    assert await backend.test_connection() is True


@pytest.mark.asyncio
async def test_webdav_test_connection_unavailable_when_requests_missing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "requests", None)
    backend = WebdavMountBackend(base_url="https://dav.example.com/dav", username="u", password="p")
    assert await backend.test_connection() is False


@pytest.mark.asyncio
async def test_webdav_constructor_validates() -> None:
    with pytest.raises(ValueError):
        WebdavMountBackend(base_url="")


# ═══════════════════════════════════════════════════════════
# SmbMountBackend — graceful fallback
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_smb_test_connection_false_when_dep_missing(monkeypatch) -> None:
    # smbprotocol/smbclient not installed → import smbclient raises.
    monkeypatch.setitem(sys.modules, "smbclient", None)
    backend = SmbMountBackend(host="srv", share="share", username="u", password="p")
    assert await backend.test_connection() is False


@pytest.mark.asyncio
async def test_smb_read_raises_when_dep_missing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "smbclient", None)
    backend = SmbMountBackend(host="srv", share="share", username="u", password="p")
    with pytest.raises(BackendUnavailableError, match="smbprotocol"):
        await backend.read_file("foo.txt")


@pytest.mark.asyncio
async def test_smb_constructor_validates() -> None:
    with pytest.raises(ValueError):
        SmbMountBackend(host="", share="s")
    with pytest.raises(ValueError):
        SmbMountBackend(host="h", share="")


@pytest.mark.asyncio
async def test_smb_unc_path_construction() -> None:
    backend = SmbMountBackend(
        host="server",
        share="share",
        username="u",
        password="p",
        root_path="subdir",
    )
    unc = backend._unc("foo/bar.txt")
    assert unc == "\\\\server\\share\\subdir\\foo\\bar.txt"


@pytest.mark.asyncio
async def test_smb_list_dir_with_mocked_smbclient(monkeypatch) -> None:
    """When smbclient IS available, list_dir routes through it."""
    fake_smbclient = MagicMock()
    fake_smbclient.listdir.return_value = ["a.txt", "sub"]
    info_file = MagicMock()
    info_file.is_directory.return_value = False
    info_file.file_size = 10
    info_file.last_write_time = 1700000000.0
    info_dir = MagicMock()
    info_dir.is_directory.return_value = True
    info_dir.file_size = 0
    info_dir.last_write_time = 1700000000.0
    fake_smbclient.getinfo.side_effect = [info_file, info_dir]
    monkeypatch.setitem(sys.modules, "smbclient", fake_smbclient)

    backend = SmbMountBackend(host="srv", share="share", root_path="sub")
    entries = await backend.list_dir(".", depth=1)
    names = {e.name for e in entries}
    assert {"a.txt", "sub"} == names


# ═══════════════════════════════════════════════════════════
# S3MountBackend — graceful fallback
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_s3_test_connection_false_when_dep_missing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    backend = S3MountBackend(bucket="mybucket")
    assert await backend.test_connection() is False


@pytest.mark.asyncio
async def test_s3_read_raises_when_dep_missing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    backend = S3MountBackend(bucket="mybucket")
    with pytest.raises(BackendUnavailableError, match="boto3"):
        await backend.read_file("foo.txt")


@pytest.mark.asyncio
async def test_s3_constructor_validates() -> None:
    with pytest.raises(ValueError):
        S3MountBackend(bucket="")


@pytest.mark.asyncio
async def test_s3_key_resolution_with_root_path() -> None:
    backend = S3MountBackend(bucket="b", root_path="prefix")
    assert backend._resolve_key("foo.txt") == "prefix/foo.txt"
    assert backend._resolve_key("/foo.txt") == "prefix/foo.txt"
    assert backend._rel_key("prefix/foo.txt") == "foo.txt"
    assert backend._rel_key("prefix") == "prefix"


@pytest.mark.asyncio
async def test_s3_list_dir_with_mocked_boto3(monkeypatch) -> None:
    """Mock boto3 client to validate list_dir pagination + CommonPrefixes."""
    fake_client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {
            "CommonPrefixes": [{"Prefix": "data/sub1/"}],
            "Contents": [
                {
                    "Key": "data/a.txt",
                    "Size": 10,
                    "LastModified": MagicMock(timestamp=lambda: 1700000000.0),
                },
            ],
        }
    ]
    fake_client.get_paginator.return_value = paginator
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    backend = S3MountBackend(bucket="mybucket", root_path="")
    entries = await backend.list_dir("data", depth=1)
    by_name = {e.name: e for e in entries}
    assert "sub1" in by_name and by_name["sub1"].is_dir is True
    assert "a.txt" in by_name and by_name["a.txt"].is_dir is False
    assert by_name["a.txt"].size == 10


@pytest.mark.asyncio
async def test_s3_write_with_mocked_boto3(monkeypatch) -> None:
    fake_client = MagicMock()
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    backend = S3MountBackend(bucket="mybucket", root_path="prefix")
    await backend.write_file("foo.txt", b"payload")
    fake_client.put_object.assert_called_once_with(
        Bucket="mybucket",
        Key="prefix/foo.txt",
        Body=b"payload",
    )


@pytest.mark.asyncio
async def test_s3_mkdir_creates_marker(monkeypatch) -> None:
    fake_client = MagicMock()
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    backend = S3MountBackend(bucket="mybucket")
    await backend.mkdir("newdir")
    fake_client.put_object.assert_called_once_with(
        Bucket="mybucket",
        Key="newdir/",
        Body=b"",
    )


@pytest.mark.asyncio
async def test_s3_stat_file(monkeypatch) -> None:
    fake_client = MagicMock()
    fake_client.head_object.return_value = {
        "ContentLength": 42,
        "LastModified": MagicMock(timestamp=lambda: 1700000000.0),
    }
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    backend = S3MountBackend(bucket="mybucket")
    st = await backend.stat("foo.txt")
    assert st.is_dir is False
    assert st.size == 42
    assert st.modified == 1700000000.0


@pytest.mark.asyncio
async def test_s3_stat_directory(monkeypatch) -> None:
    """If head_object fails but list_objects_v2 finds children → it's a dir."""
    fake_client = MagicMock()
    fake_client.head_object.side_effect = Exception("404 NoSuchKey")
    fake_client.list_objects_v2.return_value = {"KeyCount": 1, "Contents": [{"Key": "dir/a.txt"}]}
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    backend = S3MountBackend(bucket="mybucket")
    st = await backend.stat("dir")
    assert st.is_dir is True


# ═══════════════════════════════════════════════════════════
# NfsMountBackend
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_nfs_delegates_to_local(tmp_path: Path) -> None:
    backend = NfsMountBackend(mount_point=tmp_path)
    assert await backend.test_connection() is True
    await backend.write_file("a.txt", b"hello")
    assert await backend.read_file("a.txt") == b"hello"
    entries = await backend.list_dir(".", depth=1)
    assert any(e.name == "a.txt" for e in entries)


@pytest.mark.asyncio
async def test_nfs_test_connection_false_for_missing_mount(tmp_path: Path) -> None:
    backend = NfsMountBackend(mount_point=tmp_path)
    backend._local.root_path = tmp_path / "missing"
    assert await backend.test_connection() is False


# ═══════════════════════════════════════════════════════════
# MountBackendRegistry
# ═══════════════════════════════════════════════════════════


def test_default_registry_has_all_six_backends() -> None:
    for name in ("local", "sftp", "webdav", "smb", "nfs", "s3"):
        assert default_registry.is_registered(name)


def test_registry_register_rejects_non_subclass() -> None:
    reg = MountBackendRegistry()
    with pytest.raises(TypeError):
        reg.register("bogus", object)  # type: ignore[arg-type]


def test_registry_register_rejects_empty_type() -> None:
    reg = MountBackendRegistry()
    with pytest.raises(ValueError):
        reg.register("", LocalMountBackend)


def test_registry_get_backend_unknown_type_raises() -> None:
    reg = _make_registry()
    with pytest.raises(KeyError):
        reg.get_backend("ws", "bogus", "/tmp", {})


def test_registry_get_backend_creates_local(tmp_path: Path) -> None:
    reg = _make_registry()
    backend = reg.get_backend("ws", "local", str(tmp_path), {})
    assert isinstance(backend, LocalMountBackend)
    assert backend.root_path == tmp_path.resolve()


def test_registry_get_backend_creates_webdav() -> None:
    reg = _make_registry()
    backend = reg.get_backend(
        "ws",
        "webdav",
        "ignored",
        {"base_url": "https://dav.example.com", "username": "u", "password": "p"},
    )
    assert isinstance(backend, WebdavMountBackend)
    assert backend.base_url == "https://dav.example.com"


def test_registry_get_or_create_caches_per_workspace(tmp_path: Path) -> None:
    reg = _make_registry()
    b1 = reg.get_or_create("ws-1", "local", str(tmp_path), {})
    b2 = reg.get_or_create("ws-1", "local", str(tmp_path), {})
    assert b1 is b2


def test_registry_get_or_create_distinct_workspaces(tmp_path: Path) -> None:
    reg = _make_registry()
    b1 = reg.get_or_create("ws-1", "local", str(tmp_path), {})
    b2 = reg.get_or_create("ws-2", "local", str(tmp_path), {})
    assert b1 is not b2


def test_registry_get_or_create_requires_workspace_id(tmp_path: Path) -> None:
    reg = _make_registry()
    with pytest.raises(ValueError):
        reg.get_or_create("", "local", str(tmp_path), {})


def test_registry_get_backend_does_not_cache(tmp_path: Path) -> None:
    """get_backend always returns a fresh instance (no caching)."""
    reg = _make_registry()
    b1 = reg.get_backend("ws", "local", str(tmp_path), {})
    b2 = reg.get_backend("ws", "local", str(tmp_path), {})
    assert b1 is not b2


def test_registry_invalidate_clears_cache(tmp_path: Path) -> None:
    reg = _make_registry()
    b1 = reg.get_or_create("ws-1", "local", str(tmp_path), {})
    reg.invalidate("ws-1")
    b2 = reg.get_or_create("ws-1", "local", str(tmp_path), {})
    assert b1 is not b2


def test_registry_custom_registration(tmp_path: Path) -> None:
    """Operator can register a custom backend type."""

    class CustomBackend(LocalMountBackend):
        pass

    reg = _make_registry()
    reg.register("custom", CustomBackend)
    backend = reg.get_backend("ws", "custom", str(tmp_path), {})
    assert isinstance(backend, CustomBackend)


# ═══════════════════════════════════════════════════════════
# Abstract base sanity
# ═══════════════════════════════════════════════════════════


def test_mount_backend_is_abstract() -> None:
    """MountBackend itself cannot be instantiated."""
    with pytest.raises(TypeError):
        MountBackend()  # type: ignore[abstract]


def test_dir_entry_dataclass_fields() -> None:
    entry = DirEntry(name="a", path="a", is_dir=False, size=10, modified=1.0)
    assert entry.name == "a"
    assert entry.size == 10
    assert entry.modified == 1.0


def test_file_stat_dataclass_fields() -> None:
    st = FileStat(path="a", is_dir=False, size=10, modified=1.0)
    assert st.created is None  # default
    st2 = FileStat(path="a", is_dir=False, size=10, modified=1.0, created=2.0)
    assert st2.created == 2.0

