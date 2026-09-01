"""Opt-in, read-only shadow reviews on bounded workspace snapshots."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.safety.evolution.candidate_registry import CandidateRegistry, CandidateStatus

MAX_SNAPSHOT_FILES = 5_000
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024
IGNORED_NAMES = frozenset(
    {
        ".git",
        ".env",
        ".venv",
        "__pycache__",
        "data",
        "dist",
        "build",
        "node_modules",
        ".next",
        ".cache",
        ".pytest_cache",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ShadowRun:
    run_id: str
    goal: str
    primary_engine: str
    shadow_engine: str
    status: str
    created_at: str
    updated_at: str
    source_thread_id: str | None = None
    source_message_id: str | None = None
    candidate_id: str | None = None
    experiment_id: str | None = None
    workspace_snapshot: str | None = None
    result: str | None = None
    verdict: str | None = None
    hard_gates: dict[str, bool] | None = None
    evidence: list[str] | None = None
    recommendations: list[str] | None = None
    candidate_transition_error: str | None = None
    error: str | None = None


ShadowRunner = Callable[[str, Path, str], Awaitable[str]]


class DualHelixShadowService:
    def __init__(
        self,
        state_path: Path | str,
        snapshot_root: Path | str,
        *,
        allowed_workspace_root: Path | str,
        codex_runner: ShadowRunner | None = None,
        native_runner: ShadowRunner | None = None,
        candidate_registry: CandidateRegistry | None = None,
    ) -> None:
        self._state_path = Path(state_path).resolve(strict=False)
        self._snapshot_root = Path(snapshot_root).resolve(strict=False)
        self._allowed_root = Path(allowed_workspace_root).resolve(strict=True)
        self._codex_runner = codex_runner
        self._native_runner = native_runner
        self._candidate_registry = candidate_registry
        self._lock = threading.RLock()
        self._tasks: set[asyncio.Task[None]] = set()

    def status(self) -> dict[str, Any]:
        state = self._read()
        runs = list(state.get("runs") or [])[-20:]
        runs.reverse()
        return {
            "ok": True,
            "schema": "echo.dual_helix_shadow.v1",
            "enabled": bool(state.get("enabled", False)),
            "isolation": "bounded_snapshot_read_only",
            "runs": runs,
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        state = self._read()
        state["enabled"] = bool(enabled)
        self._write(state)
        return self.status()

    def queue(
        self,
        *,
        goal: str,
        primary_engine: str,
        primary_output: str,
        workspace_path: str | None = None,
        source_thread_id: str | None = None,
        source_message_id: str | None = None,
        candidate_id: str | None = None,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        state = self._read()
        if not state.get("enabled"):
            raise PermissionError("dual-helix shadow mode is disabled")
        if primary_engine not in {"echo", "codex"}:
            raise ValueError("primary engine must be echo or codex")
        workspace = self._resolve_workspace(workspace_path)
        resolved_candidate_id = (candidate_id or "").strip() or None
        if resolved_candidate_id and self._candidate_registry is not None:
            candidate = self._candidate_registry.get(resolved_candidate_id)
            if candidate is None:
                raise ValueError(f"unknown evolution candidate: {resolved_candidate_id}")
            if candidate.status != CandidateStatus.VALIDATED:
                raise ValueError("candidate must be validated before structured shadow review")
        run = ShadowRun(
            run_id=f"shadow_{uuid4().hex[:16]}",
            goal=goal.strip(),
            primary_engine=primary_engine,
            shadow_engine="codex" if primary_engine == "echo" else "echo",
            status="queued",
            created_at=_now(),
            updated_at=_now(),
            source_thread_id=(source_thread_id or "").strip() or None,
            source_message_id=(source_message_id or "").strip() or None,
            candidate_id=resolved_candidate_id,
            experiment_id=(experiment_id or "").strip() or None,
        )
        state.setdefault("runs", []).append(asdict(run))
        state["runs"] = state["runs"][-100:]
        self._write(state)
        task = asyncio.create_task(
            self._execute(run, workspace, primary_output),
            name=f"dual-helix-{run.run_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return asdict(run)

    async def _execute(self, run: ShadowRun, workspace: Path, primary_output: str) -> None:
        try:
            self._update(run.run_id, status="snapshotting")
            snapshot = await asyncio.to_thread(
                materialize_shadow_snapshot,
                workspace,
                self._snapshot_root / run.run_id / "workspace",
            )
            self._update(
                run.run_id,
                status="running",
                workspace_snapshot=str(snapshot),
            )
            runner = self._codex_runner if run.shadow_engine == "codex" else self._native_runner
            if runner is None:
                raise RuntimeError(f"{run.shadow_engine} shadow runner is unavailable")
            result = await runner(run.goal, snapshot, primary_output)
            review = parse_shadow_review(result)
            transition_error = self._record_candidate_review(run, review)
            self._update(
                run.run_id,
                status="completed",
                result=result[:50_000],
                verdict=review["verdict"],
                hard_gates=review["hard_gates"],
                evidence=review["evidence"],
                recommendations=review["recommendations"],
                candidate_transition_error=transition_error,
            )
        except Exception as exc:  # noqa: BLE001 - persisted bounded failure
            self._update(run.run_id, status="failed", error=str(exc)[:500])

    def _resolve_workspace(self, raw: str | None) -> Path:
        candidate = Path(raw).expanduser() if raw else self._allowed_root
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self._allowed_root)
        except ValueError as exc:
            raise ValueError("shadow workspace is outside the allowed project root") from exc
        if not resolved.is_dir():
            raise ValueError("shadow workspace must be a directory")
        return resolved

    def _read(self) -> dict[str, Any]:
        with self._lock:
            try:
                value = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"enabled": False, "runs": []}
            return value if isinstance(value, dict) else {"enabled": False, "runs": []}

    def _write(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._state_path.parent,
                prefix=f".{self._state_path.name}.",
                delete=False,
            ) as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                temp = Path(handle.name)
            os.chmod(temp, 0o600)
            os.replace(temp, self._state_path)

    def _update(self, run_id: str, **changes: Any) -> None:
        state = self._read()
        for row in state.get("runs") or []:
            if row.get("run_id") == run_id:
                row.update(changes)
                row["updated_at"] = _now()
                break
        self._write(state)

    def _record_candidate_review(self, run: ShadowRun, review: dict[str, Any]) -> str | None:
        registry = self._candidate_registry
        if registry is None or not run.candidate_id:
            return None
        try:
            candidate = registry.get(run.candidate_id)
            if candidate is None:
                raise KeyError(f"unknown evolution candidate: {run.candidate_id}")
            required = {"correctness", "verification", "safety", "task_satisfied"}
            review_gates = dict(review.get("hard_gates") or {})
            passed = (
                review.get("verdict") == "pass"
                and required.issubset(review_gates)
                and all(bool(review_gates[name]) for name in required)
            )
            metadata = {
                "shadow_run_id": run.run_id,
                "shadow_verdict": review.get("verdict"),
                "shadow_evidence": list(review.get("evidence") or []),
                "shadow_recommendations": list(review.get("recommendations") or []),
            }
            experiment_ids = list(candidate.experiment_ids)
            if run.experiment_id:
                experiment_ids.append(run.experiment_id)
            if passed:
                merged_gates = dict(candidate.hard_gate_results)
                merged_gates.update(
                    {f"shadow_{key}": bool(value) for key, value in review_gates.items()}
                )
                registry.transition(
                    candidate.candidate_id,
                    CandidateStatus.SHADOW,
                    hard_gate_results=merged_gates,
                    experiment_ids=list(dict.fromkeys(experiment_ids)),
                    metadata=metadata,
                )
            else:
                registry.transition(
                    candidate.candidate_id,
                    CandidateStatus.REJECTED,
                    experiment_ids=list(dict.fromkeys(experiment_ids)),
                    metadata=metadata,
                )
            return None
        except (KeyError, TypeError, ValueError) as exc:
            return str(exc)[:500]


def materialize_shadow_snapshot(source: Path, destination: Path) -> Path:
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if destination.exists():
        raise FileExistsError("shadow snapshot already exists")
    destination.mkdir(parents=True, exist_ok=False)
    files = total_bytes = 0
    try:
        for root, dirnames, filenames in os.walk(source, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in IGNORED_NAMES and not (Path(root) / name).is_symlink()
            ]
            relative = Path(root).relative_to(source)
            target_dir = destination / relative
            target_dir.mkdir(parents=True, exist_ok=True)
            for name in filenames:
                src = Path(root) / name
                if name in IGNORED_NAMES or src.is_symlink():
                    continue
                size = src.stat().st_size
                files += 1
                total_bytes += size
                if files > MAX_SNAPSHOT_FILES or total_bytes > MAX_SNAPSHOT_BYTES:
                    raise ValueError("workspace exceeds the bounded shadow snapshot budget")
                shutil.copy2(src, target_dir / name)
        return destination
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def build_codex_shadow_runner(stack: Any, agent_registry: Any) -> ShadowRunner:
    async def _run(goal: str, workspace: Path, primary_output: str) -> str:
        from runtime.execution.codex_backend.role_runner import run_agent_role

        if agent_registry is None or not agent_registry.has("coder"):
            raise RuntimeError("Coder role is unavailable")
        agent = agent_registry.get("coder")
        prompt = _review_prompt(goal, primary_output)
        result = await run_agent_role(
            stack,
            agent,
            prompt,
            context={
                "workspace_path": str(workspace),
                "workspace_contract": "audit_read_only",
                "tool_allowlist_read_only": True,
                "sandbox_policy": {"type": "readOnly", "networkAccess": False},
                "timeout_s": 600,
            },
        )
        if not result.success and not result.output:
            raise RuntimeError(f"Codex shadow review failed: {result.status}")
        return result.output

    return _run


def build_native_shadow_runner(stack: Any) -> ShadowRunner:
    async def _run(goal: str, workspace: Path, primary_output: str) -> str:
        from runtime.platform.models.llm import Message, ModelRequest

        router = getattr(getattr(stack, "planner", None), "router", None)
        if router is None or not callable(getattr(router, "call", None)):
            raise RuntimeError("Echo model router is unavailable")
        model = str(
            getattr(router, "default_model", None)
            or getattr(getattr(getattr(stack, "config", None), "planner", None), "model", None)
            or ""
        ).strip()
        if not model:
            raise RuntimeError("Echo shadow model is unavailable")
        manifest = await asyncio.to_thread(_snapshot_manifest, workspace)
        prompt = _review_prompt(goal, primary_output, manifest=manifest)

        def _call() -> str:
            response = router.call(
                ModelRequest(
                    model=model,
                    messages=[Message(role="user", content=prompt)],
                    max_tokens=1_200,
                    temperature=0.0,
                )
            )
            return str(response.text or "").strip()

        result = await asyncio.to_thread(_call)
        if not result:
            raise RuntimeError("Echo shadow review returned no result")
        return result

    return _run


def _snapshot_manifest(workspace: Path, *, limit: int = 300) -> str:
    rows: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            rows.append(str(path.relative_to(workspace)))
            if len(rows) >= limit:
                break
    return "\n".join(rows)


def _review_prompt(goal: str, primary_output: str, *, manifest: str = "") -> str:
    return (
        "You are the read-only shadow reviewer in a dual-engine evolution experiment. "
        "Do not edit files, run network actions, or request approvals. Evaluate correctness, "
        "missing verification, safety, and whether the result satisfies the task. Return ONLY "
        "one JSON object with this schema: "
        '{"verdict":"pass|fail|inconclusive","hard_gates":{"correctness":true,'
        '"verification":true,"safety":true,"task_satisfied":true},'
        '"evidence":["short evidence"],"recommendations":["short action"]}. '
        "A missing proof must make the relevant gate false; do not infer success.\n\n"
        f"TASK:\n{goal}\n\nPRIMARY ENGINE OUTPUT:\n{primary_output or '(not supplied)'}"
        + (f"\n\nISOLATED SNAPSHOT FILE MANIFEST:\n{manifest}" if manifest else "")
    )


def parse_shadow_review(value: str) -> dict[str, Any]:
    """Normalize new structured reviews and legacy PASS/FAIL text."""

    text = str(value or "").strip()
    candidate = text
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(
            lines[1:-1] if lines and lines[-1].strip().startswith("```") else lines[1:]
        ).strip()
    try:
        raw = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        try:
            raw = json.loads(candidate[start : end + 1]) if start >= 0 and end > start else None
        except json.JSONDecodeError:
            raw = None

    if isinstance(raw, dict):
        verdict = str(raw.get("verdict") or "inconclusive").strip().lower()
        if verdict not in {"pass", "fail", "inconclusive"}:
            verdict = "inconclusive"
        raw_gates = raw.get("hard_gates")
        gates = (
            {str(key): bool(item) for key, item in raw_gates.items()}
            if isinstance(raw_gates, dict)
            else {}
        )
        evidence = [str(item)[:1_000] for item in raw.get("evidence") or [] if str(item).strip()]
        recommendations = [
            str(item)[:1_000] for item in raw.get("recommendations") or [] if str(item).strip()
        ]
        # A model cannot claim PASS while any declared hard gate is false.
        if verdict == "pass" and (not gates or not all(gates.values())):
            verdict = "fail"
        return {
            "verdict": verdict,
            "hard_gates": gates,
            "evidence": evidence[:20],
            "recommendations": recommendations[:20],
        }

    upper = text.lstrip().upper()
    verdict = (
        "pass"
        if upper.startswith("PASS")
        else "fail"
        if upper.startswith("FAIL")
        else "inconclusive"
    )
    return {
        "verdict": verdict,
        "hard_gates": {"legacy_review_verdict": verdict == "pass"},
        "evidence": [text[:1_000]] if text else [],
        "recommendations": [],
    }


__all__ = [
    "DualHelixShadowService",
    "ShadowRun",
    "build_codex_shadow_runner",
    "build_native_shadow_runner",
    "materialize_shadow_snapshot",
    "parse_shadow_review",
]
