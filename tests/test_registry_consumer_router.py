from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from echo_runtime.client import (  # noqa: E402
    AssetContent,
    AssetPayload,
    BundleRef,
    RegistryAsset,
)
from echo_runtime.materialize import materialize_skill  # noqa: E402
from runtime.sensing.gateway.registry_consumer_router import (  # noqa: E402
    _install_registry_plugin_bundle,
    create_registry_consumer_router,
)


class FakeRegistryClient:
    def __init__(self, _base_url: str) -> None:
        pass

    def list_assets(self, type_: str | None = None) -> list[RegistryAsset]:
        assets = [
            RegistryAsset(
                id="role/researcher",
                type="role",
                kind="data",
                name="Researcher",
                description="Research role",
                category="research",
                tags=["analysis"],
            ),
            RegistryAsset(
                id="twin-role/operator",
                type="twin-role",
                kind="data",
                name="Operator",
                description="Operator role",
                category="ops",
                tags=["ops"],
            ),
            RegistryAsset(
                id="plugin/browser-tool",
                type="plugin",
                kind="code",
                name="Browser Tool",
                description="Executable plugin",
                category="browser",
            ),
        ]
        return [asset for asset in assets if type_ is None or asset.type == type_]

    def list_skills(self) -> list[RegistryAsset]:
        return []

    def fetch(self, asset_id: str) -> AssetPayload:
        if asset_id == "role/researcher":
            return AssetPayload(
                id="role/researcher",
                type="role",
                kind="data",
                name="Researcher",
                description="Research role",
                category="research",
                tags=["analysis"],
                body="You are a careful researcher.",
            )
        if asset_id == "role/not-really-role":
            return AssetPayload(
                id="plugin/not-really-role",
                type="plugin",
                kind="code",
                name="Executable Plugin",
                description="Plugin returned from a role-looking request",
                category="browser",
                body="plugin manifest",
            )
        if asset_id == "plugin/browser-tool":
            return AssetPayload(
                id="plugin/browser-tool",
                type="plugin",
                kind="code",
                name="Browser Tool",
                description="Executable plugin",
                category="browser",
                body="plugin manifest",
            )
        raise KeyError(asset_id)


class FakeBundleClient:
    def __init__(self, bundle: bytes) -> None:
        self.bundle = bundle

    def fetch_bundle(self, _asset_id: str, *, expected_size: int | None = None) -> bytes:
        if expected_size is not None and len(self.bundle) != expected_size:
            raise ValueError("registry bundle size mismatch")
        return self.bundle


