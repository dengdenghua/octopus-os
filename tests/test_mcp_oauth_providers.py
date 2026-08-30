"""服务商直连 OAuth App(BYO OAuth):非 PKCE 授权 URL、client_secret 交换、
凭据加密存储、capability 探测标注。

Hermetic —— ECHO_HOME 指向 tmp_path,token 端点 mock,不碰网络。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.adapters.mcp_client import oauth, oauth_providers
from runtime.platform.connectors import oauth_support


def _reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    oauth.reset_oauth_store_for_tests()


def test_build_authorize_url_omits_pkce_when_no_challenge() -> None:
    url = oauth.build_authorize_url(
        authorize_url="https://github.com/login/oauth/authorize",
        client_id="Iv23liX",
        redirect_uri="http://cb",
        scopes=["repo", "user"],
        state="ST",
        code_challenge=None,
    )
    assert "response_type=code" in url
    assert "client_id=Iv23liX" in url
    assert "scope=repo+user" in url
    assert "code_challenge" not in url


def test_exchange_code_uses_client_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

        def read(self) -> bytes:
            return b'{"access_token": "gho_x", "token_type": "bearer"}'

    def _open(req: object, timeout: float | None = None) -> _Resp:  # noqa: ARG001
        calls.append(getattr(req, "data", None))
        return _Resp()

    monkeypatch.setattr(oauth.urllib_request, "urlopen", _open)
    out = oauth.exchange_code(
        token_url="https://github.com/login/oauth/access_token",
        code="c0de",
        client_id="Iv23liX",
        client_secret="s3cret",
        redirect_uri="http://cb",
    )
    assert out["access_token"] == "gho_x"
    body = calls[0].decode("utf-8") if isinstance(calls[0], bytes) else str(calls[0])
    assert "client_secret=s3cret" in body
    assert "code_verifier" not in body


def test_app_client_store_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch, tmp_path)
    store = oauth.get_oauth_store()
    assert store.get_app_client("github") is None
    store.save_app_client("github", "Iv23liX", "s3cret")
    app = store.get_app_client("github")
    assert app == {"client_id": "Iv23liX", "client_secret": "s3cret"}
    # 加密文件存在
    assert (tmp_path / "mcp_oauth.json").exists()
    assert store.forget_app_client("github") is True
    assert store.get_app_client("github") is None


def test_start_pending_without_pkce_keeps_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, tmp_path)
    store = oauth.get_oauth_store()
    state = store.start_pending(
        server="github",
        code_verifier="",
        redirect_uri="http://cb",
        token_url="https://github.com/login/oauth/access_token",
        client_id="Iv23liX",
        client_secret="s3cret",
        use_pkce=False,
    )
    pend = store.pop_pending(state)
    assert pend is not None
    assert pend.use_pkce is False
    assert pend.client_secret == "s3cret"


def test_provider_for_capability_github() -> None:
    prov = oauth_providers.get_provider_for_capability(
        {"id": "github", "source": "github", "provider_id": ""},
    )
    assert prov is not None and prov.id == "github"
    assert prov.authorize_url == "https://github.com/login/oauth/authorize"


def test_provider_for_capability_unknown_falls_through() -> None:
    assert (
        oauth_providers.get_provider_for_capability(
            {"id": "tdx-connector", "source": "tdx-connector"}
        )
        is None
    )


def test_annotate_marks_github_supported() -> None:
    item = oauth_support.annotate(
        {
            "id": "github",
            "source": "connector",
            "mcp_servers": [{"name": "github", "url": "https://api.githubcopilot.com/mcp/"}],
        },
    )
    assert item["oauth_supported"] is True
    assert item["oauth_provider"] == "github"

