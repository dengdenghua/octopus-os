"""SEC-5 regression: production journal construction wires the secret redactor.

The journal+redactor *mechanism* is covered in
``test_journal_audit_budget_redactor``. This guards the *wiring*: the production
stack builder (``build_from_config``) must construct its ``JSONLJournal`` with a
``Redactor`` so tool args/outputs containing secrets are scrubbed before they
hit disk. Previously the redactor was implemented and tested but never wired
into any production construction site, so the audit log persisted secrets in the
clear.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from runtime.memory.journal import FileOpEvent
from runtime.platform.config.builder import build_from_config
from runtime.platform.config.schema import AgentConfig

_SECRET_LINE = "key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC end"


def test_build_from_config_journal_redacts_secrets(tmp_path: Path) -> None:
    jpath = tmp_path / "journal.jsonl"
    stack = build_from_config(AgentConfig(journal_file=str(jpath)))

    stack.journal.write(
        FileOpEvent(
            task_id=uuid4(),
            arm_id="arm-1",
            path="secrets.txt",
            action="write",
            bytes_delta=len(_SECRET_LINE),
            diff=_SECRET_LINE,
        )
    )

    content = jpath.read_text(encoding="utf-8")
    assert "sk-ant-api03" not in content  # secret scrubbed before persistence
    assert "[REDACTED:api_key]" in content

