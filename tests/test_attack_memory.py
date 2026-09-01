"""Immunity Memory tier — antibody memory (protocols/immunity.md).

Pins: crystallization at the violation threshold inside the sliding
window, recall with hit accounting, memory outranking trusted_sources,
persistence across instances, and the operator forget() escape hatch.
"""

from pathlib import Path

from runtime.platform.models import AntigenSignature, ToolCall
from runtime.safety.auth.attack_memory import AttackMemory
from runtime.safety.auth.trust_engine import TrustEngine


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _sig(entity_id: str) -> AntigenSignature:
    return AntigenSignature(
        entity_id=entity_id,
        entity_type="mcp_server",
        content_hash="deadbeef",
        origin="external",
    )


def _call(caller: str = "outsider") -> ToolCall:
    return ToolCall(sucker_id="some_tool", args={}, caller=caller)


class TestCrystallization:
    def test_promotes_at_threshold(self):
        clock = _Clock()
        mem = AttackMemory(threshold=3, window_s=60, clock=clock)
        assert mem.record_violation("mcp://evil") is False
        assert mem.record_violation("mcp://evil") is False
        assert mem.record_violation("mcp://evil") is True
        assert mem.match("mcp://evil") is not None

    def test_window_expiry_resets_count(self):
        clock = _Clock()
        mem = AttackMemory(threshold=2, window_s=60, clock=clock)
        assert mem.record_violation("mcp://slow") is False
        clock.now += 120  # outside the window
        assert mem.record_violation("mcp://slow") is False
        assert mem.match("mcp://slow") is None

    def test_match_bumps_hit_accounting(self):
        clock = _Clock()
        mem = AttackMemory(threshold=1, window_s=60, clock=clock)
        mem.record_violation("mcp://evil")
        first = mem.match("mcp://evil")
        assert first is not None
        count_after_first = first.hit_count
        second = mem.match("mcp://evil")
        assert second is not None
        assert second.hit_count == count_after_first + 1

    def test_forget_removes_pattern(self):
        mem = AttackMemory(threshold=1)
        mem.record_attack("mcp://oops", "false positive")
        assert mem.forget("mcp://oops") is True
        assert mem.match("mcp://oops") is None


class TestPersistence:
    def test_roundtrip(self, tmp_path: Path):
        path = tmp_path / "antibodies.json"
        mem = AttackMemory(path, threshold=1)
        mem.record_attack("mcp://evil", "exfil attempt")

        reloaded = AttackMemory(path)
        pattern = reloaded.match("mcp://evil")
        assert pattern is not None
        assert pattern.reason == "exfil attempt"

    def test_corrupt_file_starts_clean(self, tmp_path: Path):
        path = tmp_path / "antibodies.json"
        path.write_text("{not json", encoding="utf-8")
        mem = AttackMemory(path)
        assert mem.snapshot() == []

    def test_valid_json_wrong_shape_starts_clean(self, tmp_path: Path):
        # Valid JSON but not an object (list/null/number): raw.get()
        # would raise AttributeError and escape startup. Must degrade
        # to empty, not crash.
        for content in ("[1, 2, 3]", "null", "42", '"a string"'):
            path = tmp_path / f"ab_{abs(hash(content))}.json"
            path.write_text(content, encoding="utf-8")
            mem = AttackMemory(path)
            assert mem.snapshot() == []


class TestTrustEngineIntegration:
    def test_memory_rejects_before_trusted_allow(self):
        # A compromised entity INSIDE a trusted glob must not keep
        # its free pass once it is a known attacker.
        engine = TrustEngine(trusted_sources=["skill://public/*"])
        engine.attack_memory.record_attack(
            "skill://public/compromised",
            "reported by operator",
        )
        report = engine.check(_call(), _sig("skill://public/compromised"))
        assert report.verdict == "reject"
        assert report.strategy_used == "memory"

    def test_clean_trusted_source_still_allowed(self):
        engine = TrustEngine(trusted_sources=["skill://public/*"])
        report = engine.check(_call(), _sig("skill://public/fine"))
        assert report.verdict == "allow"
        assert report.strategy_used == "innate"

    def test_repeated_policy_rejects_crystallize(self):
        clock = _Clock()
        engine = TrustEngine(
            trusted_sources=[],
            unknown_policy="reject",
            attack_memory=AttackMemory(threshold=3, window_s=60, clock=clock),
        )
        for _ in range(3):
            report = engine.check(_call(), _sig("mcp://probe"))
            assert report.verdict == "reject"
        # Crystallized: next encounter rejects via memory, not policy.
        report = engine.check(_call(), _sig("mcp://probe"))
        assert report.verdict == "reject"
        assert report.strategy_used == "memory"

    def test_quarantine_policy_does_not_crystallize(self):
        engine = TrustEngine(trusted_sources=[], unknown_policy="quarantine")
        for _ in range(5):
            report = engine.check(_call(), _sig("mcp://unknown"))
            assert report.verdict == "quarantine"
        assert report.strategy_used == "innate"

    def test_tolerance_bypasses_memory(self):
        # Self-whitelisted callers never hit the memory tier — the
        # protocol's anti-autoimmune invariant (I2).
        engine = TrustEngine()
        engine.attack_memory.record_attack("whatever", "noise")
        report = engine.check(_call(caller="cerebrum"), _sig("whatever"))
        assert report.verdict == "allow"
        assert report.strategy_used == "tolerance"
