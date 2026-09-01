"""Test configuration layering with extends support."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.platform.config.loader import ConfigLoadError, load_from_yaml


class TestConfigExtends:
    """Test extends-based configuration inheritance."""

    def test_simple_extends(self, tmp_path: Path) -> None:
        """Test basic extends inheritance."""
        base = tmp_path / "base.yaml"
        base.write_text(
            """
name: base-config
version_compat: "0.2"
preset: personal
budget:
  max_tokens: 50000
  max_usd: 0.50
"""
        )

        child = tmp_path / "child.yaml"
        child.write_text(
            """
extends: base.yaml
name: child-config
budget:
  max_tokens: 100000
"""
        )

        config = load_from_yaml(child)
        assert config.name == "child-config"
        assert config.budget.max_tokens == 100000
        assert config.budget.max_usd == 0.50  # inherited from base

    def test_deep_merge(self, tmp_path: Path) -> None:
        """Test deep merge of nested dictionaries."""
        base = tmp_path / "base.yaml"
        base.write_text(
            """
name: base
version_compat: "0.2"
preset: personal
immunity:
  trusted_sources:
    - "skill://public/*"
  unknown_policy: quarantine
  attack_threshold: 3
"""
        )

        child = tmp_path / "child.yaml"
        child.write_text(
            """
extends: base.yaml
immunity:
  attack_threshold: 2
  enable_adaptive: true
"""
        )

        config = load_from_yaml(child)
        assert config.immunity.attack_threshold == 2  # overridden
        assert config.immunity.unknown_policy == "quarantine"  # inherited
        assert config.immunity.enable_adaptive is True  # added

    def test_chained_extends(self, tmp_path: Path) -> None:
        """Test multi-level extends chain."""
        base = tmp_path / "base.yaml"
        base.write_text(
            """
name: base
version_compat: "0.2"
preset: personal
budget:
  max_tokens: 50000
  max_usd: 0.50
"""
        )

        middle = tmp_path / "middle.yaml"
        middle.write_text(
            """
extends: base.yaml
name: middle
budget:
  max_tokens: 100000
"""
        )

        top = tmp_path / "top.yaml"
        top.write_text(
            """
extends: middle.yaml
name: top
budget:
  max_usd: 2.00
"""
        )

        config = load_from_yaml(top)
        assert config.name == "top"
        assert config.budget.max_tokens == 100000  # from middle
        assert config.budget.max_usd == 2.00  # from top

    def test_circular_extends_detection(self, tmp_path: Path) -> None:
        """Test that circular extends raises error."""
        a = tmp_path / "a.yaml"
        a.write_text("extends: b.yaml\nname: a\nversion_compat: '0.2'\npreset: personal")

        b = tmp_path / "b.yaml"
        b.write_text("extends: a.yaml\nname: b\nversion_compat: '0.2'\npreset: personal")

        with pytest.raises(ConfigLoadError, match="circular extends"):
            load_from_yaml(a)

    def test_self_reference_detection(self, tmp_path: Path) -> None:
        """Test that self-referencing extends raises error."""
        config = tmp_path / "config.yaml"
        config.write_text(
            "extends: config.yaml\nname: self\nversion_compat: '0.2'\npreset: personal"
        )

        with pytest.raises(ConfigLoadError, match="circular extends"):
            load_from_yaml(config)

    def test_missing_extends_target(self, tmp_path: Path) -> None:
        """Test error when extends target doesn't exist."""
        config = tmp_path / "config.yaml"
        config.write_text(
            "extends: nonexistent.yaml\nname: test\nversion_compat: '0.2'\npreset: personal"
        )

        with pytest.raises(ConfigLoadError, match="extends target not found"):
            load_from_yaml(config)

    def test_extends_with_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test extends combined with environment variable interpolation."""
        monkeypatch.setenv("TEST_TOKEN_BUDGET", "150000")

        base = tmp_path / "base.yaml"
        base.write_text(
            """
name: base
version_compat: "0.2"
preset: personal
budget:
  max_tokens: 50000
  max_usd: 0.50
"""
        )

        child = tmp_path / "child.yaml"
        child.write_text(
            """
extends: base.yaml
name: child
budget:
  max_tokens: ${TEST_TOKEN_BUDGET}
"""
        )

        config = load_from_yaml(child)
        assert config.budget.max_tokens == 150000

    def test_no_extends(self, tmp_path: Path) -> None:
        """Test loading config without extends works normally."""
        config = tmp_path / "config.yaml"
        config.write_text(
            """
name: standalone
version_compat: "0.2"
preset: personal
budget:
  max_tokens: 75000
  max_usd: 1.00
"""
        )

        result = load_from_yaml(config)
        assert result.name == "standalone"
        assert result.budget.max_tokens == 75000

    def test_extends_depth_limit(self, tmp_path: Path) -> None:
        """Test that extends chain depth is limited."""
        # Create a chain of 12 configs (exceeds limit of 10)
        for i in range(12):
            config = tmp_path / f"config{i}.yaml"
            if i == 0:
                config.write_text(f"name: config{i}\nversion_compat: '0.2'\npreset: personal\n")
            else:
                config.write_text(f"extends: config{i - 1}.yaml\nname: config{i}\n")

        with pytest.raises(ConfigLoadError, match="extends chain too deep"):
            load_from_yaml(tmp_path / "config11.yaml")

    def test_extends_invalid_type(self, tmp_path: Path) -> None:
        """Test error when extends is not a string."""
        config = tmp_path / "config.yaml"
        config.write_text(
            """
extends:
  - base.yaml
name: test
version_compat: "0.2"
preset: personal
"""
        )

        with pytest.raises(ConfigLoadError, match="extends must be a string"):
            load_from_yaml(config)

    def test_relative_extends_path(self, tmp_path: Path) -> None:
        """Test extends with relative paths across subdirectories."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        base = tmp_path / "base.yaml"
        base.write_text(
            """
name: base
version_compat: "0.2"
preset: personal
budget:
  max_tokens: 50000
"""
        )

        child = subdir / "child.yaml"
        child.write_text(
            """
extends: ../base.yaml
name: child
budget:
  max_tokens: 100000
"""
        )

        config = load_from_yaml(child)
        assert config.name == "child"
        assert config.budget.max_tokens == 100000

    def test_disable_extends_resolution(self, tmp_path: Path) -> None:
        """Test loading config with extends disabled."""
        base = tmp_path / "base.yaml"
        base.write_text("name: base\nversion_compat: '0.2'\npreset: personal")

        child = tmp_path / "child.yaml"
        child.write_text("extends: base.yaml\nname: child\nversion_compat: '0.2'\npreset: personal")

        # With extends disabled, the extends key should be treated as unknown field
        # Pydantic will ignore extra fields by default, so this should succeed
        config = load_from_yaml(child, resolve_extends=False)
        assert config.name == "child"

