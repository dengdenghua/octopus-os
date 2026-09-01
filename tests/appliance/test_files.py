"""NAS 文件管理器:路径安全 + 回收站语义 + HTTP。"""

from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.audit import ApplianceAudit
from appliance.data_access import DataAccessScope, DataPathRule
from appliance.files.manager import (
    FileManager,
    InsufficientStorage,
    PathEscape,
    ShareQuotaExceeded,
    UploadOffsetMismatch,
    UploadSessionLimit,
    UploadTooLarge,
)
from appliance.files.router import create_files_router


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("hello")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    return tmp_path


@pytest.fixture()
def fm(root):
    return FileManager(root)


class TestPathSafety:
    def test_list_root_hides_trash(self, fm):
        fm.trash("photo.jpg")  # 创建 .echo-trash
        names = [e.name for e in fm.list_dir("")]
        assert ".echo-trash" not in names
        assert "docs" in names

    def test_traversal_rejected(self, fm):
        # `..` 逃出 root 一律拒绝。
        for bad in ["../etc", "../../x", "docs/../../y"]:
            with pytest.raises(PathEscape):
                fm.list_dir(bad)

    def test_leading_slash_clamped_not_escaped(self, fm):
        # 绝对路径前导斜杠被剥离 → 钳制到 root 下,不会读到真实 /etc。
        with pytest.raises((FileNotFoundError, NotADirectoryError)):
            fm.list_dir("/etc/passwd")

    def test_absolute_path_clamped_to_root(self, fm):
        # 前导斜杠被剥离 → 视为 root 相对,不逃逸。
        entries = fm.list_dir("/docs")
        assert [e.name for e in entries] == ["a.txt"]

    def test_dirs_sort_before_files(self, fm):
        (fm.root / "zeta").mkdir()
        kinds = [e.kind for e in fm.list_dir("")]
        assert kinds == sorted(kinds, key=lambda k: k != "dir")

    def test_internal_trash_path_cannot_be_opened_directly(self, fm):
        fm.trash("photo.jpg")
        with pytest.raises(PathEscape):
            fm.list_dir(".echo-trash")
        with pytest.raises(PathEscape):
            fm.file_for_download(".echo-trash/manifest.json")


