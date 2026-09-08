"""Exercise the shared image tools against isolated on-disk libraries."""

from pathlib import Path

from PIL import Image

from runtime.execution.suckers import image_album_skills as albums
from runtime.execution.suckers import image_semantic_skills as tools
from runtime.execution.suckers.registry import SkillRegistry
from runtime.memory.hemolymph import image_semantic_index as index
from runtime.platform.resource_identity import photo_library_id


class _Embedding:
    def embed(self, images):
        return [[1.0, 0.0] for _ in images]


def test_same_named_libraries_remain_separate_after_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(index, "_image_model", lambda: _Embedding())
    monkeypatch.setattr(index, "_text_model", lambda: _Embedding())
    monkeypatch.setattr(index, "_face_app", lambda: None)
    first = tmp_path / "family" / "Photos"
    second = tmp_path / "private" / "Photos"
    for directory, name in [(first, "beach.png"), (second, "receipt.png")]:
        directory.mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(directory / name)
        assert tools._image_index_build(directory=str(directory), include_faces=False)["ok"]

    assert tools._idx_db(str(first)) != tools._idx_db(str(second))
    # Rebuilding a second library must not replace the first library's rows.
    assert tools._image_index_build(directory=str(second), include_faces=False)["ok"]
    result = tools._image_search_by_text("photo", directory=str(first))
    assert [hit["path"] for hit in result["results"]] == ["beach.png"]
    assert result["source"]["kind"] == "photo-library"
    assert result["source"]["id"] == photo_library_id(first)
    reference = result["results"][0]["assetReference"]
    assert reference["sourceKind"] == "photo-library"
    assert reference["sourceId"] == photo_library_id(first)
    assert len(reference["assetId"]) == 64
    assert len(reference["assetRevision"]) == 64
    visual = tools._image_search_by_image("beach.png", directory=str(first))
    assert visual["queryAssetReference"]["sourceId"] == photo_library_id(first)
    metadata = albums._image_filter_meta(directory=str(second), file_type="png")
    assert [hit["path"] for hit in metadata["matches"]] == ["receipt.png"]
    assert metadata["source"]["id"] == photo_library_id(second)
    assert metadata["matches"][0]["assetReference"]["sourceId"] == photo_library_id(second)


def test_canonical_path_and_default_directory_use_same_library(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "state"))
    library = tmp_path / "Photos"
    library.mkdir()
    monkeypatch.chdir(library)
    paths = {tools._idx_db(value) for value in ("", ".", "../Photos", str(library))}
    assert len(paths) == 1
    assert Path(paths.pop()).is_relative_to(tmp_path / "state")
    monkeypatch.chdir(tmp_path)
    assert tools._idx_db(".") != tools._idx_db(str(library))


def test_ambiguous_legacy_index_is_not_read_or_modified(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    legacy = tmp_path / "data" / "image_index_Photos.db"
    legacy.parent.mkdir()
    legacy.write_bytes(b"unknown library identity")
    result = albums._image_filter_meta(directory=str(tmp_path / "Photos"))
    assert result["error"] == "image_album_unavailable"
    assert legacy.read_bytes() == b"unknown library identity"
    assert not Path(tools._idx_db(str(tmp_path / "Photos"))).exists()


def test_image_state_honors_runtime_home(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    assert Path(tools._idx_db(str(tmp_path / "Photos"))).is_relative_to(tmp_path / "home" / "data")


def test_person_naming_is_registered_and_uses_the_same_library(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(index, "face_capable", lambda: True)
    library = str(tmp_path / "Photos")
    db = Path(tools._idx_db(library))
    with index._open(db) as conn:
        conn.execute(
            "INSERT INTO image_faces VALUES (?, ?, ?)",
            ("alice.png", 0, index._vec_to_blob([1.0, 0.0])),
        )
        conn.execute(
            "INSERT INTO image_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("alice.png", 100, 100, 0, "", ".png", ""),
        )
    registry = SkillRegistry()
    assert tools.register_image_semantic_skills(registry) == 6
    groups = tools._face_group_albums(directory=library)["groups"]
    result = registry.get("face_name_group").handler(
        person=groups[0]["person"], name="Alice", directory=library
    )
    assert result["name"] == "Alice"
    matches = albums._image_filter_meta(directory=library, person="Alice")
    assert [item["path"] for item in matches["matches"]] == ["alice.png"]
    assert albums._image_filter_meta(directory=library, person="Bob")["matches"] == []
    assert (
        tools._face_name_group(person="bad-id", name="Alice", directory=library)["error"]
        == "invalid_person_label"
    )


def test_asset_reference_rejects_a_path_outside_the_selected_library(tmp_path):
    library = tmp_path / "Photos"
    library.mkdir()
    outside = tmp_path / "outside.png"
    Image.new("RGB", (8, 8)).save(outside)

    assert tools._asset_reference(str(library), "../outside.png") is None
