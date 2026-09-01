"""Echo Photos safe projection, Agent adapter boundary and approval tests."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.data_access import DataAccessScope, DataPathRule
from appliance.photos import PhotoLibraryService, PhotoPathError, create_photos_router
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "photos-test-secret-that-is-long-enough-for-local-jwt"
PASSWORD = "photos-device-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()


def _image(path: Path, *, color: tuple[int, int, int] = (220, 80, 90)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 48), color).save(path)


class _Backend:
    def __init__(self, *, available: bool = True) -> None:
        self.is_available = available
        self.builds: list[tuple[Path, Path, tuple[str, ...], bool, int]] = []
        self.search_results: list[dict] | None = None

    def available(self) -> bool:
        return self.is_available

    def build_index(
        self,
        root: Path,
        db_path: Path,
        image_paths,
        *,
        include_faces: bool,
        max_files: int,
    ) -> dict:
        paths = tuple(image_paths)
        self.builds.append((root, db_path, paths, include_faces, max_files))
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE image_clip (path TEXT PRIMARY KEY, clip_embedding BLOB);
                CREATE TABLE image_faces (path TEXT, face_index INTEGER, face_embedding BLOB);
                CREATE TABLE image_meta (
                    path TEXT PRIMARY KEY, width INTEGER, height INTEGER,
                    mtime REAL, exif_time TEXT, file_type TEXT, location TEXT
                );
                CREATE TABLE image_hashes (path TEXT PRIMARY KEY, dhash TEXT);
                CREATE TABLE image_quality (path TEXT PRIMARY KEY, sharpness REAL);
                """
            )
            for index, path in enumerate(paths):
                conn.execute("INSERT INTO image_clip VALUES (?, ?)", (path, b"vector"))
                conn.execute(
                    "INSERT INTO image_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (path, 80, 48, 1.0, "2026:08:28 12:00:00", ".jpg", ""),
                )
                conn.execute(
                    "INSERT INTO image_hashes VALUES (?, ?)",
                    (path, "same" if index < 2 else f"hash-{index}"),
                )
                conn.execute(
                    "INSERT INTO image_quality VALUES (?, ?)",
                    (path, 10.0 if index == 0 else 100.0),
                )
            if include_faces and paths:
                conn.execute("INSERT INTO image_faces VALUES (?, ?, ?)", (paths[0], 0, b"face"))
            conn.commit()
        finally:
            conn.close()
        return {
            "ok": True,
            "indexed": len(paths),
            "faces": 1 if include_faces and paths else 0,
            "semantic": True,
        }

    def search_by_text(self, query: str, *, top_k: int, db_path: Path):
        assert query
        assert top_k >= 1
        assert db_path.is_file()
        return self.search_results


def _client(tmp_path: Path, root: Path, backend: _Backend, *, data_access=None):
    audit = ApplianceAudit.from_data_dir(tmp_path / "state", jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"photos-test-nonce" * 2,
    )
    service = PhotoLibraryService(root, tmp_path / "state", backend=backend)
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_photos_router(
            service,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
            data_access=data_access,
        )
    )
    client = TestClient(app)
    token = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    client.cookies.set("echo_session", token)
    return client, service, audit


def test_member_photo_projection_filters_every_path_and_index_side_channel(tmp_path) -> None:
    root = tmp_path / "nas"
    _image(root / "Family" / "allowed.jpg")
    _image(root / "Family" / "Private" / "secret.jpg")
    _image(root / "Unshared" / "hidden.jpg")
    backend = _Backend()
    scope = DataAccessScope(
        actor="local:alice",
        operator=False,
        rules=(
            DataPathRule(("Family",), "read"),
            DataPathRule(("Family", "Private"), "none"),
        ),
    )

    class _Policy:
        def scope_for_actor(self, _actor):
            return scope

    client, service, _audit = _client(
        tmp_path,
        root,
        backend,
        data_access=_Policy(),
    )
    scan = service.scan(fresh=True)
    backend.build_index(
        root,
        service.db_path,
        [item.path for item in scan.files],
        include_faces=True,
        max_files=100,
    )
    with sqlite3.connect(service.db_path) as connection:
        connection.execute("DELETE FROM image_faces")
        connection.execute(
            "INSERT INTO image_faces VALUES (?, ?, ?)",
            ("Family/allowed.jpg", 0, b"face"),
        )
    backend.search_results = [
        {"path": "Family/Private/secret.jpg", "score": 0.99},
        {"path": "Unshared/hidden.jpg", "score": 0.98},
        {"path": "Family/allowed.jpg", "score": 0.8},
    ]

    library = client.get("/api/appliance/photos/library").json()
    status = client.get("/api/appliance/photos/status").json()
    search = client.post(
        "/api/appliance/photos/search",
        json={"query": "family", "limit": 10},
    ).json()

    assert [item["path"] for item in library["items"]] == ["Family/allowed.jpg"]
    assert library["total"] == 1
    assert status["library"]["imageCount"] == 1
    assert status["index"]["indexed"] == 1
    assert status["index"]["faces"] == 1
    assert [item["path"] for item in search["items"]] == ["Family/allowed.jpg"]
    assert (
        client.get(
            "/api/appliance/photos/thumbnail",
            params={"path": "Family/allowed.jpg"},
        ).status_code
        == 200
    )
    for hidden in ("Family/Private/secret.jpg", "Unshared/hidden.jpg"):
        assert (
            client.get(
                "/api/appliance/photos/original",
                params={"path": hidden},
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/api/appliance/photos/plans/index",
            json={"includeFaces": False},
        ).status_code
        == 403
    )


