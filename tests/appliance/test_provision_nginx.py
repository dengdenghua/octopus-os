from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_provision_nginx_preserves_external_host_port_for_browser_security():
    config = (REPO_ROOT / "deploy/provision/base/echo-nginx.conf").read_text(encoding="utf-8")

    assert config.count("proxy_set_header Host              $http_host;") == 2
    assert config.count("proxy_set_header X-Forwarded-Host   $http_host;") == 2
    assert "proxy_set_header Host              $host;" not in config
