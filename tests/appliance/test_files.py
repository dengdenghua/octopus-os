"""NAS 文件管理器:路径安全 + 回收站语义 + HTTP。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.files.manager import FileManager, PathEscape
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
        fm.trash("photo.jpg")  # 创建 .octopus-trash
        names = [e.name for e in fm.list_dir("")]
        assert ".octopus-trash" not in names
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


class TestRecycleBin:
    def test_trash_is_not_physical_delete(self, fm):
        record = fm.trash("photo.jpg")
        assert not (fm.root / "photo.jpg").exists()  # 从原位消失
        # 但仍在回收站(物理存在),可恢复。
        assert (fm.root / ".octopus-trash" / record["id"]).exists()
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
        assert list((fm.root / ".octopus-trash").iterdir()) == [
            fm.root / ".octopus-trash" / "manifest.json"
        ]

    def test_trash_root_rejected(self, fm):
        with pytest.raises(ValueError):
            fm.trash("")


class TestRouter:
    def _client(self, fm, jwt_secret=None):
        app = FastAPI()
        app.include_router(create_files_router(fm, jwt_secret=jwt_secret))
        return TestClient(app)

    def test_list_and_trash_roundtrip(self, fm):
        client = self._client(fm)
        listed = client.get("/api/appliance/files/list", params={"path": ""}).json()
        assert {e["name"] for e in listed["entries"]} == {"docs", "photo.jpg"}

        trashed = client.post(
            "/api/appliance/files/trash", json={"path": "photo.jpg"}
        ).json()
        assert trashed["ok"]
        tid = trashed["trashed"]["id"]

        bin_list = client.get("/api/appliance/files/trash").json()
        assert bin_list["entries"][0]["id"] == tid

        restored = client.post(
            "/api/appliance/files/trash/restore", json={"id": tid}
        ).json()
        assert restored["entry"]["name"] == "photo.jpg"

    def test_traversal_returns_400(self, fm):
        resp = self._client(fm).get(
            "/api/appliance/files/list", params={"path": "../etc"}
        )
        assert resp.status_code == 400

    def test_auth_required_when_secret_set(self, fm):
        client = self._client(fm, jwt_secret="z" * 48)
        assert client.get("/api/appliance/files/list").status_code == 401
