"""Tests for the persistent approval policy: storage + router + runtime.

Three layers covered here:

1. ``approval_policy_store``    — JSON load/save/append + dedup.
2. ``permissions_router``       — REST shape (list / add / delete / 404).
3. ``CerebrumRuntime`` wiring   — runtime reads the file each turn so a
                                  rule added during one turn affects
                                  the next without reboot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.platform.ui.permissions_router import create_permissions_router
from runtime.safety.approval.approval_gate import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRule,
    AutoApproveProvider,
    AutoDenyProvider,
)
from runtime.safety.approval.approval_policy_store import (
    append_rule,
    load_policy,
    move_rule,
    save_policy,
)

# ── Store ───────────────────────────────────────────────────────


class TestPolicyStore:
    def test_missing_file_is_empty_policy(self, tmp_path: Path) -> None:
        assert load_policy(tmp_path / "nope.json").rules == ()

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "p.json"
        policy = ApprovalPolicy(
            rules=(
                ApprovalRule(effect="allow", tool="read_*"),
                ApprovalRule(
                    effect="deny",
                    tool="exec_shell",
                    args_contains="rm -rf",
                    reason="dangerous",
                ),
            )
        )
        save_policy(path, policy)
        round_tripped = load_policy(path)
        assert round_tripped == policy

    def test_append_dedupes(self, tmp_path: Path) -> None:
        path = tmp_path / "p.json"
        rule = ApprovalRule(effect="allow", tool="read_*")
        first = append_rule(path, rule)
        second = append_rule(path, rule)
        assert len(first.rules) == 1
        assert len(second.rules) == 1

    def test_append_grows(self, tmp_path: Path) -> None:
        path = tmp_path / "p.json"
        append_rule(path, ApprovalRule(effect="allow", tool="read_*"))
        policy = append_rule(path, ApprovalRule(effect="deny", tool="git_push"))
        assert len(policy.rules) == 2

    def test_corrupt_file_treated_as_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "p.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_policy(path).rules == ()

    def test_unknown_effect_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "p.json"
        path.write_text(
            '{"version": 1, "rules": ['
            '{"effect": "weird", "tool": "x"},'
            '{"effect": "allow", "tool": "ok"}]}',
            encoding="utf-8",
        )
        policy = load_policy(path)
        assert len(policy.rules) == 1
        assert policy.rules[0].tool == "ok"


class TestMoveRule:
    """``move_rule`` lets the UI re-order without forcing a delete +
    re-add round-trip. Order is load-bearing in this rule set: the
    first match wins, so a narrow deny that lives below a broad allow
    can never fire."""

    def _seed(self, tmp_path: Path) -> Path:
        path = tmp_path / "p.json"
        save_policy(
            path,
            ApprovalPolicy(
                rules=(
                    ApprovalRule(effect="allow", tool="a"),
                    ApprovalRule(effect="allow", tool="b"),
                    ApprovalRule(effect="allow", tool="c"),
                )
            ),
        )
        return path

    def test_move_down(self, tmp_path: Path) -> None:
        path = self._seed(tmp_path)
        policy = move_rule(path, 0, 2)
        assert [r.tool for r in policy.rules] == ["b", "c", "a"]
        # Persisted to disk too — not just an in-memory shuffle.
        assert [r.tool for r in load_policy(path).rules] == ["b", "c", "a"]

    def test_move_up(self, tmp_path: Path) -> None:
        path = self._seed(tmp_path)
        policy = move_rule(path, 2, 0)
        assert [r.tool for r in policy.rules] == ["c", "a", "b"]

    def test_move_to_same_position_is_noop(self, tmp_path: Path) -> None:
        path = self._seed(tmp_path)
        before_mtime = path.stat().st_mtime_ns
        policy = move_rule(path, 1, 1)
        assert [r.tool for r in policy.rules] == ["a", "b", "c"]
        # No-op must NOT rewrite the file — atomic-write tmp/rename
        # bumps mtime, which would invalidate any external watcher.
        assert path.stat().st_mtime_ns == before_mtime

    def test_from_index_out_of_range_raises(self, tmp_path: Path) -> None:
        path = self._seed(tmp_path)
        with pytest.raises(IndexError):
            move_rule(path, 99, 0)

    def test_to_index_out_of_range_raises(self, tmp_path: Path) -> None:
        path = self._seed(tmp_path)
        with pytest.raises(IndexError):
            move_rule(path, 0, 99)

    def test_negative_index_raises(self, tmp_path: Path) -> None:
        path = self._seed(tmp_path)
        with pytest.raises(IndexError):
            move_rule(path, -1, 0)
        with pytest.raises(IndexError):
            move_rule(path, 0, -1)


# ── Example file ────────────────────────────────────────────────


def _repo_root() -> Path:
    """Walk up to the repo root the same way capabilities does."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "runtime").is_dir() and (parent / "tests").is_dir():
            return parent
    raise RuntimeError("repo root not found from test file location")


