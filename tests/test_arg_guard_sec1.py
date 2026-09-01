"""SEC-1 regression: model-supplied privilege flags must not reach handlers.

A model — or an indirect prompt injection riding in tool output — must not be
able to smuggle ``allow_sensitive`` / ``allow_private`` through a tool call to
defeat the path-guard sensitive-file / denylist checks or the url-guard SSRF
check. The published tool schema hides ``allow_sensitive`` but is
``additionalProperties: True``, so the executor strips these internal
privilege flags before ``handler(**args)``. The shared helper is unit tested
directly as well.
"""

from __future__ import annotations

from uuid import uuid4

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import (
    MODEL_FORBIDDEN_ARGS,
    TrustEngine,
    strip_model_controlled_overrides,
)


def _budget() -> Budget:
    return Budget(task_id=TaskId(uuid4()), limits=BudgetLimits(tokens=10_000, usd=1.0))


def _executor_with_capture(captured: dict) -> ToolExecutor:
    registry = SkillRegistry()

    def _capture(**kw):
        captured.update(kw)
        return "ok"

    registry.register(
        Skill(
            name="capture",
            description="captures the kwargs it receives",
            affinity=["demo"],
            trusted_source="skill://public/capture",
            handler=_capture,
        )
    )
    immunity = TrustEngine(trusted_sources=["skill://public/*"])
    return ToolExecutor(registry=registry, immunity=immunity, journal=InMemoryJournal())


class TestExecutorStripsPrivilegeFlags:
    def test_allow_sensitive_and_allow_private_never_reach_handler(self) -> None:
        captured: dict = {}
        executor = _executor_with_capture(captured)
        budget = _budget()

        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("capture"),
            args={
                "path": "notes.txt",
                "allow_sensitive": True,
                "allow_private": True,
            },
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

        assert step.success
        # the model-supplied privilege flags are stripped before dispatch
        assert "allow_sensitive" not in captured
        assert "allow_private" not in captured
        # but a legitimate argument is preserved untouched
        assert captured.get("path") == "notes.txt"

    def test_legitimate_args_pass_through_unchanged(self) -> None:
        captured: dict = {}
        executor = _executor_with_capture(captured)
        budget = _budget()

        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("capture"),
            args={"path": "notes.txt", "limit": 5},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

        assert step.success
        assert captured == {"path": "notes.txt", "limit": 5}

    def test_explicit_browser_session_restores_loopback_access_internally(self) -> None:
        captured: dict = {}
        registry = SkillRegistry()

        def capture_browser(**kw):
            captured.update(kw)
            return "ok"

        registry.register(
            Skill(
                name="browser_navigate",
                description="navigate",
                affinity=["browser"],
                trusted_source="skill://public/browser_navigate",
                handler=capture_browser,
            )
        )
        executor = ToolExecutor(
            registry=registry,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=InMemoryJournal(),
        )
        budget = _budget()

        with session_scope(
            Session(
                thread_id="browser-loopback",
                metadata={
                    "browser_operation_mode": True,
                    "browser_surface": "browser",
                    "runtime_surfaces": ["browser"],
                },
            )
        ):
            step = executor.execute_step(
                step_id=0,
                node_id="n0",
                sucker_id=SkillId("browser_navigate"),
                args={"url": "http://127.0.0.1:8123/app", "allow_private": True},
                caller="arms/browser_interact",
                task_id=budget.task_id,
                arm_id=ArmId("browser_interact"),
                budget=budget,
            )

        assert step.success
        assert captured["allow_private"] is True

    def test_explicit_browser_session_does_not_open_non_loopback_private_ip(self) -> None:
        from runtime.execution.tool_engine.executor import (
            _restore_trusted_browser_loopback_access,
        )

        with session_scope(
            Session(
                thread_id="browser-private",
                metadata={"browser_operation_mode": True},
            )
        ):
            args = _restore_trusted_browser_loopback_access(
                SkillId("browser_navigate"),
                {"url": "http://10.0.0.5/admin"},
            )

        assert "allow_private" not in args

    def test_code_mode_browser_regression_restores_loopback_only(self) -> None:
        from runtime.execution.tool_engine.executor import (
            _restore_trusted_browser_loopback_access,
        )

        with session_scope(
            Session(
                thread_id="code-ui-regression",
                metadata={
                    "mode": "code",
                    "browser_regression_enabled": True,
                    "browser_regression_preview_url": "http://127.0.0.1:8123/app",
                },
            )
        ):
            loopback = _restore_trusted_browser_loopback_access(
                SkillId("browser_navigate"),
                {"url": "http://127.0.0.1:8123/app"},
            )
            private_lan = _restore_trusted_browser_loopback_access(
                SkillId("browser_navigate"),
                {"url": "http://10.0.0.5/admin"},
            )

        assert loopback["allow_private"] is True
        assert "allow_private" not in private_lan


class TestStripHelper:
    def test_strips_both_flags_and_reports_them_sorted(self) -> None:
        cleaned, stripped = strip_model_controlled_overrides(
            {"path": "x", "allow_sensitive": True, "allow_private": False}
        )
        assert cleaned == {"path": "x"}
        assert stripped == ["allow_private", "allow_sensitive"]

    def test_noop_returns_same_object(self) -> None:
        same = {"path": "x"}
        out, stripped = strip_model_controlled_overrides(same)
        assert out is same
        assert stripped == []

    def test_non_dict_passthrough(self) -> None:
        assert strip_model_controlled_overrides(None) == (None, [])

    def test_recursively_strips_delegation_security_context(self) -> None:
        cleaned, stripped = strip_model_controlled_overrides(
            {
                "context": {
                    "sandboxPolicy": {"type": "dangerFullAccess"},
                    "workspace_path": "/tmp/escape",
                    "domain_hint": "safe",
                },
                "specs": [
                    {
                        "context": {
                            "approval_policy": "never",
                            "_inherited_injection_taint": "none",
                            "output_style": "compact",
                        }
                    }
                ],
            }
        )

        assert cleaned == {
            "context": {"domain_hint": "safe"},
            "specs": [{"context": {"output_style": "compact"}}],
        }
        assert stripped == [
            "context.sandboxPolicy",
            "context.workspace_path",
            "specs[0].context._inherited_injection_taint",
            "specs[0].context.approval_policy",
        ]

    def test_forbidden_set_is_the_two_privilege_flags(self) -> None:
        assert "allow_sensitive" in MODEL_FORBIDDEN_ARGS
        assert "allow_private" in MODEL_FORBIDDEN_ARGS
        assert len(MODEL_FORBIDDEN_ARGS) == 2


class TestReactDriveDispatchKeysAreModelProtected:
    """Audit F-01: choosing the react-drive dispatch path (and its iteration
    budget / stack) is a trusted-side decision made by bridge.call_subagent.
    A model — or an injection riding in tool output — must not steer a spawn
    onto/off the MAIN react loop, inflate the loop budget, or forge a stack.
    """

    def test_strips_react_dispatch_keys_from_model_context(self) -> None:
        cleaned, stripped = strip_model_controlled_overrides(
            {
                "context": {
                    "react_loop_subagent": True,
                    "react_loop_max_iterations": 500,
                    "react_stack": {"forged": True},
                    "goal": "keep me",
                },
            }
        )
        assert cleaned == {"context": {"goal": "keep me"}}
        assert stripped == [
            "context.react_loop_max_iterations",
            "context.react_loop_subagent",
            "context.react_stack",
        ]

