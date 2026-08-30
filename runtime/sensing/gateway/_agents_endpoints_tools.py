"""Arms + tool-registry endpoints for the agents router.

Pure structural split of ``_agents_endpoints.py`` — no logic changes.
``_register_agents_tools`` attaches the arms listing and per-agent
tool-registry read/write endpoints to the injected router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from fastapi import HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from ._agents_endpoints_shared import _AuthActions
from ._agents_helpers import (
    _require_real_agent_dir,
    _require_safe_agent_id,
    _restore_text_file,
    _to_detail_wire,
)
from .agents_models import (
    AgentDetailWire,
    ArmOptionWire,
    ToolRegistryWire,
)

if TYPE_CHECKING:
    from ._agents_endpoints import _AgentsCtx


def _register_agents_tools(router: Any, ctx: _AgentsCtx, auth: _AuthActions) -> None:
    registry = ctx.registry
    runtime = ctx.runtime
    _auth = auth.auth
    _require_admin = auth.require_admin

    @router.get("/api/arms")
    def list_arms(request: Request) -> list[ArmOptionWire]:
        _auth(request)  # AUTH-OK: actor-agnostic — arm registry is server-global
        if runtime is None:
            raise HTTPException(
                503,
                "listing arms needs a GraphRuntime in this router",
            )
        from runtime.execution.agents.loader import _ARM_FACTORIES

        out: list[ArmOptionWire] = []
        for arm_id, factory in _ARM_FACTORIES.items():
            try:
                worker = factory(runtime)
            except (TypeError, ValueError, AttributeError):
                continue
            out.append(
                ArmOptionWire(
                    arm_id=arm_id,
                    display_name=getattr(worker, "display_name", "") or "",
                    description=getattr(worker, "description", "") or "",
                    affinity=list(worker.affinity),
                    icon=getattr(worker, "icon", "") or "",
                    skills=[str(s) for s in worker.allowed_skills],
                )
            )
        return out

    @router.get("/api/agents/{agent_id}/tool-registry")
    def get_tool_registry(
        request: Request,
        agent_id: str,
    ) -> ToolRegistryWire:
        _auth(request)  # AUTH-OK: actor-agnostic — tool-registry is read-only
        if "/" in agent_id or "\\" in agent_id or agent_id in ("", ".", ".."):
            raise HTTPException(400, "invalid agent_id")
        from runtime.execution.agents.loader import (
            _parse_jsonc,
            default_agents_root,
        )

        path = default_agents_root() / agent_id / "agent-core" / "tool-registry.jsonc"
        if not path.is_file():
            return ToolRegistryWire()
        try:
            data = _parse_jsonc(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(
                400,
                f"tool-registry parse failed: {type(exc).__name__}: {exc}",
            ) from exc
        return ToolRegistryWire(
            arms=[str(x) for x in (data.get("arms") or [])],
            extra_affinity=[str(x) for x in (data.get("extra_affinity") or [])],
            private_skills=[str(x) for x in (data.get("private_skills") or [])],
        )

    @router.put("/api/agents/{agent_id}/tool-registry")
    def put_tool_registry(
        request: Request,
        agent_id: str,
        body: ToolRegistryWire,
    ) -> AgentDetailWire:
        _require_admin(request)  # Mutation: rewrites agent tool-registry config
        if runtime is None:
            raise HTTPException(
                503,
                "tool-registry edits need a GraphRuntime in this router",
            )
        agent_id = _require_safe_agent_id(agent_id)
        from runtime.execution.agents.loader import (
            _ARM_FACTORIES,
            default_agents_root,
            load_agent,
        )

        root = default_agents_root().resolve()
        agent_dir = _require_real_agent_dir(root, agent_id)
        profile_path = agent_dir / "profile.jsonc"
        if profile_path.is_symlink():
            raise HTTPException(409, f"agent profile is not a real file: {agent_id}")
        if not profile_path.exists():
            raise HTTPException(404, f"agent folder not found: {agent_id}")

        # Validate arms · fail loud on unknown names
        unknown = [a for a in body.arms if a not in _ARM_FACTORIES]
        if unknown:
            raise HTTPException(
                400,
                "unknown arm(s): "
                + ", ".join(sorted(set(unknown)))
                + " · known: "
                + ", ".join(sorted(_ARM_FACTORIES)),
            )

        # Build JSON payload · preserve key order for git-friendly diffs
        import json

        payload = {
            "arms": list(body.arms),
            "extra_affinity": list(body.extra_affinity),
        }
        if body.private_skills:
            payload["private_skills"] = list(body.private_skills)

        core_dir = agent_dir / "agent-core"
        if core_dir.is_symlink():
            raise HTTPException(409, f"agent-core is not a real directory: {agent_id}")
        core_dir.mkdir(parents=True, exist_ok=True)
        target = core_dir / "tool-registry.jsonc"
        if target.is_symlink():
            raise HTTPException(409, f"tool-registry is not a real file: {agent_id}")
        original_tool_registry = target.read_text(encoding="utf-8") if target.is_file() else None

        # Atomic write via shared utility (.bak rotation + fsync)
        from runtime.platform.io import atomic_write_text

        text = (
            "// arms reference factories in "
            "runtime/execution/arms/presets.py.\n"
            "// extra_affinity keywords boost agent matching by topic.\n"
            "// edited via PUT /api/agents/{id}/tool-registry\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        try:
            atomic_write_text(target, text)
        except OSError as exc:
            raise HTTPException(
                500,
                f"tool-registry save failed: {type(exc).__name__}: {exc}",
            ) from exc

        # Hot reload · reuse the same path as POST /api/agents/{id}/reload
        try:
            new_agent = load_agent(agent_dir, runtime, root / "_shared")
        except (OSError, ValueError, TypeError) as exc:
            _restore_text_file(target, original_tool_registry)
            raise HTTPException(
                400,
                f"agent rebuild failed after tool-registry save: {type(exc).__name__}: {exc}",
            ) from exc
        try:
            registry.replace(new_agent)
        except (ValueError, TypeError) as exc:
            _restore_text_file(target, original_tool_registry)
            raise HTTPException(
                500,
                f"agent registry update failed after tool-registry save: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        return _to_detail_wire(new_agent)
