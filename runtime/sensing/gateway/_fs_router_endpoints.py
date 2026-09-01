"""Endpoint handlers for the filesystem router.

Extracted from ``fs_router.py`` (god-file reduction). All ``/api/fs`` and
``/api/git`` endpoints register here, delegating to the shared helpers in
``_fs_router_helpers`` and the analysis helpers in ``_fs_router_paths`` /
``_fs_router_diff``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import File, Form, HTTPException, Query, Request, UploadFile

from ._fs_router_diff import (
    _DiffApplyConflict,
    _DiffFormatError,
    _reverse_unified_diff,
)
from ._fs_router_helpers import (
    _assert_in_scope,
    _assert_local_request_scope,
    _broadcast_file_written,
    _check_acl,
    _check_lease_conflict_or_acquire,
    _dir_entry_to_tree,
    _extract_user_id,
    _filesystem_roots,
    _FsContext,
    _is_ignored_remote_dir,
    _parse_workspace_path,
    _pick_directory_macos,
    _pick_directory_tk,
    _pick_directory_windows,
    _remote_backend_for,
    _require_local_thread_scope,
    _resolve_remote_workspace,
    _tree_depth_of,
    _walk_tree,
)
from ._fs_router_models import (
    FsImportDirectoryResponse,
    FsPickDirectoryResponse,
    FsReadResponse,
    FsRootsResponse,
    FsTreeResponse,
    FsWriteResponse,
)
from ._fs_router_paths import _assert_within_allowed_roots, _safe_relative_parts


def _content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _assert_expected_content(current: bytes, expected_sha256: object) -> None:
    """Reject a stale editor save instead of overwriting newer work."""
    if expected_sha256 is None:
        return
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_sha256
    ):
        raise HTTPException(400, "expected_sha256 must be a 64-character hex digest")
    observed = _content_sha256(current)
    if not hmac.compare_digest(observed, expected_sha256.lower()):
        raise HTTPException(
            409,
            {
                "error": "file_changed",
                "message": "文件已被其他编辑更新，请重新加载后再保存。",
                "observed_sha256": observed,
            },
        )


def register_endpoints(router: Any, ctx: _FsContext) -> None:
    @router.get("/api/fs/roots", response_model=FsRootsResponse)
    def api_fs_roots(
        request: Request,
        thread_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if ctx.require_auth and not ctx.allow_local_workspace_access:
            roots = _require_local_thread_scope(
                ctx,
                request,
                thread_id=thread_id,
            )
            return {"entries": _filesystem_roots(roots)}
        return {"entries": _filesystem_roots()}

    @router.get("/api/fs/pick-directory", response_model=FsPickDirectoryResponse)
    def api_fs_pick_directory(
        request: Request,
        default_path: str | None = Query(default=None),
        thread_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if ctx.require_auth and not ctx.allow_local_workspace_access:
            _require_local_thread_scope(
                ctx,
                request,
                thread_id=thread_id,
            )
            if default_path:
                _assert_in_scope(
                    ctx,
                    Path(default_path),
                    thread_id=thread_id,
                )
        try:
            if sys.platform.startswith("win"):
                path = _pick_directory_windows(default_path)
            elif sys.platform == "darwin":
                path = _pick_directory_macos(default_path)
            else:
                path = _pick_directory_tk(default_path)
        except Exception as exc:  # pragma: no cover - depends on local GUI
            return {
                "success": False,
                "path": None,
                "canceled": False,
                "error": str(exc),
            }
        if not path:
            return {"success": False, "path": None, "canceled": True, "error": None}
        if ctx.require_auth and not ctx.allow_local_workspace_access:
            path = str(
                _assert_in_scope(
                    ctx,
                    Path(path),
                    thread_id=thread_id,
                )
            )
        return {"success": True, "path": path, "canceled": False, "error": None}

    @router.post(
        "/api/fs/import-directory",
        response_model=FsImportDirectoryResponse,
    )
    async def api_fs_import_directory(
        request: Request,
        files: list[UploadFile] = File(...),  # noqa: B008
        relative_paths: list[str] = Form(default=[]),  # noqa: B008
        thread_id: str | None = Form(default=None),  # noqa: B008
        workspace_path: str | None = Form(default=None),  # noqa: B008
    ) -> dict[str, Any]:
        from runtime.platform.process.paths import app_paths

        if not files:
            raise HTTPException(400, "files are required")
        max_files = max(1, int(os.environ.get("ECHO_FS_IMPORT_MAX_FILES", "1000")))
        max_bytes = max(
            1,
            int(os.environ.get("ECHO_FS_IMPORT_MAX_BYTES", str(100 * 1024 * 1024))),
        )
        if len(files) > max_files:
            raise HTTPException(413, f"too many files; maximum is {max_files}")

        scope_roots: list[Path] = []
        if ctx.require_auth and not ctx.allow_local_workspace_access:
            scope_roots = _require_local_thread_scope(
                ctx,
                request,
                thread_id=thread_id,
                workspace_path=workspace_path,
            )
        first_rel = (
            relative_paths[0] if relative_paths else files[0].filename or "imported-workspace"
        )
        first_parts = _safe_relative_parts(first_rel)
        folder_name = first_parts[0] if len(first_parts) > 1 else "imported-workspace"
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", folder_name).strip(".-") or "workspace"
        if ctx.require_auth and not ctx.allow_local_workspace_access:
            base_root = Path(workspace_path).expanduser() if workspace_path else scope_roots[0]
            base_root = _assert_in_scope(
                ctx,
                base_root,
                thread_id=thread_id,
                workspace_path=workspace_path,
            )
            import_root = (
                base_root
                / ".echo"
                / "imports"
                / f"{int(time.time())}-{slug[:48]}-{uuid.uuid4().hex[:8]}"
            )
        else:
            import_root = (
                app_paths().data_dir
                / "imported_workspaces"
                / f"{int(time.time())}-{slug[:48]}-{uuid.uuid4().hex[:8]}"
            )
        await asyncio.to_thread(import_root.mkdir, parents=True, exist_ok=True)

        saved = 0
        total_bytes = 0
        try:
            for index, upload in enumerate(files):
                rel = (
                    relative_paths[index]
                    if index < len(relative_paths)
                    else upload.filename or f"file-{index}"
                )
                parts = _safe_relative_parts(rel)
                if len(parts) > 1:
                    parts = parts[1:]
                if not parts:
                    parts = [Path(upload.filename or f"file-{index}").name]
                target = import_root.joinpath(*parts)
                try:
                    resolved_target = target.resolve(strict=False)
                    resolved_target.relative_to(import_root.resolve())
                except (OSError, ValueError) as exc:
                    raise HTTPException(400, "invalid relative path") from exc
                part_target = target.with_name(f".{target.name}.uploading")
                try:
                    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
                    with part_target.open("wb") as output:
                        while True:
                            chunk = await upload.read(1024 * 1024)
                            if not chunk:
                                break
                            total_bytes += len(chunk)
                            if total_bytes > max_bytes:
                                raise HTTPException(
                                    413,
                                    f"import exceeds maximum size of {max_bytes} bytes",
                                )
                            await asyncio.to_thread(output.write, chunk)
                    await asyncio.to_thread(os.replace, part_target, target)
                except HTTPException:
                    raise
                except OSError as exc:
                    raise HTTPException(
                        500,
                        f"failed to import directory: {exc}",
                    ) from exc
                saved += 1
        except Exception:
            # The directory is newly allocated for this request, so cleanup
            # cannot remove an existing user workspace.  It prevents partial
            # imports from becoming visible after a quota/path failure.
            await asyncio.to_thread(shutil.rmtree, import_root, ignore_errors=True)
            raise

        return {
            "success": True,
            "path": str(import_root),
            "files": saved,
        }

    @router.get("/api/fs/tree", response_model=FsTreeResponse)
    async def api_fs_tree(
        request: Request,
        path: str,
        depth: int = Query(default=2, ge=0, le=6),
        thread_id: str | None = None,
        workspace_path: str | None = None,
        include_ignored: bool = Query(default=False),
    ) -> dict[str, Any]:
        # Remote-workspace routing: if ``path`` carries a ``workspace_id:``
        # prefix and the workspace exists, list_dir via the MountBackend.
        workspace_id, rel_path = _parse_workspace_path(path)
        ws = _resolve_remote_workspace(ctx, workspace_id)
        if ws is not None:
            _check_acl(ctx, request, ws.id, write=False)
            backend = _remote_backend_for(ctx, ws)
            if backend is None:
                raise HTTPException(
                    500,
                    {
                        "error": "mount_backend_unavailable",
                        "workspace_id": ws.id,
                        "mount_type": ws.mount_type,
                    },
                )
            try:
                entries = await backend.list_dir(rel_path, depth)
            except FileNotFoundError as exc:
                raise HTTPException(404, str(exc)) from exc
            except NotADirectoryError as exc:
                raise HTTPException(404, str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 — backend error
                raise HTTPException(500, f"backend list_dir failed: {exc}") from exc
            tree = [
                _dir_entry_to_tree(e, depth=_tree_depth_of(e.path, rel_path))
                for e in entries
                if not _is_ignored_remote_dir(e)
            ]
            return {"entries": tree}
        # Local-path fallback (existing behaviour).
        _assert_local_request_scope(
            ctx,
            request,
            thread_id=thread_id,
            workspace_path=workspace_path,
        )
        root = _assert_in_scope(
            ctx,
            Path(path),
            thread_id=thread_id,
            workspace_path=workspace_path,
        )
        return {
            "entries": await asyncio.to_thread(
                _walk_tree,
                root,
                max_depth=depth,
                include_ignored=include_ignored,
            ),
        }

    @router.get("/api/fs/read", response_model=FsReadResponse)
    async def api_fs_read(
        request: Request,
        path: str,
        max_lines: int = Query(default=500, ge=1, le=5000),
        thread_id: str | None = None,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        # Remote-workspace routing: if ``path`` carries a ``workspace_id:``
        # prefix and the workspace exists, read_file via the MountBackend.
        workspace_id, rel_path = _parse_workspace_path(path)
        ws = _resolve_remote_workspace(ctx, workspace_id)
        if ws is not None:
            _check_acl(ctx, request, ws.id, write=False)
            backend = _remote_backend_for(ctx, ws)
            if backend is None:
                raise HTTPException(
                    500,
                    {
                        "error": "mount_backend_unavailable",
                        "workspace_id": ws.id,
                        "mount_type": ws.mount_type,
                    },
                )
            try:
                raw = await backend.read_file(rel_path)
            except FileNotFoundError as exc:
                raise HTTPException(404, str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 — backend error
                raise HTTPException(500, f"backend read_file failed: {exc}") from exc
            if isinstance(raw, (bytes, bytearray)):
                content = raw.decode("utf-8", errors="replace")
            else:
                content = str(raw)
            lines = content.splitlines()
            return {
                "path": f"{ws.id}:{rel_path}",
                "content": "\n".join(lines[:max_lines]),
                "lines": lines[:max_lines],
                "truncated": len(lines) > max_lines,
            }
        # Local-path fallback (existing behaviour).
        _assert_local_request_scope(
            ctx,
            request,
            thread_id=thread_id,
            workspace_path=workspace_path,
        )
        file_path = _assert_in_scope(
            ctx,
            Path(path),
            thread_id=thread_id,
            workspace_path=workspace_path,
        )
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(404, f"file not found: {file_path}")
        try:
            content = await asyncio.to_thread(
                file_path.read_text,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise HTTPException(
                500,
                f"failed to read file: {exc}",
            ) from exc
        lines = content.splitlines()
        return {
            "path": str(file_path),
            "content": "\n".join(lines[:max_lines]),
            "lines": lines[:max_lines],
            "truncated": len(lines) > max_lines,
        }

    @router.post("/api/fs/write", response_model=FsWriteResponse)
    async def api_fs_write(
        request: Request,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        path_value = body.get("path")
        content = body.get("content", "")
        expected_sha256 = body.get("expected_sha256")
        if not isinstance(path_value, str) or not path_value.strip():
            raise HTTPException(400, "path is required")
        if not isinstance(content, str):
            raise HTTPException(400, "content must be a string")
        # Remote-workspace routing: if ``path`` carries a ``workspace_id:``
        # prefix and the workspace exists, write_file via the MountBackend.
        workspace_id, rel_path = _parse_workspace_path(path_value)
        ws = _resolve_remote_workspace(ctx, workspace_id)
        if ws is not None:
            _check_acl(
                ctx,
                request,
                ws.id,
                write=True,
                body=body,
            )
            holder_id = body.get("holder_id") if isinstance(body.get("holder_id"), str) else None
            principal = getattr(getattr(request, "state", None), "principal", None)
            principal_actor = getattr(principal, "actor_id", None)
            if principal_actor:
                if holder_id and holder_id != principal_actor:
                    raise HTTPException(403, "holder_id must match the authenticated actor")
                holder_id = principal_actor
            thread_id = body.get("thread_id") if isinstance(body.get("thread_id"), str) else None
            # Task 6.3: lease gate + auto-acquire.
            _check_lease_conflict_or_acquire(ctx, ws.id, rel_path, holder_id)
            backend = _remote_backend_for(ctx, ws)
            if backend is None:
                raise HTTPException(
                    500,
                    {
                        "error": "mount_backend_unavailable",
                        "workspace_id": ws.id,
                        "mount_type": ws.mount_type,
                    },
                )
            payload = content.encode("utf-8")
            try:
                try:
                    current = await backend.read_file(rel_path)
                except FileNotFoundError:
                    current = b""
                current_bytes = (
                    bytes(current)
                    if isinstance(current, (bytes, bytearray))
                    else str(current).encode("utf-8")
                )
                _assert_expected_content(current_bytes, expected_sha256)
                await backend.write_file(rel_path, payload)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 — backend error
                raise HTTPException(500, f"backend write_file failed: {exc}") from exc
            # Task 6.4: broadcast file_written to the bound cowork group.
            _broadcast_file_written(
                ctx,
                ws.id,
                rel_path,
                holder_id or _extract_user_id(request, body) or "anonymous",
                thread_id,
            )
            return {
                "success": True,
                "path": f"{ws.id}:{rel_path}",
                "bytes": len(payload),
            }
        # Local-path fallback (existing behaviour).
        _assert_local_request_scope(
            ctx,
            request,
            thread_id=(body.get("thread_id") if isinstance(body.get("thread_id"), str) else None),
            workspace_path=(
                body.get("workspace_path") if isinstance(body.get("workspace_path"), str) else None
            ),
        )
        file_path = _assert_in_scope(
            ctx,
            Path(path_value),
            thread_id=body.get("thread_id") if isinstance(body.get("thread_id"), str) else None,
            workspace_path=body.get("workspace_path")
            if isinstance(body.get("workspace_path"), str)
            else None,
        )
        try:
            await asyncio.to_thread(file_path.parent.mkdir, parents=True, exist_ok=True)
            current = await asyncio.to_thread(file_path.read_bytes) if file_path.exists() else b""
            _assert_expected_content(current, expected_sha256)
            await asyncio.to_thread(file_path.write_text, content, encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                500,
                f"failed to write file: {exc}",
            ) from exc
        return {
            "success": True,
            "path": str(file_path),
            "bytes": len(content.encode("utf-8")),
        }

    @router.post("/api/fs/revert-diff")
    def api_fs_revert_diff(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Reverse-apply a unified diff against the current file contents."""
        path_value = body.get("path")
        diff_text = body.get("diff")
        if not isinstance(path_value, str) or not path_value.strip():
            raise HTTPException(400, "path is required")
        if not isinstance(diff_text, str) or not diff_text.strip():
            raise HTTPException(400, "diff is required")

        thread_id = body.get("thread_id") if isinstance(body.get("thread_id"), str) else None
        workspace_path = (
            body.get("workspace_path") if isinstance(body.get("workspace_path"), str) else None
        )
        _assert_local_request_scope(
            ctx,
            request,
            thread_id=thread_id,
            workspace_path=workspace_path,
        )
        file_path = _assert_in_scope(
            ctx,
            Path(path_value),
            thread_id=thread_id,
            workspace_path=workspace_path,
        )
        if file_path.exists() and not file_path.is_file():
            raise HTTPException(404, f"file not found: {file_path}")

        try:
            current = (
                file_path.read_text(encoding="utf-8", errors="replace")
                if file_path.exists()
                else ""
            )
            reverted = _reverse_unified_diff(current, diff_text)
        except _DiffFormatError as exc:
            raise HTTPException(400, str(exc)) from exc
        except _DiffApplyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"failed to read file: {exc}") from exc

        delete_empty = body.get("delete_empty") is True
        try:
            if delete_empty and reverted == "":
                if file_path.exists():
                    file_path.unlink()
                return {
                    "success": True,
                    "reverted": True,
                    "path": str(file_path),
                    "bytes": 0,
                    "deleted": True,
                }
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(reverted, encoding="utf-8")
        except OSError as exc:
            raise HTTPException(500, f"failed to write file: {exc}") from exc

        return {
            "success": True,
            "reverted": True,
            "path": str(file_path),
            "bytes": len(reverted.encode("utf-8")),
            "deleted": False,
        }

    @router.post("/api/fs/revert")
    def api_fs_revert(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Revert a file to its last git-committed state.

        Both ``path`` and ``workspace`` (if given) must resolve under
        the allowed fs roots — otherwise this endpoint would let a
        WebSocket client revert files in any directory reachable by
        the server (CVE-class bug).
        """
        path_value = body.get("path")
        workspace = body.get("workspace")
        if not isinstance(path_value, str) or not path_value.strip():
            raise HTTPException(400, "path is required")
        thread_id = body.get("thread_id") if isinstance(body.get("thread_id"), str) else None
        _assert_local_request_scope(
            ctx,
            request,
            thread_id=thread_id,
            workspace_path=workspace if isinstance(workspace, str) else None,
        )
        if (
            ctx.require_auth
            and getattr(getattr(request, "state", None), "principal", None) is not None
        ):
            file_path = _assert_in_scope(
                ctx,
                Path(path_value).expanduser(),
                thread_id=thread_id,
                workspace_path=workspace if isinstance(workspace, str) else None,
            )
        else:
            file_path = _assert_within_allowed_roots(Path(path_value).expanduser())
        if workspace:
            if not isinstance(workspace, str):
                raise HTTPException(400, "workspace must be a string")
            if (
                ctx.require_auth
                and getattr(getattr(request, "state", None), "principal", None) is not None
            ):
                cwd_path = _assert_in_scope(
                    ctx,
                    Path(workspace).expanduser(),
                    thread_id=thread_id,
                    workspace_path=workspace,
                )
            else:
                cwd_path = _assert_within_allowed_roots(Path(workspace).expanduser())
        else:
            cwd_path = _assert_within_allowed_roots(file_path.parent)
        # The file must live under the chosen cwd so that ``git
        # checkout -- <file>`` can only affect the asserted workspace.
        try:
            file_path.relative_to(cwd_path)
        except ValueError:
            raise HTTPException(
                400,
                f"path {file_path} is not inside workspace {cwd_path}",
            ) from None
        try:
            proc = subprocess.run(
                ["git", "checkout", "--", str(file_path)],
                capture_output=True,
                text=True,
                cwd=str(cwd_path),
                timeout=10.0,
                shell=False,
            )
            if proc.returncode != 0:
                raise HTTPException(500, f"git checkout failed: {proc.stderr.strip()}")
            return {"reverted": True, "path": str(file_path)}
        except FileNotFoundError:
            raise HTTPException(503, "git not found") from None
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "git checkout timed out") from None

    @router.get("/api/git/status")
    def api_git_status(
        request: Request,
        path: str = Query(default="."),
        thread_id: str | None = Query(default=None),
        workspace_path: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Run git status --porcelain in the given directory."""
        candidate = (
            Path(workspace_path).expanduser()
            if path in {"", "."} and workspace_path
            else Path(path).expanduser()
        )
        if ctx.require_auth:
            _assert_local_request_scope(
                ctx,
                request,
                thread_id=thread_id,
                workspace_path=workspace_path,
            )
            root = _assert_in_scope(
                ctx,
                candidate,
                thread_id=thread_id,
                workspace_path=workspace_path,
            )
        else:
            root = _assert_within_allowed_roots(candidate)
        if not root.is_dir():
            raise HTTPException(404, f"directory not found: {root}")
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain=v1", "--branch"],
                capture_output=True,
                text=True,
                cwd=str(root),
                timeout=10.0,
            )
            if proc.returncode != 0:
                return {"branch": "", "files": [], "error": proc.stderr.strip()}
        except FileNotFoundError:
            return {"branch": "", "files": [], "error": "git not found"}
        except subprocess.TimeoutExpired:
            return {"branch": "", "files": [], "error": "timeout"}

        branch = ""
        files: list[dict[str, str]] = []
        for line in proc.stdout.splitlines():
            if line.startswith("## "):
                branch = line[3:].split("...")[0]
                continue
            if len(line) < 4:
                continue
            xy = line[:2]
            file_path = line[3:].strip()
            status = "M"
            if "A" in xy or "?" in xy:
                status = "A"
            elif "D" in xy:
                status = "D"
            elif "R" in xy:
                status = "R"
            files.append({"path": file_path, "status": status})
        return {"branch": branch, "files": files}
