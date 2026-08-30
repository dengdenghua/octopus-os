from __future__ import annotations

"""External session importers for native evolution.

The goal is deliberately modest: turn exported chat/session files into the
same compact samples Echo already uses for GEPA and replay. Importers are
read-only and opt-in by default via local import folders or
``ECHO_EVOLUTION_SESSION_PATHS``.
"""

import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from dataclasses import asdict, dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from runtime.safety.recovery.evolution_dataset import (  # noqa: E402
    EvolutionDataset,
    EvolutionDatasetBuilder,
    EvolutionExample,
)


@dataclass(frozen=True, slots=True)
class ImportedSessionSample:
    goal: str
    success: bool
    source: str
    path: str
    last_error: str = ""
    assistant_summary: str = ""
    step_count: int = 0
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_failure(self) -> dict[str, Any] | None:
        if self.success or not self.goal.strip():
            return None
        return {
            "goal": self.goal[:300],
            "step_count": self.step_count,
            "last_error": self.last_error[:300],
            "recipe_id": None,
            "source": self.source,
            "proposal_id": self.session_id,
            "failure_source": self.metadata.get("failure_source") or "external_session",
            "external_path": self.path,
            "assistant_summary": self.assistant_summary[:500],
        }

    def to_positive_example(self) -> EvolutionExample | None:
        if not self.success or not self.goal.strip():
            return None
        return EvolutionExample(
            task_input=self.goal[:300],
            expected_behavior=(
                "Preserve the behavior that completed this imported session "
                "successfully. Use it as a positive example while evolving."
            ),
            source=f"{self.source}_success",
            difficulty="medium",
            category="external_successful_turn",
            metadata={
                "session_id": self.session_id,
                "external_path": self.path,
                "assistant_summary": self.assistant_summary[:500],
                **self.metadata,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SessionImportReport:
    samples: list[ImportedSessionSample] = field(default_factory=list)
    scanned_files: int = 0
    skipped_files: int = 0
    roots: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [
            failure
            for sample in self.samples
            for failure in [sample.to_failure()]
            if failure is not None
        ]

    @property
    def positive_examples(self) -> list[EvolutionExample]:
        return [
            example
            for sample in self.samples
            for example in [sample.to_positive_example()]
            if example is not None
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": [sample.to_dict() for sample in self.samples],
            "scanned_files": self.scanned_files,
            "skipped_files": self.skipped_files,
            "roots": list(self.roots),
        }


def discover_external_session_roots(
    paths: list[str | Path] | None = None,
) -> list[Path]:
    roots: list[Path] = []
    explicit = list(paths or [])
    env_paths = os.environ.get("ECHO_EVOLUTION_SESSION_PATHS", "")
    if env_paths:
        explicit.extend(part for part in env_paths.split(os.pathsep) if part.strip())
    if explicit:
        roots.extend(Path(path).expanduser() for path in explicit)
    else:
        roots.extend(
            [
                Path("data/imported_sessions"),
                Path("data/evolution_sessions"),
            ]
        )
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def import_external_sessions(
    paths: list[str | Path] | None = None,
    *,
    limit: int = 100,
    max_file_bytes: int = 2_000_000,
) -> SessionImportReport:
    roots = discover_external_session_roots(paths)
    samples: list[ImportedSessionSample] = []
    scanned = 0
    skipped = 0
    for file in _iter_session_files(roots):
        if len(samples) >= max(1, int(limit)):
            break
        try:
            if file.stat().st_size > max_file_bytes:
                skipped += 1
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        scanned += 1
        parsed = _parse_session_file(file, text)
        if parsed is None:
            skipped += 1
            continue
        samples.append(parsed)
    return SessionImportReport(
        samples=samples,
        scanned_files=scanned,
        skipped_files=skipped,
        roots=[str(root) for root in roots],
    )


def collect_external_session_failures(
    paths: list[str | Path] | None = None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return import_external_sessions(paths, limit=limit).failures[: max(0, int(limit))]


def build_external_session_dataset(
    paths: list[str | Path] | None = None,
    *,
    limit: int = 50,
) -> EvolutionDataset:
    report = import_external_sessions(paths, limit=limit)
    return EvolutionDatasetBuilder()._split(report.positive_examples)


def _iter_session_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            files.append(root)
            continue
        for pattern in ("*.jsonl", "*.json", "*.md", "*.txt"):
            files.extend(root.rglob(pattern))
    return sorted(files, key=lambda path: str(path).lower())


def _parse_session_file(path: Path, text: str) -> ImportedSessionSample | None:
    suffix = path.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        payloads = _load_json_payloads(text, jsonl=suffix == ".jsonl")
        if payloads:
            return _parse_structured_payload(path, payloads)
    return _parse_text_transcript(path, text)


def _load_json_payloads(text: str, *, jsonl: bool) -> list[Any]:
    if jsonl:
        out: list[Any] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    return [data]


def _parse_structured_payload(path: Path, payloads: list[Any]) -> ImportedSessionSample | None:
    messages: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        metadata.update(_small_metadata(payload))
        nested = payload.get("messages") or payload.get("conversation") or payload.get("turns")
        if isinstance(nested, list):
            messages.extend(item for item in nested if isinstance(item, dict))
        elif _looks_like_message(payload):
            messages.append(payload)
    if not messages:
        return None
    goal = _first_user_text(messages)
    assistant = _last_assistant_text(messages)
    errors = _error_texts(messages)
    success = bool(assistant.strip()) and not errors
    return (
        ImportedSessionSample(
            goal=goal,
            success=success,
            source=_source_name(path),
            path=str(path),
            last_error=errors[-1] if errors else "",
            assistant_summary=_compact_text(assistant, 600),
            step_count=len(messages),
            session_id=str(metadata.get("session_id") or metadata.get("id") or path.stem),
            metadata={
                **metadata,
                "format": path.suffix.lower().lstrip("."),
                "failure_source": "external_session_error" if errors else None,
            },
        )
        if goal.strip()
        else None
    )


def _parse_text_transcript(path: Path, text: str) -> ImportedSessionSample | None:
    compact = str(text or "")[:4000]
    goal = _extract_text_goal(compact)
    if not goal:
        return None
    errors = _extract_error_lines(compact)
    assistant = _extract_last_assistant_text(compact)
    success = bool(assistant) and not errors
    return ImportedSessionSample(
        goal=goal,
        success=success,
        source=_source_name(path),
        path=str(path),
        last_error=errors[-1] if errors else "",
        assistant_summary=_compact_text(assistant, 600),
        step_count=max(
            1,
            len(
                re.findall(
                    r"(?im)^(user|assistant|human|ai|error)\s*:",
                    compact,
                )
            ),
        ),
        session_id=path.stem,
        metadata={
            "format": path.suffix.lower().lstrip(".") or "text",
            "failure_source": "external_session_error" if errors else None,
        },
    )


def _looks_like_message(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("role", "type", "speaker", "content", "text", "message"))


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        role = str(
            message.get("role") or message.get("type") or message.get("speaker") or ""
        ).lower()
        if role in {"user", "human"} or "user" in role:
            text = _message_text(message)
            if text:
                return _compact_text(text, 300)
    for message in messages:
        text = _message_text(message)
        if text:
            return _compact_text(text, 300)
    return ""


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        role = str(
            message.get("role") or message.get("type") or message.get("speaker") or ""
        ).lower()
        if role in {"assistant", "ai", "model"} or "assistant" in role:
            text = _message_text(message)
            if text:
                return text
    return ""


def _error_texts(messages: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for message in messages:
        text = _message_text(message)
        if _is_error_record(message, text):
            errors.append(_compact_text(text or str(message), 300))
    return errors


def _message_text(message: dict[str, Any]) -> str:
    value = (
        message.get("content")
        or message.get("text")
        or message.get("message")
        or message.get("summary")
    )
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("input")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _is_error_record(message: dict[str, Any], text: str) -> bool:
    if message.get("is_error") is True or message.get("error") or message.get("exception"):
        return True
    status = str(message.get("status") or message.get("outcome") or "").lower()
    if status in {"error", "failed", "failure"}:
        return True
    return bool(
        re.search(
            r"(?i)\b(error|exception|traceback|failed|failure|timeout)\b",
            text,
        )
    )


def _extract_text_goal(text: str) -> str:
    patterns = [
        r"(?ims)^\s*(?:user|human)\s*:\s*(.+?)(?=^\s*(?:assistant|ai|system)\s*:|\Z)",
        r"(?ims)^\s*#\s*user\s*\n(.+?)(?=^\s*#\s*|\Z)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _compact_text(match.group(1), 300)
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return _compact_text(first, 300)


def _extract_last_assistant_text(text: str) -> str:
    matches = list(
        re.finditer(
            r"(?ims)^\s*(?:assistant|ai)\s*:\s*(.+?)(?=^\s*(?:user|human|system)\s*:|\Z)",
            text,
        )
    )
    if not matches:
        return ""
    return matches[-1].group(1).strip()


def _extract_error_lines(text: str) -> list[str]:
    return [
        _compact_text(line, 300)
        for line in text.splitlines()
        if re.search(
            r"(?i)\b(error|exception|traceback|failed|failure|timeout)\b",
            line,
        )
    ]


def _small_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("id", "session_id", "conversation_id", "title", "created_at", "updated_at"):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    return out


_SOURCE_VENDORS: tuple[tuple[str, str], ...] = (
    ("claude", "claude_session"),
    ("copilot", "copilot_session"),
    ("hermes", "hermes_session"),
)


def _source_name(path: Path) -> str:
    """Name the originating tool, giving the most specific path part priority.

    Matching walks from the filename outwards so a vendor hint on the file
    itself wins over one on an ancestor directory. Without that ordering an
    unrelated ancestor (a ``/tmp/claude-501`` scratch dir, a ``~/claude-backup/``
    folder holding exports from another tool) would relabel every session under
    it, because the old implementation substring-matched the whole joined path
    with ``claude`` checked first.
    """
    for part in reversed([p.lower() for p in path.parts]):
        for needle, source in _SOURCE_VENDORS:
            if needle in part:
                return source
    return "external_session"


def _compact_text(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[: max(0, int(limit))]


__all__ = [
    "ImportedSessionSample",
    "SessionImportReport",
    "build_external_session_dataset",
    "collect_external_session_failures",
    "discover_external_session_roots",
    "import_external_sessions",
]
