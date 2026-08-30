from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from runtime.sensing.model_router import Message, ModelRequest, ModelRouter

from .computer_skills import (
    PYAUTOGUI_AVAILABLE,
    _keyboard_press,
    _keyboard_type,
    _mouse_click,
    _mouse_move,
    _screen_capture,
)
from .registry import Skill, SkillRegistry

_VALID_ACTIONS = {
    "click",
    "type",
    "key",
    "move",
    "wait",
    "done",
    "fail",
}


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class VisionPlanner(Protocol):
    def next_action(
        self,
        *,
        goal: str,
        screenshot_path: str,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class MockVisionPlanner:
    actions: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def next_action(
        self,
        *,
        goal: str,
        screenshot_path: str,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "goal": goal,
                "screenshot_path": screenshot_path,
                "history_len": len(history),
            }
        )
        if self.calls and len(self.calls) - 1 < len(self.actions):
            return dict(self.actions[len(self.calls) - 1])
        return {"action": "fail", "reason": "mock script exhausted"}


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


_SYSTEM_PROMPT = """You are a desktop-automation planner. You see a screenshot \
and output EXACTLY one action as a JSON object on a single line. No prose.

Allowed schemas:
  {"action":"click","x":INT,"y":INT,"button":"left|right|middle"}
  {"action":"type","text":STRING}
  {"action":"key","keys":["ctrl","c"]}
  {"action":"move","x":INT,"y":INT}
  {"action":"wait","ms":INT}
  {"action":"done","summary":STRING}
  {"action":"fail","reason":STRING}

Prefer "done" when the goal appears satisfied. Prefer "fail" if stuck after \
multiple attempts. Coordinates are 0-indexed pixels from top-left of the \
provided screenshot."""


