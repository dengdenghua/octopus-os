#!/usr/bin/env python3
"""Generate the pinned native/data notices for the bundled Codex tools.

This is deliberately separate from the Cargo license-report generator. Cargo
metadata describes the Rust wrappers, but it does not prove the license inputs
for vendored C/C++ sources, downloaded static libraries, or embedded data.

The generator never follows a branch or downloads a file. Every source commit,
crate archive, native artifact, and license input is checked against a reviewed
SHA-256 before it can contribute to the committed output.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, overload

CODEX_VERSION = "0.149.0"
CODEX_TAG = f"rust-v{CODEX_VERSION}"
CODEX_COMMIT = "758ef40f50c1a458425c7cfbf1eb12cbc07af0b0"
CODEX_LOCK_SHA256 = "0c32858e9c47d0acf82735c8620c96840a5381152eec63acad15d1acadb9edad"
RIPGREP_VERSION = "15.2.0"
RIPGREP_TAG = RIPGREP_VERSION
RIPGREP_COMMIT = "e89fff89ac9af12e8d4ce9d5fd07beb408ca730f"
RIPGREP_LOCK_SHA256 = "7a7d39cda8a03930e578f1dbb724e055771901842eca239e03b01e19da946a64"
RUSTY_V8_VERSION = "150.4.0"
RUSTY_V8_TAG = f"v{RUSTY_V8_VERSION}"
RUSTY_V8_COMMIT = "5c15a6995c9bb4bacd3e341b59fff32c909c80bf"
V8_COMMIT = "ac1e23989121713ca642f6650b34deff7b686896"
ICU_COMMIT = "ee5f27adc28bd3f15b2c293f726d14d2e336cbd5"
TREE_SITTER_COMMIT = "da6fe9beb4f7f67beb75914ca8e0d48ae48d6406"
TARGET = "x86_64-pc-windows-msvc"

RUSTY_V8_ARCHIVE_NAME = "rusty_v8_ptrcomp_sandbox_release_x86_64-pc-windows-msvc.lib.gz"
RUSTY_V8_ARCHIVE_URL = (
    "https://github.com/openai/codex/releases/download/rusty-v8-v150.4.0/" + RUSTY_V8_ARCHIVE_NAME
)
RUSTY_V8_ARCHIVE_SHA256 = "732ec5da4243aa166799780c8519a5eea6f32f6e47657a323342794dc3c239d6"
RUSTY_V8_EXPANDED_SHA256 = "00baffcf54b30fe198c3e5cc40ec88674ff99cac2b8b60f8e325efce3ce771bb"
RUSTY_V8_BINDING_NAME = "src_binding_ptrcomp_sandbox_release_x86_64-pc-windows-msvc.rs"
RUSTY_V8_BINDING_URL = (
    "https://github.com/openai/codex/releases/download/rusty-v8-v150.4.0/" + RUSTY_V8_BINDING_NAME
)
RUSTY_V8_BINDING_SHA256 = "dabf78ba1faac127660db9862b1d0354175c71b8db2d4fcb5bacbd9c93576b16"

REPO_ROOT = Path(__file__).resolve().parents[2]
LICENSE_ROOT = REPO_ROOT / "extras/desktop/licenses" / f"codex-{CODEX_VERSION}"
MANIFEST_PATH = LICENSE_ROOT / "NATIVE_PROVENANCE.json"
NOTICE_PATH = LICENSE_ROOT / "NATIVE_THIRD_PARTY_NOTICES.md"


@dataclass(frozen=True)
class LicenseFile:
    title: str
    path: str
    sha256: str


@dataclass(frozen=True)
class GitlinkSpec:
    path: str
    commit: str
    source_url: str
    disposition: str
    licenses: tuple[LicenseFile, ...] = ()
    source_evidence: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class CrateSpec:
    name: str
    version: str
    checksum: str
    vcs_commit: str
    vcs_file_sha256: str
    reachable_from: tuple[str, ...]
    licenses: tuple[LicenseFile, ...]
    native_payloads: tuple[tuple[str, str, str], ...] = ()

    @property
    def directory(self) -> str:
        return f"{self.name}-{self.version}"


CRATES = (
    CrateSpec(
        "aws-lc-sys",
        "0.39.0",
        "1fa7e52a4c5c547c741610a2c6f123f3881e409b714cd27e6798ef020c514f0a",
        "d61726b69d589460645e1b22ebd9a847b7f2f63a",
        "fb6ac984c1eba4cfdc56591241ed2d390e0e788d7bb9dd0e490e9586fc794955",
        ("codex-cli", "code-mode-host", "windows-sandbox"),
        (
            LicenseFile(
                "AWS-LC and BoringSSL notices",
                "LICENSE",
                "728536b4160e051f86d7c9c388f704866b3d512cd7df97ac3516c65279523c4e",
            ),
            LicenseFile(
                "AWS-LC vendored Fiat Cryptography license",
                "aws-lc/third_party/fiat/LICENSE",
                "43e358d7b6eb109d0f51f7b3a090fd82607965767c25fadee39e922475de2061",
            ),
        ),
    ),
    CrateSpec(
        "ring",
        "0.17.14",
        "a4689e6c2294d81e88dc6261c768b63bc4fcdb852be6d1352498b114f61383b7",
        "2723abbca9e83347d82b056d5b239c6604f786df",
        "814e1ce7f5a67f2e37d5f9a9e566defa89961b32cce17d1b4da028673929b5b8",
        ("codex-cli", "code-mode-host", "windows-sandbox"),
        (
            LicenseFile(
                "ring license routing notice",
                "LICENSE",
                "b3d734001a94efff3579978d953391aa7115f877657d25eb54037a43875d078a",
            ),
            LicenseFile(
                "ring BoringSSL license",
                "LICENSE-BoringSSL",
                "005fc765ddc5115da796cca915baa9557abae13ff35e0a47c47affc56f6c414d",
            ),
            LicenseFile(
                "ring ISC license",
                "LICENSE-other-bits",
                "f025ccfb7dfb6bdfedc75ca0f67acc69e6fb4998143d834f7c2f38a29989680f",
            ),
            LicenseFile(
                "ring once_cell Apache-2.0 license",
                "src/polyfill/once_cell/LICENSE-APACHE",
                "a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2",
            ),
            LicenseFile(
                "ring once_cell MIT license",
                "src/polyfill/once_cell/LICENSE-MIT",
                "6ee2ed6c77710de911761acd5fc1ad1da00f476beb1a7ef27e78c2d1858deafc",
            ),
            LicenseFile(
                "ring vendored Fiat Cryptography license",
                "third_party/fiat/LICENSE",
                "9eacbcb81be660840c714a560a9d65ba07913db98dd4baf969f78dd499fdd60f",
            ),
        ),
    ),
    CrateSpec(
        "blake3",
        "1.8.2",
        "3888aaa89e4b2a40fca9848e400f6a658a5a3978de7be858e209cafa8be9a4a0",
        "df610ddc3b93841ffc59a87e3da659a15910eb46",
        "7894c00ae736974024f739babc8f07e0fe4579fe917c9d19e45e8f8635b47981",
        ("codex-cli", "code-mode-host", "windows-sandbox"),
        (
            LicenseFile(
                "BLAKE3 Apache-2.0 license",
                "LICENSE_A2",
                "00fcc7a934ddbc9ece2a7cc063ac788e284b703b1d705ccbba72d462aa97921e",
            ),
            LicenseFile(
                "BLAKE3 Apache-2.0 with LLVM exception license",
                "LICENSE_A2LLVM",
                "a5695f57ea0c221e0e8b7d784ff774c35e88c3d3270353646a925880bb3492cc",
            ),
            LicenseFile(
                "BLAKE3 CC0-1.0 license",
                "LICENSE_CC0",
                "a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499",
            ),
        ),
    ),
    CrateSpec(
        "zstd-sys",
        "2.0.16+zstd.1.5.7",
        "91e19ebc2adc8f83e43039e79776e3fda8ca919132d68a1fed6a5faca2683748",
        "434ca4cb364e8a81846a2d99d430977e07b15a52",
        "2b91fc14fde629d0d98341452c86a6c31977cc47304bd1ee75d6f625b28be212",
        ("codex-cli", "code-mode-host", "windows-sandbox"),
        (
            LicenseFile(
                "zstd-sys license",
                "LICENSE",
                "a77b7cfeaf911ed410ffbe76f0cb2b24ad8a4d94e7ead5727e914425c416cc63",
            ),
            LicenseFile(
                "zstd-sys Apache-2.0 license",
                "LICENSE.Apache-2.0",
                "0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594",
            ),
            LicenseFile(
                "zstd-sys BSD-3-Clause license",
                "LICENSE.BSD-3-Clause",
                "48341f685c87304089aa099b23c386f8bacc519ef555aa7a13e239908907b3fd",
            ),
            LicenseFile(
                "zstd-sys MIT license",
                "LICENSE.Mit",
                "129e8edef29e9abcd2ebabe252f4ef1b1289cdca356bf0040284a2fbccfb96c8",
            ),
            LicenseFile(
                "Zstandard copying notice",
                "zstd/COPYING",
                "f9c375a1be4a41f7b70301dd83c91cb89e41567478859b77eef375a52d782505",
            ),
            LicenseFile(
                "Zstandard BSD license",
                "zstd/LICENSE",
                "7055266497633c9025b777c78eb7235af13922117480ed5c674677adc381c9d8",
            ),
        ),
    ),
    CrateSpec(
        "bzip2-sys",
        "0.1.13+1.0.8",
        "225bff33b2141874fe80d71e07d6eec4f85c5c216453dd96388240f96e1acc14",
        "f5f9d090d8a43b789ab9484ec1f78a46be076e62",
        "6b03f10cc9fb5d3f5de77a790bb991430097542a7a4b45e5d3db91a470d5bab2",
        ("codex-cli",),
        (
            LicenseFile(
                "bzip2-sys Apache-2.0 license",
                "LICENSE-APACHE",
                "a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2",
            ),
            LicenseFile(
                "bzip2-sys MIT license",
                "LICENSE-MIT",
                "c8c6324cf2f0f076e3ed5dea4a21f00bd4dd6fce44f72b110bc52e7fe06f6e1e",
            ),
            LicenseFile(
                "libbzip2 1.0.8 license",
                "bzip2-1.0.8/LICENSE",
                "c6dbbf828498be844a89eaa3b84adbab3199e342eb5cb2ed2f0d4ba7ec0f38a3",
            ),
        ),
    ),
    CrateSpec(
        "libsqlite3-sys",
        "0.37.0",
        "b1f111c8c41e7c61a49cd34e44c7619462967221a6443b0ec299e0ac30cfb9b1",
        "2a1790a69107cd03dae85d501dcbdb11c5b32ef3",
        "51cd79ec7d1b5b4c72b29c0552369d2715bfed17ba97cc47c80f8b168cf5f2e0",
        ("codex-cli",),
        (
            LicenseFile(
                "libsqlite3-sys MIT license",
                "LICENSE",
                "c10c1f27337546471e5f7e4e97fdd398b35b9d4e126115dcd22de8d8e65abf6f",
            ),
            LicenseFile(
                "SQLCipher license (conservative vendored notice)",
                "sqlcipher/LICENSE",
                "ea4fcb309f14a22065e1ea45362d494d320012249ed865fe9c7c0946db754131",
            ),
        ),
        (
            (
                "vendored SQLite 3.51.3 amalgamation",
                "sqlite3/sqlite3.c",
                "9512509b1bccb7461f79bea8aad6280ae4699e925fa4804381b71f59e7efb0c5",
            ),
        ),
    ),
    CrateSpec(
        "lzma-sys",
        "0.1.20",
        "5fda04ab3764e6cde78b9974eec4f779acaba7c4e84b36eca3cf77c581b85d27",
        "f68a0980b0a478a54b3f069c643867853dac4ed9",
        "cdd053c1406962d91b15351ffaf295d1fe5ffbef2d79a6f4e23fd2809e903131",
        ("codex-cli",),
        (
            LicenseFile(
                "lzma-sys Apache-2.0 license",
                "LICENSE-APACHE",
                "a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2",
            ),
            LicenseFile(
                "lzma-sys MIT license",
                "LICENSE-MIT",
                "69036b033e4bb951821964dbc3d9b1efe6913a6e36d9c1f206de4035a1a85cc4",
            ),
            LicenseFile(
                "XZ Utils licensing summary",
                "xz-5.2/COPYING",
                "bcb02973ef6e87ea73d331b3a80df7748407f17efdb784b61b47e0e610d3bb5c",
            ),
            LicenseFile(
                "XZ Utils authors",
                "xz-5.2/AUTHORS",
                "72d7a7ee8a4eaca5d0b53f20609eff95d5e6f9e155ecce98127414b8215b0b15",
            ),
            LicenseFile(
                "XZ vendored GPL-2.0 text",
                "xz-5.2/COPYING.GPLv2",
                "8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643",
            ),
            LicenseFile(
                "XZ vendored GPL-3.0 text",
                "xz-5.2/COPYING.GPLv3",
                "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
            ),
            LicenseFile(
                "XZ vendored LGPL-2.1 text",
                "xz-5.2/COPYING.LGPLv2.1",
                "dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551",
            ),
        ),
    ),
    CrateSpec(
        "onig_sys",
        "69.9.1",
        "c7f86c6eef3d6df15f23bcfb6af487cbd2fed4e5581d58d5bf1f5f8b7f6727dc",
        "ed05d7ac1a1a138c6d9c46b451b9d9bea0fbe0b1",
        "7207d907564d6e5aacf9cfdd6be043cb923079b80ab96318806c1e96e8371365",
        ("codex-cli",),
        (
            LicenseFile(
                "onig_sys license",
                "LICENSE.md",
                "71f321038b088358004bee991635ac09e4c703bec467d3c30c06992c0595f189",
            ),
            LicenseFile(
                "Oniguruma native source license",
                "oniguruma/COPYING",
                "70ba5469ea0bab6e18a32d7009068f996503168d27be57747e08da34337ff26f",
            ),
            LicenseFile(
                "Oniguruma authors",
                "oniguruma/AUTHORS",
                "172a2a72c080d3067969599308c91385af86fdeb2419f8ad687883ca5a8ff912",
            ),
        ),
    ),
    CrateSpec(
        "tree-sitter",
        "0.25.10",
        "78f873475d258561b06f1c595d93308a7ed124d9977cb26b148c2084a4a3cc87",
        TREE_SITTER_COMMIT,
        "b7e5478ceabc6757e31509d955e50c41da48e38edc6f09bb4e9b0d24fb3d8e69",
        ("codex-cli",),
        (
            LicenseFile(
                "Tree-sitter vendored Unicode data license",
                "src/unicode/LICENSE",
                "6a18c5fac70d7860b57f5b72b4e2c9a1ba6b3d2741eef7ff9767c5379364f10d",
            ),
        ),
    ),
    CrateSpec(
        "pcre2-sys",
        "0.2.10",
        "18b9073c1a2549bd409bf4a32c94d903bb1a09bf845bc306ae148897fa0760a4",
        "fd05026e1bf3ad62b3876cf9bd952dc742368462",
        "a7b079cc9c3d927023a3ef57f0e7c1876c0a3ca0848f61917f2e6d614267e9b8",
        ("ripgrep",),
        (
            LicenseFile(
                "pcre2-sys wrapper notice",
                "COPYING",
                "01c266bced4a434da0051174d6bee16a4c82cf634e2679b6155d40d75012390f",
            ),
            LicenseFile(
                "pcre2-sys MIT license",
                "LICENSE-MIT",
                "cb3c929a05e6cbc9de9ab06a4c57eeb60ca8c724bef6c138c87d3a577e27aa14",
            ),
            LicenseFile(
                "pcre2-sys Unlicense text",
                "UNLICENSE",
                "7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c",
            ),
        ),
        (
            (
                "vendored PCRE2 10.46 source",
                "upstream/src/pcre2_compile.c",
                "54a8fb643749f0a7753f68d65f4e9e0dbc4728ed04461b580a0fba3b35d57b7d",
            ),
            (
                "vendored SLJIT source",
                "upstream/deps/sljit/sljit_src/sljitLir.c",
                "0746669d1bf493f6f927a3318743c9a1488088b65826d0fd413008154a1b192f",
            ),
            (
                "PCRE2 10.46 update recipe",
                "update-pcre2",
                "40b55b8e57aedfff5b3a33c5026c98da0163713048ba6cc48255bc23aa500c98",
            ),
        ),
    ),
    CrateSpec(
        "v8",
        RUSTY_V8_VERSION,
        "42a978ff11f15b24e5c05a7123cf2b68f41e763546699781a924ef4e2cf43a49",
        RUSTY_V8_COMMIT,
        "51ff2633825e8ae2d16640ac2557240c720c4bedd60504e08bd5a29ae1e13c6c",
        ("code-mode-host",),
        (),
    ),
    CrateSpec(
        "deno_core_icudata",
        "0.77.0",
        "a9efff8990a82c1ae664292507e1a5c6749ddd2312898cdf9cd7cb1fd4bc64c6",
        "83537ba16a4bf1645fc8322e81f7aaf0149a8922",
        "424d8a8ac54fa0dbfb93eda4eafdae1ab1c193cfd9b482030191ea3f599756ff",
        ("code-mode-host",),
        (),
        (
            (
                "embedded ICU data",
                "src/icudtl.dat",
                "1cf67874b5a87a8363a86fb3f81e3cbbed54d389062dab8fb52308d5cf8c8612",
            ),
        ),
    ),
)


RUSTY_V8_GITLINKS = (
    GitlinkSpec(
        "v8",
        V8_COMMIT,
        "https://github.com/denoland/v8.git",
        "primary V8 source; 1197 reviewed archive members",
    ),
    GitlinkSpec(
        "build",
        "8acb33ac8dceef0503443109c0a92988189563ef",
        "https://github.com/denoland/chromium_build.git",
        "Chromium Rust allocator source; 2 reviewed archive members",
        source_evidence=(
            (
                "Chromium Rust allocator error handler",
                "rust/allocator/alloc_error_handler_impl.cc",
                "b39be8f83054ec9b655244cb5bdb3e2b71a22e7ea70f039260439a8a4a36d1ee",
            ),
            (
                "Chromium Rust allocator alias implementation",
                "rust/allocator/alias.cc",
                "4e71087b1dd5aeae401550bdfd1e28d7edbdec0d0e2ac10a3843c665b80ff4e6",
            ),
        ),
    ),
    GitlinkSpec(
        "buildtools",
        "17495e454aae81b581e8b3caccbb53054509b280",
        "https://chromium.googlesource.com/chromium/src/buildtools.git",
        "Chromium build support and the BSD terms referenced by Chromium sources",
        licenses=(
            LicenseFile(
                "Chromium BSD license for allocator and PartitionAlloc sources",
                "LICENSE",
                "ff11d445fb41a1087c7630e120ab15f1a2cb67c1b707173cb494141805fca35e",
            ),
        ),
    ),
    GitlinkSpec(
        "tools/clang",
        "45f4b9e25124809497a27a8ae0e63d603b0f9f1b",
        "https://chromium.googlesource.com/chromium/src/tools/clang.git",
        "reviewed build-only gitlink; no distinct archive member or root license",
    ),
    GitlinkSpec(
        "third_party/jinja2",
        "c3027d884967773057bf74b957e3fea87e5df4d7",
        "https://chromium.googlesource.com/chromium/src/third_party/jinja2.git",
        "reviewed build-time code-generation input; no archive member",
    ),
    GitlinkSpec(
        "third_party/markupsafe",
        "4256084ae14175d38a3ff7d739dca83ae49ccec6",
        "https://chromium.googlesource.com/chromium/src/third_party/markupsafe.git",
        "reviewed build-time code-generation input; no archive member",
    ),
    GitlinkSpec(
        "third_party/icu",
        ICU_COMMIT,
        "https://chromium.googlesource.com/chromium/deps/icu.git",
        "ICU source and data; 456 reviewed archive members",
    ),
    GitlinkSpec(
        "third_party/abseil-cpp",
        "d16e32215c3ab90ba57c2e904a5344d85c7353e4",
        "https://chromium.googlesource.com/chromium/src/third_party/abseil-cpp.git",
        "Abseil source; 138 reviewed archive members",
        licenses=(
            LicenseFile(
                "Abseil Apache-2.0 license",
                "LICENSE",
                "c79a7fea0e3cac04cd43f20e7b648e5a0ff8fa5344e644b0ee09ca1162b62747",
            ),
            LicenseFile(
                "Abseil authors",
                "AUTHORS",
                "1fd39030c119b4c97014f59e0ad0ed65f23475bbdf55a52740f31eb82b34b4ee",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/fp16/src",
        "3d2de1816307bac63c16a297e8c4dc501b4076df",
        "https://github.com/Maratyszcza/FP16.git",
        "header-only V8 dependency with reviewed symbol evidence",
        licenses=(
            LicenseFile(
                "FP16 MIT license",
                "LICENSE",
                "17e4f539024be2749ee729d1e2f01d24cef12ece8c9bf18e91a4349be29c80bf",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/libc++/src",
        "5abc7f839700f0f17338434e1c1c6a8c87c00c11",
        "https://chromium.googlesource.com/external/github.com/llvm/llvm-project/libcxx.git",
        "custom libc++ source; 51 reviewed archive members",
        licenses=(
            LicenseFile(
                "LLVM libc++ Apache-2.0 WITH LLVM-exception license",
                "LICENSE.TXT",
                "539dd7aed86e8a4f12cbdd0e6c50c189c7d74847e4fecc64ce2c6ee3a01da38b",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/libc++abi/src",
        "8f11bb1d4438d0239d0dfc1bd9456a9f31629dda",
        "https://chromium.googlesource.com/external/github.com/llvm/llvm-project/libcxxabi.git",
        "custom libc++ include/build source; conservatively attributed",
        licenses=(
            LicenseFile(
                "LLVM libc++abi Apache-2.0 WITH LLVM-exception license",
                "LICENSE.TXT",
                "e2b35be49f7284a45b7baca8fc7b3ab7440e7902392b2528a457816b5bb2a15c",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/libunwind/src",
        "d6c7a21e978f0adaa43accaad53bc64f0b64f6ec",
        "https://chromium.googlesource.com/external/github.com/llvm/llvm-project/libunwind.git",
        "custom libc++ include/build source; conservatively attributed",
        licenses=(
            LicenseFile(
                "LLVM libunwind Apache-2.0 WITH LLVM-exception license",
                "LICENSE.TXT",
                "b5efebcaca80879234098e52d1725e6d9eb8fb96a19fce625d39184b705f7b6d",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/fast_float/src",
        "05087a303dad9c98768b33c829d398223a649bc6",
        "https://chromium.googlesource.com/external/github.com/fastfloat/fast_float.git",
        "header-only V8 dependency with reviewed symbol evidence",
        licenses=(
            LicenseFile(
                "fast_float MIT license",
                "LICENSE-MIT",
                "e562f3f974ced7e69dd1db77b820b36bcf8f30377f1aa105723fba449c53c4e6",
            ),
            LicenseFile(
                "fast_float Apache-2.0 license",
                "LICENSE-APACHE",
                "097a889aa954d04e088b790b10a4014d6189561d0a6013935a73ce3d4ddaaf06",
            ),
            LicenseFile(
                "fast_float Boost-1.0 license",
                "LICENSE-BOOST",
                "8d8291caf1cee26d23acf3eb67c9f9a2d58f1c681b16a4fbe8cbfb9e3c0b5a9b",
            ),
            LicenseFile(
                "fast_float authors",
                "AUTHORS",
                "ed249424bce4e318fa190dd6d8becf60cfc37287132ad3f89e16e0f28d878dcf",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/llvm-libc/src",
        "9309c117ebae84dd2f9df1ef99de4782162527d5",
        "https://chromium.googlesource.com/external/github.com/llvm/llvm-project/libc.git",
        "V8 include/build source; conservatively attributed",
        licenses=(
            LicenseFile(
                "LLVM libc Apache-2.0 WITH LLVM-exception license",
                "LICENSE.TXT",
                "ebcd9bbf783a73d05c53ba4d586b8d5813dcdf3bbec50265860ccc885e606f47",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/simdutf",
        "f7356eed293f8208c40b3c1b344a50bd70971983",
        "https://chromium.googlesource.com/chromium/src/third_party/simdutf",
        "simdutf source; 1 reviewed archive member",
        licenses=(
            LicenseFile(
                "simdutf MIT license",
                "LICENSE",
                "fc8dbc04e03ad4efc08a647ffe7f995b811a95bc04c0e85a56d5277c6593fa5f",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/highway/src",
        "2607d3b5b0113992fe84d3848859eae13b3b52c1",
        "https://chromium.googlesource.com/external/github.com/google/highway.git",
        "Highway source; 7 reviewed archive members",
        licenses=(
            LicenseFile(
                "Highway full Apache-2.0/BSD-3-Clause/CC0 license",
                "LICENSE",
                "e340270d4f64384569a91d546acb5b094d69ce47f0c015db77abb74dc6f815af",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/partition_alloc",
        "ff3b8b885b8374cbd3902642d94dc737bda93d5d",
        "https://chromium.googlesource.com/chromium/src/base/allocator/partition_allocator.git",
        "PartitionAlloc source; 66 reviewed archive members",
        source_evidence=(
            (
                "PartitionAlloc Chromium BSD source header",
                "src/partition_alloc/address_pool_manager.cc",
                "2c2c1f1483df6e276ff58417f9095f212034652e7c1c2f43cc86bc064cd7bd32",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/dragonbox/src",
        "beeeef91cf6fef89a4d4ba5e95d47ca64ccb3a44",
        "https://chromium.googlesource.com/external/github.com/jk-jeon/dragonbox.git",
        "header-only V8 dependency with reviewed symbol evidence",
        licenses=(
            LicenseFile(
                "Dragonbox Apache-2.0 WITH LLVM-exception license",
                "LICENSE-Apache2-LLVM",
                "9e45e856bedccee9f67254082ca11851d954de2fed7448c4bed19ad9aab99a91",
            ),
            LicenseFile(
                "Dragonbox Boost-1.0 license",
                "LICENSE-Boost",
                "c9bff75738922193e67fa726fa225535870d2aa1059f91452c411736284ad566",
            ),
        ),
    ),
    GitlinkSpec(
        "third_party/rust",
        "26e8ff47f18a8d28d6187a04b6a16cb7332356f8",
        "https://chromium.googlesource.com/chromium/src/third_party/rust",
        "reviewed build-only gitlink; no distinct archive member or root license",
    ),
    GitlinkSpec(
        "tools/win",
        "faefd1b6fa9eeb033ad6fe60368ccb9bf908cbd0",
        "https://chromium.googlesource.com/chromium/src/tools/win",
        "reviewed build-only gitlink; no distinct archive member or root license",
    ),
)


RUSTY_V8_ARCHIVE_MEMBER_PREFIX_COUNTS = {
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

RUSTY_V8_HEADER_ONLY_SYMBOL_MARKERS = {
    "fast_float": (b"fast_float@@", 49),
    "dragonbox": (b"dragonbox@jkj@@", 27),
    "fp16": (b"fp64_to_fp16_raw_bits", 13),
}


V8_LICENSES = {
    "AUTHORS": "fa60d5a34dff8985ccee2bbd9702cac4ce8712e1c0d7521ce00879403b0fcc27",
    "LICENSE": "6ab33af8774a0f396ee3aeeb761e3229057682d6f9fa7f572e390c2cb3a6e509",
    "LICENSE.fdlibm": "e7115e18444dae09d17f361ddc365fb1d342640fe500796209c63f7c80dfae10",
    "LICENSE.strongtalk": "6a585a9f466654abc8fc0829d56b1bc987e3a073d31faa03bba37d33640a23cd",
    "LICENSE.v8": "4af93c12062c58058378de2397dc1c92bbff9ddfb1d583a01c84127557ce97ca",
    "third_party/colorama/LICENSE": "15137d6c822e3ab097093a33c3a39a9df699f373f6438867ad534ff60762a947",
    "third_party/fp16/LICENSE": "c91377adafa2f498211a4fe017788e20c38892124673860f068f2919c289193d",
    "third_party/glibc/LICENSE": "b634ab5640e258563c536e658cad87080553df6f34f62269a21d554844e58bfe",
    "third_party/highway/LICENSE": "43070e2d4e532684de521b885f385d0841030efa2b1a20bafb76133a5e1379c1",
    "third_party/inspector_protocol/LICENSE": "450ebb9e9e50c2809d9ca581931fc8946d8d5aa0c8c35aae9ac471342fe4a88e",
    "third_party/jsoncpp/LICENSE": "76c45ece83a26117f86f4e349e7df118708e061e87225328fb478ce1e8b3eb86",
    "third_party/rapidhash-v8/LICENSE": "db672df7faf793fbe28b8074fdd10822363e1525816b45c9627754885de842b0",
    "third_party/re2/LICENSE": "6040cda75d90b1738292a631d89934c411ef7ffd543c4d6a1b7edfc8edf29449",
    "third_party/siphash/LICENSE": "36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673",
    "third_party/utf8-decoder/LICENSE": "3f712e5fbdfdbd5ee7d9b8c8152580220df55de47f4eba2f26c95c4de19ad096",
    "third_party/v8/builtins/LICENSE": "b9a6d9320b8f2693e8d41e496ce56caadacaddcca9be2a64a61749278f425cf2",
    "third_party/v8/codegen/LICENSE": "17e4f539024be2749ee729d1e2f01d24cef12ece8c9bf18e91a4349be29c80bf",
    "third_party/valgrind/LICENSE": "ebf25b8ce59c9e8883acd1ca75b6fc121937ca034f666c4077d2be739d2e1622",
    "third_party/vtune/LICENSE": "02aea3ade0c02470335987f7d93ffaed877bcefc8414a416e2e548665bb0bfd0",
    "third_party/wasm-api/LICENSE": "c6596eb7be8581c18be736c846fb9173b69eccf6ef94c5135893ec56bd92ba08",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@overload
def run(*args: str, cwd: Path | None = None, binary: Literal[False] = False) -> str: ...


@overload
def run(*args: str, cwd: Path | None = None, binary: Literal[True]) -> bytes: ...


def run(*args: str, cwd: Path | None = None, binary: bool = False) -> bytes | str:
    result = subprocess.run(args, cwd=cwd, check=False, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode(errors="replace").strip()
        raise RuntimeError(f"command failed ({' '.join(args)}): {detail}")
    return result.stdout if binary else result.stdout.decode().strip()


def git_bytes(repo: Path, commit: str, path: str) -> bytes:
    return run("git", "-C", str(repo), "show", f"{commit}:{path}", binary=True)


def verify_git_commit(repo: Path, revision: str, expected: str) -> None:
    actual = run("git", "-C", str(repo), "rev-parse", f"{revision}^{{commit}}")
    if actual != expected:
        raise RuntimeError(f"{repo}: {revision} resolved to {actual}, expected {expected}")


def verify_sha(data: bytes, expected: str, origin: str) -> None:
    actual = sha256_bytes(data)
    if actual != expected:
        raise RuntimeError(f"{origin}: SHA-256 {actual}, expected {expected}")


def find_unique(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern!r} below {root}, found {matches}")
    return matches[0]


def normalize_notice(data: bytes) -> str:
    text = data.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def leading_c_comment(data: bytes) -> str:
    text = normalize_notice(data)
    if not text.startswith("/*") or "*/" not in text:
        raise RuntimeError("expected a leading C license comment")
    return text[: text.index("*/") + 2].rstrip() + "\n"


def pcre2_license(data: bytes) -> str:
    text = normalize_notice(data)
    marker = "Original API code Copyright (c) 1997-2012 University of Cambridge"
    marker_index = text.find(marker)
    if marker_index < 0:
        raise RuntimeError("PCRE2 redistribution terms were not found")
    start = text.rfind("/*", 0, marker_index)
    end = text.find("*/", marker_index)
    if start < 0 or end < 0:
        raise RuntimeError("PCRE2 redistribution comment is malformed")
    return text[start : end + 2].rstrip() + "\n"


def sqlite_blessing(data: bytes) -> str:
    text = normalize_notice(data)
    marker = "The author disclaims copyright to this source code."
    marker_index = text.find(marker)
    if marker_index < 0:
        raise RuntimeError("SQLite public-domain blessing was not found")
    start = text.rfind("/*", 0, marker_index)
    end = text.find("*/", marker_index)
    if start < 0 or end < 0:
        raise RuntimeError("SQLite public-domain blessing comment is malformed")
    return text[start : end + 2].rstrip() + "\n"


def coff_archive_member_names(data: bytes) -> list[str]:
    """Read the member names from a Microsoft COFF archive fail-closed."""
    if not data.startswith(b"!<arch>\n"):
        raise RuntimeError("rusty_v8 expanded archive has no COFF archive magic")
    position = 8
    long_names = b""
    members: list[str] = []
    while position < len(data):
        header = data[position : position + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise RuntimeError("rusty_v8 expanded archive has a malformed member header")
        try:
            raw_name = header[:16].decode("ascii").rstrip()
            size = int(header[48:58].decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("rusty_v8 expanded archive has invalid member metadata") from exc
        body_start = position + 60
        body_end = body_start + size
        if body_end > len(data):
            raise RuntimeError("rusty_v8 expanded archive member exceeds the file")
        body = data[body_start:body_end]
        if raw_name == "//":
            long_names = body
        elif raw_name not in {"/", "/SYM64/"}:
            if raw_name.startswith("/") and raw_name[1:].isdigit():
                if not long_names:
                    raise RuntimeError("COFF member references a missing long-name table")
                offset = int(raw_name[1:])
                terminators = [
                    end
                    for end in (
                        long_names.find(b"/\n", offset),
                        long_names.find(b"\x00", offset),
                    )
                    if end >= 0
                ]
                end = min(terminators) if terminators else -1
                if end < 0:
                    raise RuntimeError("COFF long-name entry is unterminated")
                try:
                    name = long_names[offset:end].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeError("COFF long-name entry is not UTF-8") from exc
            else:
                name = raw_name.removesuffix("/")
            if not name:
                raise RuntimeError("COFF archive contains an empty member name")
            members.append(name)
        position = body_end + (size & 1)
    if position != len(data):
        raise RuntimeError("rusty_v8 expanded archive has trailing malformed bytes")
    return members


def verify_rusty_v8_archive_inventory(expanded: bytes) -> dict[str, Any]:
    members = coff_archive_member_names(expanded)
    expected_total = sum(RUSTY_V8_ARCHIVE_MEMBER_PREFIX_COUNTS.values())
    if len(members) != expected_total:
        raise RuntimeError(
            f"rusty_v8 archive has {len(members)} objects, expected {expected_total}"
        )
    counts: dict[str, int] = {}
    unmatched = set(members)
    for prefix, expected in RUSTY_V8_ARCHIVE_MEMBER_PREFIX_COUNTS.items():
        matching = {member for member in members if member.startswith(prefix)}
        if len(matching) != expected:
            raise RuntimeError(
                f"rusty_v8 archive prefix {prefix!r} has {len(matching)} objects, "
                f"expected {expected}"
            )
        counts[prefix] = len(matching)
        unmatched.difference_update(matching)
    if unmatched:
        raise RuntimeError(f"rusty_v8 archive has unreviewed members: {sorted(unmatched)[:10]}")

    markers: list[dict[str, Any]] = []
    for component, (marker, expected) in RUSTY_V8_HEADER_ONLY_SYMBOL_MARKERS.items():
        actual = expanded.count(marker)
        if actual != expected:
            raise RuntimeError(
                f"rusty_v8 archive marker for {component} occurs {actual} times, expected {expected}"
            )
        markers.append(
            {
                "component": component,
                "marker_ascii": marker.decode("ascii"),
                "occurrence_count": actual,
            }
        )
    return {
        "object_member_count": len(members),
        "member_prefix_counts": counts,
        "header_only_symbol_markers": markers,
    }


def parse_lock(text: str) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for block in re.split(r"(?m)^\[\[package\]\]\s*$", text)[1:]:
        name_match = re.search(r'(?m)^name = "([^"]+)"$', block)
        version_match = re.search(r'(?m)^version = "([^"]+)"$', block)
        if not name_match or not version_match:
            raise RuntimeError("malformed package in Cargo.lock")
        checksum_match = re.search(r'(?m)^checksum = "([0-9a-f]{64})"$', block)
        dependencies: list[str] = []
        dependency_match = re.search(r"(?ms)^dependencies = \[(.*?)^\]$", block)
        if dependency_match:
            try:
                dependencies = ast.literal_eval("[" + dependency_match.group(1) + "]")
            except (SyntaxError, ValueError) as exc:
                raise RuntimeError("malformed dependency list in Cargo.lock") from exc
        packages.append(
            {
                "name": name_match.group(1),
                "version": version_match.group(1),
                "checksum": checksum_match.group(1) if checksum_match else None,
                "dependencies": dependencies,
            }
        )
    if not packages:
        raise RuntimeError("Cargo.lock has no packages")
    return packages


def reachable_packages(packages: list[dict[str, Any]], root: str) -> set[tuple[str, str]]:
    by_name: dict[str, list[int]] = defaultdict(list)
    for index, package in enumerate(packages):
        by_name[package["name"]].append(index)
    if root not in by_name:
        raise RuntimeError(f"Cargo.lock does not contain root package {root}")
    queue = deque(by_name[root])
    visited = set(queue)
    while queue:
        package = packages[queue.popleft()]
        for dependency in package["dependencies"]:
            parts = dependency.split()
            name = parts[0]
            version = parts[1] if len(parts) > 1 and parts[1][0].isdigit() else None
            matches = by_name.get(name, [])
            if version:
                matches = [index for index in matches if packages[index]["version"] == version]
            if not matches:
                raise RuntimeError(f"unresolved Cargo.lock dependency {dependency!r}")
            for index in matches:
                if index not in visited:
                    visited.add(index)
                    queue.append(index)
    return {(packages[index]["name"], packages[index]["version"]) for index in visited}


def add_license(
    component: dict[str, Any],
    *,
    title: str,
    origin: str,
    path: str,
    raw: bytes,
    expected_sha256: str,
    extraction: str = "full-file",
    extractor: Callable[[bytes], str] = normalize_notice,
) -> None:
    verify_sha(raw, expected_sha256, origin)
    notice = extractor(raw)
    component["license_inputs"].append(
        {
            "title": title,
            "origin": origin,
            "path": path,
            "sha256": expected_sha256,
            "notice_sha256": sha256_bytes(notice.encode()),
            "extraction": extraction,
            "_notice": notice,
        }
    )


def crate_roots(cargo_home: Path, spec: CrateSpec) -> tuple[Path, Path]:
    archive = find_unique(cargo_home / "registry/cache", f"*/{spec.directory}.crate")
    source = find_unique(cargo_home / "registry/src", f"*/{spec.directory}")
    if sha256_file(archive) != spec.checksum:
        raise RuntimeError(f"{archive}: crate archive does not match registry checksum")
    vcs_file = source / ".cargo_vcs_info.json"
    if sha256_file(vcs_file) != spec.vcs_file_sha256:
        raise RuntimeError(f"{vcs_file}: provenance file changed")
    vcs = json.loads(vcs_file.read_text(encoding="utf-8"))
    if vcs.get("git", {}).get("sha1") != spec.vcs_commit:
        raise RuntimeError(f"{vcs_file}: unexpected source commit")
    return archive, source


def verify_lock_components(
    packages: list[dict[str, Any]], specs: tuple[CrateSpec, ...], ecosystem: str
) -> None:
    indexed = {(package["name"], package["version"]): package for package in packages}
    for spec in specs:
        package = indexed.get((spec.name, spec.version))
        if not package or package["checksum"] != spec.checksum:
            raise RuntimeError(
                f"{ecosystem} Cargo.lock does not pin {spec.name} {spec.version} to {spec.checksum}"
            )


def build_outputs(args: argparse.Namespace) -> tuple[bytes, bytes]:
    verify_git_commit(args.codex_source, CODEX_TAG, CODEX_COMMIT)
    verify_git_commit(args.ripgrep_source, RIPGREP_TAG, RIPGREP_COMMIT)
    verify_git_commit(args.rusty_v8_source, RUSTY_V8_TAG, RUSTY_V8_COMMIT)
    verify_git_commit(args.v8_source, V8_COMMIT, V8_COMMIT)
    verify_git_commit(args.icu_source, ICU_COMMIT, ICU_COMMIT)
    verify_git_commit(args.tree_sitter_source, TREE_SITTER_COMMIT, TREE_SITTER_COMMIT)

    codex_lock = git_bytes(args.codex_source, CODEX_COMMIT, "codex-rs/Cargo.lock")
    ripgrep_lock = git_bytes(args.ripgrep_source, RIPGREP_COMMIT, "Cargo.lock")
    verify_sha(codex_lock, CODEX_LOCK_SHA256, "Codex Cargo.lock")
    verify_sha(ripgrep_lock, RIPGREP_LOCK_SHA256, "ripgrep Cargo.lock")
    codex_packages = parse_lock(codex_lock.decode())
    ripgrep_packages = parse_lock(ripgrep_lock.decode())

    codex_specs = tuple(spec for spec in CRATES if spec.name != "pcre2-sys")
    verify_lock_components(codex_packages, codex_specs, "codex")
    verify_lock_components(
        ripgrep_packages, tuple(spec for spec in CRATES if spec.name == "pcre2-sys"), "ripgrep"
    )

    root_graphs = {
        "codex-cli": reachable_packages(codex_packages, "codex-cli"),
        "code-mode-host": reachable_packages(codex_packages, "codex-code-mode-host"),
        "windows-sandbox": reachable_packages(codex_packages, "codex-windows-sandbox"),
        "ripgrep": reachable_packages(ripgrep_packages, "ripgrep"),
    }
    for spec in CRATES:
        for root in spec.reachable_from:
            if (spec.name, spec.version) not in root_graphs[root]:
                raise RuntimeError(f"{spec.name} {spec.version} is not reachable from {root}")

    gitmodules = git_bytes(args.rusty_v8_source, RUSTY_V8_COMMIT, ".gitmodules")
    verify_sha(
        gitmodules,
        "64abd45978444b338c5d3816cfbec508f68d6877c0c26ece649b9593d2665054",
        "rusty_v8 .gitmodules",
    )
    submodule_sources: dict[str, Path] = {}
    for gitlink in RUSTY_V8_GITLINKS:
        tree_line = run(
            "git",
            "-C",
            str(args.rusty_v8_source),
            "ls-tree",
            RUSTY_V8_COMMIT,
            gitlink.path,
        )
        if tree_line != f"160000 commit {gitlink.commit}\t{gitlink.path}":
            raise RuntimeError(f"rusty_v8 gitlink {gitlink.path} does not equal {gitlink.commit}")
        if gitlink.licenses or gitlink.source_evidence:
            source = args.rusty_v8_source / gitlink.path
            verify_git_commit(source, gitlink.commit, gitlink.commit)
            submodule_sources[gitlink.path] = source

    if sha256_file(args.rusty_v8_archive) != RUSTY_V8_ARCHIVE_SHA256:
        raise RuntimeError("rusty_v8 Windows archive SHA-256 mismatch")
    with gzip.open(args.rusty_v8_archive, "rb") as stream:
        expanded_archive = stream.read()
    if sha256_bytes(expanded_archive) != RUSTY_V8_EXPANDED_SHA256:
        raise RuntimeError("expanded rusty_v8 Windows .lib SHA-256 mismatch")
    archive_inventory = verify_rusty_v8_archive_inventory(expanded_archive)
    if sha256_file(args.rusty_v8_binding) != RUSTY_V8_BINDING_SHA256:
        raise RuntimeError("rusty_v8 Windows binding SHA-256 mismatch")

    components: list[dict[str, Any]] = []
    source_by_name: dict[str, Path] = {}
    for spec in CRATES:
        archive, source = crate_roots(args.cargo_home, spec)
        source_by_name[spec.name] = source
        component: dict[str, Any] = {
            "name": spec.name,
            "version": spec.version,
            "source": f"https://crates.io/crates/{spec.name}/{spec.version}",
            "registry_checksum": spec.checksum,
            "vcs_commit": spec.vcs_commit,
            "crate_archive_sha256": sha256_file(archive),
            "reachable_from": list(spec.reachable_from),
            "native_payloads": [
                {
                    "kind": "crates.io package archive (pins all vendored/build/include sources)",
                    "path": f"crate-archive:{spec.directory}.crate",
                    "sha256": spec.checksum,
                }
            ],
            "license_inputs": [],
        }
        for title, path, expected in spec.native_payloads:
            payload = source / path
            if sha256_file(payload) != expected:
                raise RuntimeError(f"{payload}: native payload SHA-256 mismatch")
            component["native_payloads"].append(
                {"kind": title, "path": f"crate:{spec.directory}/{path}", "sha256": expected}
            )
        for license_file in spec.licenses:
            raw = (source / license_file.path).read_bytes()
            add_license(
                component,
                title=license_file.title,
                origin=f"crate:{spec.directory}",
                path=license_file.path,
                raw=raw,
                expected_sha256=license_file.sha256,
            )
        components.append(component)

    by_name = {component["name"]: component for component in components}
    sqlite_source = source_by_name["libsqlite3-sys"] / "sqlite3/sqlite3.c"
    add_license(
        by_name["libsqlite3-sys"],
        title="SQLite public-domain dedication and blessing",
        origin="crate:libsqlite3-sys-0.37.0",
        path="sqlite3/sqlite3.c",
        raw=sqlite_source.read_bytes(),
        expected_sha256="9512509b1bccb7461f79bea8aad6280ae4699e925fa4804381b71f59e7efb0c5",
        extraction="SQLite amalgamation public-domain comment",
        extractor=sqlite_blessing,
    )
    pcre_source = source_by_name["pcre2-sys"] / "upstream/src/pcre2_compile.c"
    add_license(
        by_name["pcre2-sys"],
        title="PCRE2 10.46 redistribution terms",
        origin="crate:pcre2-sys-0.2.10",
        path="upstream/src/pcre2_compile.c",
        raw=pcre_source.read_bytes(),
        expected_sha256="54a8fb643749f0a7753f68d65f4e9e0dbc4728ed04461b580a0fba3b35d57b7d",
        extraction="C comment containing the PCRE2 redistribution terms",
        extractor=pcre2_license,
    )
    sljit_source = source_by_name["pcre2-sys"] / "upstream/deps/sljit/sljit_src/sljitLir.c"
    add_license(
        by_name["pcre2-sys"],
        title="SLJIT redistribution terms",
        origin="crate:pcre2-sys-0.2.10",
        path="upstream/deps/sljit/sljit_src/sljitLir.c",
        raw=sljit_source.read_bytes(),
        expected_sha256="0746669d1bf493f6f927a3318743c9a1488088b65826d0fd413008154a1b192f",
        extraction="leading C license comment",
        extractor=leading_c_comment,
    )

    tree_license = git_bytes(args.tree_sitter_source, TREE_SITTER_COMMIT, "LICENSE")
    add_license(
        by_name["tree-sitter"],
        title="Tree-sitter MIT license",
        origin=f"git:tree-sitter@{TREE_SITTER_COMMIT}",
        path="LICENSE",
        raw=tree_license,
        expected_sha256="5f9cf9fb6acb1972b35ae29119ce563bb60ec097656bc4b69b9bac2d04c7a147",
    )
    rusty_license = git_bytes(args.rusty_v8_source, RUSTY_V8_COMMIT, "LICENSE")
    add_license(
        by_name["v8"],
        title="rusty_v8 MIT license",
        origin=f"git:denoland/rusty_v8@{RUSTY_V8_COMMIT}",
        path="LICENSE",
        raw=rusty_license,
        expected_sha256="1c6356fb751d45f0c53093ebf8a7f5e580e802f51999178e19d60f3ec39e147d",
    )
    v8_build = git_bytes(args.v8_source, V8_COMMIT, "BUILD.gn")
    verify_sha(
        v8_build,
        "d2c3b33e4bc82792bee79431c15a70baf634dae230356307b72b4ae9b0952f47",
        "V8 BUILD.gn dependency graph",
    )
    by_name["v8"]["native_payloads"].append(
        {
            "kind": "V8 native dependency graph for Abseil, FP16, fast_float, "
            "Highway, PartitionAlloc, Dragonbox, ICU, and simdutf",
            "path": f"git:denoland/v8@{V8_COMMIT}/BUILD.gn",
            "sha256": "d2c3b33e4bc82792bee79431c15a70baf634dae230356307b72b4ae9b0952f47",
        }
    )
    for gitlink in RUSTY_V8_GITLINKS:
        source = submodule_sources.get(gitlink.path)
        for title, path, expected in gitlink.source_evidence:
            if source is None:
                raise RuntimeError(f"missing checked-out source for {gitlink.path}")
            raw = git_bytes(source, gitlink.commit, path)
            verify_sha(raw, expected, f"rusty_v8 gitlink {gitlink.path}/{path}")
            by_name["v8"]["native_payloads"].append(
                {
                    "kind": title,
                    "path": f"git:{gitlink.source_url}@{gitlink.commit}/{path}",
                    "sha256": expected,
                }
            )
        for license_file in gitlink.licenses:
            if source is None:
                raise RuntimeError(f"missing checked-out source for {gitlink.path}")
            add_license(
                by_name["v8"],
                title=license_file.title,
                origin=f"git:{gitlink.source_url}@{gitlink.commit}",
                path=license_file.path,
                raw=git_bytes(source, gitlink.commit, license_file.path),
                expected_sha256=license_file.sha256,
            )
    for path, expected in V8_LICENSES.items():
        add_license(
            by_name["v8"],
            title=f"V8 source notice: {path}",
            origin=f"git:denoland/v8@{V8_COMMIT}",
            path=path,
            raw=git_bytes(args.v8_source, V8_COMMIT, path),
            expected_sha256=expected,
        )
    icu_license = git_bytes(args.icu_source, ICU_COMMIT, "LICENSE")
    add_license(
        by_name["v8"],
        title="ICU full license and third-party notices",
        origin=f"git:chromium/deps/icu@{ICU_COMMIT}",
        path="LICENSE",
        raw=icu_license,
        expected_sha256="451167c55c0fa447cc2d5632714f5e3c567fe4f1e1badefab2c1333852198aca",
    )
    # deno_core_icudata has an exact MIT declaration but its repository/crate
    # omits a standalone license file. It is authored by the Deno authors and
    # uses the exact Deno MIT text shipped by its pinned rusty_v8 data source.
    add_license(
        by_name["deno_core_icudata"],
        title="Deno MIT license for deno_core_icudata wrapper",
        origin=f"git:denoland/rusty_v8@{RUSTY_V8_COMMIT}",
        path="LICENSE",
        raw=rusty_license,
        expected_sha256="1c6356fb751d45f0c53093ebf8a7f5e580e802f51999178e19d60f3ec39e147d",
    )
    add_license(
        by_name["deno_core_icudata"],
        title="ICU full license for embedded icudtl.dat",
        origin=f"git:chromium/deps/icu@{ICU_COMMIT}",
        path="LICENSE",
        raw=icu_license,
        expected_sha256="451167c55c0fa447cc2d5632714f5e3c567fe4f1e1badefab2c1333852198aca",
    )

    roots: list[dict[str, Any]] = [
        {
            "ecosystem": "codex",
            "name": "codex-cli",
            "manifest": "codex-rs/cli/Cargo.toml",
            "features": [],
        },
        {
            "ecosystem": "codex",
            "name": "code-mode-host",
            "package": "codex-code-mode-host",
            "manifest": "codex-rs/code-mode-host/Cargo.toml",
            "features": [],
        },
        {
            "ecosystem": "codex",
            "name": "windows-sandbox",
            "package": "codex-windows-sandbox",
            "manifest": "codex-rs/windows-sandbox-rs/Cargo.toml",
            "features": [],
        },
        {
            "ecosystem": "ripgrep",
            "name": "ripgrep",
            "manifest": "Cargo.toml",
            "features": ["pcre2"],
        },
    ]
    for root_entry in roots:
        root_entry["reachable_native_components"] = sorted(
            component["name"]
            for component in components
            if root_entry["name"] in component["reachable_from"]
        )

    notice = render_notice(components)
    notice_bytes = notice.encode()
    input_count = sum(len(component["license_inputs"]) for component in components)
    manifest: dict[str, Any] = {
        "schema_version": "codex-native-notices.v1",
        "target": TARGET,
        "roots": roots,
        "source_locks": {
            "codex": {
                "version": CODEX_VERSION,
                "tag": CODEX_TAG,
                "commit": CODEX_COMMIT,
                "cargo_lock_sha256": CODEX_LOCK_SHA256,
            },
            "ripgrep": {
                "version": RIPGREP_VERSION,
                "tag": RIPGREP_TAG,
                "commit": RIPGREP_COMMIT,
                "cargo_lock_sha256": RIPGREP_LOCK_SHA256,
            },
            "rusty_v8": {
                "version": RUSTY_V8_VERSION,
                "tag": RUSTY_V8_TAG,
                "commit": RUSTY_V8_COMMIT,
                "gitmodules_sha256": "64abd45978444b338c5d3816cfbec508f68d6877c0c26ece649b9593d2665054",
                "gitlinks": [
                    {
                        "path": gitlink.path,
                        "commit": gitlink.commit,
                        "source_url": gitlink.source_url,
                        "disposition": gitlink.disposition,
                    }
                    for gitlink in RUSTY_V8_GITLINKS
                ],
            },
            "v8": {"version": "15.0.245.2", "commit": V8_COMMIT},
            "icu": {
                "commit": ICU_COMMIT,
                "license_sha256": "451167c55c0fa447cc2d5632714f5e3c567fe4f1e1badefab2c1333852198aca",
            },
            "tree_sitter": {"version": "0.25.10", "commit": TREE_SITTER_COMMIT},
        },
        "components": components,
        "artifacts": [
            {
                "name": RUSTY_V8_ARCHIVE_NAME,
                "kind": "OpenAI Codex rusty_v8 ptrcomp+sandbox static library (gzip)",
                "source_url": RUSTY_V8_ARCHIVE_URL,
                "sha256": RUSTY_V8_ARCHIVE_SHA256,
                "expanded_sha256": RUSTY_V8_EXPANDED_SHA256,
                "reviewed_component_inventory": archive_inventory,
            },
            {
                "name": RUSTY_V8_BINDING_NAME,
                "kind": "OpenAI Codex rusty_v8 ptrcomp+sandbox Rust binding",
                "source_url": RUSTY_V8_BINDING_URL,
                "sha256": RUSTY_V8_BINDING_SHA256,
            },
        ],
        "notice_bundle": {
            "path": "NATIVE_THIRD_PARTY_NOTICES.md",
            "sha256": sha256_bytes(notice_bytes),
            "license_input_count": input_count,
        },
    }
    for component in components:
        for item in component["license_inputs"]:
            del item["_notice"]
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()
    return manifest_bytes, notice_bytes


def render_notice(components: list[dict[str, Any]]) -> str:
    lines = [
        "# Codex Windows native and embedded-data notices",
        "",
        "Generated from exact, SHA-256-verified inputs by "
        "`extras/desktop/generate-codex-native-notices.py`.",
        "Target: `x86_64-pc-windows-msvc`. Dependency roots: `codex-cli`, "
        "`codex-code-mode-host`, `codex-windows-sandbox`, and ripgrep "
        "`15.2.0` with feature `pcre2`.",
        "",
        "The Cargo reports cover Rust packages. This companion bundle preserves "
        "the notices for native sources, the prebuilt rusty_v8 archive, and "
        "embedded ICU data that Cargo package metadata alone cannot prove.",
        "",
    ]
    emitted: dict[str, str] = {}
    for component in components:
        lines.extend(
            [
                f"## {component['name']} {component['version']}",
                "",
                "Reachable from: "
                + ", ".join(f"`{root}`" for root in component["reachable_from"])
                + ".",
                "",
            ]
        )
        for license_input in component["license_inputs"]:
            title = license_input["title"]
            lines.extend(
                [
                    f"### {title}",
                    "",
                    f"Source: `{license_input['origin']}/{license_input['path']}`  ",
                    f"Source SHA-256: `{license_input['sha256']}`  ",
                    f"Included text SHA-256: `{license_input['notice_sha256']}`",
                    "",
                ]
            )
            notice_hash = license_input["notice_sha256"]
            if notice_hash in emitted:
                lines.extend(
                    [
                        f"Identical verified text is reproduced under “{emitted[notice_hash]}”.",
                        "",
                    ]
                )
                continue
            emitted[notice_hash] = title
            lines.extend(["````text", license_input["_notice"].rstrip(), "````", ""])
    rendered = "\n".join(lines).rstrip() + "\n"
    forbidden = re.compile(r"(?i)\b(?:todo|tbd|placeholder|unknown copyright)\b")
    match = forbidden.search(rendered)
    if match:
        raise RuntimeError(f"notice contains forbidden placeholder marker: {match.group(0)}")
    return rendered


def write_or_check(path: Path, expected: bytes, write: bool) -> None:
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(expected)
        os.replace(temporary, path)
        return
    if not path.is_file():
        raise RuntimeError(f"missing generated artifact: {path}")
    actual = path.read_bytes()
    if actual != expected:
        raise RuntimeError(f"generated artifact is stale: {path}; rerun with --write")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-source", required=True, type=Path)
    parser.add_argument("--ripgrep-source", required=True, type=Path)
    parser.add_argument("--rusty-v8-source", required=True, type=Path)
    parser.add_argument("--v8-source", required=True, type=Path)
    parser.add_argument("--icu-source", required=True, type=Path)
    parser.add_argument("--tree-sitter-source", required=True, type=Path)
    parser.add_argument(
        "--cargo-home",
        type=Path,
        default=Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")),
    )
    parser.add_argument("--rusty-v8-archive", required=True, type=Path)
    parser.add_argument("--rusty-v8-binding", required=True, type=Path)
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        manifest, notice = build_outputs(args)
        write_or_check(NOTICE_PATH, notice, args.write)
        write_or_check(MANIFEST_PATH, manifest, args.write)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    action = "wrote" if args.write else "verified"
    print(f"{action} {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    print(f"{action} {NOTICE_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

