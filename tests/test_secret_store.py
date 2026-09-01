"""Tests for the OS-keychain backed secret store.

The real keychain is never touched: the suite-wide ``_disable_os_keychain``
fixture pins ``ECHO_KEYCHAIN=off``, and the tests that need a backend
fake the per-platform helpers instead of shelling out.
"""

from __future__ import annotations

import pytest

from runtime.platform.credentials import secret_store as ss


def test_keychain_disabled_by_default_in_tests() -> None:
    """The autouse fixture must actually keep the suite off the keychain."""
    assert ss.keychain_backend() is None


def test_get_secret_prefers_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "from-env")
    # Even with a working backend, the env var wins so vault-injected
    # deployments keep control of their key material.
    monkeypatch.setattr(ss, "keychain_backend", lambda: "macos-keychain")
    monkeypatch.setattr(ss, "_macos_get", lambda name: "from-keychain")

    assert ss.get_secret("whatever", env_var="MY_KEY") == "from-env"


def test_get_secret_falls_back_to_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(ss, "keychain_backend", lambda: "macos-keychain")
    monkeypatch.setattr(ss, "_macos_get", lambda name: "from-keychain")

    assert ss.get_secret("k", env_var="MY_KEY") == "from-keychain"


def test_get_secret_returns_none_without_backend_or_env() -> None:
    assert ss.get_secret("absent", env_var="DEFINITELY_UNSET_KEY") is None


def test_blank_env_var_is_not_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # An exported-but-empty var is a common shell accident; treating it as a
    # key would hand Fernet an empty string and break token storage.
    monkeypatch.setenv("MY_KEY", "   ")
    assert ss.get_secret("k", env_var="MY_KEY") is None


def test_set_secret_without_backend_reports_failure() -> None:
    assert ss.set_secret("k", "v") is False


def test_set_secret_rejects_empty_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "keychain_backend", lambda: "macos-keychain")
    monkeypatch.setattr(ss, "_macos_set", lambda name, value: True)
    assert ss.set_secret("k", "") is False


def test_keychain_off_switch_beats_available_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ss, "_macos_available", lambda: True)
    for value in ("off", "0", "false", "disabled"):
        monkeypatch.setenv("ECHO_KEYCHAIN", value)
        assert ss.keychain_backend() is None


def test_keychain_lookup_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The keychain is consulted once per name, not once per call."""
    calls: list[str] = []
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(ss, "keychain_backend", lambda: "macos-keychain")
    monkeypatch.setattr(ss, "_macos_get", lambda name: (calls.append(name), "from-keychain")[1])

    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") == "from-keychain"
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") == "from-keychain"
    assert len(calls) == 1, "keychain helper should not be forked twice"

    ss.reset_key_cache_for_tests()
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") == "from-keychain"
    assert len(calls) == 2


def test_env_var_is_reread_and_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit key stays rotatable — caching it would pin a stale value."""
    monkeypatch.setattr(ss, "keychain_backend", lambda: None)
    monkeypatch.setenv("MY_KEY", "first")
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") == "first"
    monkeypatch.setenv("MY_KEY", "second")
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") == "second"


def test_negative_keychain_result_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A readable-but-unwritable keychain must not retry a doomed mint."""
    pytest.importorskip("cryptography")
    mint_attempts: list[str] = []
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(ss, "keychain_backend", lambda: "macos-keychain")
    monkeypatch.setattr(ss, "_macos_get", lambda name: None)
    monkeypatch.setattr(
        ss, "_macos_set", lambda name, value: (mint_attempts.append(name), False)[1]
    )

    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") is None
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") is None
    assert len(mint_attempts) == 1


def test_get_or_create_mints_and_persists_key(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    stored: dict[str, str] = {}
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(ss, "keychain_backend", lambda: "macos-keychain")
    monkeypatch.setattr(ss, "_macos_get", lambda name: stored.get(name))
    monkeypatch.setattr(
        ss, "_macos_set", lambda name, value: stored.setdefault(name, value) or True
    )

    first = ss.get_or_create_fernet_key("tok", env_var="MY_KEY")
    assert first is not None
    Fernet(first.encode())  # a usable Fernet key, not arbitrary bytes

    # Stability is the whole point: a second call must return the same key or
    # every restart would orphan the previously encrypted file.
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") == first


def test_get_or_create_returns_none_without_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No backend and no env var → callers must keep their plaintext path
    # rather than lose the ability to store credentials at all.
    monkeypatch.delenv("MY_KEY", raising=False)
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") is None


def test_get_or_create_does_not_mint_when_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(ss, "keychain_backend", lambda: "macos-keychain")
    monkeypatch.setattr(ss, "_macos_get", lambda name: None)
    monkeypatch.setattr(ss, "_macos_set", lambda name, value: False)

    # A key we minted but could not persist would encrypt data we can never
    # read again after restart, so it must not be returned. This is the real
    # behavior on a host whose keychain rejects writes (observed: macOS
    # returning UNIX[Operation not permitted] under a restricted launch).
    assert ss.get_or_create_fernet_key("tok", env_var="MY_KEY") is None


def test_require_secret_raises_when_missing() -> None:
    with pytest.raises(ss.SecretStoreUnavailable):
        ss.require_secret("nope", env_var="DEFINITELY_UNSET_KEY")


def test_run_helper_never_raises_on_missing_binary() -> None:
    code, out = ss._run(["definitely-not-a-real-binary-xyz"])
    assert code == 1
    assert out == ""

