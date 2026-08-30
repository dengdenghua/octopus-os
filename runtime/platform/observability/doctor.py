from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str
    fix_hint: str = ""


@dataclass
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def warn_count(self) -> int:
        return sum(1 for r in self.results if r.status == "warn")

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if r.status == "fail")

    @property
    def all_ok(self) -> bool:
        return self.fail_count == 0

    def summary(self) -> str:
        lines = []
        for r in self.results:
            icon = {"ok": "✓", "warn": "⚠", "fail": "✗"}[r.status]
            lines.append(f"  {icon} {r.name}: {r.message}")
            if r.fix_hint and r.status != "ok":
                lines.append(f"    → {r.fix_hint}")
        lines.append("")
        total = len(self.results)
        lines.append(
            f"  {total} checks: {self.ok_count} ok, "
            f"{self.warn_count} warnings, {self.fail_count} failures"
        )
        return "\n".join(lines)


class Doctor:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path else None

    def run(self) -> DoctorReport:
        report = DoctorReport()
        self._check_python(report)
        self._check_core_deps(report)
        self._check_optional_deps(report)
        self._check_api_keys(report)
        self._check_ollama(report)
        self._check_config(report)
        self._check_data_dir(report)
        self._check_sandbox(report)
        return report

    def _check_sandbox(self, report: DoctorReport) -> None:
        """Report the process-sandbox posture for agent shell commands.

        A hard OS backend (bwrap / seatbelt) means a misbehaving command
        is stopped by the kernel; the soft fallback only applies policy
        at the Python layer, so it earns a visible warn.
        """
        try:
            from runtime.safety.sandboxing.sandbox import resolved_process_backend

            choice = resolved_process_backend()
        except Exception as exc:  # noqa: BLE001 — doctor must never crash
            report.results.append(
                CheckResult(
                    name="Process sandbox",
                    status="warn",
                    message=f"backend probe failed: {type(exc).__name__}: {exc}",
                )
            )
            return
        if choice.hard:
            report.results.append(
                CheckResult(
                    name="Process sandbox",
                    status="ok",
                    message=f"{choice.name} (kernel-level isolation active)",
                )
            )
            return
        import sys as _sys

        if _sys.platform.startswith("linux"):
            hint = "Install bubblewrap (bwrap), or set ECHO_PROCESS_SANDBOX=strict"
        elif _sys.platform == "darwin":
            hint = "sandbox-exec missing; set ECHO_PROCESS_SANDBOX=strict to fail closed"
        else:
            hint = "No hard backend on this platform yet; soft constraints still apply"
        report.results.append(
            CheckResult(
                name="Process sandbox",
                status="warn",
                message=f"{choice.name} (soft constraints only — no kernel isolation)",
                fix_hint=hint,
            )
        )

    def _check_python(self, report: DoctorReport) -> None:
        major, minor = sys.version_info[:2]
        if (major, minor) >= (3, 11):
            report.results.append(
                CheckResult(
                    name="Python",
                    status="ok",
                    message=f"{major}.{minor}.{sys.version_info.micro}",
                )
            )
        elif (major, minor) >= (3, 10):
            report.results.append(
                CheckResult(
                    name="Python",
                    status="warn",
                    message=f"{major}.{minor} (3.11+ recommended)",
                    fix_hint="Upgrade to Python 3.11+",
                )
            )
        else:
            report.results.append(
                CheckResult(
                    name="Python",
                    status="fail",
                    message=f"{major}.{minor} (3.11+ required)",
                    fix_hint="Install Python 3.11 or later",
                )
            )

    def _check_core_deps(self, report: DoctorReport) -> None:
        core = [
            ("pydantic", "pydantic"),
            ("httpx", "httpx"),
            ("yaml", "pyyaml"),
        ]
        for module, pip_name in core:
            try:
                mod = __import__(module)
                version = getattr(mod, "__version__", "installed")
                report.results.append(
                    CheckResult(
                        name=pip_name,
                        status="ok",
                        message=version,
                    )
                )
            except ImportError:
                report.results.append(
                    CheckResult(
                        name=pip_name,
                        status="fail",
                        message="not installed",
                        fix_hint=f"pip install {pip_name}",
                    )
                )

    def _check_optional_deps(self, report: DoctorReport) -> None:
        optional = [
            ("anthropic", "anthropic", "Anthropic Claude SDK"),
            ("openai", "openai", "OpenAI SDK"),
            ("fastapi", "fastapi", "FastAPI (UI server)"),
            ("uvicorn", "uvicorn", "Uvicorn (ASGI server)"),
        ]
        for module, pip_name, desc in optional:
            try:
                mod = __import__(module)
                version = getattr(mod, "__version__", "installed")
                report.results.append(
                    CheckResult(
                        name=desc,
                        status="ok",
                        message=version,
                    )
                )
            except ImportError:
                report.results.append(
                    CheckResult(
                        name=desc,
                        status="warn",
                        message="not installed",
                        fix_hint=f"pip install {pip_name} (optional)",
                    )
                )

    def _check_api_keys(self, report: DoctorReport) -> None:
        keys = [
            ("ANTHROPIC_API_KEY", "Anthropic"),
            ("OPENAI_API_KEY", "OpenAI"),
            ("GOOGLE_API_KEY", "Google Gemini"),
        ]
        any_set = False
        for env_var, provider in keys:
            if os.environ.get(env_var):
                report.results.append(
                    CheckResult(
                        name=f"{provider} API Key",
                        status="ok",
                        message=f"{env_var} is set",
                    )
                )
                any_set = True
        if not any_set:
            report.results.append(
                CheckResult(
                    name="API Keys",
                    status="warn",
                    message="no API keys detected",
                    fix_hint="Set ANTHROPIC_API_KEY or OPENAI_API_KEY, or use Ollama for free local inference",
                )
            )

    def _check_ollama(self, report: DoctorReport) -> None:
        try:
            import httpx

            resp = httpx.get(
                f"{os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')}/api/tags",
                timeout=3.0,
            )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                names = [m.get("name", "?") for m in models[:5]]
                report.results.append(
                    CheckResult(
                        name="Ollama",
                        status="ok",
                        message=f"running ({len(models)} models: {', '.join(names)})",
                    )
                )
            else:
                report.results.append(
                    CheckResult(
                        name="Ollama",
                        status="warn",
                        message="not running",
                        fix_hint="Start Ollama: ollama serve",
                    )
                )
        except Exception:  # noqa: BLE001 — Ollama probe must always degrade to "not running" warn, never abort doctor
            report.results.append(
                CheckResult(
                    name="Ollama",
                    status="warn",
                    message="not running",
                    fix_hint="Install Ollama from https://ollama.com (optional, free local inference)",
                )
            )

    def _check_config(self, report: DoctorReport) -> None:
        if self._config_path is None:
            for candidate in ["config.yaml", "config.example.yaml"]:
                if Path(candidate).exists():
                    self._config_path = Path(candidate)
                    break

        if self._config_path is None or not self._config_path.exists():
            report.results.append(
                CheckResult(
                    name="Config",
                    status="warn",
                    message="no config.yaml found",
                    fix_hint="Run: python -m runtime setup",
                )
            )
            return

        try:
            # Absolute import: the config package is runtime.platform.config,
            # NOT a subpackage of observability. The old relative
            # ``.config.loader`` resolved to
            # runtime.platform.observability.config.loader (nonexistent), so
            # this check always failed with ModuleNotFoundError and aborted
            # `quickstart`. Match the canonical import used across the CLI.
            from runtime.platform.config import load_from_yaml

            load_from_yaml(self._config_path)
            report.results.append(
                CheckResult(
                    name="Config",
                    status="ok",
                    message=f"{self._config_path} valid",
                )
            )
        except Exception as e:
            report.results.append(
                CheckResult(
                    name="Config",
                    status="fail",
                    message=f"{self._config_path}: {e}",
                    fix_hint="Fix config or run: python -m runtime setup",
                )
            )

    def _check_data_dir(self, report: DoctorReport) -> None:
        data_dir = Path(os.path.expanduser("~/.echo"))
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            test_file = data_dir / ".doctor_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            report.results.append(
                CheckResult(
                    name="Data Dir",
                    status="ok",
                    message=f"{data_dir} writable",
                )
            )
        except (OSError, ValueError, TypeError) as exc:
            report.results.append(
                CheckResult(
                    name="Data Dir",
                    status="fail",
                    message=f"{data_dir}: {exc}",
                    fix_hint=f"Check permissions: {data_dir}",
                )
            )