class TestMutations:
    def test_mkdir_and_move(self, fm):
        fm.mkdir("inbox")
        fm.move("photo.jpg", "inbox")
        assert (fm.root / "inbox" / "photo.jpg").exists()
        assert not (fm.root / "photo.jpg").exists()

    def test_move_into_existing_dir_keeps_name(self, fm):
        fm.move("photo.jpg", "docs")
        assert (fm.root / "docs" / "photo.jpg").exists()

    def test_move_collision_rejected(self, fm):
        (fm.root / "docs" / "photo.jpg").write_bytes(b"x")
        with pytest.raises(FileExistsError):
            fm.move("photo.jpg", "docs/photo.jpg")

    def test_mkdir_root_rejected(self, fm):
        with pytest.raises(ValueError):
            fm.mkdir("")

    def test_copy_file_and_directory_without_overwrite(self, fm):
        copied_file = fm.copy("photo.jpg", "photo-copy.jpg")
        copied_dir = fm.copy("docs", "docs-copy")

        assert copied_file.path == "photo-copy.jpg"
        assert (fm.root / "photo-copy.jpg").read_bytes() == b"\xff\xd8\xff"
        assert copied_dir.path == "docs-copy"
        assert (fm.root / "docs-copy" / "a.txt").read_text() == "hello"
        with pytest.raises(FileExistsError):
            fm.copy("photo.jpg", "photo-copy.jpg")

    def test_copy_directory_into_itself_rejected(self, fm):
        with pytest.raises(ValueError):
            fm.copy("docs", "docs/nested")

    def test_upload_commit_is_atomic_and_hides_temp_file(self, fm):
        temp, target = fm.prepare_upload("docs", "upload.txt")
        temp.write_bytes(b"streamed")

        assert all(not entry.name.startswith(".echo-upload-") for entry in fm.list_dir("docs"))
        entry = fm.finalize_upload(temp, target)

        assert entry.path == "docs/upload.txt"
        assert target.read_bytes() == b"streamed"
        assert not temp.exists()

    def test_upload_collision_does_not_overwrite(self, fm):
        with pytest.raises(FileExistsError):
            fm.prepare_upload("docs", "a.txt")
        assert (fm.root / "docs" / "a.txt").read_text() == "hello"

    def test_upload_capacity_and_maximum_are_checked_before_write(
        self,
        root,
        monkeypatch,
    ):
        manager = FileManager(
            root,
            upload_reserve_bytes=100,
            max_upload_bytes=1_000,
        )
        monkeypatch.setattr(
            "appliance.files.manager.shutil.disk_usage",
            lambda _path: SimpleNamespace(total=1_000, used=850, free=150),
        )

        with pytest.raises(InsufficientStorage, match="reserved free-space"):
            manager.preflight_upload("docs", "large.bin", 51)
        with pytest.raises(UploadTooLarge, match="maximum size"):
            manager.preflight_upload("docs", "huge.bin", 1_001)

    def test_share_quota_configuration_is_strict_and_symlink_safe(self, root):
        with pytest.raises(ValueError, match="valid JSON"):
            FileManager(root, share_quotas="{")
        with pytest.raises(ValueError, match="JSON object"):
            FileManager(root, share_quotas="[]")
        with pytest.raises(ValueError, match="non-negative integers"):
            FileManager(root, share_quotas={"docs": True})
        with pytest.raises(ValueError, match="invalid share quota path"):
            FileManager(root, share_quotas={"../outside": 1})
        with pytest.raises(ValueError, match="duplicate normalized"):
            FileManager(root, share_quotas={"docs": 1, "docs/": 2})

        (root / "docs-link").symlink_to(root / "docs", target_is_directory=True)
        with pytest.raises(ValueError, match="must not traverse symbolic links"):
            FileManager(root, share_quotas={"docs-link": 1})

    def test_nested_share_quotas_report_logical_usage_and_all_matching_rules(self, root):
        private = root / "docs" / "private"
        private.mkdir()
        (private / "secret.bin").write_bytes(b"xx")
        manager = FileManager(
            root,
            share_quotas={"docs": 10, "docs/private": 3},
        )

        report = manager.preflight_upload("docs/private", "one.bin", 1)

        assert report["shareQuotas"] == [
            {
                "path": "docs",
                "limitBytes": 10,
                "usedBytes": 7,
                "reservedBytes": 0,
                "availableBytes": 3,
                "requestedGrowthBytes": 1,
                "projectedBytes": 8,
            },
            {
                "path": "docs/private",
                "limitBytes": 3,
                "usedBytes": 2,
                "reservedBytes": 0,
                "availableBytes": 1,
                "requestedGrowthBytes": 1,
                "projectedBytes": 3,
            },
        ]
        with pytest.raises(ShareQuotaExceeded) as exceeded:
            manager.preflight_upload("docs/private", "two.bin", 2)
        assert exceeded.value.report["path"] == "docs/private"
        assert exceeded.value.report["projectedBytes"] == 4

    def test_share_quota_reserves_concurrent_uploads_without_blocking_other_share(
        self,
        root,
    ):
        (root / "media").mkdir()
        manager = FileManager(root, share_quotas={"docs": 10, "media": 20})
        manager.create_upload_session("docs", "first.bin", 3)

        with pytest.raises(ShareQuotaExceeded, match="docs"):
            manager.create_upload_session("docs", "second.bin", 3)
        media = manager.create_upload_session("media", "video.bin", 12)
        assert media["target"] == "media/video.bin"

    def test_restarted_upload_session_keeps_its_share_quota_reservation(self, root):
        manager = FileManager(root, share_quotas={"docs": 10})
        session = manager.create_upload_session("docs", "first.bin", 3)
        manager.append_upload_session_chunk(session["sessionId"], 0, b"x")

        restarted = FileManager(root, share_quotas={"docs": 10})

        with pytest.raises(ShareQuotaExceeded):
            restarted.preflight_upload("docs", "second.bin", 3)
        restarted.cancel_upload_session(session["sessionId"])
        assert (
            restarted.preflight_upload("docs", "second.bin", 3)["shareQuotas"][0]["projectedBytes"]
            == 8
        )

    def test_lowered_quota_blocks_recovered_session_before_more_data_is_written(
        self,
        root,
    ):
        manager = FileManager(root, share_quotas={"docs": 10})
        session = manager.create_upload_session("docs", "large.bin", 3)
        manager.append_upload_session_chunk(session["sessionId"], 0, b"x")

        restarted = FileManager(root, share_quotas={"docs": 7})
        assert restarted.get_upload_session(session["sessionId"])["quotaBlocked"] is True
        with pytest.raises(ShareQuotaExceeded):
            restarted.append_upload_session_chunk(session["sessionId"], 1, b"y")
        assert next((root / "docs").glob(".echo-upload-*.part")).read_bytes() == b"x"

    def test_multipart_upload_requires_size_and_holds_quota_until_discard(self, root):
        manager = FileManager(root, share_quotas={"docs": 8})

        with pytest.raises(ValueError, match="size is required"):
            manager.prepare_upload("docs", "unknown.bin")
        temp, target = manager.prepare_upload("docs", "reserved.bin", expected_bytes=3)
        with pytest.raises(ShareQuotaExceeded):
            manager.prepare_upload("docs", "blocked.bin", expected_bytes=1)

        temp.write_bytes(b"abc")
        entry = manager.finalize_upload(temp, target)
        assert entry.path == "docs/reserved.bin"
        assert (root / "docs" / "reserved.bin").read_bytes() == b"abc"

    def test_share_quota_counts_only_overwrite_growth(self, root):
        manager = FileManager(root, share_quotas={"docs": 6})
        temp, target = manager.prepare_upload(
            "docs",
            "a.txt",
            overwrite=True,
            expected_bytes=4,
        )
        temp.write_bytes(b"four")
        manager.finalize_upload(temp, target, overwrite=True)

        with pytest.raises(ShareQuotaExceeded):
            manager.prepare_upload(
                "docs",
                "a.txt",
                overwrite=True,
                expected_bytes=7,
            )

    def test_copy_move_and_restore_cannot_bypass_share_quota(self, root):
        (root / "media").mkdir()
        (root / "media" / "clip.bin").write_bytes(b"clip")
        manager = FileManager(root, share_quotas={"docs": 5})

        with pytest.raises(ShareQuotaExceeded):
            manager.copy("docs/a.txt", "docs/a-copy.txt")
        with pytest.raises(ShareQuotaExceeded):
            manager.move("media/clip.bin", "docs/clip.bin")
        assert (root / "media" / "clip.bin").read_bytes() == b"clip"

        moved = manager.move("docs/a.txt", "docs/renamed.txt")
        assert moved.path == "docs/renamed.txt"
        trashed = manager.trash("docs/renamed.txt")
        (root / "docs" / "replacement.txt").write_bytes(b"12345")
        with pytest.raises(ShareQuotaExceeded):
            manager.restore(trashed["id"])
        assert manager.list_trash()[0]["id"] == trashed["id"]

    def test_copy_excludes_active_internal_upload_temporary_files(self, root):
        manager = FileManager(root, upload_reserve_bytes=0)
        temp, _target = manager.prepare_upload("docs", "active.bin", expected_bytes=3)
        temp.write_bytes(b"abc")

        with pytest.raises(ValueError, match="active uploads"):
            manager.move("docs", "docs-moved")
        with pytest.raises(ValueError, match="active uploads"):
            manager.trash("docs")
        manager.copy("docs", "docs-copy")

        assert (root / "docs-copy" / "a.txt").read_text() == "hello"
        assert not any(
            child.name.startswith(".echo-upload-") for child in (root / "docs-copy").iterdir()
        )
        manager.discard_upload(temp)

    def test_stale_internal_upload_is_cleaned_without_touching_active_upload(
        self,
        root,
    ):
        manager = FileManager(root, stale_upload_seconds=60)
        temp, _target = manager.prepare_upload("docs", "active.bin", expected_bytes=1)
        temp.write_bytes(b"x")
        os.utime(temp, (0, 0))

        manager.list_dir("docs")
        assert temp.exists(), "an in-process upload must never be reaped"
        manager.discard_upload(temp)

        orphan = root / "docs" / ".echo-upload-deadbeef.part"
        orphan.write_bytes(b"orphan")
        os.utime(orphan, (0, 0))
        manager.list_dir("docs")
        assert not orphan.exists()
        with pytest.raises(PathEscape):
            manager.file_for_download("docs/.echo-upload-deadbeef.part")

    def test_resumable_upload_survives_restart_and_commits_atomically(self, root):
        payload = b"echo-resumable-nas"
        expected = hashlib.sha256(payload).hexdigest()
        manager = FileManager(root)
        session = manager.create_upload_session(
            "docs",
            "large.bin",
            len(payload),
            expected_sha256=expected,
            fingerprint="f" * 64,
        )
        manager.append_upload_session_chunk(session["sessionId"], 0, payload[:5])

        restarted = FileManager(root)
        recovered = restarted.get_upload_session(session["sessionId"])
        assert recovered["uploadedBytes"] == 5
        assert recovered["fingerprint"] == "f" * 64
        restarted.append_upload_session_chunk(session["sessionId"], 5, payload[5:])
        entry, digest, verified = restarted.complete_upload_session(session["sessionId"])

        assert entry.path == "docs/large.bin"
        assert (root / "docs" / "large.bin").read_bytes() == payload
        assert digest == expected
        assert verified is True
        assert not list((root / ".echo-upload-sessions").glob("*.json"))

    def test_resumable_upload_recovers_data_fsynced_before_metadata(self, root):
        manager = FileManager(root)
        session = manager.create_upload_session("docs", "recover.bin", 6)
        temp = next((root / "docs").glob(".echo-upload-*.part"))
        temp.write_bytes(b"abc")

        restarted = FileManager(root)

        assert restarted.get_upload_session(session["sessionId"])["uploadedBytes"] == 3

    def test_resumable_upload_rejects_wrong_offset_and_cancel_cleans_state(self, root):
        manager = FileManager(root)
        session = manager.create_upload_session("docs", "cancel.bin", 4)
        session_id = session["sessionId"]

        with pytest.raises(UploadOffsetMismatch) as mismatch:
            manager.append_upload_session_chunk(session_id, 1, b"x")
        assert mismatch.value.expected_offset == 0

        assert manager.cancel_upload_session(session_id)["cancelled"] is True
        assert not list((root / "docs").glob(".echo-upload-*.part"))
        assert ".echo-upload-sessions" not in {entry.name for entry in manager.list_dir("")}
        with pytest.raises(FileNotFoundError):
            manager.get_upload_session(session_id)

    def test_resumable_upload_reserves_declared_remaining_capacity(self, root, monkeypatch):
        manager = FileManager(root, upload_reserve_bytes=100, max_upload_bytes=1_000)
        monkeypatch.setattr(
            "appliance.files.manager.shutil.disk_usage",
            lambda _path: SimpleNamespace(total=1_000, used=0, free=1_000),
        )
        manager.create_upload_session("docs", "first.bin", 600)

        with pytest.raises(InsufficientStorage, match="reserved free-space"):
            manager.create_upload_session("docs", "second.bin", 301)

    def test_resumable_upload_session_count_is_bounded(self, root):
        manager = FileManager(root, max_upload_sessions=1)
        manager.create_upload_session("docs", "first.bin", 1)

        with pytest.raises(UploadSessionLimit, match="too many"):
            manager.create_upload_session("docs", "second.bin", 1)

    def test_expired_resumable_upload_is_reaped_with_metadata(self, root):
        manager = FileManager(root, stale_upload_seconds=60)
        session = manager.create_upload_session("docs", "expired.bin", 4)
        manager.append_upload_session_chunk(session["sessionId"], 0, b"ab")

        report = manager.cleanup_expired_upload_sessions(now=session["updatedAt"] + 61)

        assert report == {"removed": 1, "removedBytes": 2}
        assert not list((root / "docs").glob(".echo-upload-*.part"))
        assert not list((root / ".echo-upload-sessions").glob("*.json"))