class TestPermissionsExampleFile:
    """The example file ships at the repo root so users can copy it
    into ``data/permissions.json`` (gitignored) and edit there. It
    must (a) be valid JSON, (b) load cleanly via the same path the
    runtime uses, and (c) actually decide the way the comments claim
    it does. Otherwise we're shipping a misleading template."""

    def setup_method(self) -> None:
        self.path = _repo_root() / "permissions.example.json"
        assert self.path.is_file(), f"permissions.example.json missing at repo root: {self.path}"

    def test_is_valid_json_with_v1_schema(self) -> None:
        import json as _json

        raw = _json.loads(self.path.read_text(encoding="utf-8"))
        assert raw.get("version") == 1
        assert isinstance(raw.get("rules"), list)
        assert raw["rules"], "example must ship a non-empty rule list"

    def test_loads_via_load_policy(self) -> None:
        policy = load_policy(self.path)
        # Loader must accept every well-formed rule. Comment-only
        # entries (those without an "effect" key) are silently skipped
        # by ``_rule_from_json`` — we don't ship any of those, so the
        # loaded count must equal the on-disk count.
        import json as _json

        raw = _json.loads(self.path.read_text(encoding="utf-8"))
        assert len(policy.rules) == len(raw["rules"]), (
            "example file has rule entries that fail to parse — either fix them, or drop them"
        )

    def test_decisions_match_documented_intent(self) -> None:
        """Spot-check the load-bearing claims in the example's section
        headers. If someone reorders rules into a state where these
        flip, this test fails before the example reaches users."""
        policy = load_policy(self.path)

        def decide(tool: str, args: str = "") -> tuple[bool, str]:
            decision = policy.decide(
                ApprovalRequest(
                    thread_id="t",
                    tool_name=tool,
                    tool_call_id="c",
                    args_preview=args,
                )
            )
            assert decision is not None, f"no rule matches {tool!r}"
            return decision.approved, decision.reason or ""

        # Read-only filesystem allowed.
        assert decide("read_file")[0] is True
        assert decide("list_cwd")[0] is True
        # Browser reads allowed; live bridge denied.
        assert decide("browser_get")[0] is True
        assert decide("browser_extract")[0] is True
        assert decide("live_browser_click")[0] is False
        assert decide("live_browser_navigate")[0] is False
        # Outbound messaging denied.
        assert decide("send_message")[0] is False
        assert decide("email_send")[0] is False
        assert decide("slack_post")[0] is False
        # Shell · destructive denied, read-only allowed.
        approved, _ = decide("exec_shell", args="rm -rf /tmp")
        assert approved is False
        approved, _ = decide("exec_shell", args="git status")
        assert approved is True
        # Filesystem writes denied.
        assert decide("write_text_file")[0] is False
        assert decide("edit_text_file")[0] is False
        assert decide("delete_file")[0] is False
        # Git mutations denied; reads allowed.
        assert decide("git_status")[0] is True
        assert decide("git_push")[0] is False
        assert decide("git_create_pr")[0] is False
        # Desktop input denied.
        assert decide("mouse_click")[0] is False
        assert decide("keyboard_type")[0] is False
        assert decide("screen_capture")[0] is False
        # Computer-use API · observe + preview allowed; plan + execute denied.
        assert decide("computer_observe")[0] is True
        assert decide("computer_preview_action")[0] is True
        assert decide("computer_plan_next")[0] is False
        assert decide("computer_execute_token")[0] is False

    def test_destructive_deny_fires_before_allow(self) -> None:
        """Order matters: ``rm -rf`` must lose to the deny rule even
        though a later allow on ``git status`` exists. This is the
        single foot-gun the example's comments warn about — pin it."""
        policy = load_policy(self.path)
        decision = policy.decide(
            ApprovalRequest(
                thread_id="t",
                tool_name="exec_shell",
                tool_call_id="c",
                args_preview="git status && rm -rf /tmp",
            )
        )
        assert decision is not None
        assert decision.approved is False
        assert "rm -rf" in (decision.reason or "")


# ── Router ──────────────────────────────────────────────────────


@pytest.fixture
def router_client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_permissions_router(lambda: tmp_path / "perm.json"))
    return TestClient(app)


