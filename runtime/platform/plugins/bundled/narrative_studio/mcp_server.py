"""Candidate-only MCP JSON-RPC surface for Narrative Studio.

This is a stable HTTP JSON-RPC endpoint rather than an independently managed
MCP process.  Authentication remains the host application's responsibility;
the route passes only the server-resolved principal into this allowlisted tool
surface.  No governance mutation or direct canon-promotion tool is exposed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import ChapterCreate, ContextPackBuildRequest
from .store import NarrativeStore, NarrativeStoreError

try:
    from mcp.types import LATEST_PROTOCOL_VERSION
except ImportError:  # pragma: no cover - minimal source installs without the MCP extra
    LATEST_PROTOCOL_VERSION = "2026-07-28"

_LOG = logging.getLogger(__name__)
CANON_POLICY = "candidate_only"
MCP_ENDPOINT = "/api/plugins/narrative-studio/mcp"
SERVER_NAME = "echo-narrative-studio"
SERVER_VERSION = "0.2.0"


class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _NoArguments(_StrictArguments):
    pass


class _ProjectArguments(_StrictArguments):
    project_id: str = Field(min_length=1, max_length=80)


class _ListChaptersArguments(_ProjectArguments):
    branch_id: str | None = Field(default=None, min_length=1, max_length=80)


class _BuildContextArguments(_ProjectArguments):
    id: str | None = None
    branch_id: str = Field(min_length=1, max_length=80)
    target_chapter_id: str | None = Field(default=None, min_length=1, max_length=80)
    label: str = Field(default="", max_length=240)
    max_chars: int | None = Field(default=None, ge=256, le=2_000_000)
    max_items: int | None = Field(default=None, ge=1, le=10_000)


class _CreateChapterArguments(_ProjectArguments):
    id: str | None = None
    branch_id: str = Field(min_length=1, max_length=80)
    ordinal: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=30_000)
    body: str = Field(default="", max_length=2_000_000)
    status: str = "draft"


@dataclass(frozen=True)
class _ToolDefinition:
    name: str
    title: str
    description: str
    arguments_model: type[_StrictArguments]
    handler: Callable[[_StrictArguments, str | None], Any]
    read_only: bool
    idempotent: bool

    def mcp_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.arguments_model.model_json_schema(),
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "idempotentHint": self.idempotent,
                "openWorldHint": False,
            },
        }


class NarrativeMcpServer:
    """Small MCP 2.0-compatible JSON-RPC dispatcher with a fixed tool allowlist."""

    def __init__(self, store_provider: Callable[[], NarrativeStore]) -> None:
        self._store_provider = store_provider
        definitions = (
            _ToolDefinition(
                name="narrative_list_projects",
                title="List narrative projects",
                description="List Narrative Studio projects without changing any story data.",
                arguments_model=_NoArguments,
                handler=self._list_projects,
                read_only=True,
                idempotent=True,
            ),
            _ToolDefinition(
                name="narrative_get_project",
                title="Get narrative project",
                description="Read one project and its candidate artifact counts.",
                arguments_model=_ProjectArguments,
                handler=self._get_project,
                read_only=True,
                idempotent=True,
            ),
            _ToolDefinition(
                name="narrative_list_chapters",
                title="List candidate chapters",
                description="List candidate chapters, optionally restricted to one branch.",
                arguments_model=_ListChaptersArguments,
                handler=self._list_chapters,
                read_only=True,
                idempotent=True,
            ),
            _ToolDefinition(
                name="narrative_build_context_candidate",
                title="Build candidate context pack",
                description=(
                    "Build and persist a bounded, source-cited candidate context pack. "
                    "This never promotes story material to canon."
                ),
                arguments_model=_BuildContextArguments,
                handler=self._build_context_candidate,
                read_only=False,
                idempotent=False,
            ),
            _ToolDefinition(
                name="narrative_create_chapter_candidate",
                title="Create candidate chapter",
                description=(
                    "Create a chapter with server-owned canon_status=candidate. "
                    "Canon promotion, voting, blocker resolution, and CanonCommit are unavailable."
                ),
                arguments_model=_CreateChapterArguments,
                handler=self._create_chapter_candidate,
                read_only=False,
                idempotent=False,
            ),
        )
        self._tools = {definition.name: definition for definition in definitions}

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def list_tools(self) -> list[dict[str, Any]]:
        return [definition.mcp_schema() for definition in self._tools.values()]

    async def handle_request(
        self,
        request: Any,
        *,
        actor: str | None = None,
    ) -> dict[str, Any] | None:
        """Handle one JSON-RPC request; notifications intentionally return no body."""

        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._rpc_error(None, -32600, "Invalid Request")
        req_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str) or not method:
            return self._rpc_error(req_id, -32600, "Invalid Request")
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._rpc_error(req_id, -32602, "Invalid params")

        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method in {"notifications/initialized", "initialized"}:
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                    return self._rpc_error(req_id, -32602, "Invalid tools/call params")
                result = await self.call_tool(name, arguments, actor=actor)
            else:
                return self._rpc_error(req_id, -32601, f"Method not found: {method}")
        except Exception as exc:  # noqa: BLE001 - protocol boundary must remain well formed
            _LOG.exception("Narrative MCP request failed: %s", method)
            return self._rpc_error(req_id, -32603, f"Internal error: {exc}")

        if "id" not in request:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        definition = self._tools.get(name)
        if definition is None:
            return self._tool_error(f"Unknown tool: {name}")
        try:
            validated = definition.arguments_model.model_validate(arguments)
            result = definition.handler(validated, actor)
            payload = {
                "ok": True,
                "canon_policy": CANON_POLICY,
                "result": self._json_value(result),
            }
            return self._tool_result(payload)
        except (ValidationError, NarrativeStoreError, ValueError, TypeError) as exc:
            return self._tool_error(str(exc))

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        client_info = params.get("clientInfo") or {}
        if isinstance(client_info, dict):
            _LOG.info(
                "Narrative MCP client connected: %s v%s",
                client_info.get("name", "unknown"),
                client_info.get("version", "?"),
            )
        return {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": SERVER_NAME,
                "title": "Echo Narrative Studio",
                "version": SERVER_VERSION,
                "description": "Candidate-only narrative tools governed by the Echo host.",
            },
            "instructions": (
                "All writes create candidate artifacts only. This server cannot vote, resolve "
                "review blockers, create CanonCommit records, or promote content to canon."
            ),
        }

    def _list_projects(self, _args: _StrictArguments, _actor: str | None) -> list[Any]:
        return self._store_provider().list_projects()

    def _get_project(self, args: _StrictArguments, _actor: str | None) -> Any:
        parsed = _ProjectArguments.model_validate(args.model_dump())
        return self._store_provider().project_detail(parsed.project_id)

    def _list_chapters(self, args: _StrictArguments, _actor: str | None) -> list[Any]:
        parsed = _ListChaptersArguments.model_validate(args.model_dump())
        return self._store_provider().list_chapters(parsed.project_id, parsed.branch_id)

    def _build_context_candidate(self, args: _StrictArguments, _actor: str | None) -> Any:
        parsed = _BuildContextArguments.model_validate(args.model_dump())
        return self._store_provider().build_context_pack(
            parsed.project_id,
            ContextPackBuildRequest.model_validate(parsed.model_dump(exclude={"project_id"})),
        )

    def _create_chapter_candidate(self, args: _StrictArguments, actor: str | None) -> Any:
        parsed = _CreateChapterArguments.model_validate(args.model_dump())
        actor_label = str(actor or "local").strip()[:220] or "local"
        return self._store_provider().create_chapter(
            parsed.project_id,
            ChapterCreate.model_validate(parsed.model_dump(exclude={"project_id"})),
            actor=f"mcp:{actor_label}",
            actor_source="authenticated_principal" if actor else "local",
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [NarrativeMcpServer._json_value(item) for item in value]
        if isinstance(value, dict):
            return {key: NarrativeMcpServer._json_value(item) for key, item in value.items()}
        return value

    @staticmethod
    def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
            "structuredContent": payload,
            "isError": False,
        }

    @classmethod
    def _tool_error(cls, message: str) -> dict[str, Any]:
        payload = {
            "ok": False,
            "canon_policy": CANON_POLICY,
            "error": message,
        }
        value = cls._tool_result(payload)
        value["isError"] = True
        return value

    @staticmethod
    def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }


__all__ = [
    "LATEST_PROTOCOL_VERSION",
    "MCP_ENDPOINT",
    "NarrativeMcpServer",
]
