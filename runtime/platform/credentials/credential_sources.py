from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

_LOG = logging.getLogger("echo.credentials.sources")


class CredentialSource(ABC):
    @abstractmethod
    def load(self) -> dict[str, str]: ...


class EnvVarSource(CredentialSource):
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}

    def load(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, env_var in self.mapping.items():
            value = os.environ.get(env_var)
            if value:
                result[name] = value
        return result


class FileSource(CredentialSource):
    def __init__(self, path: str | Path, fmt: str = "json") -> None:
        self.path = Path(path)
        self.fmt = fmt

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            _LOG.debug("credential file not found: %s", self.path)
            return {}
        try:
            content = self.path.read_text(encoding="utf-8")
            if self.fmt == "json":
                data = json.loads(content)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            elif self.fmt == "dotenv":
                result: dict[str, str] = {}
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, _, v = line.partition("=")
                        result[k.strip()] = v.strip().strip("\"'")
                return result
        except Exception as exc:
            _LOG.warning("credential file load failed: %s", exc)
        return {}


__all__ = ["CredentialSource", "EnvVarSource", "FileSource"]