@dataclass
class ModelRouterVisionPlanner:
    router: ModelRouter
    model: str = "claude-sonnet-4-6"
    system_provider: str = "anthropic"  # anthropic / openai / mock
    max_tokens: int = 512
    system_prompt: str = _SYSTEM_PROMPT
    # Optional semantic-grounding hook. Called fresh each step; its text (e.g.
    # the on-screen window list) is added to the prompt alongside the
    # screenshot. ``None`` or a "" return → pure-pixel behaviour (unchanged).
    grounding: Callable[[], str] | None = None

    def next_action(
        self,
        *,
        goal: str,
        screenshot_path: str,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            image_bytes = Path(screenshot_path).read_bytes()
        except OSError as e:
            return {
                "action": "fail",
                "reason": f"screenshot read failed: {e}",
            }
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")

        history_text = _compact_history(history)
        grounding_text = ""
        if self.grounding is not None:
            try:
                grounding_text = self.grounding() or ""
            except Exception:  # noqa: BLE001 — grounding is best-effort, never fatal
                grounding_text = ""
        grounding_block = f"{grounding_text}\n\n" if grounding_text else ""
        user_text = (
            f"Goal: {goal}\n\n"
            f"{grounding_block}"
            f"Previous actions (most recent last):\n{history_text}\n\n"
            f"Output ONE JSON action."
        )

        request = ModelRequest(
            model=self.model,
            messages=[
                Message(role="system", content=self.system_prompt),
                Message(role="user", content=user_text),
            ],
            max_tokens=self.max_tokens,
            system_provider=self.system_provider,
            images_b64=[b64],
        )

        try:
            resp = self.router.call(request)
        except Exception as e:  # noqa: BLE001
            return {
                "action": "fail",
                "reason": f"router call failed: {type(e).__name__}: {e}",
            }

        return _parse_action_text(resp.text)


def _compact_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return "(none)"
    tail = history[-10:]
    lines = []
    for i, h in enumerate(tail):
        action = h.get("action", {}).get("action", "?")
        result_summary = h.get("result_summary", "")
        lines.append(f"{i + 1}. {action} → {result_summary}")
    return "\n".join(lines)


def _parse_action_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if "```" in text:
        try:
            after = text.split("```", 1)[1]
            if "\n" in after:
                after = after.split("\n", 1)[1]
            text = after.split("```", 1)[0].strip()
        except IndexError:  # noqa: BLE001 — empty list edge case; benign
            pass
    if "{" in text and "}" in text:
        text = text[text.index("{") : text.rindex("}") + 1]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {"action": "fail", "reason": f"planner JSON parse failed: {text[:200]}"}
    if not isinstance(obj, dict) or "action" not in obj:
        return {"action": "fail", "reason": "planner output missing 'action'"}
    return obj


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _run_computer_use_loop(
    goal: str,
    planner: VisionPlanner,
    *,
    screenshot_dir: str,
    sandbox_dir: str | None,
    max_iterations: int,
    wait_between_ms: int,
    stop_on_error: bool,
    capture_screen: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    screenshots_saved: list[str] = []

    shot_dir = Path(screenshot_dir)
    shot_dir.mkdir(parents=True, exist_ok=True)

    for iteration in range(max_iterations):
        requested_shot_path = str(shot_dir / f"iter_{iteration:03d}.png")
        cap = (capture_screen or _screen_capture)(
            path=requested_shot_path,
            sandbox_dir=sandbox_dir,
        )
        if "error" in cap:
            return _final(
                "error",
                goal,
                history,
                screenshots_saved,
                reason=f"screenshot failed: {cap['error']}",
                iterations=iteration,
            )
        # ``path_guard`` may resolve a relative request into a sandboxed,
        # absolute destination. The capture result is authoritative: passing
        # the original relative name to the vision planner made it look in the
        # process cwd and fail with ``iter_000.png`` not found even though the
        # screenshot had been saved successfully elsewhere.
        shot_path = str(cap.get("path") or requested_shot_path)
        if not Path(shot_path).is_file():
            return _final(
                "error",
                goal,
                history,
                screenshots_saved,
                reason=(
                    "screenshot capture returned no readable artifact: "
                    f"requested={requested_shot_path!r}, captured={shot_path!r}"
                ),
                iterations=iteration,
            )
        screenshots_saved.append(shot_path)

        try:
            action = planner.next_action(
                goal=goal,
                screenshot_path=shot_path,
                history=list(history),
            )
        except Exception as e:  # noqa: BLE001
            return _final(
                "error",
                goal,
                history,
                screenshots_saved,
                reason=f"planner raised: {type(e).__name__}: {e}",
                iterations=iteration,
            )

        if not isinstance(action, dict) or "action" not in action:
            return _final(
                "error",
                goal,
                history,
                screenshots_saved,
                reason="planner returned malformed action",
                iterations=iteration,
            )

        kind = action.get("action")
        if kind not in _VALID_ACTIONS:
            return _final(
                "error",
                goal,
                history,
                screenshots_saved,
                reason=f"unknown action: {kind!r}",
                iterations=iteration,
            )

        if kind == "done":
            history.append({"action": action, "result_summary": "done"})
            return _final(
                "success",
                goal,
                history,
                screenshots_saved,
                summary=str(action.get("summary", "")),
                iterations=iteration + 1,
            )

        if kind == "fail":
            history.append({"action": action, "result_summary": "fail"})
            return _final(
                "planner_gave_up",
                goal,
                history,
                screenshots_saved,
                reason=str(action.get("reason", "")),
                iterations=iteration + 1,
            )

        result = _dispatch_action(action)
        summary = _summarize_action_result(action, result)
        history.append(
            {
                "action": action,
                "result": result,
                "result_summary": summary,
            }
        )

        if "error" in result and stop_on_error:
            return _final(
                "error",
                goal,
                history,
                screenshots_saved,
                reason=f"action failed: {summary}",
                iterations=iteration + 1,
            )

        if wait_between_ms > 0:
            time.sleep(wait_between_ms / 1000.0)

    return _final(
        "max_iterations",
        goal,
        history,
        screenshots_saved,
        reason=f"reached max_iterations={max_iterations}",
        iterations=max_iterations,
    )


def _dispatch_action(action: dict[str, Any]) -> dict[str, Any]:
    kind = action["action"]
    if kind == "click":
        return _mouse_click(
            x=int(action.get("x", -1)),
            y=int(action.get("y", -1)),
            button=str(action.get("button", "left")),
            clicks=int(action.get("clicks", 1)),
        )
    if kind == "type":
        return _keyboard_type(text=str(action.get("text", "")))
    if kind == "key":
        keys = action.get("keys")
        if not isinstance(keys, list):
            return {"error": "key action requires list 'keys'"}
        return _keyboard_press(keys=keys)
    if kind == "move":
        return _mouse_move(
            x=int(action.get("x", -1)),
            y=int(action.get("y", -1)),
        )
    if kind == "wait":
        ms = int(action.get("ms", 0))
        if ms < 0 or ms > 60_000:
            return {"error": f"wait ms out of range: {ms}"}
        time.sleep(ms / 1000.0)
        return {"waited_ms": ms}
    return {"error": f"unreachable action kind: {kind!r}"}


def _summarize_action_result(
    action: dict[str, Any],
    result: dict[str, Any],
) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    kind = action["action"]
    if kind == "click":
        return f"clicked ({action.get('x')},{action.get('y')})"
    if kind == "type":
        return f"typed {len(action.get('text', ''))} chars"
    if kind == "key":
        return f"pressed {action.get('keys')}"
    if kind == "move":
        return f"moved to ({action.get('x')},{action.get('y')})"
    if kind == "wait":
        return f"waited {action.get('ms')}ms"
    return "ok"


def _final(
    status: str,
    goal: str,
    history: list[dict[str, Any]],
    screenshots: list[str],
    *,
    summary: str = "",
    reason: str = "",
    iterations: int = 0,
) -> dict[str, Any]:
    return {
        "status": status,
        "goal": goal,
        "iterations": iterations,
        "summary": summary,
        "reason": reason,
        "actions_taken": [h["action"] for h in history],
        "screenshots": screenshots,
    }


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def make_computer_use_loop_skill(
    planner: VisionPlanner,
    *,
    journal: Any = None,
    default_screenshot_dir: str = ".",
    default_sandbox_dir: str | None = None,
    default_max_iterations: int = 10,
    default_wait_between_ms: int = 300,
    default_stop_on_error: bool = False,
) -> Skill:

    def _handler(
        goal: str = "",
        *,
        screenshot_dir: str = default_screenshot_dir,
        sandbox_dir: str | None = default_sandbox_dir,
        max_iterations: int = default_max_iterations,
        wait_between_ms: int = default_wait_between_ms,
        stop_on_error: bool = default_stop_on_error,
        **_kw: Any,
    ) -> dict[str, Any]:
        if not goal:
            return {"error": "missing goal"}
        if not PYAUTOGUI_AVAILABLE:
            return {"error": "pyautogui not installed"}
        if max_iterations <= 0 or max_iterations > 200:
            return {"error": f"max_iterations out of range: {max_iterations}"}
        result = _run_computer_use_loop(
            goal=goal,
            planner=planner,
            screenshot_dir=screenshot_dir,
            sandbox_dir=sandbox_dir,
            max_iterations=max_iterations,
            wait_between_ms=wait_between_ms,
            stop_on_error=stop_on_error,
        )
        # Record loop outcomes (best-effort; never break the live loop).
        # Success → a journal Trajectory SkillForge can distil into an
        # (immune-gated) macro. Failure → a review-queue case the
        # browser-desktop repair-recipe pipeline clusters into a repair.
        if journal is not None:
            from runtime.execution.suckers.computer_use_record import (
                record_failed_loop,
                record_successful_loop,
            )

            if result.get("status") == "success":
                record_successful_loop(journal, result)
            else:
                record_failed_loop(result)
        return result

    return Skill(
        name="computer_use_loop",
        description=(
            "Autonomous perception-action loop for desktop automation. "
            "Screenshots → vision planner → action → verify. "
            "Requires vision-capable planner + pyautogui."
        ),
        affinity=["desktop", "autonomous", "loop", "dangerous"],
        cost_profile="high",
        trusted_source="skill://public/computer_use_loop",
        handler=_handler,
        tests=[],  # Implementation note.
    )


def register_computer_use_loop(
    registry: SkillRegistry,
    planner: VisionPlanner,
    **kwargs: Any,
) -> int:
    skill = make_computer_use_loop_skill(planner, **kwargs)
    registry.register(skill, verify_tests=False)
    return 1
