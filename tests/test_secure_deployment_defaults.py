"""Regression checks for safe, copy-pasteable deployment defaults."""

from pathlib import Path

import pytest
import yaml

from runtime.cli_serve import _insecure_bind_error
from runtime.platform.config import ConfigLoadError, load_from_yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_services_bind_control_plane_to_loopback_by_default() -> None:
    expected = "${ECHO_BIND_IP:-127.0.0.1}:${PORT:-8000}:8000"
    for filename in ("docker-compose.yml", "docker-compose.full.yml"):
        compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
        assert expected in compose["services"]["echo-os"]["ports"]


def test_documented_docker_command_does_not_publish_on_all_interfaces() -> None:
    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "docker run --rm -p 127.0.0.1:8000:8000" in deployment
    assert "docker run --rm -p 127.0.0.1:8000:8000" in dockerfile
    assert "docker run --rm -p 8000:8000" not in deployment


def test_example_local_auth_does_not_accept_arbitrary_usernames() -> None:
    config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

    assert config["local_auth"]["enabled"] is False
    assert config["local_auth"]["allow_any_username"] is False
    assert config["local_auth"]["allowed_usernames"] == []


def test_systemd_baseline_is_authenticated_production_and_loadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = ROOT / "deploy/systemd-config.yaml"
    monkeypatch.setenv(
        "ECHO_LOCAL_AUTH_JWT_SECRET",
        "Systemd!Release9Jwt#Secret2With$Entropy4AndLength",
    )
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD_HASH", "sha256:" + "a" * 64)

    config = load_from_yaml(config_path)

    assert config.planner.type == "llm"
    assert not config.planner.model.startswith("mock/")
    assert config.planner.mock_response is None
    assert config.local_auth.enabled is True
    assert config.local_auth.allow_any_username is False
    assert config.local_auth.users == {"admin": "sha256:" + "a" * 64}
    assert config.local_auth.jwt_expire_seconds == 28_800
    assert config.local_auth.default_roles == ["user", "local", "admin", "operator"]
    assert config.execution.deployment_mode == "production"
    assert config.execution.process_sandbox == "strict"
    assert config.journal_file == "/var/lib/echo/events.jsonl"
    assert _insecure_bind_error(host="0.0.0.0", uds=None, require_auth=True) is None


def test_systemd_baseline_fails_closed_without_auth_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_LOCAL_AUTH_JWT_SECRET", raising=False)
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD_HASH", "sha256:" + "a" * 64)

    with pytest.raises(ConfigLoadError, match="jwt_secret"):
        load_from_yaml(ROOT / "deploy/systemd-config.yaml")


def test_systemd_unit_uses_dedicated_baseline_and_persistent_data_root() -> None:
    service = (ROOT / "deploy/echo-agent.service").read_text(encoding="utf-8")
    deployment = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")

    assert "--config /etc/echo/config.yaml" in service
    assert "--host 0.0.0.0" in service
    assert "Environment=ECHO_DATA_DIR=/var/lib/echo" in service
    assert "deploy/systemd-config.yaml" in service
    assert "deploy/systemd-config.yaml" in deployment
    assert "echo-os[serve,local-auth,anthropic]" in deployment
    assert "printf 'Echo!9%s\\n' \"$(openssl rand -base64 48)\"" in deployment
    assert "local_auth.config import hash_password" in deployment
    assert "sudo cp config.example.yaml /etc/echo/config.yaml" not in deployment


def test_compose_healthchecks_use_real_readiness_probe() -> None:
    for filename in ("docker-compose.yml", "docker-compose.full.yml"):
        compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
        command = " ".join(compose["services"]["echo-os"]["healthcheck"]["test"])
        assert "/readyz" in command
        assert "/api/health" not in command