def _tar_bytes(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, body in files.items():
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_registry_plugin_bundle_manual_extraction_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.platform.plugins.plugin_lifecycle as lifecycle
    import runtime.sensing.gateway.registry_consumer_router as registry_router

    bundle = _tar_bytes(
        {
            "safe-plugin/.codex-plugin/plugin.json": json.dumps(
                {"id": "safe-plugin", "name": "Safe Plugin", "version": "1.0.0"}
            ),
            "safe-plugin/README.md": "bounded extraction",
        }
    )
    asset = RegistryAsset(
        id="plugin/safe-plugin",
        type="plugin",
        kind="code",
        name="Safe Plugin",
        description="safe",
        bundle=BundleRef(
            ref="bundle.tar.gz",
            checksum="sha256:" + hashlib.sha256(bundle).hexdigest(),
        ),
    )
    captured: dict[str, str | bool] = {}

    def fake_install(source: Path, **kwargs):
        captured["readme"] = (source / "README.md").read_text(encoding="utf-8")
        captured["require_trusted_publisher"] = kwargs.get("require_trusted_publisher") is True
        return {"ok": True}

    monkeypatch.setattr(lifecycle, "install_local_plugin", fake_install)
    result = _install_registry_plugin_bundle(
        asset,
        client=FakeBundleClient(bundle),
        plugin_root=tmp_path / "plugins",
        publisher_trust_store_path=None,
    )
    assert result == {"ok": True}
    assert captured["readme"] == "bounded extraction"
    assert captured["require_trusted_publisher"] is True

    monkeypatch.setattr(registry_router, "_MAX_PLUGIN_UNCOMPRESSED_BYTES", 8)
    with pytest.raises(ValueError, match="expands beyond"):
        _install_registry_plugin_bundle(
            asset,
            client=FakeBundleClient(bundle),
            plugin_root=tmp_path / "plugins",
            publisher_trust_store_path=None,
        )


def test_registry_plugin_bundle_requires_sha256_checksum(tmp_path: Path) -> None:
    bundle = _tar_bytes(
        {
            "unsafe-plugin/.codex-plugin/plugin.json": json.dumps(
                {"name": "unsafe-plugin", "version": "1.0.0"}
            )
        }
    )
    asset = RegistryAsset(
        id="plugin/unsafe-plugin",
        type="plugin",
        kind="code",
        name="Unsafe Plugin",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    with pytest.raises(ValueError, match="requires a valid sha256 checksum"):
        _install_registry_plugin_bundle(
            asset,
            client=FakeBundleClient(bundle),
            plugin_root=tmp_path / "plugins",
            publisher_trust_store_path=None,
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    import echo_runtime
    from runtime.execution.agents import loader

    monkeypatch.setattr(echo_runtime, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path / "agents")

    app = FastAPI()
    app.include_router(
        create_registry_consumer_router(
            registry_base="https://registry.test", skills_root=tmp_path / "skills"
        )
    )
    return TestClient(app)


@pytest.fixture
def no_raise_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    import echo_runtime
    from runtime.execution.agents import loader

    monkeypatch.setattr(echo_runtime, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path / "agents")

    app = FastAPI()
    app.include_router(
        create_registry_consumer_router(
            registry_base="https://registry.test", skills_root=tmp_path / "skills"
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_lists_registry_roles(client: TestClient) -> None:
    data = client.get("/api/registry/roles").json()

    assert data["source"] == "https://registry.test"
    assert data["total"] == 2
    assert {role["id"] for role in data["roles"]} == {
        "role/researcher",
        "twin-role/operator",
    }


def test_installs_registry_role_as_local_agent(client: TestClient, tmp_path) -> None:
    data = client.post("/api/registry/roles/role/researcher/install").json()

    assert data["installed"] is True
    assert data["agent_id"] == "registry_researcher"
    agent_root = tmp_path / "agents" / "registry_researcher"
    profile = json.loads((agent_root / "profile.jsonc").read_text(encoding="utf-8"))
    assert profile["source"] == "registry"
    assert profile["name"] == "Researcher"
    assert (agent_root / "agent-core" / "SOUL.md").read_text(
        encoding="utf-8"
    ) == "You are a careful researcher."


def test_rejects_registry_role_install_when_agent_root_is_symlink(
    no_raise_client: TestClient, tmp_path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "registry_researcher").symlink_to(outside, target_is_directory=True)

    resp = no_raise_client.post("/api/registry/roles/role/researcher/install")

    assert resp.status_code == 500
    assert not (outside / "profile.jsonc").exists()


def test_rejects_registry_role_install_when_agent_core_is_symlink(
    no_raise_client: TestClient, tmp_path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    agent_root = tmp_path / "agents" / "registry_researcher"
    agent_root.mkdir(parents=True)
    (agent_root / "agent-core").symlink_to(outside, target_is_directory=True)

    resp = no_raise_client.post("/api/registry/roles/role/researcher/install")

    assert resp.status_code == 500
    assert not (outside / "SOUL.md").exists()


def test_registry_role_install_cleans_temp_file_when_atomic_write_fails(
    no_raise_client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_replace = Path.replace

    def fail_profile_replace(self: Path, target: Path) -> Path:
        if self.name.startswith(".profile.jsonc.") and target.name == "profile.jsonc":
            raise OSError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_profile_replace)

    resp = no_raise_client.post("/api/registry/roles/role/researcher/install")

    assert resp.status_code == 500
    agent_root = tmp_path / "agents" / "registry_researcher"
    assert list(agent_root.glob(".profile.jsonc.*")) == []


def test_rejects_role_install_for_non_role_asset(client: TestClient) -> None:
    resp = client.post("/api/registry/roles/roleplay/fake/install")

    assert resp.status_code == 400
    assert "not a role asset" in resp.json()["detail"]


def test_rejects_registry_payload_that_is_not_installable_role(
    client: TestClient, tmp_path
) -> None:
    resp = client.post("/api/registry/roles/role/not-really-role/install")

    assert resp.status_code == 400
    assert "not an installable role asset" in resp.json()["detail"]
    assert not (tmp_path / "agents" / "registry_not_really_role").exists()


def test_plugins_are_browsable_and_install_as_prompt_capabilities(
    client: TestClient, tmp_path
) -> None:
    data = client.get("/api/registry/plugins").json()

    assert data["installable"] is True
    assert data["install_mode"] == "prompt-only"
    assert data["total"] == 1
    assert data["plugins"][0]["id"] == "plugin/browser-tool"

    detail = client.get("/api/registry/plugins/browser-tool").json()
    assert detail["installable"] is True
    assert detail["install_mode"] == "prompt-only"
    assert detail["body_preview"] == "plugin manifest"

    installed = client.post("/api/registry/plugins/browser-tool/install").json()
    assert installed["installed"] == "plugin/browser-tool"
    assert installed["installed_name"] == "plugin-browser-tool"
    assert installed["install_mode"] == "prompt-only"
    skill_md = tmp_path / "skills" / "plugin-browser-tool" / "SKILL.md"
    assert skill_md.is_file()
    assert "plugin manifest" in skill_md.read_text(encoding="utf-8")
    assert (
        json.loads(
            (tmp_path / "skills" / "plugin-browser-tool" / "PLUGIN.json").read_text(
                encoding="utf-8"
            )
        )["execution"]
        == "prompt-only"
    )


def test_production_browses_registry_but_rejects_unsigned_prompt_mutations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")

    assert client.get("/api/registry/roles").status_code == 200
    assert client.get("/api/registry/plugins").status_code == 200
    skill = client.post("/api/registry/skills/remote-skill/install")
    role = client.post("/api/registry/roles/role/researcher/install")
    prompt_plugin = client.post("/api/registry/plugins/browser-tool/install")

    assert skill.status_code == 403
    assert role.status_code == 403
    assert prompt_plugin.status_code == 403
    assert "reviewed release artifact" in skill.json()["detail"]
    assert "reviewed release artifact" in role.json()["detail"]
    assert "trusted signed plugin bundle" in prompt_plugin.json()["detail"]


def test_production_still_allows_trusted_signed_plugin_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import echo_runtime
    import runtime.sensing.gateway.registry_consumer_router as registry_router

    class SignedPluginClient(FakeRegistryClient):
        def fetch(self, asset_id: str) -> AssetPayload:
            assert asset_id == "plugin/signed-tool"
            return AssetPayload(
                id=asset_id,
                type="plugin",
                kind="code",
                name="Signed Tool",
                bundle=BundleRef(
                    ref="bundle.tar.gz",
                    checksum="sha256:" + "a" * 64,
                ),
            )

    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")
    monkeypatch.setattr(echo_runtime, "RegistryClient", SignedPluginClient)
    monkeypatch.setattr(
        registry_router,
        "_install_registry_plugin_bundle",
        lambda *_args, **_kwargs: {"ok": True, "plugin_id": "signed-tool"},
    )
    app = FastAPI()
    app.include_router(
        create_registry_consumer_router(
            registry_base="https://registry.test",
            skills_root=tmp_path / "skills",
            plugin_root=tmp_path / "plugins",
        )
    )

    response = TestClient(app).post("/api/registry/plugins/signed-tool/install")

    assert response.status_code == 200
    assert response.json() == {
        "installed": "plugin/signed-tool",
        "install_mode": "plugin-bundle",
        "ok": True,
        "plugin_id": "signed-tool",
    }


def test_materialize_skill_rejects_unsafe_registry_slug(tmp_path) -> None:
    payload = AssetPayload(
        id="skill/../../escape",
        type="skill",
        kind="data",
        name="Escape",
        description="unsafe path",
        body="never write outside skills root",
    )

    with pytest.raises(ValueError, match="unsafe skill (id|slug)"):
        materialize_skill(payload, tmp_path / "skills")

    assert not (tmp_path / "escape").exists()


def test_materialize_skill_extracts_full_bundle_under_matching_slug(tmp_path) -> None:
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        body="ignored when bundle exists",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )
    bundle = _tar_bytes(
        {
            "research-pack/SKILL.md": "---\nname: Research Pack\n---\n\nUse sources.",
            "research-pack/references/source-policy.md": "cite primary sources",
        }
    )

    md = materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert md == tmp_path / "skills" / "research-pack" / "SKILL.md"
    assert md.read_text(encoding="utf-8").endswith("Use sources.")
    assert (tmp_path / "skills" / "research-pack" / "references" / "source-policy.md").read_text(
        encoding="utf-8"
    ) == "cite primary sources"


def test_materialize_skill_rejects_bundle_that_writes_other_skill_dir(tmp_path) -> None:
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )
    bundle = _tar_bytes(
        {
            "research-pack/SKILL.md": "safe skill",
            "other-pack/SKILL.md": "should not be written",
        }
    )

    with pytest.raises(ValueError, match="outside skill dir"):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert not (tmp_path / "skills" / "research-pack").exists()
    assert not (tmp_path / "skills" / "other-pack").exists()


def test_materialize_skill_rejects_bundle_missing_skill_md_without_clobbering(
    tmp_path,
) -> None:
    existing = tmp_path / "skills" / "research-pack"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("old safe version", encoding="utf-8")
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )
    bundle = _tar_bytes({"research-pack/README.md": "missing skill md"})

    with pytest.raises(ValueError, match="missing required file"):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "old safe version"


def test_materialize_skill_verifies_payload_bundle_checksum(tmp_path) -> None:
    bundle = _tar_bytes({"research-pack/SKILL.md": "new safe version"})
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(ref="bundle.tar.gz", checksum="sha256:" + ("0" * 64)),
    )

    with pytest.raises(ValueError, match="bundle checksum mismatch"):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert not (tmp_path / "skills" / "research-pack").exists()


def test_materialize_skill_replaces_existing_bundle_after_checksum_match(tmp_path) -> None:
    existing = tmp_path / "skills" / "research-pack"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("old safe version", encoding="utf-8")
    bundle = _tar_bytes({"research-pack/SKILL.md": "new safe version"})
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(
            ref="bundle.tar.gz",
            checksum="sha256:" + hashlib.sha256(bundle).hexdigest(),
        ),
    )

    md = materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert md.read_text(encoding="utf-8") == "new safe version"


def test_materialize_skill_rejects_bundle_larger_than_declared_size(tmp_path) -> None:
    bundle = _tar_bytes({"research-pack/SKILL.md": "safe skill"})
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(
            ref="bundle.tar.gz",
            checksum="sha256:" + hashlib.sha256(bundle).hexdigest(),
            size=len(bundle) - 1,
        ),
    )

    with pytest.raises(ValueError, match="bundle size mismatch"):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert not (tmp_path / "skills" / "research-pack").exists()


