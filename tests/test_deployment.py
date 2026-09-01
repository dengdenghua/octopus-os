"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDockerfile:
    def test_dockerfile_exists(self):
        assert (REPO / "Dockerfile").exists()

    def test_dockerignore_exists(self):
        assert (REPO / ".dockerignore").exists()

    def test_multi_stage_build(self):
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        # Implementation note.
        assert "AS runtime" in text
        # Implementation note.
        assert "AS builder" in text or "AS py-builder" in text
        # Implementation note.
        assert "COPY --from=" in text

    def test_non_root_user(self):
        """The root bootstrap must permanently drop to the configured Echo uid."""
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (REPO / "appliance" / "entrypoint.py").read_text(encoding="utf-8")
        assert "USER root" in text
        assert "useradd" in text
        assert "os.setgid(gid)" in entrypoint
        assert "os.setuid(uid)" in entrypoint
        assert "failed to drop root privileges" in entrypoint

    def test_exposes_port(self):
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert "EXPOSE 8000" in text

    def test_entrypoint_is_cli(self):
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert 'ENTRYPOINT ["python", "-m", "appliance.entrypoint"]' in text
        assert "serve" in text  # Implementation note.

    def test_dockerignore_excludes_data_and_env(self):
        text = (REPO / ".dockerignore").read_text(encoding="utf-8")
        # Implementation note.
        for pattern in ["data/", "*.jsonl", "*.sqlite", ".env"]:
            assert pattern in text, f"missing .dockerignore entry: {pattern}"

    def test_dockerignore_keeps_readme(self):
        """Implementation note."""
        text = (REPO / ".dockerignore").read_text(encoding="utf-8")
        assert "!README.md" in text


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDockerCompose:
    def test_compose_file_valid_yaml(self):
        yaml = pytest.importorskip("yaml")
        path = REPO / "docker-compose.yml"
        assert path.exists()
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict)
        assert "services" in doc
        assert "echo-os" in doc["services"]

    def test_compose_mounts_data_volume(self):
        yaml = pytest.importorskip("yaml")
        doc = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = doc["services"]["echo-os"]
        vols = svc.get("volumes", [])
        assert any("./data:/data" in v for v in vols), "需要把 ./data:/data 挂上做 journal 持久化"

    def test_compose_mounts_config_readonly(self):
        yaml = pytest.importorskip("yaml")
        doc = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = doc["services"]["echo-os"]
        vols = svc.get("volumes", [])
        assert any(":ro" in v and "config" in v for v in vols)

    def test_compose_command_uses_serve(self):
        yaml = pytest.importorskip("yaml")
        doc = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = doc["services"]["echo-os"]
        cmd = svc.get("command", [])
        # Implementation note.
        assert "serve" in cmd
        assert "--config" in cmd

    def test_compose_has_healthcheck(self):
        yaml = pytest.importorskip("yaml")
        doc = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = doc["services"]["echo-os"]
        hc = svc.get("healthcheck")
        assert hc is not None
        assert "test" in hc
        # Implementation note.
        test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
        assert "/readyz" in test_cmd

    def test_compose_restart_policy(self):
        yaml = pytest.importorskip("yaml")
        doc = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = doc["services"]["echo-os"]
        assert svc.get("restart") in ("unless-stopped", "always", "on-failure")


# ═══════════════════════════════════════════════════════════
# pyproject · console_scripts + optional extras
# ═══════════════════════════════════════════════════════════


class TestPyproject:
    def test_echo_agent_script_registered(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert 'echo-agent = "runtime.cli:main"' in text

    def test_serve_extra_declared(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "serve" in text
        assert "fastapi" in text
        assert "uvicorn" in text

    def test_anthropic_extra_declared(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "anthropic = [" in text

    def test_mcp_extra_declared(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "mcp = [" in text

    def test_browser_extra_declared(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "browser = [" in text
        assert "playwright" in text


# ═══════════════════════════════════════════════════════════
# .env.example + DEPLOY.md
# ═══════════════════════════════════════════════════════════


class TestDocumentation:
    def test_env_example_exists_and_has_placeholders(self):
        path = REPO / ".env.example"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" in text
        # Implementation note.
        for line in text.splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                assert line.endswith("=") or "sk-" not in line

    def test_deploy_md_exists(self):
        # Implementation note.
        # Implementation note.
        path = REPO / "docs" / "deployment.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        for section in ["Docker", "docker compose", "/api/health"]:
            assert section in text

    def test_license_present_and_apache2(self):
        """Implementation note."""
        path = REPO / "LICENSE"
        assert path.exists(), "LICENSE file missing"
        text = path.read_text(encoding="utf-8")
        assert "Apache License" in text
        assert "Version 2.0" in text

    def test_gitignore_present_and_excludes_secrets(self):
        path = REPO / ".gitignore"
        assert path.exists(), ".gitignore missing"
        text = path.read_text(encoding="utf-8")
        for pat in ["__pycache__/", ".env", "data/", "*.jsonl", "*.sqlite"]:
            assert pat in text, f"missing .gitignore entry: {pat}"
        # Implementation note.
        assert "!.env.example" in text
        assert "!config.example.yaml" in text

    def test_contributing_md_exists(self):
        path = REPO / "CONTRIBUTING.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        for section in ["pytest", "lint", "Apache-2.0"]:
            assert section in text

    def test_pyproject_urls_not_example(self):
        """Implementation note."""
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "github.com/example" not in text, (
            "pyproject.toml still has github.com/example placeholder URL"
        )

    def test_english_readme_present(self):
        """Implementation note."""
        path = REPO / "README.en.md"
        assert path.exists(), "README.en.md missing"
        text = path.read_text(encoding="utf-8")
        for section in [
            "Quick Start",
            "CLI",
            "Reflection closure",
            "Apache-2.0",
            "OpenAI",
        ]:
            assert section in text, f"README.en.md missing section: {section}"

    def test_chinese_readme_links_to_english(self):
        """Implementation note."""
        text = (REPO / "README.md").read_text(encoding="utf-8")
        assert "README.en.md" in text
