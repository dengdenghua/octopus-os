from __future__ import annotations

import os
import re
import stat

import pytest

from appliance.docker_credential import ensure_credential

pytestmark = pytest.mark.skipif(os.name != "posix", reason="root-owned POSIX credential contract")


def test_credential_is_random_private_and_idempotent(tmp_path, monkeypatch) -> None:
    os.chmod(tmp_path, 0o700)
    target = tmp_path / "docker-proxy-token"
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    assert ensure_credential(target) is True
    first = target.read_text(encoding="ascii")
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    assert ensure_credential(target) is False
    assert target.read_text(encoding="ascii") == first


def test_credential_rejects_existing_permissive_file(tmp_path, monkeypatch) -> None:
    os.chmod(tmp_path, 0o700)
    target = tmp_path / "docker-proxy-token"
    target.write_text("a" * 64, encoding="ascii")
    os.chmod(target, 0o644)
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    with pytest.raises(RuntimeError, match="root-owned mode 0600"):
        ensure_credential(target)


def test_credential_rejects_non_root_caller(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    with pytest.raises(RuntimeError, match="requires root"):
        ensure_credential(tmp_path / "docker-proxy-token")


def test_credential_rejects_non_private_parent(tmp_path, monkeypatch) -> None:
    os.chmod(tmp_path, 0o755)
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    with pytest.raises(RuntimeError, match="root-owned mode 0700"):
        ensure_credential(tmp_path / "docker-proxy-token")