class TestPermissionsRouter:
    def test_empty_list(self, router_client: TestClient) -> None:
        r = router_client.get("/api/permissions")
        assert r.status_code == 200
        assert r.json() == {"rules": []}

    def test_add_then_list(self, router_client: TestClient) -> None:
        r = router_client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "read_*"},
        )
        assert r.status_code == 200
        assert r.json()["rules"] == [
            {"effect": "allow", "tool": "read_*", "args_contains": "", "reason": ""}
        ]
        listed = router_client.get("/api/permissions").json()
        assert listed["rules"][0]["tool"] == "read_*"

    def test_add_dedupes(self, router_client: TestClient) -> None:
        router_client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "read_*"},
        )
        r = router_client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "read_*"},
        )
        assert len(r.json()["rules"]) == 1

    def test_delete_by_index(self, router_client: TestClient) -> None:
        router_client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "a"},
        )
        router_client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "b"},
        )
        r = router_client.delete("/api/permissions/rules/0")
        assert r.status_code == 200
        assert [rule["tool"] for rule in r.json()["rules"]] == ["b"]

    def test_delete_out_of_range_404(self, router_client: TestClient) -> None:
        r = router_client.delete("/api/permissions/rules/99")
        assert r.status_code == 404

    def test_move_reorders(self, router_client: TestClient) -> None:
        for tool in ("a", "b", "c"):
            router_client.post(
                "/api/permissions/rules",
                json={"effect": "allow", "tool": tool},
            )
        r = router_client.post(
            "/api/permissions/rules/0/move",
            json={"to": 2},
        )
        assert r.status_code == 200
        assert [rule["tool"] for rule in r.json()["rules"]] == ["b", "c", "a"]

    def test_move_out_of_range_404(self, router_client: TestClient) -> None:
        router_client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "a"},
        )
        r = router_client.post(
            "/api/permissions/rules/0/move",
            json={"to": 99},
        )
        assert r.status_code == 404

    def test_move_negative_to_rejected(self, router_client: TestClient) -> None:
        router_client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "a"},
        )
        r = router_client.post(
            "/api/permissions/rules/0/move",
            json={"to": -1},
        )
        # pydantic ge=0 rejects negative ``to``.
        assert r.status_code == 422

    def test_invalid_effect_400(self, router_client: TestClient) -> None:
        r = router_client.post(
            "/api/permissions/rules",
            json={"effect": "maybe", "tool": "x"},
        )
        assert r.status_code == 422  # pydantic Literal rejection

    def test_default_path_uses_app_paths_permissions_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        data_dir = tmp_path / "runtime-data"
        monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
        monkeypatch.delenv("ECHO_HOME", raising=False)

        app = FastAPI()
        app.include_router(create_permissions_router())
        client = TestClient(app)

        r = client.post(
            "/api/permissions/rules",
            json={"effect": "allow", "tool": "read_*"},
        )

        assert r.status_code == 200
        assert (data_dir / "permissions.json").exists()


# ── Runtime wiring ──────────────────────────────────────────────


class TestCerebrumRuntimePolicyHook:
    """``_wrap_with_policy`` is the integration point — verify that
    rules added between turns take effect on the next call without
    rebuilding the runtime."""

    def test_no_policy_path_returns_fallback_unchanged(self) -> None:
        from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

        runtime = CerebrumRuntime.__new__(CerebrumRuntime)
        runtime._policy_path = None  # type: ignore[attr-defined]
        fallback = AutoDenyProvider()
        wrapped = runtime._wrap_with_policy(fallback)  # type: ignore[attr-defined]
        assert wrapped is fallback

    def test_empty_file_returns_fallback_unchanged(self, tmp_path: Path) -> None:
        from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

        runtime = CerebrumRuntime.__new__(CerebrumRuntime)
        runtime._policy_path = tmp_path / "missing.json"  # type: ignore[attr-defined]
        fallback = AutoDenyProvider()
        wrapped = runtime._wrap_with_policy(fallback)  # type: ignore[attr-defined]
        assert wrapped is fallback

    def test_rule_short_circuits_fallback(self, tmp_path: Path) -> None:
        from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

        path = tmp_path / "perm.json"
        save_policy(
            path,
            ApprovalPolicy(rules=(ApprovalRule(effect="allow", tool="read_*"),)),
        )
        runtime = CerebrumRuntime.__new__(CerebrumRuntime)
        runtime._policy_path = path  # type: ignore[attr-defined]
        fallback = AutoDenyProvider()
        wrapped = runtime._wrap_with_policy(fallback)  # type: ignore[attr-defined]

        decision = wrapped.request(
            ApprovalRequest(
                thread_id="t",
                tool_name="read_file",
                tool_call_id="c1",
            )
        )
        assert decision.approved is True

    def test_rules_reload_per_call(self, tmp_path: Path) -> None:
        """The point of reading the file per turn — newly-added rules
        affect subsequent turns without restarting the gateway."""
        from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

        path = tmp_path / "perm.json"
        runtime = CerebrumRuntime.__new__(CerebrumRuntime)
        runtime._policy_path = path  # type: ignore[attr-defined]

        fallback = AutoApproveProvider()
        # Initial: empty policy, fallback wins.
        first = runtime._wrap_with_policy(fallback)  # type: ignore[attr-defined]
        assert first is fallback

        # Append a deny rule; next wrap must layer it in.
        append_rule(
            path,
            ApprovalRule(effect="deny", tool="exec_shell", reason="trial"),
        )
        second = runtime._wrap_with_policy(fallback)  # type: ignore[attr-defined]
        assert second is not fallback
        denied = second.request(
            ApprovalRequest(
                thread_id="t",
                tool_name="exec_shell",
                tool_call_id="c1",
            )
        )
        assert denied.approved is False
        assert denied.reason == "trial"