def test_materialize_skill_bundle_replaces_existing_symlink_dir(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "research-pack").symlink_to(outside, target_is_directory=True)
    bundle = _tar_bytes({"research-pack/SKILL.md": "new safe version"})
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    md = materialize_skill(payload, skills, client=FakeBundleClient(bundle))

    assert md.read_text(encoding="utf-8") == "new safe version"
    assert md.parent.is_dir()
    assert not md.parent.is_symlink()
    assert not (outside / "SKILL.md").exists()


def test_materialize_skill_rejects_bundle_parent_traversal_member(tmp_path) -> None:
    bundle = _tar_bytes(
        {
            "research-pack/SKILL.md": "safe skill",
            "research-pack/../escape.txt": "should not be written",
        }
    )
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    with pytest.raises(ValueError, match="unsafe path"):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert not (tmp_path / "skills" / "escape.txt").exists()


def test_materialize_skill_rejects_bundle_over_member_count_limit(tmp_path) -> None:
    bundle = _tar_bytes(
        {
            "research-pack/SKILL.md": "safe skill",
            "research-pack/references/one.md": "one",
            "research-pack/references/two.md": "two",
        }
    )
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    with pytest.raises(ValueError, match="exceeds 2 member limit"):
        materialize_skill(
            payload,
            tmp_path / "skills",
            client=FakeBundleClient(bundle),
            max_bundle_members=2,
        )

    assert not (tmp_path / "skills" / "research-pack").exists()


