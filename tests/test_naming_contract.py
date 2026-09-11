"""Lock the Echo OS naming contract.

``octopus`` survives in this repository **on purpose**. It is not leftover
branding waiting to be swept away:

* ``octopus`` is the durable execution engine id. ``EngineId.NATIVE`` is an
  explicit alias that resolves to the same value so older callers keep working.
* ``octopus.*.v1`` is a persisted schema namespace. Records already on disk
  carry it, so renaming it makes old data unreadable.
* ``octopus`` is a legacy install account. Older install media created it, so
  provisioning must keep probing it or the unit dies with ``217/USER``.

Renaming any of the above breaks already-written data or bricks machines that
were installed by older media. See ``docs/NAMING_CONVENTIONS.md`` for the full
rationale and the file:line inventory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Persisted schema namespaces. The prefix is part of the on-disk contract.
SCHEMA_OWNERS = {
    "runtime/memory/semantics.py": "octopus.memory_origin.v1",
    "runtime/execution/agents/collaboration_quality.py": "octopus.collaboration_quality.v1",
    "runtime/execution/agents/team_patterns.py": "octopus.team_pattern_decision.v1",
    "runtime/execution/host_boundary.py": "octopus.execution.v1",
}


def test_engine_id_keeps_durable_octopus_value():
    from runtime.execution.engines import EngineId

    assert EngineId.OCTOPUS.value == "octopus"
    # NATIVE is a deliberate alias, not a separate engine.
    assert EngineId.NATIVE.value == EngineId.OCTOPUS.value


@pytest.mark.parametrize(("relative", "schema"), sorted(SCHEMA_OWNERS.items()))
def test_persisted_schema_namespace_is_unchanged(relative: str, schema: str) -> None:
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert schema in text, f"{relative} must keep the persisted namespace {schema!r}"


def test_provision_probes_legacy_account_names():
    text = (REPO_ROOT / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    assert "for u in echo octopus admin" in text


def test_provision_still_cleans_legacy_octopus_units():
    text = (REPO_ROOT / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    assert "octopus-[a-z-]*\\.service" in text
    assert "/etc/nginx/sites-enabled/octopus" in text


def test_canonical_repository_points_at_the_existing_repo():
    """``dengdenghua/echo-os`` does not exist and returns 404."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dengdenghua/octopus-os" in text
    assert "github.com/dengdenghua/echo-os" not in text


def test_container_image_keeps_the_echo_brand():
    """The GHCR image name is a deliberate brand choice, unrelated to the repo."""
    text = (REPO_ROOT / "deploy/k8s/deployment.yaml").read_text(encoding="utf-8")
    assert "ghcr.io/dengdenghua/echo-os" in text