def test_k8s_config_is_authenticated_real_and_loadable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configmap = yaml.safe_load((ROOT / "deploy/k8s/configmap.yaml").read_text(encoding="utf-8"))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(configmap["data"]["config.yaml"], encoding="utf-8")
    monkeypatch.setenv(
        "ECHO_LOCAL_AUTH_JWT_SECRET",
        "K8s!Release9Jwt#Secret2With$Entropy4AndLength",
    )
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD_HASH", "sha256:" + "a" * 64)
    monkeypatch.setenv("REDIS_PASSWORD", "redis-test-password")

    config = load_from_yaml(config_path)

    assert config.planner.type == "llm"
    assert not config.planner.model.startswith("mock/")
    assert config.planner.mock_response is None
    assert config.local_auth.enabled is True
    assert config.local_auth.allow_any_username is False
    assert config.local_auth.users == {"admin": "sha256:" + "a" * 64}
    assert config.local_auth.login_max_failures == 5
    assert config.local_auth.login_ip_max_failures == 20
    assert config.local_auth.login_failure_window_seconds == 300
    assert config.local_auth.login_lockout_seconds == 60
    assert config.local_auth.login_rate_limit_max_entries == 10_000
    assert config.local_auth.jwt_expire_seconds == 28_800
    assert config.local_auth.default_roles == ["user", "local", "admin", "operator"]
    assert config.execution.deployment_mode == "production"
    assert config.execution.process_sandbox == "strict"
    assert config.tool_effects.backend == "redis"
    assert config.tool_effects.require_distributed is True
    assert _insecure_bind_error(host="0.0.0.0", uds=None, require_auth=True) is None


def test_k8s_manifest_binds_auth_redis_and_real_probe_contract() -> None:
    secret = yaml.safe_load((ROOT / "deploy/k8s/secret.yaml").read_text(encoding="utf-8"))
    deployment = yaml.safe_load((ROOT / "deploy/k8s/deployment.yaml").read_text(encoding="utf-8"))
    values = secret["stringData"]
    assert values["ECHO_LOCAL_AUTH_JWT_SECRET"] == "<CHANGE_ME>"
    assert values["ECHO_ADMIN_PASSWORD_HASH"] == "<CHANGE_ME>"
    assert values["ECHO_CLOUD_EDGE_TOKEN_SECRET"] == "<CHANGE_ME>"

    container = deployment["spec"]["template"]["spec"]["containers"][0]
    pod_spec = deployment["spec"]["template"]["spec"]
    fail_closed_image = "ghcr.io/dengdenghua/echo-os@sha256:" + "0" * 64
    assert container["image"] == fail_closed_image
    assert pod_spec["initContainers"][0]["image"] == fail_closed_image
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/livez"
    env = {item["name"]: item for item in container["env"]}
    assert env["ECHO_HEARTS_REDIS_URL"]["value"].startswith("redis://:")
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert pod_spec["securityContext"]["runAsUser"] == 10001
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    init = pod_spec["initContainers"][0]
    assert "/app/resources/. /data/resources/" in init["command"][-1]
    assert "--no-clobber" in init["command"][-1]
    mounts = {(item["mountPath"], item.get("subPath")) for item in container["volumeMounts"]}
    assert ("/app/resources", "resources") in mounts