class TestRecycleBin:
    def test_trash_is_not_physical_delete(self, fm):
        record = fm.trash("photo.jpg")
        assert not (fm.root / "photo.jpg").exists()  # 从原位消失
        # 但仍在回收站(物理存在),可恢复。
        assert (fm.root / ".echo-trash" / record["id"]).exists()
        assert fm.list_trash()[0]["name"] == "photo.jpg"

    def test_restore_returns_to_origin(self, fm):
        record = fm.trash("docs/a.txt")
        fm.restore(record["id"])
        assert (fm.root / "docs" / "a.txt").read_text() == "hello"
        assert fm.list_trash() == []

    def test_restore_when_origin_taken_uses_suffix(self, fm):
        record = fm.trash("photo.jpg")
        (fm.root / "photo.jpg").write_bytes(b"new")  # 原位被占
        entry = fm.restore(record["id"])
        assert entry.name != "photo.jpg" and "restored" in entry.name
        assert (fm.root / "photo.jpg").read_bytes() == b"new"  # 不覆盖

    def test_empty_trash_is_the_only_physical_delete(self, fm):
        fm.trash("photo.jpg")
        fm.trash("docs")
        assert fm.empty_trash() == 2
        assert fm.list_trash() == []
        # 物理清空后无法再恢复。
        assert list((fm.root / ".echo-trash").iterdir()) == [
            fm.root / ".echo-trash" / "manifest.json"
        ]

    def test_trash_root_rejected(self, fm):
        with pytest.raises(ValueError):
            fm.trash("")


