from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.io import atomic_write_json

from .testing import SkillTestCase, SkillTester, SkillTestReport, SkillTestsFailed

CostProfile = Literal["low", "mid", "high"]


class Skill(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str = Field(..., min_length=1)
    description: str = ""
    # Progressive disclosure · ``summary`` is the always-on index
    # entry (≤ ~15 tokens) the planner sees in the skill list. The
    # full ``description`` is loaded into context only when the
    # planner picks the skill or the agent calls
    # ``SkillRegistry.load_full_description(name)``. Keeps the
    # always-injected skill index small even with 100+ skills.
    #
    # If a skill is registered without a ``summary``, ``effective_summary``
    # falls back to the first sentence/clause of ``description``.
    summary: str = ""
    affinity: list[str] = Field(default_factory=list)
    cost_profile: CostProfile = "low"
    trusted_source: str = Field(..., min_length=1)  # "builtin://x" / "skill://public/y" / "mcp://z"
    # Runtime-installed capabilities may belong to one tenant.  ``None``
    # keeps the built-in/local skill surface process-wide; a populated value
    # is filtered by the ambient turn session before discovery or execution.
    tenant_id: str | None = None
    # Governed evolution candidates can be present in the process-wide
    # catalog while remaining visible only to their sticky canary cohort.
    rollout_candidate_id: str | None = None
    handler: Callable[..., Any]
    tests: list[SkillTestCase] = Field(default_factory=list)
    # ADR-010 · the exclusive resource this skill must hold while running, e.g.
    # ``device:desktop`` (pyautogui drives the one physical screen/mouse/keyboard,
    # so two parallel arms can't). The swarm splitter copies it onto the
    # ArmAssignment, which serialises via the BoidsArbitrator. ``None`` (the
    # default, and the vast majority) ⇒ no claim ⇒ unchanged behaviour. Use a
    # ``…:read`` suffix for shared readers (they coexist).
    exclusive_resource: str | None = None
    # Audit T-06: optional wall-clock ceiling for the handler call, in
    # seconds. When set (>0), a handler that exceeds it is judged
    # status="timeout" by the tool engine instead of holding its worker
    # thread forever. ``None`` (default) keeps the direct call.
    timeout_s: float | None = None

    @property
    def has_tests(self) -> bool:
        return len(self.tests) > 0

    @property
    def effective_summary(self) -> str:
        """Short index-line for this skill.

        If ``summary`` is set, return it as-is. Otherwise derive
        from ``description`` by taking the first sentence (split on
        ".\n" or first 100 chars), so existing skills get a sane
        default without a code change.
        """
        if self.summary:
            return self.summary.strip()
        desc = (self.description or "").strip()
        if not desc:
            return ""
        # First newline-or-period boundary, capped at 100 chars.
        for sep in (".\n", "\n", ". "):
            idx = desc.find(sep)
            if 0 < idx <= 100:
                return desc[: idx + (1 if sep != "\n" else 0)].strip()
        return desc[:100].rstrip() + ("…" if len(desc) > 100 else "")


class SkillNotFound(KeyError):
    pass


class SkillRegistry:
    def __init__(
        self,
        strict_mode: bool = False,
        *,
        event_bus: Any = None,
    ) -> None:
        self._by_name: dict[str, Skill] = {}
        self._last_reports: dict[str, SkillTestReport] = {}
        self._tester = SkillTester()
        self._strict_mode = strict_mode
        self._event_bus = event_bus
        self._enabled: dict[str, bool] = {}
        self._state_file: Path | None = None
        self._lock = RLock()

    @staticmethod
    def _ambient_tenant_id() -> str | None:
        """Read the server-resolved tenant carried by the active turn.

        The registry is also used by local CLIs and migration tools, where
        no session exists; those callers intentionally retain the unscoped
        view.  HTTP-backed agent turns bind a session before planning and
        execution, making tenant-owned skills invisible outside their tenant.
        """
        try:
            from runtime.platform.process.session import current_session

            session = current_session()
            value = session.metadata.get("tenant_id") if session is not None else None
            if not value:
                # The OpenAI gateway binds journal context before planning;
                # use it as the synchronous request-thread fallback while a
                # worker thread is being handed its full Session.
                from runtime.memory.journal.journal_context import current_tenant_id

                value = current_tenant_id()
            return str(value).strip() if value else None
        except (ImportError, AttributeError, TypeError):
            return None

    @classmethod
    def _visible_to_ambient_tenant(cls, skill: Skill) -> bool:
        tenant_id = cls._ambient_tenant_id()
        if tenant_id is not None and skill.tenant_id not in (None, tenant_id):
            return False
        if skill.rollout_candidate_id:
            try:
                from runtime.safety.evolution.runtime_deployment import (
                    is_rollout_candidate_visible,
                )

                return is_rollout_candidate_visible(skill.rollout_candidate_id)
            except (ImportError, OSError, TypeError, ValueError):
                return False
        return True

    def _get_visible(self, name: str) -> Skill:
        lookup_name = self._canonical_lookup(name)
        try:
            skill = self._by_name[lookup_name]
        except KeyError as e:
            raise SkillNotFound(f"no skill named {name!r}") from e
        if not self._visible_to_ambient_tenant(skill):
            # Deliberately use the same error as a missing skill.  This avoids
            # confirming another tenant's capability names to the caller.
            raise SkillNotFound(f"no skill named {name!r}")
        return skill

    def register(
        self,
        skill: Skill,
        *,
        verify_tests: bool = True,
        replace: bool = False,
    ) -> SkillTestReport | None:
        with self._lock:
            if skill.name in self._by_name and not replace:
                raise ValueError(f"duplicate skill name: {skill.name!r}")

            if verify_tests and skill.has_tests:
                report = self._tester.verify(skill)
                self._last_reports[skill.name] = report
            elif self._strict_mode:
                raise SkillTestsFailed(
                    skill.name,
                    SkillTestReport(skill_name=skill.name, overall_passed=False),
                )
            else:
                report = None

            self._by_name[skill.name] = skill

        if self._event_bus is not None:
            try:
                from runtime.core.nerves import SkillRegistered

                self._event_bus.publish(
                    SkillRegistered(
                        skill_name=skill.name,
                        trusted_source=skill.trusted_source,
                        forged="forged" in skill.affinity,
                    )
                )
            except Exception:  # noqa: BLE001 — bus is best-effort; never break register
                pass

        return report

    def get(self, name: str) -> Skill:
        with self._lock:
            return self._get_visible(name)

    def has(self, name: str) -> bool:
        with self._lock:
            try:
                self._get_visible(name)
            except SkillNotFound:
                return False
            return True

    @staticmethod
    def _canonical_lookup(name: str) -> str:
        """Resolve legacy skill ids without duplicating registry entries.

        Aliases are compatibility input, not catalog entries: the planner and
        audit trail continue to expose the canonical skill name while old
        prompts/configs still resolve successfully.
        """
        try:
            from runtime.execution.skill_aliases import canonical_skill_id

            return canonical_skill_id(name)
        except (ImportError, TypeError):
            # Keep the registry usable in minimal deployments that omit the
            # optional alias table.
            return name

    def unregister(self, name: str) -> bool:
        with self._lock:
            existed = name in self._by_name
            self._by_name.pop(name, None)
            self._last_reports.pop(name, None)
            self._enabled.pop(name, None)
            return existed

    def list_by_affinity(self, tag: str) -> list[Skill]:
        with self._lock:
            return [
                s
                for s in self._by_name.values()
                if tag in s.affinity and self._visible_to_ambient_tenant(s)
            ]

    def all_names(self) -> list[str]:
        with self._lock:
            return [
                name
                for name, skill in self._by_name.items()
                if self._visible_to_ambient_tenant(skill)
            ]

    def last_test_report(self, name: str) -> SkillTestReport | None:
        with self._lock:
            return self._last_reports.get(name)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_name)

    def set_state_file(self, path: Path | str) -> None:
        with self._lock:
            self._state_file = Path(path)
            self._load_enabled_state()

    def _load_enabled_state(self) -> None:
        with self._lock:
            if self._state_file is None or not self._state_file.exists():
                return
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._enabled = {k: bool(v) for k, v in data.get("enabled", {}).items()}
            except (OSError, json.JSONDecodeError, TypeError, ValueError):  # noqa: BLE001
                pass

    def _save_enabled_state(self) -> None:
        with self._lock:
            if self._state_file is None:
                return
            state_file = self._state_file
            enabled = dict(self._enabled)
        with contextlib.suppress(Exception):
            atomic_write_json(
                state_file,
                {"enabled": enabled},
            )

    def is_enabled(self, name: str) -> bool:
        with self._lock:
            self._get_visible(name)
            return self._enabled.get(name, True)

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            if name not in self._by_name:
                raise SkillNotFound(f"no skill named {name!r}")
            self._enabled[name] = enabled
        self._save_enabled_state()

    def enable(self, name: str) -> None:
        self.set_enabled(name, True)

    def disable(self, name: str) -> None:
        self.set_enabled(name, False)

    def list_enabled(self) -> list[str]:
        with self._lock:
            return [
                name
                for name, skill in self._by_name.items()
                if self._visible_to_ambient_tenant(skill) and self.is_enabled(name)
            ]

    def list_disabled(self) -> list[str]:
        with self._lock:
            return [
                name
                for name, skill in self._by_name.items()
                if self._visible_to_ambient_tenant(skill) and not self.is_enabled(name)
            ]

    # ─── Progressive Disclosure ────────────────────────────────
    # The planner sees a compact skill index (name + one-line
    # summary). Full descriptions are loaded on-demand · either
    # when a skill is picked or when ``load_full_description`` is
    # called explicitly (e.g. from a skill_inspect tool).

    def index(
        self,
        *,
        only_enabled: bool = True,
    ) -> list[dict[str, str]]:
        """Return a compact index of all skills · always-on slot
        for the planner system prompt.

        Each entry has only ``name``, ``summary``, ``cost_profile``,
        and the first 3 affinity tags. Total size ≈ 30-50 tokens
        per skill, vs ~150-300 tokens for full descriptions.
        """
        out: list[dict[str, str]] = []
        with self._lock:
            for name, skill in self._by_name.items():
                if not self._visible_to_ambient_tenant(skill):
                    continue
                if only_enabled and not self.is_enabled(name):
                    continue
                out.append(
                    {
                        "name": skill.name,
                        "summary": skill.effective_summary,
                        "cost_profile": skill.cost_profile,
                        # Cap affinity to 3 tags · index stays compact.
                        "affinity": ",".join(skill.affinity[:3]),
                    }
                )
        return out

    def load_full_description(self, name: str) -> str:
        """Return the full ``description`` for a skill · the
        planner calls this after picking from the index.

        Equivalent to ``get(name).description`` but exposed as a
        named operation so callers (and metrics) can track which
        skill bodies actually get materialized into context.
        """
        skill = self.get(name)
        return skill.description