def test_library_skips_internals_and_links_without_exposing_absolute_paths(tmp_path) -> None:
    root = tmp_path / "nas"
    _image(root / "相册" / "夏天.jpg")
    _image(root / "cover.png", color=(30, 100, 220))
    _image(root / ".echo-trash" / "deleted.jpg")
    _image(root / ".echo-private" / "internal.jpg")
    outside = tmp_path / "outside.jpg"
    _image(outside)
    (root / "linked.jpg").symlink_to(outside)
    service = PhotoLibraryService(root, tmp_path / "state", backend=_Backend())

    result = service.library()

    assert result["total"] == 2
    assert result["unsafeLinksSkipped"] == 1
    assert {item["path"] for item in result["items"]} == {"相册/夏天.jpg", "cover.png"}
    assert all(str(tmp_path) not in item["path"] for item in result["items"])


def test_thumbnail_is_bounded_cacheable_and_rejects_traversal_or_symlink(tmp_path) -> None:
    root = tmp_path / "nas"
    source = root / "album" / "wide.jpg"
    _image(source)
    outside = tmp_path / "outside.jpg"
    _image(outside)
    (root / "linked.jpg").symlink_to(outside)
    service = PhotoLibraryService(root, tmp_path / "state", backend=_Backend())

    payload, media_type, etag = service.thumbnail("album/wide.jpg", size=64)

    assert media_type == "image/webp"
    assert len(etag) == 64
    assert payload is not None
    cached, cached_type, cached_etag = service.thumbnail(
        "album/wide.jpg",
        size=64,
        if_none_match=f'W/"{etag}"',
    )
    assert (cached, cached_type, cached_etag) == (None, "image/webp", etag)
    with Image.open(Path(source).with_name("wide.jpg")) as original:
        assert original.size == (80, 48)
    with Image.open(__import__("io").BytesIO(payload)) as preview:
        assert max(preview.size) <= 64
    for unsafe in ("../outside.jpg", "linked.jpg", ".echo-trash/deleted.jpg"):
        try:
            service.thumbnail(unsafe)
        except (PhotoPathError, FileNotFoundError):
            pass
        else:  # pragma: no cover - a path boundary regression
            raise AssertionError(f"unsafe thumbnail path accepted: {unsafe}")