def test_materialize_skill_rejects_terabyte_declared_member_before_reading(tmp_path) -> None:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo("research-pack/SKILL.md")
        info.size = 1 << 40
        tar.addfile(info)
    bundle = out.getvalue()
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    with pytest.raises(ValueError, match="declared size.*33554432 byte limit"):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert len(bundle) < 1024
    assert not (tmp_path / "skills" / "research-pack").exists()


def test_materialize_skill_rejects_cumulative_declared_size_limit(tmp_path) -> None:
    bundle = _tar_bytes(
        {
            "research-pack/SKILL.md": "1234",
            "research-pack/references/one.md": "5678",
            "research-pack/references/two.md": "9abc",
        }
    )
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    with pytest.raises(ValueError, match="cumulative declared bundle size exceeds 10"):
        materialize_skill(
            payload,
            tmp_path / "skills",
            client=FakeBundleClient(bundle),
            max_bundle_extracted_bytes=10,
        )

    assert not (tmp_path / "skills" / "research-pack").exists()


def test_materialize_skill_rejects_high_compression_ratio_bundle(tmp_path) -> None:
    expanded = "A" * (2 * 1024 * 1024)
    bundle = _tar_bytes({"research-pack/SKILL.md": expanded})
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    assert len(bundle) < 16 * 1024
    with pytest.raises(ValueError, match="cumulative declared bundle size exceeds 1048576"):
        materialize_skill(
            payload,
            tmp_path / "skills",
            client=FakeBundleClient(bundle),
            max_bundle_extracted_bytes=1024 * 1024,
        )

    assert not (tmp_path / "skills" / "research-pack").exists()


