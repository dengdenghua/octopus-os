from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_provision_nginx_preserves_external_host_port_for_browser_security():
    config = (REPO_ROOT / "deploy/provision/base/echo-nginx.conf").read_text(encoding="utf-8")

    assert config.count("proxy_set_header Host              $http_host;") == 2
    assert config.count("proxy_set_header X-Forwarded-Host   $http_host;") == 2
    assert "proxy_set_header Host              $host;" not in config


def test_provision_nginx_reports_cold_backend_as_retryable_startup():
    config = (REPO_ROOT / "deploy/provision/base/echo-nginx.conf").read_text(encoding="utf-8")

    assert "proxy_intercept_errors on;" in config
    assert "error_page 502 504 =503 @echo_api_starting;" in config
    assert 'add_header Retry-After "3" always;' in config
    assert '"code":"appliance_starting"' in config
    assert "error_page 502 503" not in config
    assert "server_tokens off;" in config