def test_original_is_authenticated_cacheable_range_safe_and_keeps_inline_mime(tmp_path) -> None:
    root = tmp_path / "nas"
    source = root / "album" / "wide.jpg"
    _image(source)
    outside = tmp_path / "outside.jpg"
    _image(outside)
    (root / "linked.jpg").symlink_to(outside)
    client, _service, _audit = _client(tmp_path, root, _Backend())

    anonymous = TestClient(client.app).get(
        "/api/appliance/photos/original", params={"path": "album/wide.jpg"}
    )
    assert anonymous.status_code == 401
    response = client.get(
        "/api/appliance/photos/original",
        params={"path": "album/wide.jpg"},
        headers={"Range": "bytes=0-15"},
    )
    assert response.status_code == 206
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-range"] == f"bytes 0-15/{source.stat().st_size}"
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) == 16
    assert "content-disposition" not in response.headers

    cached = client.get(
        "/api/appliance/photos/original",
        params={"path": "album/wide.jpg"},
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304
    invalid_range = client.get(
        "/api/appliance/photos/original",
        params={"path": "album/wide.jpg"},
        headers={"Range": "bytes=999999-"},
    )
    assert invalid_range.status_code == 416
    assert invalid_range.headers["content-range"] == f"bytes */{source.stat().st_size}"
    for unsafe in ("../outside.jpg", "linked.jpg"):
        rejected = client.get("/api/appliance/photos/original", params={"path": unsafe})
        assert rejected.status_code == 400


def test_plan_is_deterministic_and_blocks_missing_backend_or_unsafe_link(tmp_path) -> None:
    root = tmp_path / "nas"
    _image(root / "one.jpg")
    unavailable = PhotoLibraryService(root, tmp_path / "state-a", backend=_Backend(available=False))

    first = unavailable.plan_index(include_faces=False)
    second = unavailable.plan_index(include_faces=False)

    assert first == second
    assert first["ready"] is False
    assert [item["code"] for item in first["blockers"]] == ["AGENT_INDEX_UNAVAILABLE"]
    assert first["approvalAction"] == "photos.index.build"

    outside = tmp_path / "outside.jpg"
    _image(outside)
    (root / "linked.jpg").symlink_to(outside)
    linked = PhotoLibraryService(root, tmp_path / "state-b", backend=_Backend())
    linked_plan = linked.plan_index()
    assert linked_plan["ready"] is True
    assert linked_plan["blockers"] == []
    assert [item["code"] for item in linked_plan["warnings"]] == ["UNSAFE_LINKS_PRESENT"]


def test_approved_index_job_uses_vetted_paths_and_publishes_status_and_search(tmp_path) -> None:
    root = tmp_path / "nas"
    _image(root / "trip" / "beach.jpg")
    _image(root / "trip" / "family.jpg", color=(30, 180, 120))
    _image(root / ".echo-trash" / "secret.jpg")
    backend = _Backend()
    client, service, audit = _client(tmp_path, root, backend)

    assert TestClient(client.app).get("/api/appliance/photos/status").status_code == 401
    plan_response = client.post(
        "/api/appliance/photos/plans/index",
        json={"includeFaces": True},
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["ready"] is True
    assert plan["imageCount"] == 2

    missing = client.post(
        "/api/appliance/photos/plans/index/apply",
        json={"planId": plan["planId"], "includeFaces": True},
    )
    assert missing.status_code == 403
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "photos.index.build",
            "target": plan["planId"],
            "password": PASSWORD,
        },
    )
    assert issued.status_code == 200
    applied = client.post(
        "/api/appliance/photos/plans/index/apply",
        json={"planId": plan["planId"], "includeFaces": True},
        headers={APPROVAL_HEADER: issued.json()["approvalToken"]},
    )
    assert applied.status_code == 200
    assert applied.json()["schema"] == "echo.photos.index-job.v1"

    job = service.wait_for_idle()
    assert job["state"] == "succeeded"
    assert set(backend.builds[0][2]) == {"trip/beach.jpg", "trip/family.jpg"}
    assert ".echo-trash/secret.jpg" not in backend.builds[0][2]
    status = client.get("/api/appliance/photos/status").json()
    assert status["index"] == {
        "backendAvailable": True,
        "databaseExists": True,
        "maxFiles": 4000,
        "indexed": 2,
        "faces": 1,
        "duplicateGroups": 1,
        "blurry": 1,
    }

    backend.search_results = [
        {"path": "trip/family.jpg", "score": 0.91},
        {"path": "../outside.jpg", "score": 0.99},
    ]
    search = client.post(
        "/api/appliance/photos/search",
        json={"query": "海边的家人", "limit": 10},
    ).json()
    assert search["mode"] == "semantic"
    assert [(item["path"], item["score"]) for item in search["items"]] == [
        ("trip/family.jpg", 0.91)
    ]
    outcomes = [
        event["payload"]["outcome"]
        for event in audit.recent(20)
        if event["payload"]["action"] == "photos.index.build"
    ]
    assert outcomes == ["attempted", "succeeded"]


def test_plan_drift_is_rejected_before_approval_is_consumed(tmp_path) -> None:
    root = tmp_path / "nas"
    photo = root / "one.jpg"
    _image(photo)
    backend = _Backend()
    client, _service, _audit = _client(tmp_path, root, backend)
    plan = client.post("/api/appliance/photos/plans/index", json={}).json()
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "photos.index.build",
            "target": plan["planId"],
            "password": PASSWORD,
        },
    ).json()
    _image(photo, color=(1, 2, 3))

    changed = client.post(
        "/api/appliance/photos/plans/index/apply",
        json={"planId": plan["planId"], "includeFaces": False},
        headers={APPROVAL_HEADER: issued["approvalToken"]},
    )

    assert changed.status_code == 409
    assert backend.builds == []
