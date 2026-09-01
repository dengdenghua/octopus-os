"""echo-runtime · registry 客户端(httpx + pydantic,**永不 import 产品 runtime**)。

实现 capability-plane.md §B 的「拉取 → 验签」半边:列目录 / 下载单资产 / sha256 校验。
读/解析层无任何 runtime 依赖,可被任何产品 vendor。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel

DEFAULT_BASE = "https://os.echo-age.com"
_API = "/api/v1/registry/assets"
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_ASSET_ID_RE = re.compile(r"^[a-z][a-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

# Network response limits are enforced while bytes are streamed, before JSON
# parsing or bundle buffering.  Keep these independent: the catalog may contain
# many small envelopes, a single prompt asset should stay compact, while a
# checked full bundle legitimately needs more room for references/scripts.
DEFAULT_JSON_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_SKILL_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_BUNDLE_RESPONSE_MAX_BYTES = 50 * 1024 * 1024


class RegistryResponseTooLarge(ValueError):
    """The registry response crossed its configured streaming byte limit."""


def _positive_limit(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _json_object(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid registry JSON for {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"registry JSON for {label} must be an object")
    return payload


def _sha256_expected(value: str | None, *, label: str) -> str | None:
    if not value:
        return None
    m = _SHA256_RE.fullmatch(value.strip())
    if not m:
        raise ValueError(f"invalid sha256 checksum for {label}: {value!r}")
    return m.group(1).lower()


def _safe_asset_id(asset_id: str) -> str:
    if not _ASSET_ID_RE.fullmatch(asset_id):
        raise ValueError(f"unsafe registry asset id: {asset_id!r}")
    return asset_id


def safe_registry_asset_id(value: str) -> str:
    return _safe_asset_id(value)


def safe_registry_skill_slug(value: str) -> str:
    asset_id = value if "/" in value else f"skill/{value}"
    safe = _safe_asset_id(asset_id)
    return safe.split("/", 1)[1]


class AssetContent(BaseModel):
    ref: str | None = None
    checksum: str | None = None  # "sha256:<hex>"


class BundleRef(BaseModel):
    """full-bundle:技能整目录 tar.gz(带 scripts/refs/requirements)。None = body-only 足够。"""

    ref: str | None = None
    checksum: str | None = None  # "sha256:<hex>"
    size: int | None = None


class RegistryAsset(BaseModel):
    """registry 信封(轻量元数据;list 不含 body)。"""

    id: str = ""  # "skill/<slug>"
    type: str = ""
    kind: str = ""  # data | code(安全分水岭:data 可落地,code 只作广告)
    version: str = ""
    name: str = ""
    description: str = ""
    category: str | None = None
    tags: list[str] | None = None
    mode: str | None = None  # inject | tool
    platforms: list[str] | None = None
    deps: list[str] | None = None
    # Registry publishers may provide a small text/emoji icon.  Keep the
    # metadata instead of silently dropping it during Pydantic validation;
    # image assets are resolved from trusted local plugin bundles by the
    # consumer route.
    icon: str | None = None
    logo: str | None = None
    icon_url: str | None = None
    content: AssetContent | None = None
    bundle: BundleRef | None = None  # 有则该技能是整目录分发(full-bundle)

    @property
    def slug(self) -> str:
        return self.id.rsplit("/", 1)[-1]


class AssetPayload(RegistryAsset):
    """download 返回:信封 + 正文 body。"""

    body: str = ""


class RegistryClient:
    """瘦客户端:列目录 + 下载(内容寻址、验签)。无状态、无 runtime 依赖。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE,
        timeout: float = 30.0,
        *,
        max_json_bytes: int = DEFAULT_JSON_RESPONSE_MAX_BYTES,
        max_skill_bytes: int = DEFAULT_SKILL_RESPONSE_MAX_BYTES,
        max_bundle_bytes: int = DEFAULT_BUNDLE_RESPONSE_MAX_BYTES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_json_bytes = _positive_limit(max_json_bytes, name="max_json_bytes")
        self._max_skill_bytes = _positive_limit(max_skill_bytes, name="max_skill_bytes")
        self._max_bundle_bytes = _positive_limit(max_bundle_bytes, name="max_bundle_bytes")
        # A transport seam keeps byte-limit tests fully deterministic without
        # making the standalone package import product/runtime internals.
        self._transport = transport

    def _stream_get(
        self,
        url: str,
        *,
        max_bytes: int,
        label: str,
        params: Mapping[str, str] | None = None,
    ) -> tuple[bytes, httpx.Headers]:
        """GET and buffer at most ``max_bytes`` decoded response bytes."""

        with (
            httpx.Client(timeout=self._timeout, transport=self._transport) as client,
            client.stream("GET", url, params=params) as response,
        ):
            response.raise_for_status()
            raw_length = response.headers.get("content-length")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError as exc:
                    raise ValueError(
                        f"invalid Content-Length for registry {label}: {raw_length!r}"
                    ) from exc
                if content_length < 0:
                    raise ValueError(f"invalid Content-Length for registry {label}: {raw_length!r}")
                if content_length > max_bytes:
                    raise RegistryResponseTooLarge(
                        f"registry {label} exceeds {max_bytes} byte limit "
                        f"(Content-Length={content_length})"
                    )

            data = bytearray()
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                if len(data) + len(chunk) > max_bytes:
                    raise RegistryResponseTooLarge(
                        f"registry {label} exceeds {max_bytes} byte limit"
                    )
                data.extend(chunk)
            return bytes(data), response.headers

    def list_assets(self, type_: str | None = None) -> list[RegistryAsset]:
        params = {"type": type_} if type_ else {}
        raw, _headers = self._stream_get(
            self.base + _API,
            params=params,
            max_bytes=self._max_json_bytes,
            label="catalog response",
        )
        data = _json_object(raw, label="catalog response").get("data", []) or []
        if not isinstance(data, list):
            raise ValueError("registry catalog data must be a list")
        return [RegistryAsset.model_validate(a) for a in data]

    def list_skills(self) -> list[RegistryAsset]:
        return self.list_assets(type_="skill")

    def fetch(self, asset_id: str) -> AssetPayload:
        """下载单资产(信封 + body)并**校验 sha256 checksum**。失败抛异常。"""
        asset_id = _safe_asset_id(asset_id)
        raw, _headers = self._stream_get(
            f"{self.base}{_API}/{asset_id}/download",
            max_bytes=self._max_skill_bytes,
            label=f"asset {asset_id}",
        )
        d = _json_object(raw, label=f"asset {asset_id}").get("data") or {}
        payload = AssetPayload.model_validate(d)
        self._verify(payload)
        return payload

    def fetch_bundle(self, asset_id: str, *, expected_size: int | None = None) -> bytes:
        """下载技能 **full-bundle**(整目录 tar.gz)并校验 sha256(X-Checksum-Sha256 头)。
        无 bundle → httpx 抛 404。"""
        asset_id = _safe_asset_id(asset_id)
        declared_size: int | None = None
        if expected_size is not None:
            if isinstance(expected_size, bool):
                raise ValueError(f"invalid declared bundle size for {asset_id}: {expected_size!r}")
            try:
                declared_size = int(expected_size)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid declared bundle size for {asset_id}: {expected_size!r}"
                ) from exc
            if declared_size < 0:
                raise ValueError(f"invalid declared bundle size for {asset_id}: {declared_size}")
            if declared_size > self._max_bundle_bytes:
                raise RegistryResponseTooLarge(
                    f"declared bundle size for {asset_id} exceeds "
                    f"{self._max_bundle_bytes} byte limit ({declared_size})"
                )

        data, headers = self._stream_get(
            f"{self.base}{_API}/{asset_id}/bundle",
            # A publisher-declared size is also a tighter streaming cap: do
            # not buffer up to the global bundle limit merely to report that a
            # supposedly tiny bundle was larger than advertised.
            max_bytes=(
                min(self._max_bundle_bytes, declared_size)
                if declared_size is not None
                else self._max_bundle_bytes
            ),
            label=f"bundle {asset_id}",
        )
        if declared_size is not None and len(data) != declared_size:
            raise ValueError(
                f"registry bundle size mismatch for {asset_id}: "
                f"declared {declared_size}, got {len(data)}"
            )
        expected = _sha256_expected(headers.get("X-Checksum-Sha256"), label=asset_id)
        if expected:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise ValueError(
                    f"bundle checksum mismatch for {asset_id}: expected {expected} got {actual}"
                )
        return data

    @staticmethod
    def _verify(p: AssetPayload) -> None:
        expected = _sha256_expected(p.content.checksum if p.content else None, label=p.id)
        if expected:
            actual = hashlib.sha256(p.body.encode("utf-8")).hexdigest()
            if actual != expected:
                raise ValueError(f"checksum mismatch for {p.id}: expected {expected} got {actual}")