def test_materialize_skill_stops_stream_before_writing_actual_bytes_over_limit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _tar_bytes({"research-pack/SKILL.md": "safe"})
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )
    original_extractfile = tarfile.TarFile.extractfile

    def oversized_extractfile(self, member):
        source = original_extractfile(self, member)
        assert source is not None
        with source:
            return io.BytesIO(source.read() + b"X")

    monkeypatch.setattr(tarfile.TarFile, "extractfile", oversized_extractfile)

    with pytest.raises(ValueError, match="actual size.*exceeds 4 byte limit"):
        materialize_skill(
            payload,
            tmp_path / "skills",
            client=FakeBundleClient(bundle),
            max_bundle_member_bytes=4,
        )

    assert not (tmp_path / "skills" / "research-pack").exists()


@pytest.mark.parametrize(
    ("member_type", "error"),
    [
        (tarfile.SYMTYPE, "link not allowed"),
        (tarfile.LNKTYPE, "link not allowed"),
        (tarfile.FIFOTYPE, "unsupported file type"),
        (tarfile.CHRTYPE, "unsupported file type"),
        (tarfile.BLKTYPE, "unsupported file type"),
        (tarfile.GNUTYPE_SPARSE, "sparse file not allowed"),
    ],
)
def test_materialize_skill_rejects_links_sparse_and_special_members(
    tmp_path,
    member_type: bytes,
    error: str,
) -> None:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        skill = b"safe"
        skill_info = tarfile.TarInfo("research-pack/SKILL.md")
        skill_info.size = len(skill)
        tar.addfile(skill_info, io.BytesIO(skill))
        unsafe = tarfile.TarInfo("research-pack/unsafe")
        unsafe.type = member_type
        unsafe.linkname = (
            "../../outside" if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE} else ""
        )
        tar.addfile(unsafe)
    bundle = out.getvalue()
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        bundle=BundleRef(ref="bundle.tar.gz"),
    )

    with pytest.raises(ValueError, match=error):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert not (tmp_path / "skills" / "research-pack").exists()


