"""Local CLI partner discovery and registration endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from ._agents_helpers import _to_detail_wire
from .agents_local_partner import (
    LOCAL_PARTNER_SPECS,
    safe_executable,
    to_wire,
    validate_alias,
    which_command,
    write_partner_agent,
)
from .agents_models import (
    LocalPartnerRegisterRequest,
    LocalPartnerRegisterResponse,
    LocalPartnerRegisterResult,
    LocalPartnerWire,
)

_which_local_partner_command = which_command
_safe_local_partner_executable = safe_executable
_validate_local_partner_alias = validate_alias


def _register_local_partners(router: Any, ctx: Any, auth: Any) -> None:
    registry = ctx.registry

    @router.get("/api/agents/local-partners")
    def list_local_partners(request: Request) -> dict[str, list[LocalPartnerWire]]:
        auth.auth(request)
        return {
            "partners": [
                to_wire(spec, registry, which_fn=_which_local_partner_command)
                for spec in LOCAL_PARTNER_SPECS.values()
            ]
        }

    @router.post("/api/agents/local-partners/register")
    def register_local_partners(
        request: Request,
        body: LocalPartnerRegisterRequest,
    ) -> LocalPartnerRegisterResponse:
        auth.require_admin(request)
        if ctx.runtime is None:
            raise HTTPException(503, "local partner registration needs a GraphRuntime")
        if not body.partners:
            raise HTTPException(400, "at least one local partner is required")

        aliases: dict[str, str] = {}
        for item in body.partners:
            try:
                aliases[item.id] = _validate_local_partner_alias(item.alias)
            except ValueError as exc:
                raise HTTPException(400, f"{item.id}: {exc}") from exc

        results: list[LocalPartnerRegisterResult] = []
        registered = already_exists = skipped = 0
        for item in body.partners:
            spec = LOCAL_PARTNER_SPECS.get(item.id)
            if spec is None:
                skipped += 1
                results.append(
                    LocalPartnerRegisterResult(
                        id=item.id,
                        agent_id="",
                        status="error",
                        message=f"unknown local partner: {item.id}",
                    )
                )
                continue

            agent_id = str(spec["agent_id"])
            if registry.has(agent_id):
                already_exists += 1
                results.append(
                    LocalPartnerRegisterResult(
                        id=str(spec["id"]),
                        agent_id=agent_id,
                        status="already_exists",
                        message="already registered",
                        agent=_to_detail_wire(registry.get(agent_id)),
                    )
                )
                continue

            command, executable = _which_local_partner_command(list(spec["commands"]))
            if not command or not executable:
                skipped += 1
                results.append(
                    LocalPartnerRegisterResult(
                        id=str(spec["id"]),
                        agent_id=agent_id,
                        status="not_detected",
                        message="local executable was not found on PATH",
                    )
                )
                continue
            if not _safe_local_partner_executable(executable):
                skipped += 1
                results.append(
                    LocalPartnerRegisterResult(
                        id=str(spec["id"]),
                        agent_id=agent_id,
                        status="error",
                        message=f"refusing executable from a user-writable location: {executable}",
                    )
                )
                continue

            try:
                agent = write_partner_agent(
                    spec=spec,
                    alias=aliases[item.id] or str(spec["default_alias"]),
                    command=command,
                    executable=executable,
                    runtime=ctx.runtime,
                    registry=registry,
                )
            except (OSError, TypeError, ValueError) as exc:
                skipped += 1
                results.append(
                    LocalPartnerRegisterResult(
                        id=str(spec["id"]),
                        agent_id=agent_id,
                        status="error",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            registered += 1
            results.append(
                LocalPartnerRegisterResult(
                    id=str(spec["id"]),
                    agent_id=agent_id,
                    status="registered",
                    message="registered",
                    agent=_to_detail_wire(agent),
                )
            )

        return LocalPartnerRegisterResponse(
            results=results,
            registered_count=registered,
            already_exists_count=already_exists,
            skipped_count=skipped,
        )
