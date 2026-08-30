"""Reflex YAML editor / card-mode / panel admin endpoints.

Extracted from ``_reflex_admin_endpoints.py`` so the router module
stays small. ``register_reflex_editor_endpoints`` registers the
YAML editor, card-mode, and HTML panel endpoints on the given
router.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import HTMLResponse


def register_reflex_editor_endpoints(
    _reflex_admin: Any,
    *,
    _reflex_router: Any,
    panel_html: str,
    editor_html: str,
) -> None:
    """Register the reflex YAML editor / card-mode / panel endpoints."""
    _REFLEX_PANEL_HTML = panel_html  # noqa: N806
    _REFLEX_EDITOR_HTML = editor_html  # noqa: N806

    @_reflex_admin.get("/admin/reflex", response_class=HTMLResponse)
    def _reflex_panel() -> str:
        """Self-contained HTML monitoring panel · no React, no
        build step. Polls /api/reflex/stats and /api/reflex/rules
        every 2 s. Useful for ops who want to watch hit rates
        during a rule iteration session without setting up the
        full frontend dev environment."""
        return _REFLEX_PANEL_HTML

    @_reflex_admin.get("/api/reflex/rules-yaml")
    def _reflex_rules_yaml_get() -> dict:
        """Return the raw YAML rules file as a string · feeds
        the in-browser editor at /admin/reflex/edit. Returns
        the file mtime too so the editor can warn the operator
        if someone else edited the file in between."""
        from runtime.core.nerves.reflex.rules_loader import find_default_rules_file

        path = find_default_rules_file()
        if path is None or not path.is_file():
            return {"ok": False, "error": "no rules file"}
        try:
            content = path.read_text(encoding="utf-8")
            return {
                "ok": True,
                "path": str(path),
                "content": content,
                "mtime": path.stat().st_mtime,
                "size": len(content),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @_reflex_admin.post("/api/reflex/rules-yaml")
    def _reflex_rules_yaml_put(body: dict) -> dict:
        """Persist new YAML content · validates by attempting to
        parse with the loader BEFORE writing to disk · invalid
        YAML returns the parse error, file untouched.

        Body shape: ``{"content": "<full file>", "expected_mtime":
        <float>, "reload": true}``. ``expected_mtime`` is checked
        against the on-disk mtime to prevent lost-update races
        between two browser tabs · pass 0 to bypass.

        ``reload=true`` (default) hot-reloads after save so the
        change is live immediately.
        """
        from runtime.core.nerves.reflex.rules_loader import (
            find_default_rules_file,
            load_rules_from_file,
        )

        content = body.get("content")
        if not isinstance(content, str):
            return {"ok": False, "error": "missing content"}
        path = find_default_rules_file()
        if path is None:
            return {"ok": False, "error": "no rules file"}
        # Optimistic concurrency · refuse to overwrite a file
        # that's been edited under us.
        expected = body.get("expected_mtime") or 0
        try:
            actual = path.stat().st_mtime
            if expected and abs(actual - float(expected)) > 0.5:
                return {
                    "ok": False,
                    "error": "file was modified externally · reload first",
                    "actual_mtime": actual,
                    "expected_mtime": expected,
                }
        except OSError:  # noqa: BLE001 — temp file cleanup; best-effort
            pass
        # Pre-validate · the loader is permissive (returns [] on
        # parse error so the running router can survive a bad
        # file), but for an interactive editor we want STRICT
        # validation: any YAML parse error rejects the save so
        # the file on disk stays valid. We call the YAML parser
        # directly instead of going through the loader.
        try:
            if path.suffix.lower() in (".yaml", ".yml"):
                import yaml as _yaml  # type: ignore[import]

                parsed = _yaml.safe_load(content)
            else:
                parsed = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"YAML parse failed: {exc}",
            }
        # Soft schema check · rules must be a list under "rules"
        # OR the whole document is a list. Anything else is
        # almost certainly an editor mistake.
        rules_list = parsed.get("rules") if isinstance(parsed, dict) else parsed
        if not isinstance(rules_list, list):
            return {
                "ok": False,
                "error": "expected a 'rules:' list at top level (or a top-level list)",
            }
        # Now parse via the loader to get the rule count for
        # the response. Failures here surface as 0 rules in
        # the file; the caller can still see what landed.
        tmp = path.with_name(path.stem + ".reflex_pending" + path.suffix)
        try:
            tmp.write_text(content, encoding="utf-8")
            try:
                rules = load_rules_from_file(tmp)
            except Exception as exc:  # noqa: BLE001
                tmp.unlink(missing_ok=True)
                return {
                    "ok": False,
                    "error": f"loader rejected: {exc}",
                }
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            return {"ok": False, "error": f"write failed: {exc}"}

        result = {
            "ok": True,
            "rules_in_file": len(rules),
            "new_mtime": path.stat().st_mtime,
        }
        # Live-reload by default · the editor is for active
        # iteration so "save" should mean "apply".
        if body.get("reload", True):
            try:
                from runtime.cli import _build_reflex_router

                fresh = _build_reflex_router()
                count = _reflex_router.replace_reflexes(fresh._reflexes)
                result["reloaded"] = True
                result["rules_loaded"] = count
            except Exception as exc:  # noqa: BLE001
                result["reloaded"] = False
                result["reload_error"] = f"{type(exc).__name__}: {exc}"
        return result

    # ─── Card-mode endpoints ─────────────────────────────────
    # The YAML editor is the power-user surface · for the 90%
    # case (greeting / canned-Q&A / smart-home one-liner) we
    # expose a simplified card shape that hides regex anchors,
    # numeric priorities, and unused fields. Rules that contain
    # advanced features (`variants`, `per_actor`, `enabled_when`,
    # `action`, custom matcher types) are returned with
    # ``advanced: true`` and the card UI must surface them as
    # read-only · the YAML mode is the escape hatch for those.
    @_reflex_admin.get("/api/reflex/rules-cards")
    def _reflex_rules_cards_get() -> dict:
        from runtime.core.nerves.reflex.rules_loader import find_default_rules_file

        path = find_default_rules_file()
        if path is None or not path.is_file():
            return {"ok": False, "error": "no rules file"}
        try:
            from ruamel.yaml import YAML  # type: ignore[import]

            yaml = YAML(typ="rt")
            yaml.preserve_quotes = True
            with path.open("r", encoding="utf-8") as fh:
                doc = yaml.load(fh)  # nosec B506 — ruamel rt loader is safe; trusted rules file
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"YAML parse failed: {exc}"}

        raw_rules = doc.get("rules") if isinstance(doc, dict) else doc
        if not isinstance(raw_rules, list):
            return {"ok": False, "error": "missing 'rules:' list"}

        cards = []
        for r in raw_rules:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "")
            rtype = str(r.get("type") or "regex")
            pattern = r.get("pattern") or ""
            reply = r.get("reply") or ""
            reply_on_failure = r.get("reply_on_action_failure") or ""
            delegate = r.get("delegate_to_workflow") or ""
            prio = int(r.get("priority") or 20)
            # Card-incompatible features · these route-block from
            # the simplified UI and force YAML mode.
            non_action_advanced = {
                "variants",
                "per_actor",
                "enabled_when",
            }
            has_advanced = bool(non_action_advanced & set(r.keys())) or rtype != "regex"
            # Action handling · expose webhook XOR mqtt in the card.
            # exec or multi-action (webhook+mqtt together) is too
            # power-user for cards · stays advanced.
            action_card: dict = {"mode": "none"}
            action_block = r.get("action") if isinstance(r.get("action"), dict) else None
            if action_block:
                has_wh = isinstance(action_block.get("webhook"), dict)
                has_mq = isinstance(action_block.get("mqtt"), dict)
                has_ex = isinstance(action_block.get("exec"), dict)
                if has_ex or (has_wh and has_mq):
                    has_advanced = True
                elif has_wh:
                    wh = action_block["webhook"]
                    action_card = {
                        "mode": "webhook",
                        "webhook": {
                            "url": str(wh.get("url") or ""),
                            "method": str(wh.get("method") or "POST").upper(),
                            "headers": dict(wh.get("headers") or {}),
                            "body": wh.get("body") if wh.get("body") is not None else None,
                            "timeout_ms": int(wh.get("timeout_ms") or 1000),
                        },
                    }
                elif has_mq:
                    mq = action_block["mqtt"]
                    action_card = {
                        "mode": "mqtt",
                        "mqtt": {
                            "broker": str(mq.get("broker") or ""),
                            "port": int(mq.get("port") or 1883),
                            "topic": str(mq.get("topic") or ""),
                            "payload": str(mq.get("payload") or ""),
                            "qos": int(mq.get("qos") or 0),
                            "retain": bool(mq.get("retain")),
                        },
                    }
            # Trigger inference: ^literal$ without regex meta → exact;
            # bare literal without anchors and no meta → contains.
            trigger_mode = "regex"
            trigger_text = str(pattern)
            meta_chars = set(r"\.^$*+?()[]{}|")
            if isinstance(pattern, str) and not has_advanced:
                body_pat = pattern
                is_anchored = body_pat.startswith("^") and body_pat.endswith("$")
                inner = body_pat[1:-1] if is_anchored else body_pat
                has_meta = any(c in meta_chars for c in inner)
                if is_anchored and not has_meta:
                    trigger_mode = "exact"
                    trigger_text = inner
                elif not is_anchored and not has_meta and "\n" not in inner:
                    trigger_mode = "contains"
                    trigger_text = inner
            # Priority bucket · low=10, medium=20, high=30+
            if prio < 15:
                prio_band = "low"
            elif prio < 25:
                prio_band = "medium"
            else:
                prio_band = "high"
            cards.append(
                {
                    "id": rid,
                    "trigger_mode": trigger_mode,
                    "trigger_text": trigger_text,
                    "reply": str(reply),
                    "reply_on_failure": str(reply_on_failure),
                    "reply_source": "workflow" if str(delegate).strip() else "text",
                    "delegate_to_workflow": str(delegate),
                    "priority": prio_band,
                    "priority_raw": prio,
                    "action": action_card,
                    "advanced": has_advanced,
                }
            )
        return {
            "ok": True,
            "path": str(path),
            "mtime": path.stat().st_mtime,
            "cards": cards,
        }

    @_reflex_admin.post("/api/reflex/rules-cards")
    def _reflex_rules_cards_put(body: dict) -> dict:
        """Patch the YAML file using ruamel · only the basic fields
        (pattern / reply / priority) of non-advanced rules are
        updated · new cards append · advanced rules are
        untouchable through this endpoint by design.

        Body shape::

            {
              "expected_mtime": <float>,
              "reload": true,
              "upserts": [{id, trigger_mode, trigger_text, reply, priority}],
              "deletes": ["id1", "id2"]
            }
        """
        from runtime.core.nerves.reflex.rules_loader import (
            find_default_rules_file,
            load_rules_from_file,
        )

        path = find_default_rules_file()
        if path is None:
            return {"ok": False, "error": "no rules file"}
        expected = body.get("expected_mtime") or 0
        try:
            actual = path.stat().st_mtime
            if expected and abs(actual - float(expected)) > 0.5:
                return {
                    "ok": False,
                    "error": "file was modified externally · reload first",
                    "actual_mtime": actual,
                    "expected_mtime": expected,
                }
        except OSError:  # noqa: BLE001 — temp file cleanup; best-effort
            pass

        try:
            from ruamel.yaml import YAML  # type: ignore[import]
            from ruamel.yaml.comments import CommentedMap, CommentedSeq  # type: ignore[import]

            yaml = YAML(typ="rt")
            yaml.preserve_quotes = True
            yaml.width = 120
            yaml.indent(mapping=2, sequence=4, offset=2)
            with path.open("r", encoding="utf-8") as fh:
                doc = yaml.load(fh)  # nosec B506 — ruamel rt loader is safe; trusted rules file
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"YAML load failed: {exc}"}

        if not isinstance(doc, dict):
            doc = CommentedMap()
        rules_seq = doc.get("rules")
        if not isinstance(rules_seq, list):
            rules_seq = CommentedSeq()
            doc["rules"] = rules_seq

        prio_map = {"low": 10, "medium": 20, "high": 30}

        def _to_pattern(mode: str, text: str) -> str:
            import re as _re

            t = text or ""
            if mode == "exact":
                return f"^{_re.escape(t)}$"
            if mode == "contains":
                return _re.escape(t)
            return t

        upserts = body.get("upserts") or []
        deletes = set(body.get("deletes") or [])

        # Apply deletes — only on non-advanced rules.
        non_action_advanced = {"variants", "per_actor", "enabled_when"}

        def _is_advanced(rule: dict) -> bool:
            if rule.get("type") not in (None, "regex"):
                return True
            if non_action_advanced & set(rule.keys()):
                return True
            act = rule.get("action") if isinstance(rule.get("action"), dict) else None
            if act:
                has_wh = isinstance(act.get("webhook"), dict)
                has_mq = isinstance(act.get("mqtt"), dict)
                has_ex = isinstance(act.get("exec"), dict)
                if has_ex or (has_wh and has_mq):
                    return True
            return False

        kept: list = []
        for r in rules_seq:
            rid = r.get("id") if isinstance(r, dict) else None
            if rid in deletes and isinstance(r, dict) and not _is_advanced(r):
                continue
            kept.append(r)
        rules_seq[:] = kept

        # Apply upserts.
        existing_by_id = {r.get("id"): i for i, r in enumerate(rules_seq) if isinstance(r, dict)}

        def _build_action_block(action_in: dict | None) -> CommentedMap | None:
            if not isinstance(action_in, dict):
                return None
            mode = str(action_in.get("mode") or "none")
            if mode == "webhook":
                wh = action_in.get("webhook") or {}
                if not str(wh.get("url") or "").strip():
                    return None
                block = CommentedMap()
                sub = CommentedMap()
                sub["url"] = str(wh.get("url") or "")
                sub["method"] = str(wh.get("method") or "POST").upper()
                headers = wh.get("headers") or {}
                if isinstance(headers, dict) and headers:
                    sub["headers"] = CommentedMap((str(k), str(v)) for k, v in headers.items())
                body_val = wh.get("body")
                if body_val not in (None, "", {}):
                    sub["body"] = body_val
                timeout = int(wh.get("timeout_ms") or 0)
                if timeout > 0:
                    sub["timeout_ms"] = timeout
                block["webhook"] = sub
                return block
            if mode == "mqtt":
                mq = action_in.get("mqtt") or {}
                if (
                    not str(mq.get("broker") or "").strip()
                    or not str(mq.get("topic") or "").strip()
                ):
                    return None
                block = CommentedMap()
                sub = CommentedMap()
                sub["broker"] = str(mq.get("broker") or "")
                sub["port"] = int(mq.get("port") or 1883)
                sub["topic"] = str(mq.get("topic") or "")
                sub["payload"] = str(mq.get("payload") or "")
                sub["qos"] = int(mq.get("qos") or 0)
                if mq.get("retain"):
                    sub["retain"] = True
                block["mqtt"] = sub
                return block
            return None

        for u in upserts:
            if not isinstance(u, dict):
                continue
            uid = str(u.get("id") or "").strip()
            if not uid:
                continue
            mode = str(u.get("trigger_mode") or "regex")
            text = str(u.get("trigger_text") or "")
            reply_text = str(u.get("reply") or "")
            reply_on_failure = str(u.get("reply_on_failure") or "").strip()
            reply_source = str(u.get("reply_source") or "text")
            delegate_wf = str(u.get("delegate_to_workflow") or "").strip()
            prio = prio_map.get(str(u.get("priority") or "medium"), 20)
            pattern = _to_pattern(mode, text)
            action_block = _build_action_block(u.get("action"))
            use_workflow = reply_source == "workflow" and bool(delegate_wf)
            if uid in existing_by_id:
                rule = rules_seq[existing_by_id[uid]]
                if isinstance(rule, dict) and not _is_advanced(rule):
                    rule["pattern"] = pattern
                    rule["reply"] = reply_text
                    rule["priority"] = prio
                    if reply_on_failure:
                        rule["reply_on_action_failure"] = reply_on_failure
                    else:
                        rule.pop("reply_on_action_failure", None)
                    if use_workflow:
                        rule["delegate_to_workflow"] = delegate_wf
                    else:
                        rule.pop("delegate_to_workflow", None)
                    if action_block is not None:
                        rule["action"] = action_block
                    else:
                        rule.pop("action", None)
            else:
                new_rule = CommentedMap()
                new_rule["id"] = uid
                new_rule["type"] = "regex"
                new_rule["pattern"] = pattern
                new_rule["reply"] = reply_text
                new_rule["priority"] = prio
                if reply_on_failure:
                    new_rule["reply_on_action_failure"] = reply_on_failure
                if use_workflow:
                    new_rule["delegate_to_workflow"] = delegate_wf
                if action_block is not None:
                    new_rule["action"] = action_block
                rules_seq.append(new_rule)

        # Serialize and re-validate before overwriting on disk.
        import io as _io

        buf = _io.StringIO()
        try:
            yaml.dump(doc, buf)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"YAML dump failed: {exc}"}
        new_content = buf.getvalue()

        tmp = path.with_name(path.stem + ".reflex_pending" + path.suffix)
        try:
            tmp.write_text(new_content, encoding="utf-8")
            try:
                rules_loaded = load_rules_from_file(tmp)
            except Exception as exc:  # noqa: BLE001
                tmp.unlink(missing_ok=True)
                return {"ok": False, "error": f"loader rejected: {exc}"}
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            return {"ok": False, "error": f"write failed: {exc}"}

        result = {
            "ok": True,
            "rules_in_file": len(rules_loaded),
            "new_mtime": path.stat().st_mtime,
        }
        if body.get("reload", True):
            try:
                from runtime.cli import _build_reflex_router

                fresh = _build_reflex_router()
                count = _reflex_router.replace_reflexes(fresh._reflexes)
                result["reloaded"] = True
                result["rules_loaded"] = count
            except Exception as exc:  # noqa: BLE001
                result["reloaded"] = False
                result["reload_error"] = f"{type(exc).__name__}: {exc}"
        return result

    @_reflex_admin.get("/admin/reflex/edit", response_class=HTMLResponse)
    def _reflex_editor() -> str:
        """Self-contained YAML editor · loads via /api/reflex/rules-yaml,
        saves back through the same endpoint with optimistic-lock
        mtime checks, runs /api/reflex/test pre-save."""
        return _REFLEX_EDITOR_HTML
