"""Deployment contract for the loopback-only standalone QuoteHub service."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "quote-hub"


def _read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_systemd_unit_is_loopback_only_hardened_and_release_scoped() -> None:
    unit = _read("quote-hub.service")

    assert "User=echo-quote" in unit
    assert "Group=echo-quote" in unit
    assert "WorkingDirectory=/opt/echo-cloud/quote-hub/current" in unit
    assert "EnvironmentFile=/etc/echo/quote-hub.env" in unit
    assert "/opt/echo-cloud/quote-hub/current/.venv/bin/python -m uvicorn" in unit
    assert "paper_trading.quote_service:app" in unit
    assert "--host 127.0.0.1" in unit
    assert "--port 8091" in unit
    assert "--host 0.0.0.0" not in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "ReadWritePaths=/var/lib/echo/quote-hub" in unit
    assert "MemoryMax=512M" in unit
    assert "UMask=0077" in unit


def test_environment_and_secret_examples_have_placeholders_only() -> None:
    env = _read("quote-hub.env.example")
    secret = json.loads(_read("quote-hub-secret.json.example"))

    assert "QUOTE_HUB_SECRET_FILE=/etc/echo/quote-hub-secret.json" in env
    assert "QUOTE_HUB_UPSTREAM_URL=https://api.echo-age.com/internal/paper-origin/api" in env
    assert "QUOTE_HUB_REST_INTERVAL=3" in env
    assert "QUOTE_HUB_PLATFORM_REST_INTERVAL" not in env
    assert "QUOTE_HUB_SSE_KEEPALIVE=15" in env
    assert "QUOTE_HUB_SSE_MAX_LIFETIME=600" in env
    assert "QUOTE_HUB_READINESS_PROBE_TIMEOUT=3" in env
    assert "QUOTE_HUB_READINESS_CACHE_SECONDS=30" in env
    assert secret == {
        "phone": "<OFFICIAL_RELAY_ACCOUNT>",
        "password": "<OFFICIAL_RELAY_PASSWORD>",
    }
    assert "Bearer " not in env
    assert "token=" not in env.lower()


def test_nginx_requires_account_auth_and_disables_sse_buffering() -> None:
    nginx = _read("nginx-api.echo-age.com.conf")
    stream = "location = /api/plugins/paper-trading/quotes/stream"
    quote_prefix = "location ^~ /api/plugins/paper-trading/quotes/"

    assert "location = /_quote_hub_auth" in nginx
    assert "internal;" in nginx
    assert "proxy_pass http://127.0.0.1:8081/account/profile;" in nginx
    assert nginx.count('proxy_set_header Cookie "";') == 2
    assert nginx.count('proxy_set_header Proxy-Authorization "";') == 2
    assert nginx.count("auth_request /_quote_hub_auth;") == 2
    assert nginx.index(stream) < nginx.index(quote_prefix)
    assert "proxy_pass http://127.0.0.1:8091;" in nginx
    assert "proxy_buffering off;" in nginx
    assert "proxy_request_buffering off;" in nginx
    assert "proxy_cache off;" in nginx
    assert "gzip off;" in nginx
    assert 'add_header X-Accel-Buffering "no" always;' in nginx
    assert 'proxy_set_header Authorization "";' in nginx


def test_nginx_restricted_origin_bridge_rewrites_rest_and_websocket_paths() -> None:
    nginx = _read("nginx-api.echo-age.com.conf")

    assert "location ^~ /internal/paper-origin/api/" in nginx
    assert "proxy_pass http://114.66.32.152:58868/api/;" in nginx
    assert "location ^~ /internal/paper-origin/socket.io/" in nginx
    assert "proxy_pass http://114.66.32.152:58868/socket.io/;" in nginx
    assert "proxy_set_header Upgrade $http_upgrade;" in nginx
    assert 'proxy_set_header Connection "upgrade";' in nginx
    for address in ("127.0.0.1", "::1", "47.85.24.213"):
        assert nginx.count(f"allow {address};") >= 2
    assert nginx.count("deny all;") >= 3
    assert nginx.count("{") == nginx.count("}")

    def rewrite(request_uri: str, location_prefix: str, upstream_path: str) -> str:
        """Model Nginx's trailing-slash prefix replacement for this contract."""

        assert request_uri.startswith(location_prefix)
        return upstream_path + request_uri[len(location_prefix) :]

    assert (
        rewrite(
            "/internal/paper-origin/api/member/member/login",
            "/internal/paper-origin/api/",
            "/api/",
        )
        == "/api/member/member/login"
    )
    assert (
        rewrite(
            "/internal/paper-origin/socket.io/",
            "/internal/paper-origin/socket.io/",
            "/socket.io/",
        )
        == "/socket.io/"
    )


