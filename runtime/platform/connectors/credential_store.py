"""加密凭据库(AES-256-GCM)。

对齐 WorkBuddy connector-states.v3.json 的凭据加密思路:
  - 主密钥随机生成,以 0600 权限存本机(~/.echo/connectors/master.key)
  - 每个 secret 用随机 nonce + AES-256-GCM 加密,密文+nonce 存 JSON
  - 敏感值绝不明文落盘;get 时才解密

用法::

    store = CredentialStore()
    store.set_secret("westock-mcp", "access_token", "xxx")
    token = store.get_secret("westock-mcp", "access_token")
    store.delete_secret("westock-mcp", "access_token")
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from runtime.platform.io import (
    JsonMutation,
    TransactionalFileError,
    create_file_exclusive,
    mutate_json_file,
    path_transaction,
    read_json_file,
)
from runtime.safety.auth.scope import tenant_scoped_path

CONNECTOR_ROOT = Path(os.path.expanduser("~/.echo/connectors"))
MASTER_KEY_FILE = CONNECTOR_ROOT / "master.key"
CREDENTIALS_FILE = CONNECTOR_ROOT / "credentials.v1.json"
_KEY_LEN = 32  # AES-256


class CredentialStore:
    """AES-256-GCM 加密的本地凭据库(按 connector_id 分组)。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        master_key_file: str | Path | None = None,
        credentials_file: str | Path | None = None,
    ) -> None:
        self._root = Path(CONNECTOR_ROOT if root is None else root).expanduser()
        self._key_file = Path(
            self._root / "master.key" if master_key_file is None else master_key_file
        ).expanduser()
        self._base_cred_file = Path(
            self._root / "credentials.v1.json" if credentials_file is None else credentials_file
        ).expanduser()
        self._key = self._load_or_create_key()
        if self._cred_file.exists():
            self._read_all()

    @property
    def _cred_file(self) -> Path:
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        return tenant_scoped_path(self._base_cred_file, current_capability_scope())

    @staticmethod
    def _ensure_private_permissions(path: Path) -> None:
        """Ensure an existing secret-bearing file is accessible only by its owner."""

        try:
            path.chmod(0o600)
        except OSError as exc:
            raise RuntimeError(f"cannot secure connector credential file: {path}") from exc

    # ── 主密钥 ────────────────────────────────────────────────
    def _load_or_create_key(self) -> bytes:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path_transaction(self._key_file):
            if self._key_file.exists():
                return self._decode_key_file()

            # A credential file without its original key is not recoverable.
            # Generating a replacement would make that loss look healthy.
            if self._cred_file.exists():
                raise RuntimeError("connector master key is missing for existing credentials")

            key = secrets.token_bytes(_KEY_LEN)
            if create_file_exclusive(self._key_file, base64.b64encode(key), mode=0o600):
                return key
            # A non-cooperating creator can still win O_EXCL between the probe and
            # create; re-read that canonical key instead of retaining our loser.
            return self._decode_key_file()

    def _decode_key_file(self) -> bytes:
        encoded_key = self._key_file.read_bytes()
        self._ensure_private_permissions(self._key_file)
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("connector master key is not valid base64") from exc
        if len(key) != _KEY_LEN:
            raise RuntimeError(
                f"connector master key must decode to {_KEY_LEN} bytes; got {len(key)}"
            )
        return key

    # ── 加密原语 ──────────────────────────────────────────────
    @staticmethod
    def _encrypt(key: bytes, plaintext: str) -> dict[str, str]:
        nonce = secrets.token_bytes(12)
        aesgcm = AESGCM(key)
        ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return {
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ct).decode(),
        }

    @staticmethod
    def _decrypt(key: bytes, blob: dict[str, str]) -> str:
        nonce = base64.b64decode(blob["nonce"])
        ct = base64.b64decode(blob["ciphertext"])
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")

    # ── 读写 ──────────────────────────────────────────────────
    @staticmethod
    def _empty_credentials() -> dict[str, Any]:
        return {
            "version": 1,
            "connectors": {},
            "auth_generations": {},
            "refresh_leases": {},
        }

    @staticmethod
    def _validate_credentials(data: Any) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("connectors"), dict):
            raise RuntimeError("connector credential file has an invalid structure")
        generations = data.get("auth_generations", {})
        if not isinstance(generations, dict) or any(
            not isinstance(connector_id, str) or not isinstance(generation, int) or generation < 0
            for connector_id, generation in generations.items()
        ):
            raise RuntimeError("connector credential file has invalid auth generations")
        leases = data.get("refresh_leases", {})
        if not isinstance(leases, dict) or any(
            not isinstance(connector_id, str)
            or not isinstance(lease, dict)
            or not isinstance(lease.get("generation"), int)
            or not isinstance(lease.get("worker_nonce"), str)
            or not lease.get("worker_nonce")
            or not isinstance(lease.get("owner_pid"), int)
            or not isinstance(lease.get("child_pid"), int)
            or not isinstance(lease.get("started_at"), int | float)
            for connector_id, lease in leases.items()
        ):
            raise RuntimeError("connector credential file has invalid refresh leases")

    @staticmethod
    def _auth_generation(data: dict[str, Any], connector_id: str) -> int:
        return int(data.get("auth_generations", {}).get(connector_id, 0))

    @classmethod
    def _advance_auth_generation(cls, data: dict[str, Any], connector_id: str) -> int:
        generation = cls._auth_generation(data, connector_id) + 1
        data.setdefault("auth_generations", {})[connector_id] = generation
        return generation

    def _read_all(self) -> dict[str, Any]:
        try:
            return read_json_file(
                self._cred_file,
                default_factory=self._empty_credentials,
                validate=self._validate_credentials,
                mode=0o600,
            )
        except TransactionalFileError as exc:
            raise RuntimeError("connector credential file is unreadable or corrupt") from exc

    def _mutate_all(
        self,
        mutate: Callable[[dict[str, Any]], JsonMutation[Any]],
    ) -> Any:
        try:
            return mutate_json_file(
                self._cred_file,
                default_factory=self._empty_credentials,
                validate=self._validate_credentials,
                mutate=mutate,
                mode=0o600,
            )
        except TransactionalFileError as exc:
            raise RuntimeError("connector credential file is unreadable or corrupt") from exc

    @contextmanager
    def connector_lifecycle(self, connector_id: str) -> Iterator[None]:
        """Serialize one connector's auth transition across workers/processes."""

        digest = hashlib.sha256(connector_id.encode("utf-8")).hexdigest()
        target = self._cred_file.parent / ".auth-lifecycle" / digest
        with path_transaction(target):
            yield

    @property
    def storage_identity(self) -> str:
        """Stable identity used by the in-process refresh supervisor."""

        return os.path.normcase(str(self._cred_file.resolve(strict=False)))

    def auth_generation(self, connector_id: str) -> int:
        """Return the durable credential generation for one connector."""

        return self._auth_generation(self._read_all(), connector_id)

    def advance_auth_generation(self, connector_id: str) -> int:
        """Fence any in-flight writer without changing the current secrets."""

        def advance(data: dict[str, Any]) -> JsonMutation[int]:
            return JsonMutation(self._advance_auth_generation(data, connector_id))

        return int(self._mutate_all(advance))

    def begin_auth_generation(self, connector_id: str, values: dict[str, str]) -> int:
        """Publish supplied credentials and a new generation in one transaction."""

        encrypted = {key: self._encrypt(self._key, value) for key, value in values.items() if value}

        def begin(data: dict[str, Any]) -> JsonMutation[int]:
            generation = self._advance_auth_generation(data, connector_id)
            data["connectors"].setdefault(connector_id, {}).update(encrypted)
            return JsonMutation(generation)

        return int(self._mutate_all(begin))

    def set_secret_if_generation(
        self,
        connector_id: str,
        key: str,
        value: str,
        *,
        expected_generation: int,
    ) -> bool:
        """Write a refreshed secret only while its auth generation is canonical."""

        encrypted = self._encrypt(self._key, value)

        def update(data: dict[str, Any]) -> JsonMutation[bool]:
            if self._auth_generation(data, connector_id) != expected_generation:
                return JsonMutation(False, changed=False)
            data["connectors"].setdefault(connector_id, {})[key] = encrypted
            return JsonMutation(True)

        return bool(self._mutate_all(update))

    def refresh_lease(self, connector_id: str) -> dict[str, Any] | None:
        """Return a copy of the durable refresh-child lease, when present."""

        lease = self._read_all().get("refresh_leases", {}).get(connector_id)
        return dict(lease) if isinstance(lease, dict) else None

    def register_refresh_lease(
        self,
        connector_id: str,
        *,
        expected_generation: int,
        worker_nonce: str,
        child_pid: int,
        started_at: float,
        owner_pid: int | None = None,
    ) -> bool:
        """Publish a refresh child only while its generation is canonical.

        Callers spawn and register while holding :meth:`connector_lifecycle`,
        closing the otherwise dangerous spawn-before-registration window.
        """

        lease = {
            "generation": expected_generation,
            "worker_nonce": worker_nonce,
            "owner_pid": os.getpid() if owner_pid is None else owner_pid,
            "child_pid": child_pid,
            "started_at": started_at,
        }

        def register(data: dict[str, Any]) -> JsonMutation[bool]:
            if self._auth_generation(data, connector_id) != expected_generation:
                return JsonMutation(False, changed=False)
            leases = data.setdefault("refresh_leases", {})
            if connector_id in leases:
                return JsonMutation(False, changed=False)
            leases[connector_id] = lease
            return JsonMutation(True)

        return bool(self._mutate_all(register))

    def clear_refresh_lease(self, connector_id: str, *, worker_nonce: str) -> bool:
        """Clear only the refresh lease owned by ``worker_nonce``."""

        def clear(data: dict[str, Any]) -> JsonMutation[bool]:
            leases = data.get("refresh_leases", {})
            current = leases.get(connector_id)
            if not isinstance(current, dict) or current.get("worker_nonce") != worker_nonce:
                return JsonMutation(False, changed=False)
            del leases[connector_id]
            return JsonMutation(True)

        return bool(self._mutate_all(clear))

    # ── 对外 API ──────────────────────────────────────────────
    def set_secret(self, connector_id: str, key: str, value: str) -> None:
        encrypted = self._encrypt(self._key, value)

        def update(data: dict[str, Any]) -> JsonMutation[None]:
            conn = data["connectors"].setdefault(connector_id, {})
            conn[key] = encrypted
            return JsonMutation(None)

        self._mutate_all(update)

    def get_secret(self, connector_id: str, key: str) -> str | None:
        data = self._read_all()
        blob = data.get("connectors", {}).get(connector_id, {}).get(key)
        if not blob:
            return None
        try:
            return self._decrypt(self._key, blob)
        except Exception:  # noqa: BLE001 — key mismatch/corrupt; treat as missing
            return None

    def delete_secret(self, connector_id: str, key: str) -> bool:
        def delete(data: dict[str, Any]) -> JsonMutation[bool]:
            conn = data.get("connectors", {}).get(connector_id, {})
            if key in conn:
                del conn[key]
                self._advance_auth_generation(data, connector_id)
                return JsonMutation(True)
            return JsonMutation(False, changed=False)

        return bool(self._mutate_all(delete))

    def list_secrets(self, connector_id: str) -> list[str]:
        data = self._read_all()
        return list(data.get("connectors", {}).get(connector_id, {}).keys())

    def clear_connector(self, connector_id: str) -> bool:
        def clear(data: dict[str, Any]) -> JsonMutation[bool]:
            self._advance_auth_generation(data, connector_id)
            if connector_id in data.get("connectors", {}):
                del data["connectors"][connector_id]
                return JsonMutation(True)
            return JsonMutation(False)

        return bool(self._mutate_all(clear))

    def has_credentials(self, connector_id: str) -> bool:
        data = self._read_all()
        return bool(data.get("connectors", {}).get(connector_id, {}))


__all__ = ["CredentialStore", "CONNECTOR_ROOT"]