def test_k8s_admin_login_can_reach_real_admin_control_plane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hashlib

    from fastapi.testclient import TestClient

    from runtime.platform.ui.app import create_app

    configmap = yaml.safe_load((ROOT / "deploy/k8s/configmap.yaml").read_text(encoding="utf-8"))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(configmap["data"]["config.yaml"], encoding="utf-8")
    password = "K8s-admin-password!9"
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    monkeypatch.setenv(
        "ECHO_LOCAL_AUTH_JWT_SECRET",
        "K8s!Release9Jwt#Secret2With%Entropy4AndLength",
    )
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD_HASH", "sha256:" + password_hash)
    monkeypatch.setenv("REDIS_PASSWORD", "redis-test-password")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    config = load_from_yaml(config_path)

    with TestClient(
        create_app(
            cocoloop_require_auth=True,
            local_auth_config=config.local_auth,
        )
    ) as client:
        login = client.post(
            "/api/auth/local/login",
            json={"username": "admin", "password": password},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        whoami = client.get("/api/auth/local/whoami", headers=headers)
        assert whoami.status_code == 200, whoami.text
        assert {"admin", "operator"}.issubset(whoami.json()["roles"])

        mutation = client.post(
            "/api/path-denylist",
            headers=headers,
            json={"path": str(tmp_path / "blocked")},
        )
        assert mutation.status_code == 200, mutation.text


def test_k8s_mutable_resource_contract_supports_real_market_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from runtime.execution.agents.loader import default_agents_root
    from runtime.platform.process.paths import resources_root
    from runtime.sensing.gateway import agent_world_router

    mutable_resources = tmp_path / "data/resources"
    monkeypatch.setenv("ECHO_RESOURCES_DIR", str(mutable_resources))
    monkeypatch.setattr(
        agent_world_router,
        "_INSTALL_STATE",
        tmp_path / "data/agents-installed.json",
    )

    agents_root = default_agents_root()
    skills_root = resources_root() / "skills/public"
    installed = agent_world_router._install_template_agent(
        "financial_pitch_agent",
        agents_root,
        skills_root=skills_root,
    )

    assert installed == mutable_resources / "agents/financial_pitch_agent"
    assert (installed / "profile.jsonc").is_file()
    assert (skills_root / "pitch-deck/SKILL.md").is_file()
    assert (skills_root / "dcf-model/SKILL.md").is_file()


def test_k8s_network_policy_is_explicit_and_fail_closed() -> None:
    policies = list(
        yaml.safe_load_all((ROOT / "deploy/k8s/networkpolicy.yaml").read_text(encoding="utf-8"))
    )
    by_name = {item["metadata"]["name"]: item for item in policies}
    agent_ingress = by_name["echo-agent-ingress"]["spec"]["ingress"][0]
    namespace_labels = agent_ingress["from"][0]["namespaceSelector"]["matchLabels"]
    assert namespace_labels == {"echo-agent.io/ingress-access": "true"}
    redis_ingress = by_name["redis-ingress"]["spec"]["ingress"][0]
    assert redis_ingress["from"][0]["podSelector"]["matchLabels"] == {"app": "echo-agent"}
    kustomization = yaml.safe_load(
        (ROOT / "deploy/k8s/kustomization.yaml").read_text(encoding="utf-8")
    )
    assert "networkpolicy.yaml" in kustomization["resources"]
    kustomization_text = (ROOT / "deploy/k8s/kustomization.yaml").read_text(encoding="utf-8")
    assert "digest: sha256:<RELEASE_MANIFEST_DIGEST>" in kustomization_text


def test_k8s_redis_drops_service_account_and_uses_runtime_seccomp() -> None:
    resources = list(yaml.safe_load_all((ROOT / "deploy/k8s/redis.yaml").read_text()))
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert container["image"] == (
        "redis:7.4.11-alpine@sha256:"
        "ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf"
    )
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    env = {item["name"]: item for item in container["env"]}
    assert env["REDISCLI_AUTH"]["valueFrom"]["secretKeyRef"]["key"] == "REDIS_PASSWORD"
    assert container["readinessProbe"]["exec"]["command"] == ["redis-cli", "ping"]
    assert container["livenessProbe"]["exec"]["command"] == ["redis-cli", "ping"]
    mounts = {item["mountPath"]: item["name"] for item in container["volumeMounts"]}
    assert mounts["/data"] == "redis-data"
    assert mounts["/tmp"] == "tmp"
    volumes = {item["name"]: item for item in pod_spec["volumes"]}
    assert volumes["tmp"]["emptyDir"] == {}


def test_deployment_docs_describe_builtin_auth_and_probe_semantics() -> None:
    deployment = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "项目内置 `oct` 与 `local_auth`" in deployment
    assert "本项目未内置 auth" not in deployment
    assert "不要把总是 HTTP 200 的 `/api/health`" in deployment
    assert "@sha256:" in deployment
    assert "echo-agent.io/ingress-access=true" in deployment
    assert "configuration-snippet" in deployment
    assert "cosign verify" in deployment
    assert "不推送 `latest`" in (ROOT / "deploy/k8s/README.md").read_text(encoding="utf-8")
    assert "仅命名当前本地 build" in deployment
    assert "set images[].digest" in makefile
    assert "sha256:0{64}" in makefile


def test_deployment_docs_do_not_overstate_single_node_high_availability() -> None:
    deployment = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")
    k8s_readme = (ROOT / "deploy/k8s/README.md").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.full.yml").read_text(encoding="utf-8")

    assert "Redis HA" not in deployment
    assert "单 Redis 本身不是 HA" in deployment
    assert "基线清单固定为单副本" in deployment
    assert "ReadWriteOnce" in k8s_readme
    assert "固定 `replicas: 1`" in k8s_readme
    assert "`Recreate`" in k8s_readme
    assert "不会在升级时覆盖同名" in k8s_readme
    assert "不构成 HA" in compose


def test_security_policy_contains_no_placeholder_contact_or_key() -> None:
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "security@echo-agent.local" not in policy
    assert "PGP key 待添加" not in policy
    assert "Report a vulnerability" in policy
