"""Tests for the preflight CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from runtime.safety.evolution import preflight


@pytest.fixture(autouse=True)
def _isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Run inside a tmp dir so yaml lookup hits only what we put there."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "ECHO_DISABLED_GUARDS",
        "ECHO_CHECKPOINT_EVERY_N",
        "ECHO_CHECKPOINT_MIRROR_URL",
        "ECHO_ENABLE_TRUST_SIGNAL",
        "ECHO_DISABLE_GUARD_TELEMETRY",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# ══════════════════════════════════════════════════════════════════
# _probe_env / _probe_yaml / _probe_optional_deps
# ══════════════════════════════════════════════════════════════════


class TestProbes:
    def test_probe_env_clean(self) -> None:
        env = preflight._probe_env()
        assert env["ECHO_DISABLED_GUARDS"] is None
        assert env["ECHO_CHECKPOINT_EVERY_N"] is None

    def test_probe_env_picks_up_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "5")
        env = preflight._probe_env()
        assert env["ECHO_CHECKPOINT_EVERY_N"] == "5"

    def test_probe_yaml_no_file(self) -> None:
        out = preflight._probe_yaml()
        assert out["config_path"] is None
        assert out["disabled_guards"] == []
        assert out["enable_trust_signal"] is None

    def test_probe_yaml_reads_settings(self, tmp_path: Path) -> None:
        (tmp_path / "config.local.yaml").write_text(
            "safety:\n  disabled_guards:\n    - magic-number guard\n  enable_trust_signal: true\n",
            encoding="utf-8",
        )
        out = preflight._probe_yaml()
        assert out["config_path"].endswith("config.local.yaml")
        assert out["disabled_guards"] == ["magic-number guard"]
        assert out["enable_trust_signal"] is True

    def test_probe_optional_deps_pyyaml_available(self) -> None:
        out = preflight._probe_optional_deps()
        # PyYAML is in dev deps for this repo.
        assert out["yaml"] is True

    def test_probe_journal_missing(self) -> None:
        out = preflight._probe_journal()
        assert out["exists"] is False
        assert out["size_bytes"] == 0

    def test_probe_journal_present(self, tmp_path: Path) -> None:
        d = tmp_path / "data"
        d.mkdir()
        (d / "guard_hits.jsonl").write_text(
            '{"x": 1}\n',
            encoding="utf-8",
        )
        out = preflight._probe_journal()
        assert out["exists"] is True
        assert out["size_bytes"] > 0


# ══════════════════════════════════════════════════════════════════
# _classify_features
# ══════════════════════════════════════════════════════════════════


class TestFeatureClassifier:
    def test_all_off_default(self) -> None:
        out = preflight._classify_features(
            env={k: None for k in preflight._INTEREST_ENV_VARS},
            yaml_settings={},
            deps={"yaml": True, "redis": False},
        )
        assert out["P3_auto_checkpoint"] == "off"
        assert out["P3_distributed_mirror"] == "off"
        assert out["P0_trust_gate"] == "off (default)"
        assert out["kill_switch"] == "all guards active"

    def test_auto_checkpoint_on(self) -> None:
        out = preflight._classify_features(
            env={
                "ECHO_CHECKPOINT_EVERY_N": "5",
                "ECHO_DISABLED_GUARDS": None,
                "ECHO_CHECKPOINT_MIRROR_URL": None,
                "ECHO_ENABLE_TRUST_SIGNAL": None,
                "ECHO_DISABLE_GUARD_TELEMETRY": None,
            },
            yaml_settings={},
            deps={"yaml": True, "redis": False},
        )
        assert out["P3_auto_checkpoint"] == "on"

    def test_mirror_needs_attention(self) -> None:
        out = preflight._classify_features(
            env={
                "ECHO_CHECKPOINT_EVERY_N": None,
                "ECHO_DISABLED_GUARDS": None,
                "ECHO_CHECKPOINT_MIRROR_URL": "redis://x",
                "ECHO_ENABLE_TRUST_SIGNAL": None,
                "ECHO_DISABLE_GUARD_TELEMETRY": None,
            },
            yaml_settings={},
            deps={"yaml": True, "redis": False},
        )
        assert "needs-attention" in out["P3_distributed_mirror"]

    def test_trust_gate_env_on_wins(self) -> None:
        out = preflight._classify_features(
            env={
                "ECHO_CHECKPOINT_EVERY_N": None,
                "ECHO_DISABLED_GUARDS": None,
                "ECHO_CHECKPOINT_MIRROR_URL": None,
                "ECHO_ENABLE_TRUST_SIGNAL": "1",
                "ECHO_DISABLE_GUARD_TELEMETRY": None,
            },
            yaml_settings={"enable_trust_signal": False},
            deps={"yaml": True, "redis": False},
        )
        assert out["P0_trust_gate"] == "on (env)"

    def test_trust_gate_yaml_when_no_env(self) -> None:
        out = preflight._classify_features(
            env={k: None for k in preflight._INTEREST_ENV_VARS},
            yaml_settings={"enable_trust_signal": True},
            deps={"yaml": True, "redis": False},
        )
        assert out["P0_trust_gate"] == "on (yaml)"

    def test_kill_switch_count_includes_env_and_yaml(self) -> None:
        out = preflight._classify_features(
            env={
                "ECHO_DISABLED_GUARDS": "guard-x,guard-y",
                "ECHO_CHECKPOINT_EVERY_N": None,
                "ECHO_CHECKPOINT_MIRROR_URL": None,
                "ECHO_ENABLE_TRUST_SIGNAL": None,
                "ECHO_DISABLE_GUARD_TELEMETRY": None,
            },
            yaml_settings={"disabled_guards": ["guard-z"]},
            deps={"yaml": True, "redis": False},
        )
        # 2 from env + 1 from yaml = 3
        assert "3 guard(s) disabled" in out["kill_switch"]


# ══════════════════════════════════════════════════════════════════
# _build_warnings
# ══════════════════════════════════════════════════════════════════


class TestWarnings:
    def test_mirror_without_redis_warns(self) -> None:
        warnings = preflight._build_warnings(
            env={"ECHO_CHECKPOINT_MIRROR_URL": "redis://x", "ECHO_CHECKPOINT_EVERY_N": None},
            yaml_settings={},
            deps={"yaml": True, "redis": False},
            journal={"exists": True, "path": "data/guard_hits.jsonl"},
        )
        assert any("redis" in w.lower() for w in warnings)

    def test_no_pyyaml_warns(self) -> None:
        warnings = preflight._build_warnings(
            env={"ECHO_CHECKPOINT_EVERY_N": None, "ECHO_CHECKPOINT_MIRROR_URL": None},
            yaml_settings={},
            deps={"yaml": False, "redis": False},
            journal={"exists": True, "path": "data/guard_hits.jsonl"},
        )
        assert any("PyYAML" in w for w in warnings)

    def test_journal_missing_warns(self) -> None:
        warnings = preflight._build_warnings(
            env={"ECHO_CHECKPOINT_EVERY_N": None, "ECHO_CHECKPOINT_MIRROR_URL": None},
            yaml_settings={},
            deps={"yaml": True, "redis": False},
            journal={"exists": False, "path": "data/guard_hits.jsonl"},
        )
        assert any("doesn't exist" in w for w in warnings)

    def test_garbage_checkpoint_n_warns(self) -> None:
        warnings = preflight._build_warnings(
            env={"ECHO_CHECKPOINT_EVERY_N": "five", "ECHO_CHECKPOINT_MIRROR_URL": None},
            yaml_settings={},
            deps={"yaml": True, "redis": False},
            journal={"exists": True, "path": "data/guard_hits.jsonl"},
        )
        assert any("not an int" in w for w in warnings)


# ══════════════════════════════════════════════════════════════════
# Render + main
# ══════════════════════════════════════════════════════════════════


class TestRender:
    def test_text_render_smoke(self) -> None:
        result = preflight.run_preflight()
        out = preflight.render_text(result)
        assert "Environment:" in out
        assert "YAML settings:" in out
        assert "Feature status:" in out

    def test_json_render_parseable(self) -> None:
        result = preflight.run_preflight()
        out = preflight.render_json(result)
        parsed = json.loads(out)
        assert "env" in parsed
        assert "feature_status" in parsed


class TestMain:
    def test_main_text_runs_clean(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = preflight.main([])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "preflight" in captured.lower()

    def test_main_json_emits_json(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = preflight.main(["--json"])
        assert rc == 0
        out = capsys.readouterr().out
        # Should be parseable JSON.
        parsed = json.loads(out)
        assert "env" in parsed

    def test_main_top_level_exception_returns_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def boom():
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(preflight, "run_preflight", boom)
        rc = preflight.main([])
        assert rc == 1
        assert "ERROR" in capsys.readouterr().err
