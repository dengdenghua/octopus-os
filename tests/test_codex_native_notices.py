"""Fail-closed regressions for the Codex Windows native notice bundle."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LICENSE_ROOT = ROOT / "extras/desktop/licenses/codex-0.149.0"
PROVENANCE_PATH = LICENSE_ROOT / "NATIVE_PROVENANCE.json"
NOTICE_PATH = LICENSE_ROOT / "NATIVE_THIRD_PARTY_NOTICES.md"

HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|PLACEHOLDER|UNKNOWN\s+COPYRIGHT)\b", re.IGNORECASE)

CODEX_COMMIT = "758ef40f50c1a458425c7cfbf1eb12cbc07af0b0"
CODEX_LOCK_SHA256 = "0c32858e9c47d0acf82735c8620c96840a5381152eec63acad15d1acadb9edad"
RIPGREP_COMMIT = "e89fff89ac9af12e8d4ce9d5fd07beb408ca730f"
RIPGREP_LOCK_SHA256 = "7a7d39cda8a03930e578f1dbb724e055771901842eca239e03b01e19da946a64"

RUSTY_V8_COMMIT = "5c15a6995c9bb4bacd3e341b59fff32c909c80bf"
V8_COMMIT = "ac1e23989121713ca642f6650b34deff7b686896"
ICU_COMMIT = "ee5f27adc28bd3f15b2c293f726d14d2e336cbd5"
TREE_SITTER_COMMIT = "da6fe9beb4f7f67beb75914ca8e0d48ae48d6406"

RUSTY_V8_GITLINKS = {
    "v8": V8_COMMIT,
    "build": "8acb33ac8dceef0503443109c0a92988189563ef",
    "buildtools": "17495e454aae81b581e8b3caccbb53054509b280",
    "tools/clang": "45f4b9e25124809497a27a8ae0e63d603b0f9f1b",
    "third_party/jinja2": "c3027d884967773057bf74b957e3fea87e5df4d7",
    "third_party/markupsafe": "4256084ae14175d38a3ff7d739dca83ae49ccec6",
    "third_party/icu": ICU_COMMIT,
    "third_party/abseil-cpp": "d16e32215c3ab90ba57c2e904a5344d85c7353e4",
    "third_party/fp16/src": "3d2de1816307bac63c16a297e8c4dc501b4076df",
    "third_party/libc++/src": "5abc7f839700f0f17338434e1c1c6a8c87c00c11",
    "third_party/libc++abi/src": "8f11bb1d4438d0239d0dfc1bd9456a9f31629dda",
    "third_party/libunwind/src": "d6c7a21e978f0adaa43accaad53bc64f0b64f6ec",
    "third_party/fast_float/src": "05087a303dad9c98768b33c829d398223a649bc6",
    "third_party/llvm-libc/src": "9309c117ebae84dd2f9df1ef99de4782162527d5",
    "third_party/simdutf": "f7356eed293f8208c40b3c1b344a50bd70971983",
    "third_party/highway/src": "2607d3b5b0113992fe84d3848859eae13b3b52c1",
    "third_party/partition_alloc": "ff3b8b885b8374cbd3902642d94dc737bda93d5d",
    "third_party/dragonbox/src": "beeeef91cf6fef89a4d4ba5e95d47ca64ccb3a44",
    "third_party/rust": "26e8ff47f18a8d28d6187a04b6a16cb7332356f8",
    "tools/win": "faefd1b6fa9eeb033ad6fe60368ccb9bf908cbd0",
}

RUSTY_V8_MEMBER_PREFIX_COUNTS = {
    "obj/build/rust/allocator/": 2,
    "obj/buildtools/third_party/libc++/": 51,
    "obj/rusty_v8/": 2,
    "obj/src/deno_inspector/": 8,
    "obj/third_party/abseil-cpp/": 138,
    "obj/third_party/highway/": 7,
    "obj/third_party/icu/": 456,
    "obj/third_party/partition_alloc/": 66,
    "obj/third_party/simdutf/": 1,
    "obj/v8/": 1197,
}

RUSTY_V8_HEADER_MARKERS = {
    "fast_float": ("fast_float@@", 49),
    "dragonbox": ("dragonbox@jkj@@", 27),
    "fp16": ("fp64_to_fp16_raw_bits", 13),
}

REQUIRED_RUSTY_V8_LICENSE_SHA256 = {
    "ff11d445fb41a1087c7630e120ab15f1a2cb67c1b707173cb494141805fca35e",
    "c79a7fea0e3cac04cd43f20e7b648e5a0ff8fa5344e644b0ee09ca1162b62747",
    "1fd39030c119b4c97014f59e0ad0ed65f23475bbdf55a52740f31eb82b34b4ee",
    "17e4f539024be2749ee729d1e2f01d24cef12ece8c9bf18e91a4349be29c80bf",
    "539dd7aed86e8a4f12cbdd0e6c50c189c7d74847e4fecc64ce2c6ee3a01da38b",
    "e2b35be49f7284a45b7baca8fc7b3ab7440e7902392b2528a457816b5bb2a15c",
    "b5efebcaca80879234098e52d1725e6d9eb8fb96a19fce625d39184b705f7b6d",
    "e562f3f974ced7e69dd1db77b820b36bcf8f30377f1aa105723fba449c53c4e6",
    "097a889aa954d04e088b790b10a4014d6189561d0a6013935a73ce3d4ddaaf06",
    "8d8291caf1cee26d23acf3eb67c9f9a2d58f1c681b16a4fbe8cbfb9e3c0b5a9b",
    "ed249424bce4e318fa190dd6d8becf60cfc37287132ad3f89e16e0f28d878dcf",
    "ebcd9bbf783a73d05c53ba4d586b8d5813dcdf3bbec50265860ccc885e606f47",
    "fc8dbc04e03ad4efc08a647ffe7f995b811a95bc04c0e85a56d5277c6593fa5f",
    "e340270d4f64384569a91d546acb5b094d69ce47f0c015db77abb74dc6f815af",
    "9e45e856bedccee9f67254082ca11851d954de2fed7448c4bed19ad9aab99a91",
    "c9bff75738922193e67fa726fa225535870d2aa1059f91452c411736284ad566",
}

RUSTY_V8_ARCHIVE_SHA256 = "732ec5da4243aa166799780c8519a5eea6f32f6e47657a323342794dc3c239d6"
RUSTY_V8_EXPANDED_ARCHIVE_SHA256 = (
    "00baffcf54b30fe198c3e5cc40ec88674ff99cac2b8b60f8e325efce3ce771bb"
)
RUSTY_V8_BINDING_SHA256 = "dabf78ba1faac127660db9862b1d0354175c71b8db2d4fcb5bacbd9c93576b16"

REQUIRED_COMPONENTS = {
    "aws-lc-sys": (
        "0.39.0",
        "1fa7e52a4c5c547c741610a2c6f123f3881e409b714cd27e6798ef020c514f0a",
    ),
    "ring": (
        "0.17.14",
        "a4689e6c2294d81e88dc6261c768b63bc4fcdb852be6d1352498b114f61383b7",
    ),
    "blake3": (
        "1.8.2",
        "3888aaa89e4b2a40fca9848e400f6a658a5a3978de7be858e209cafa8be9a4a0",
    ),
    "zstd-sys": (
        "2.0.16+zstd.1.5.7",
        "91e19ebc2adc8f83e43039e79776e3fda8ca919132d68a1fed6a5faca2683748",
    ),
    "bzip2-sys": (
        "0.1.13+1.0.8",
        "225bff33b2141874fe80d71e07d6eec4f85c5c216453dd96388240f96e1acc14",
    ),
    "libsqlite3-sys": (
        "0.37.0",
        "b1f111c8c41e7c61a49cd34e44c7619462967221a6443b0ec299e0ac30cfb9b1",
    ),
    "lzma-sys": (
        "0.1.20",
        "5fda04ab3764e6cde78b9974eec4f779acaba7c4e84b36eca3cf77c581b85d27",
    ),
    "onig_sys": (
        "69.9.1",
        "c7f86c6eef3d6df15f23bcfb6af487cbd2fed4e5581d58d5bf1f5f8b7f6727dc",
    ),
    "tree-sitter": (
        "0.25.10",
        "78f873475d258561b06f1c595d93308a7ed124d9977cb26b148c2084a4a3cc87",
    ),
    "pcre2-sys": (
        "0.2.10",
        "18b9073c1a2549bd409bf4a32c94d903bb1a09bf845bc306ae148897fa0760a4",
    ),
    "v8": (
        "150.4.0",
        "42a978ff11f15b24e5c05a7123cf2b68f41e763546699781a924ef4e2cf43a49",
    ),
    "deno_core_icudata": (
        "0.77.0",
        "a9efff8990a82c1ae664292507e1a5c6749ddd2312898cdf9cd7cb1fd4bc64c6",
    ),
}

REQUIRED_ATTRIBUTIONS = (
    "Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.",
    "Copyright (c) 2014-2024 Google Inc.",
    "copyright (C) 1996-2019 Julian R Seward",
    "Copyright (c) 2002-2021  K.Kosako",
    "Original API code Copyright (c) 1997-2012 University of Cambridge",
    "Copyright Zoltan Herczeg",
    "Copyright © 2016-2025 Unicode, Inc.",
    "Copyright (c) 2018-2019 the Deno authors",
    "Copyright (c) 2021 The fast_float authors",
    "Copyright 2021 The simdutf authors",
    "Copyright (c) 2017 Facebook Inc.",
    "The LLVM Project is under the Apache License v2.0 with LLVM Exceptions:",
    "Copyright 2014 The Chromium Authors",
    "This project is primarily dual-licensed under your choice of either the Apache",
)


def _load_provenance() -> dict[str, Any]:
    payload = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_sha256(value: object, *, field: str) -> None:
    assert isinstance(value, str), f"{field} must be a string"
    assert HEX_64.fullmatch(value), f"{field} must be a lowercase SHA-256: {value!r}"
    assert value != "0" * 64, f"{field} cannot be an all-zero placeholder"


def _artifact_matching(artifacts: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    matches = [
        artifact
        for artifact in artifacts
        if needle.casefold()
        in " ".join(str(artifact.get(key, "")) for key in ("name", "kind", "source_url")).casefold()
    ]
    assert len(matches) == 1, f"expected one artifact matching {needle!r}, got {matches!r}"
    return matches[0]


def test_native_provenance_schema_and_reachability_are_closed() -> None:
    provenance = _load_provenance()
    assert set(provenance) >= {
        "schema_version",
        "target",
        "roots",
        "source_locks",
        "components",
        "artifacts",
        "notice_bundle",
    }
    assert isinstance(provenance["schema_version"], str)
    assert provenance["schema_version"].endswith(".v1")
    assert provenance["target"] == "x86_64-pc-windows-msvc"

    roots = provenance["roots"]
    assert isinstance(roots, list) and roots
    components = provenance["components"]
    assert isinstance(components, list) and components
    component_names = {component["name"] for component in components}
    assert len(component_names) == len(components)

    for root in roots:
        assert set(root) >= {
            "ecosystem",
            "name",
            "manifest",
            "features",
            "reachable_native_components",
        }
        assert all(
            isinstance(root[field], str) and root[field]
            for field in ("ecosystem", "name", "manifest")
        )
        assert isinstance(root["features"], list)
        reachable = root["reachable_native_components"]
        assert isinstance(reachable, list) and reachable
        assert set(reachable) <= component_names

    root_names = {root["name"] for root in roots}
    assert {"codex-cli", "code-mode-host", "windows-sandbox", "ripgrep"} <= root_names
    ripgrep_root = next(root for root in roots if root["name"] == "ripgrep")
    assert "pcre2" in {feature.casefold() for feature in ripgrep_root["features"]}

    for component in components:
        assert set(component) >= {
            "name",
            "version",
            "source",
            "registry_checksum",
            "vcs_commit",
            "crate_archive_sha256",
            "reachable_from",
            "native_payloads",
            "license_inputs",
        }
        assert isinstance(component["reachable_from"], list) and component["reachable_from"]
        assert set(component["reachable_from"]) <= root_names
        assert isinstance(component["native_payloads"], list)
        assert isinstance(component["license_inputs"], list) and component["license_inputs"]


def test_source_locks_and_required_native_versions_are_exact() -> None:
    provenance = _load_provenance()
    locks = provenance["source_locks"]
    assert isinstance(locks, dict)
    assert set(locks) >= {"codex", "ripgrep", "rusty_v8", "v8", "icu", "tree_sitter"}

    assert locks["codex"]["tag"] == "rust-v0.149.0"
    assert locks["codex"]["commit"] == CODEX_COMMIT
    assert locks["codex"]["cargo_lock_sha256"] == CODEX_LOCK_SHA256
    assert locks["ripgrep"]["tag"] == "15.2.0"
    assert locks["ripgrep"]["commit"] == RIPGREP_COMMIT
    assert locks["ripgrep"]["cargo_lock_sha256"] == RIPGREP_LOCK_SHA256
    assert locks["rusty_v8"]["tag"] == "v150.4.0"
    assert locks["rusty_v8"]["commit"] == RUSTY_V8_COMMIT
    gitlinks = locks["rusty_v8"]["gitlinks"]
    assert isinstance(gitlinks, list)
    assert {gitlink["path"]: gitlink["commit"] for gitlink in gitlinks} == RUSTY_V8_GITLINKS
    for gitlink in gitlinks:
        assert isinstance(gitlink["source_url"], str) and gitlink["source_url"]
        assert isinstance(gitlink["disposition"], str) and gitlink["disposition"]
    assert locks["v8"]["commit"] == V8_COMMIT
    assert locks["icu"]["commit"] == ICU_COMMIT
    assert locks["tree_sitter"]["commit"] == TREE_SITTER_COMMIT

    for name, lock in locks.items():
        commit = lock.get("commit")
        if commit is not None:
            assert HEX_40.fullmatch(commit), f"source_locks.{name}.commit is not a full Git SHA"
        for field, value in lock.items():
            if field.endswith("sha256"):
                _assert_sha256(value, field=f"source_locks.{name}.{field}")

    by_name = {component["name"]: component for component in provenance["components"]}
    assert set(REQUIRED_COMPONENTS) <= set(by_name)
    for name, (version, checksum) in REQUIRED_COMPONENTS.items():
        component = by_name[name]
        assert component["version"] == version
        assert component["registry_checksum"] == checksum
        assert component["crate_archive_sha256"] == checksum
        assert isinstance(component["source"], str) and component["source"]
        assert component["native_payloads"], f"{name} has no pinned native/data payload"
        assert component["license_inputs"], f"{name} has no pinned license input"


def test_every_native_payload_and_license_input_has_a_real_digest() -> None:
    provenance = _load_provenance()
    license_input_count = 0
    license_input_hashes: set[str] = set()
    for component in provenance["components"]:
        name = component["name"]
        registry_checksum = component["registry_checksum"]
        if registry_checksum is not None:
            _assert_sha256(registry_checksum, field=f"components.{name}.registry_checksum")
        vcs_commit = component["vcs_commit"]
        if vcs_commit is not None:
            assert HEX_40.fullmatch(vcs_commit), f"components.{name}.vcs_commit"
        crate_hash = component["crate_archive_sha256"]
        if crate_hash is not None:
            _assert_sha256(crate_hash, field=f"components.{name}.crate_archive_sha256")

        for index, native_payload in enumerate(component["native_payloads"]):
            assert set(native_payload) >= {"path", "sha256", "kind"}
            assert native_payload["path"]
            assert native_payload["kind"]
            _assert_sha256(
                native_payload["sha256"],
                field=f"components.{name}.native_payloads[{index}].sha256",
            )

        for index, license_input in enumerate(component["license_inputs"]):
            assert set(license_input) >= {
                "title",
                "origin",
                "path",
                "sha256",
                "notice_sha256",
                "extraction",
            }
            for field in ("title", "origin", "path", "extraction"):
                assert isinstance(license_input[field], str) and license_input[field]
            _assert_sha256(
                license_input["sha256"],
                field=f"components.{name}.license_inputs[{index}].sha256",
            )
            _assert_sha256(
                license_input["notice_sha256"],
                field=f"components.{name}.license_inputs[{index}].notice_sha256",
            )
            license_input_hashes.add(license_input["sha256"])
            license_input_count += 1

    notice_bundle = provenance["notice_bundle"]
    assert notice_bundle["license_input_count"] == license_input_count
    assert license_input_hashes >= REQUIRED_RUSTY_V8_LICENSE_SHA256


def test_rusty_v8_windows_archive_and_binding_are_exact() -> None:
    provenance = _load_provenance()
    artifacts = provenance["artifacts"]
    assert isinstance(artifacts, list) and artifacts
    for index, artifact in enumerate(artifacts):
        assert set(artifact) >= {"name", "kind", "source_url", "sha256"}
        assert all(artifact[field] for field in ("name", "kind", "source_url"))
        _assert_sha256(artifact["sha256"], field=f"artifacts[{index}].sha256")
        if artifact.get("expanded_sha256") is not None:
            _assert_sha256(
                artifact["expanded_sha256"],
                field=f"artifacts[{index}].expanded_sha256",
            )

    archive = _artifact_matching(
        artifacts, "rusty_v8_ptrcomp_sandbox_release_x86_64-pc-windows-msvc.lib.gz"
    )
    assert archive["source_url"].endswith(
        "/rusty-v8-v150.4.0/rusty_v8_ptrcomp_sandbox_release_x86_64-pc-windows-msvc.lib.gz"
    )
    assert archive["sha256"] == RUSTY_V8_ARCHIVE_SHA256
    assert archive["expanded_sha256"] == RUSTY_V8_EXPANDED_ARCHIVE_SHA256
    inventory = archive["reviewed_component_inventory"]
    assert inventory["object_member_count"] == 1928
    assert inventory["member_prefix_counts"] == RUSTY_V8_MEMBER_PREFIX_COUNTS
    markers = {
        marker["component"]: (marker["marker_ascii"], marker["occurrence_count"])
        for marker in inventory["header_only_symbol_markers"]
    }
    assert markers == RUSTY_V8_HEADER_MARKERS

    binding = _artifact_matching(
        artifacts, "src_binding_ptrcomp_sandbox_release_x86_64-pc-windows-msvc.rs"
    )
    assert binding["sha256"] == RUSTY_V8_BINDING_SHA256


def test_runtime_notice_excludes_reviewed_build_only_gitlinks() -> None:
    provenance = _load_provenance()
    v8_component = next(
        component for component in provenance["components"] if component["name"] == "v8"
    )
    license_origins = {item["origin"] for item in v8_component["license_inputs"]}
    for build_only in (
        "third_party/jinja2",
        "third_party/markupsafe",
        "tools/clang",
        "third_party/rust",
        "tools/win",
    ):
        assert all(build_only not in origin for origin in license_origins)


def test_notice_bundle_hash_and_required_attributions_are_exact() -> None:
    provenance = _load_provenance()
    notice = NOTICE_PATH.read_text(encoding="utf-8")
    notice_bundle = provenance["notice_bundle"]

    assert notice_bundle["path"].endswith("NATIVE_THIRD_PARTY_NOTICES.md")
    _assert_sha256(notice_bundle["sha256"], field="notice_bundle.sha256")
    assert notice_bundle["sha256"] == _sha256(NOTICE_PATH)
    assert not PLACEHOLDER.search(notice)
    for attribution in REQUIRED_ATTRIBUTIONS:
        assert attribution in notice

