"""Real filesystem tests for photo streams and Windows handle boundaries."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from appliance.agent_api import images
from appliance.photos import AgentImageIndexAdapter, PhotoLibraryService, PhotoPathError, safe_file


def _library(tmp_path):
    root = tmp_path / "nas"
    album = root / "相册"
    album.mkdir(parents=True)
    Image.new("RGB", (80, 48), (200, 40, 70)).save(album / "图片.jpg")
    return root, PhotoLibraryService(root, tmp_path / "state")


def test_real_photo_stream_handles_unicode_and_retains_the_verified_file(tmp_path):
    root, service = _library(tmp_path)
    original = (root / "相册" / "图片.jpg").read_bytes()

    opened = service.original("相册/图片.jpg")
    with opened.stream:
        assert opened.stream.read() == original
        assert opened.size == len(original)
    assert opened.stream.closed
    assert service.thumbnail("相册/图片.jpg")[1] == "image/webp"


def test_intermediate_symbolic_link_cannot_expose_a_photo(tmp_path):
    root, service = _library(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    Image.new("RGB", (2, 2)).save(outside / "private.jpg")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PhotoPathError):
        service.original("linked/private.jpg")
    assert service.library()["total"] == 1


def test_root_replaced_with_a_link_after_configuration_is_rejected(tmp_path):
    root, service = _library(tmp_path)
    previous = tmp_path / "previous-root"
    root.rename(previous)
    root.symlink_to(previous, target_is_directory=True)

    with pytest.raises(PhotoPathError):
        service.original("相册/图片.jpg")


def test_case_variants_of_internal_photo_paths_are_rejected(tmp_path):
    root, service = _library(tmp_path)
    internal = root / ".ECHO-TRASH"
    internal.mkdir()
    Image.new("RGB", (2, 2)).save(internal / "private.jpg")

    with pytest.raises(PhotoPathError):
        service.original(".ECHO-TRASH/private.jpg")
    assert service.library()["total"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows stream and DOS-name syntax")
@pytest.mark.parametrize(
    "path",
    ["相册/图片.jpg:hidden.jpg", "相册/图片.jpg ", "相册./图片.jpg", "NUL.jpg", "C:/图片.jpg"],
)
def test_windows_alternate_streams_and_aliases_are_not_photos(tmp_path, path):
    _root, service = _library(tmp_path)

    with pytest.raises(PhotoPathError):
        service.original(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction reparse-point semantics")
def test_windows_junction_is_rejected_by_scan_and_real_open(tmp_path):
    import _winapi

    root, service = _library(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    Image.new("RGB", (2, 2)).save(outside / "private.jpg")
    junction = root / "junction"
    _winapi.CreateJunction(str(outside), str(junction))
    assert junction.is_junction()

    with pytest.raises(PhotoPathError):
        service.original("junction/private.jpg")
    assert service.library()["total"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-handle sharing semantics")
def test_windows_pinned_directory_cannot_be_renamed_between_component_opens(tmp_path, monkeypatch):
    root, service = _library(tmp_path)
    actual_open = safe_file._open_windows_component
    attempted = []

    def racing_open(path, *, directory):
        handle = actual_open(path, directory=directory)
        if directory and path == root / "相册":
            try:
                with pytest.raises(OSError):
                    path.rename(root / "moved")
                attempted.append(True)
            except BaseException:
                safe_file._windows_api().CloseHandle(handle)
                raise
        return handle

    monkeypatch.setattr(safe_file, "_open_windows_component", racing_open)
    opened = service.original("相册/图片.jpg")
    with opened.stream:
        assert opened.stream.read() == (root / "相册" / "图片.jpg").read_bytes()
    assert attempted == [True]
    # All parent handles were released once the verified file handle existed.
    (root / "相册").rename(root / "moved")


@pytest.mark.skipif(os.name != "nt", reason="Windows directory swap before handle acquisition")
def test_windows_directory_swapped_to_link_before_open_is_rejected(tmp_path, monkeypatch):
    root, service = _library(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    Image.new("RGB", (2, 2)).save(outside / "图片.jpg")
    actual_open = safe_file._open_windows_component
    replaced = []

    def racing_open(path, *, directory):
        if directory and path == Path(root.anchor) and not replaced:
            (root / "相册").rename(root / "moved")
            (root / "相册").symlink_to(outside, target_is_directory=True)
            replaced.append(True)
        return actual_open(path, directory=directory)

    monkeypatch.setattr(safe_file, "_open_windows_component", racing_open)
    with pytest.raises(PhotoPathError):
        service.original("相册/图片.jpg")
    assert replaced == [True]


@pytest.mark.skipif(os.name != "nt", reason="Windows file-handle sharing semantics")
def test_windows_open_photo_denies_overwrite_until_stream_is_closed(tmp_path):
    root, service = _library(tmp_path)
    source = root / "相册" / "图片.jpg"
    expected = source.read_bytes()

    opened = service.original("相册/图片.jpg")
    with opened.stream:
        with pytest.raises(OSError):
            source.write_bytes(b"replacement")
        assert opened.stream.read() == expected
    source.write_bytes(b"replacement")


def test_agent_adapter_uses_the_same_real_safe_stream_without_unsafe_fallback(
    tmp_path, monkeypatch
):
    root, _service = _library(tmp_path)
    outside = tmp_path / "outside.jpg"
    Image.new("RGB", (2, 2)).save(outside)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the indexing thread must not fall back to an unvetted path")

    module = SimpleNamespace(
        search_by_text=forbidden,
        _iter_images=forbidden,
        _load_image=forbidden,
        _mtime=forbidden,
    )

    def build(scan_root, **kwargs):
        paths = module._iter_images(scan_root, max_files=kwargs["max_files"])
        assert len(paths) == 1
        image = module._load_image(paths[0])
        assert image.size == (80, 48)
        image.close()
        assert module._mtime(paths[0]) > 0
        assert module._load_image(outside) is None
        assert module._iter_images(tmp_path, max_files=10) == []
        paths[0].unlink()
        paths[0].symlink_to(outside)
        assert module._load_image(paths[0]) is None
        return {"ok": True, "indexed": 1, "semantic": True}

    module.build_index = build
    monkeypatch.delenv("ECHO_IMAGE_SEMANTIC", raising=False)
    monkeypatch.setattr(images, "_dependency_present", lambda _name: True)
    adapter = AgentImageIndexAdapter()
    monkeypatch.setattr(adapter, "_module", lambda: module)
    result = adapter.build_index(
        root, tmp_path / "index.db", ["相册/图片.jpg"], include_faces=False, max_files=10
    )

    assert result["indexed"] == 1
    assert module._load_image is forbidden
