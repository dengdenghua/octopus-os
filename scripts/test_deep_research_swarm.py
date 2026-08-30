#!/usr/bin/env python3
"""Test deep-research-swarm via the running backend's bootstrapped runner.

Imports the live app's stack so set_ephemeral_role_runner has been wired
properly (this is what the standalone test was missing).
"""
import logging
import sys
import time


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 70)
    print("deep-research-swarm test via app bootstrap")
    print("=" * 70)

    # Bootstrap a minimal app to wire ephemeral runner
    from runtime.platform.ui.app import create_app
    print("→ Booting app (wires set_ephemeral_role_runner)...")
    create_app()
    print("✓ App booted")

    # Verify runner is now wired
    from runtime.execution.suckers.ephemeral_agents import _EPHEMERAL_RUNNER
    print(f"✓ Ephemeral runner: {_EPHEMERAL_RUNNER.__name__ if _EPHEMERAL_RUNNER else 'NONE'}")

    # Now run swarm
    from runtime.safety.organization.forge import load_registry
    from runtime.safety.organization.team_runner import TeamRunner

    registry = load_registry()
    topology = next(t for t in registry.values() if t.name == "research_swarm_v1")
    print(f"✓ Topology loaded: {topology.name}")
    print(f"  Roles: {[str(r) for r in topology.agents]}")
    print()

    topic = (
        "Eight Sleep smart mattress technology innovations 2024-2025: "
        "temperature control, sleep tracking sensors, AI features."
    )

    print(f"Topic: {topic[:80]}...")
    print()
    print("→ Launching swarm (will spawn planner → researcher → critic → synthesizer)...")
    print()

    start = time.time()
    runner = TeamRunner(timeout_seconds=300)
    result = runner.run(
        topology,
        topic,
        context={"source": "test_swarm_v2", "thread_id": "test_swarm_002"},
    )
    elapsed = time.time() - start

    print()
    print("=" * 70)
    print(f"✓ COMPLETED in {elapsed:.1f}s")
    print("=" * 70)

    role_outputs = list(getattr(result, "role_outputs", []) or [])
    print(f"\n  Role outputs: {len(role_outputs)}")

    for i, ro in enumerate(role_outputs, 1):
        role = str(getattr(ro, "role", "?"))
        agent_id = getattr(ro, "agent_id", "?")
        output = str(getattr(ro, "output", "") or "")
        duration = getattr(ro, "duration_ms", 0)
        error = getattr(ro, "error", None)
        status = "ERROR" if error else "OK"
        print(f"    {i}. [{status}] role={role:15s} agent={agent_id:20s} "
              f"output={len(output):6d}c duration={duration}ms")
        if error:
            print(f"        error: {error}")
        elif output:
            preview = output[:120].replace("\n", " ")
            print(f"        preview: {preview}...")

    final_text = (getattr(result, "final_output", "") or "").strip()
    print(f"\n  Final output: {len(final_text)} chars")

    if final_text:
        print("\n" + "─" * 70)
        print("FINAL REPORT (synthesizer aggregation):")
        print("─" * 70)
        print(final_text[:2000])
        if len(final_text) > 2000:
            print(f"\n... [+{len(final_text) - 2000} more chars]")
        print("─" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