class TestStorageUsage:
    def test_usage_groups_content_trash_folders_quotas_and_upload_reservations(
        self,
        root,
        monkeypatch,
    ):
        videos = root / "videos"
        videos.mkdir()
        (videos / "trip.mp4").write_bytes(b"v" * 10)
        (root / ".echo-internal").mkdir()
        (root / ".echo-internal" / "private.mp4").write_bytes(b"hidden")
        outside = root.parent / "outside.bin"
        outside.write_bytes(b"outside")
        (root / "unsafe-link").symlink_to(outside)
        manager = FileManager(
            root,
            upload_reserve_bytes=0,
            share_quotas={"docs": 20},
        )
        manager.trash("photo.jpg")
        manager.create_upload_session("docs", "pending.bin", 4)
        monkeypatch.setattr(
            "appliance.files.manager.shutil.disk_usage",
            lambda _path: SimpleNamespace(total=100, used=15, free=85),
        )

        usage = manager.storage_usage(fresh=True)
        categories = {item["id"]: item for item in usage["categories"]}
        folders = {item["name"]: item for item in usage["topFolders"]}

        assert usage["schema"] == "echo.storage.usage.v1"
        assert usage["readOnly"] is True
        assert usage["disk"] == {
            "totalBytes": 100,
            "usedBytes": 15,
            "freeBytes": 85,
            "reserveBytes": 0,
            "availableForUploadsBytes": 81,
            "usedPercent": 15.0,
        }
        assert usage["library"]["logicalBytes"] == 15
        assert usage["library"]["files"] == 2
        assert usage["library"]["skippedLinks"] == 1
        assert categories["documents"] == {
            "id": "documents",
            "bytes": 5,
            "files": 1,
        }
        assert categories["videos"] == {"id": "videos", "bytes": 10, "files": 1}
        assert categories["photos"]["bytes"] == 0
        assert folders == {
            "docs": {"name": "docs", "bytes": 5, "files": 1},
            "videos": {"name": "videos", "bytes": 10, "files": 1},
        }
        assert usage["trash"] == {"bytes": 3, "files": 1}
        assert usage["uploads"] == {"reservedBytes": 4, "active": 1}
        assert usage["quotas"] == [
            {
                "path": "docs",
                "limitBytes": 20,
                "usedBytes": 5,
                "reservedBytes": 4,
                "availableBytes": 11,
                "estimated": False,
            }
        ]
        assert manager.storage_usage(fresh=True, max_entries=1)["quotas"][0]["estimated"] is True
        assert str(root) not in json.dumps(usage, ensure_ascii=False)

    def test_usage_is_bounded_and_refreshable(self, root, monkeypatch):
        monkeypatch.setattr(
            "appliance.files.manager.shutil.disk_usage",
            lambda _path: SimpleNamespace(total=100, used=8, free=92),
        )
        manager = FileManager(root, upload_reserve_bytes=0)
        first = manager.storage_usage()
        (root / "later.mp3").write_bytes(b"audio")

        assert manager.storage_usage() is first
        refreshed = manager.storage_usage(fresh=True)
        categories = {item["id"]: item for item in refreshed["categories"]}
        assert categories["audio"]["bytes"] == 5
        assert manager.storage_usage(fresh=True, max_entries=1)["library"]["truncated"]