def test_quotes_http_context_has_exact_origins_limits_and_private_queries() -> None:
    context = _read("nginx-quotes-http-context.conf")

    for origin in (
        "https://echo-age.com",
        "https://ai.echo-age.com",
        "https://os.echo-age.com",
        "https://api.echo-age.com",
    ):
        assert context.count(f'"{origin}"') == 2
    assert "http://localhost" not in context
    assert "http://127.0.0.1" not in context
    assert '"null"' not in context
    assert "Access-Control-Allow-Origin *" not in context
    assert "limit_conn_zone $binary_remote_addr zone=quote_hub_sse_per_ip:10m" in context
    assert "limit_req_zone $binary_remote_addr zone=quote_hub_http_per_ip:10m" in context
    assert '"$request_method $uri $server_protocol"' in context
    assert "$request_uri" not in context


def test_quotes_http_stage_only_serves_acme_and_never_plaintext_quotes() -> None:
    stage = _read("nginx-quotes-http.echo-age.com.conf")

    assert "server_name quotes.echo-age.com;" in stage
    assert "listen 80;" in stage
    assert "/.well-known/acme-challenge/" in stage
    assert "root /var/www/certbot;" in stage
    assert "return 404;" in stage
    assert "proxy_pass" not in stage
    assert "listen 443" not in stage


def test_quotes_tls_vhost_is_independent_authenticated_and_narrow() -> None:
    nginx = _read("nginx-quotes.echo-age.com.conf")

    assert "server_name quotes.echo-age.com;" in nginx
    assert "/etc/letsencrypt/live/quotes.echo-age.com/fullchain.pem" in nginx
    assert "return 308 https://$host$request_uri;" in nginx
    assert "location = /_quote_hub_account_auth" in nginx
    assert "proxy_pass http://127.0.0.1:8081/account/profile;" in nginx
    assert "proxy_set_header Authorization $http_authorization;" in nginx
    assert "auth_request /_quote_hub_account_auth;" in nginx
    assert "(?:status|snapshot)$" in nginx
    assert "location = /api/plugins/paper-trading/quotes/stream" in nginx
    assert nginx.count("proxy_pass http://127.0.0.1:8091;") == 2
    assert nginx.count('proxy_set_header Authorization "";') == 2
    assert "if ($quote_hub_origin_allowed = 0) { return 403; }" in nginx
    assert "if ($request_method = OPTIONS) { return 204; }" in nginx
    assert "add_header Access-Control-Allow-Origin $quote_hub_cors_origin always;" in nginx
    assert 'add_header Access-Control-Allow-Methods "GET, OPTIONS" always;' in nginx
    assert "Access-Control-Allow-Credentials" not in nginx
    assert "limit_conn quote_hub_sse_per_ip 4;" in nginx
    assert "limit_req zone=quote_hub_http_per_ip burst=20 nodelay;" in nginx
    assert "proxy_buffering off;" in nginx
    assert 'add_header X-Accel-Buffering "no" always;' in nginx
    assert "/internal/paper-origin" not in nginx
    assert "location = /health" not in nginx
    assert "location = /readyz" not in nginx


def test_release_scripts_are_executable_syntax_checked_and_atomic() -> None:
    for name in ("build-release-artifact.sh", "deploy-release.sh", "rollback-release.sh"):
        path = DEPLOY / name
        script = path.read_text(encoding="utf-8")
        assert os.access(path, os.X_OK)
        subprocess.run(["bash", "-n", str(path)], check=True)
        assert "rm -rf" not in script

    for name in ("deploy-release.sh", "rollback-release.sh"):
        script = _read(name)
        assert "BASE_DIR=/opt/echo-cloud/quote-hub" in script
        assert "mv -Tf" in script
        assert ".current.next.$$" in script
        assert "flock -n" in script

    deploy = _read("deploy-release.sh")
    rollback = _read("rollback-release.sh")
    build = _read("build-release-artifact.sh")
    assert "mode 0600" in deploy
    assert "uv build" in build
    assert "--wheel" in build
    assert "uv export" in build
    assert "--locked" in build
    assert "--no-emit-project" in build
    assert "--extra serve" in build
    assert "--extra channels" in build
    assert "SHA256SUMS" in build
    assert "sha256sum --strict --check" in deploy
    assert "artifact contains unexpected files" in deploy
    assert "artifact may only contain regular files" in deploy
    assert "uv sync" not in deploy
    assert "pip sync" in deploy
    assert "--require-hashes" in deploy
    assert "--no-index" in deploy
    assert "rsync" not in deploy
    assert "wait_ready" in deploy
    assert "automatic rollback" in deploy
    assert "still contains a placeholder" in deploy
    assert "realpath -e" in rollback
    assert "target escaped releases directory" in rollback
    assert "[0-9a-f]{8,12}" in rollback


def test_deployment_guide_documents_permissions_auth_and_rollback() -> None:
    guide = _read("README.md")

    assert "echo-quote:echo-quote" in guide
    assert "0600" in guide
    assert "auth_request" in guide
    assert "127.0.0.1:8091" in guide
    assert "原子软链" in guide
    assert "rollback-release.sh" in guide
    assert "fetch()" in guide
    assert "token 放进 URL" in guide
    assert "最终网络段不是 TLS" in guide
    assert "SHA256SUMS" in guide
    assert "不会把本地工作区" in guide