def test_materialize_skill_restores_existing_bundle_when_replace_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "skills" / "research-pack"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("old safe version", encoding="utf-8")
    bundle = _tar_bytes({"research-pack/SKILL.md": "new safe version"})
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="bundle",
        bundle=BundleRef(
            ref="bundle.tar.gz",
            checksum="sha256:" + hashlib.sha256(bundle).hexdigest(),
        ),
    )
    original_rename = Path.rename

    def fail_staged_replace(self: Path, target: Path) -> Path:
        if self.name == "research-pack" and self.parent.name.startswith(".research-pack."):
            raise OSError("replace failed")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_staged_replace)

    with pytest.raises(OSError, match="replace failed"):
        materialize_skill(payload, tmp_path / "skills", client=FakeBundleClient(bundle))

    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "old safe version"


def test_materialize_skill_verifies_body_checksum_without_clobbering(tmp_path) -> None:
    existing = tmp_path / "skills" / "research-pack"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("old safe version", encoding="utf-8")
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="body only",
        body="new instructions",
        content=AssetContent(checksum="sha256:" + ("0" * 64)),
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        materialize_skill(payload, tmp_path / "skills")

    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "old safe version"


def test_materialize_skill_writes_body_only_atomically_after_checksum_match(
    tmp_path,
) -> None:
    body = "new instructions"
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="body only",
        body=body,
        content=AssetContent(checksum="sha256:" + hashlib.sha256(body.encode()).hexdigest()),
    )

    md = materialize_skill(payload, tmp_path / "skills")

    assert md == tmp_path / "skills" / "research-pack" / "SKILL.md"
    text = md.read_text(encoding="utf-8")
    assert "source: registry" in text
    assert text.endswith("new instructions\n")


def test_materialize_skill_keeps_existing_body_only_file_when_replace_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "skills" / "research-pack"
    existing.mkdir(parents=True)
    md = existing / "SKILL.md"
    md.write_text("old safe version", encoding="utf-8")
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="body only",
        body="new instructions",
    )
    original_replace = Path.replace

    def fail_temp_replace(self: Path, target: Path) -> Path:
        if self.name.startswith(".SKILL.md.") and target.name == "SKILL.md":
            raise OSError("atomic replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_temp_replace)

    with pytest.raises(OSError, match="atomic replace failed"):
        materialize_skill(payload, tmp_path / "skills")

    assert md.read_text(encoding="utf-8") == "old safe version"
    assert list(existing.glob(".SKILL.md.*")) == []


def test_materialize_skill_rejects_body_only_symlink_skill_dir(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "research-pack").symlink_to(outside, target_is_directory=True)
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="body only",
        body="new instructions",
    )

    with pytest.raises(ValueError, match="must not be a symlink"):
        materialize_skill(payload, skills)

    assert not (outside / "SKILL.md").exists()


def test_materialize_skill_rejects_body_only_non_directory_skill_path(tmp_path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "research-pack").write_text("not a directory", encoding="utf-8")
    payload = AssetPayload(
        id="skill/research-pack",
        type="skill",
        kind="data",
        name="Research Pack",
        description="body only",
        body="new instructions",
    )

    with pytest.raises(ValueError, match="must be a directory"):
        materialize_skill(payload, skills)

    assert (skills / "research-pack").read_text(encoding="utf-8") == "not a directory"