class TestRouter:
    def _client(self, fm, jwt_secret=None, data_access=None):
        app = FastAPI()
        app.include_router(
            create_files_router(
                fm,
                jwt_secret=jwt_secret,
                data_access=data_access,
            )
        )
        return TestClient(app)

    def test_list_and_trash_roundtrip(self, fm):
        client = self._client(fm)
        listed = client.get("/api/appliance/files/list", params={"path": ""}).json()
        assert {e["name"] for e in listed["entries"]} == {"docs", "photo.jpg"}

        trashed = client.post("/api/appliance/files/trash", json={"path": "photo.jpg"}).json()
        assert trashed["ok"]
        tid = trashed["trashed"]["id"]

        bin_list = client.get("/api/appliance/files/trash").json()
        assert bin_list["entries"][0]["id"] == tid

        restored = client.post("/api/appliance/files/trash/restore", json={"id": tid}).json()
        assert restored["entry"]["name"] == "photo.jpg"

    def test_usage_endpoint_is_read_only_and_supports_explicit_refresh(self, fm):
        client = self._client(fm)

        response = client.get("/api/appliance/files/usage", params={"fresh": "true"})

        assert response.status_code == 200
        assert response.json()["schema"] == "echo.storage.usage.v1"
        assert response.json()["library"]["files"] == 2

    def test_traversal_returns_400(self, fm):
        resp = self._client(fm).get("/api/appliance/files/list", params={"path": "../etc"})
        assert resp.status_code == 400

    def test_upload_download_and_copy_roundtrip(self, fm):
        client = self._client(fm)
        uploaded = client.post(
            "/api/appliance/files/upload",
            data={"path": "docs"},
            files={"file": ("report.txt", b"echo nas", "text/plain")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["entry"]["path"] == "docs/report.txt"
        assert uploaded.json()["sha256"] == hashlib.sha256(b"echo nas").hexdigest()
        assert uploaded.json()["hashVerified"] is False

        downloaded = client.get(
            "/api/appliance/files/download",
            params={"path": "docs/report.txt"},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == b"echo nas"
        assert "report.txt" in downloaded.headers["content-disposition"]

        ranged = client.get(
            "/api/appliance/files/download",
            params={"path": "docs/report.txt"},
            headers={"Range": "bytes=0-3"},
        )
        assert ranged.status_code == 206
        assert ranged.content == b"echo"
        assert ranged.headers["content-range"] == "bytes 0-3/8"

        copied = client.post(
            "/api/appliance/files/copy",
            json={"src": "docs/report.txt", "dst": "docs/report-copy.txt"},
        )
        assert copied.status_code == 200
        assert (fm.root / "docs" / "report-copy.txt").read_bytes() == b"echo nas"

    def test_upload_duplicate_returns_409_without_overwrite(self, fm):
        response = self._client(fm).post(
            "/api/appliance/files/upload",
            data={"path": "docs"},
            files={"file": ("a.txt", b"replacement", "text/plain")},
        )

        assert response.status_code == 409
        assert (fm.root / "docs" / "a.txt").read_text() == "hello"

    def test_preflight_rejects_full_disk_and_oversized_upload(self, root, monkeypatch):
        manager = FileManager(
            root,
            upload_reserve_bytes=100,
            max_upload_bytes=1_000,
        )
        client = self._client(manager)
        monkeypatch.setattr(
            "appliance.files.manager.shutil.disk_usage",
            lambda _path: SimpleNamespace(total=1_000, used=850, free=150),
        )

        full = client.post(
            "/api/appliance/files/upload/preflight",
            json={"path": "docs", "filename": "large.bin", "size": 51},
        )
        oversized = client.post(
            "/api/appliance/files/upload/preflight",
            json={"path": "docs", "filename": "huge.bin", "size": 1_001},
        )

        assert full.status_code == 507
        assert oversized.status_code == 413

    def test_share_quota_preflight_and_upload_return_507_without_temp_artifacts(
        self,
        root,
    ):
        manager = FileManager(root, share_quotas={"docs": 5})
        client = self._client(manager)

        preflight = client.post(
            "/api/appliance/files/upload/preflight",
            json={"path": "docs", "filename": "blocked.bin", "size": 1},
        )
        upload = client.post(
            "/api/appliance/files/upload",
            data={"path": "docs", "size": "1"},
            files={"file": ("blocked.bin", b"x", "application/octet-stream")},
        )

        assert preflight.status_code == 507
        assert preflight.json()["detail"] == {
            "message": "share quota exceeded for 'docs'",
            "path": "docs",
            "limitBytes": 5,
            "usedBytes": 5,
            "reservedBytes": 0,
            "availableBytes": 0,
            "requestedGrowthBytes": 1,
            "projectedBytes": 6,
        }
        assert upload.status_code == 507
        assert not (root / "docs" / "blocked.bin").exists()
        assert not any(
            child.name.startswith(".echo-upload-") for child in (root / "docs").iterdir()
        )

    def test_upload_validates_declared_size_and_optional_sha256(self, fm):
        client = self._client(fm)
        payload = b"hash me"
        expected = hashlib.sha256(payload).hexdigest()
        verified = client.post(
            "/api/appliance/files/upload",
            data={"path": "docs", "size": str(len(payload)), "sha256": expected},
            files={"file": ("verified.bin", payload, "application/octet-stream")},
        )
        wrong_size = client.post(
            "/api/appliance/files/upload",
            data={"path": "docs", "size": "99"},
            files={"file": ("wrong-size.bin", payload, "application/octet-stream")},
        )
        wrong_hash = client.post(
            "/api/appliance/files/upload",
            data={"path": "docs", "size": str(len(payload)), "sha256": "0" * 64},
            files={"file": ("wrong-hash.bin", payload, "application/octet-stream")},
        )

        assert verified.status_code == 200
        assert verified.json()["sha256"] == expected
        assert verified.json()["hashVerified"] is True
        assert wrong_size.status_code == 400
        assert wrong_hash.status_code == 422
        assert not (fm.root / "docs" / "wrong-size.bin").exists()
        assert not (fm.root / "docs" / "wrong-hash.bin").exists()
        assert not any(
            child.name.startswith(".echo-upload-") for child in (fm.root / "docs").iterdir()
        )

    def test_resumable_upload_chunk_hash_offset_and_completion(self, fm):
        client = self._client(fm)
        payload = b"chunked echo nas"
        created = client.post(
            "/api/appliance/files/upload/sessions",
            json={
                "path": "docs",
                "filename": "chunked.bin",
                "size": len(payload),
                "fingerprint": "a" * 64,
            },
        )
        assert created.status_code == 200
        session_id = created.json()["sessionId"]

        first = payload[:7]
        appended = client.put(
            f"/api/appliance/files/upload/sessions/{session_id}/chunk",
            content=first,
            headers={
                "Upload-Offset": "0",
                "Upload-Chunk-SHA256": hashlib.sha256(first).hexdigest(),
            },
        )
        conflict = client.put(
            f"/api/appliance/files/upload/sessions/{session_id}/chunk",
            content=b"wrong offset",
            headers={
                "Upload-Offset": "0",
                "Upload-Chunk-SHA256": hashlib.sha256(b"wrong offset").hexdigest(),
            },
        )
        rest = payload[7:]
        client.put(
            f"/api/appliance/files/upload/sessions/{session_id}/chunk",
            content=rest,
            headers={
                "Upload-Offset": "7",
                "Upload-Chunk-SHA256": hashlib.sha256(rest).hexdigest(),
            },
        )
        completed = client.post(
            f"/api/appliance/files/upload/sessions/{session_id}/complete",
            json={},
        )

        assert appended.status_code == 200
        assert appended.json()["uploadedBytes"] == 7
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["uploadedBytes"] == 7
        assert completed.status_code == 200
        assert completed.json()["sha256"] == hashlib.sha256(payload).hexdigest()
        assert (fm.root / "docs" / "chunked.bin").read_bytes() == payload

    def test_resumable_upload_rejects_bad_chunk_hash_without_advancing(self, fm):
        client = self._client(fm)
        created = client.post(
            "/api/appliance/files/upload/sessions",
            json={"path": "docs", "filename": "bad-hash.bin", "size": 3},
        ).json()
        session_id = created["sessionId"]

        rejected = client.put(
            f"/api/appliance/files/upload/sessions/{session_id}/chunk",
            content=b"abc",
            headers={"Upload-Offset": "0", "Upload-Chunk-SHA256": "0" * 64},
        )
        status = client.get(f"/api/appliance/files/upload/sessions/{session_id}")

        assert rejected.status_code == 422
        assert status.json()["uploadedBytes"] == 0

    def test_successful_chunks_do_not_flood_the_device_audit(self, fm, tmp_path):
        audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret="a" * 48)
        app = FastAPI()
        app.include_router(create_files_router(fm, audit=audit))
        client = TestClient(app)
        payload = b"twelve-bytes"
        created = client.post(
            "/api/appliance/files/upload/sessions",
            json={
                "path": "docs",
                "filename": "audited.bin",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        ).json()
        session_id = created["sessionId"]
        for offset in range(0, len(payload), 4):
            chunk = payload[offset : offset + 4]
            response = client.put(
                f"/api/appliance/files/upload/sessions/{session_id}/chunk",
                content=chunk,
                headers={
                    "Upload-Offset": str(offset),
                    "Upload-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                },
            )
            assert response.status_code == 200
        assert (
            client.post(f"/api/appliance/files/upload/sessions/{session_id}/complete").status_code
            == 200
        )

        records = [json.loads(line) for line in audit.path.read_text().splitlines()]
        actions = [record["payload"]["action"] for record in records]
        assert actions.count("files.upload.session.create") == 2
        assert actions.count("files.upload.session.complete") == 2
        assert "files.upload.session.chunk" not in actions

    def test_download_directory_and_internal_trash_are_rejected(self, fm):
        client = self._client(fm)
        assert (
            client.get("/api/appliance/files/download", params={"path": "docs"}).status_code == 400
        )
        assert (
            client.get(
                "/api/appliance/files/download",
                params={"path": ".echo-trash/manifest.json"},
            ).status_code
            == 400
        )

    def test_auth_required_when_secret_set(self, fm):
        client = self._client(fm, jwt_secret="z" * 48)
        assert client.get("/api/appliance/files/list").status_code == 401

    def test_member_lists_reads_and_measures_only_authorized_share_paths(self, fm):
        (fm.root / "Family").mkdir()
        (fm.root / "Family" / "public.txt").write_text("family")
        (fm.root / "Family" / "Private").mkdir()
        (fm.root / "Family" / "Private" / "secret.txt").write_text("secret")
        (fm.root / "Drop Box").mkdir()
        (fm.root / "Unshared").mkdir()
        (fm.root / "Unshared" / "hidden.txt").write_text("hidden")
        scope = DataAccessScope(
            actor="local:alice",
            operator=False,
            rules=(
                DataPathRule(("Family",), "read"),
                DataPathRule(("Family", "Private"), "none"),
                DataPathRule(("Drop Box",), "readWrite"),
            ),
        )

        class _Policy:
            def scope_for_actor(self, _actor):
                return scope

        client = self._client(fm, data_access=_Policy())

        root = client.get("/api/appliance/files/list").json()
        family = client.get("/api/appliance/files/list", params={"path": "Family"}).json()
        usage = client.get("/api/appliance/files/usage", params={"fresh": "true"}).json()

        assert {entry["name"] for entry in root["entries"]} == {"Family", "Drop Box"}
        assert [entry["name"] for entry in family["entries"]] == ["public.txt"]
        assert (
            client.get(
                "/api/appliance/files/download",
                params={"path": "Family/public.txt"},
            ).status_code
            == 200
        )
        for hidden in ("Family/Private/secret.txt", "Unshared/hidden.txt"):
            assert (
                client.get(
                    "/api/appliance/files/download",
                    params={"path": hidden},
                ).status_code
                == 403
            )
        assert usage["library"]["logicalBytes"] == len(b"family")
        assert {folder["name"] for folder in usage["topFolders"]} == {"Family"}
        assert "hidden" not in json.dumps(usage)

    def test_member_mutations_sessions_and_trash_cannot_cross_share_permissions(self, fm):
        for name in ("Family", "Drop Box", "Unshared"):
            (fm.root / name).mkdir()
        (fm.root / "Drop Box" / "own.txt").write_text("own")
        (fm.root / "Unshared" / "other.txt").write_text("other")
        scope = DataAccessScope(
            actor="local:alice",
            operator=False,
            rules=(
                DataPathRule(("Family",), "read"),
                DataPathRule(("Drop Box",), "readWrite"),
            ),
        )

        class _Policy:
            def scope_for_actor(self, _actor):
                return scope

        member = self._client(fm, data_access=_Policy())
        admin = self._client(fm)

        denied_upload = member.post(
            "/api/appliance/files/upload",
            data={"path": "Family"},
            files={"file": ("blocked.txt", b"blocked", "text/plain")},
        )
        allowed_upload = member.post(
            "/api/appliance/files/upload",
            data={"path": "Drop Box"},
            files={"file": ("allowed.txt", b"allowed", "text/plain")},
        )
        foreign_session = admin.post(
            "/api/appliance/files/upload/sessions",
            json={"path": "Unshared", "filename": "foreign.bin", "size": 3},
        ).json()["sessionId"]

        own_trash = fm.trash("Drop Box/own.txt")
        foreign_trash = fm.trash("Unshared/other.txt")
        visible_trash = member.get("/api/appliance/files/trash").json()["entries"]

        assert denied_upload.status_code == 403
        assert allowed_upload.status_code == 200
        assert not (fm.root / "Family" / "blocked.txt").exists()
        assert (fm.root / "Drop Box" / "allowed.txt").read_bytes() == b"allowed"
        assert (
            member.get(f"/api/appliance/files/upload/sessions/{foreign_session}").status_code == 403
        )
        assert [entry["id"] for entry in visible_trash] == [own_trash["id"]]
        assert (
            member.post(
                "/api/appliance/files/trash/restore",
                json={"id": foreign_trash["id"]},
            ).status_code
            == 404
        )
        assert (
            member.post(
                "/api/appliance/files/trash/restore",
                json={"id": own_trash["id"]},
            ).status_code
            == 200
        )
        assert member.post("/api/appliance/files/trash/empty").status_code == 403
