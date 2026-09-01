"""容器 → 启动器应用卡片的纯映射(无 IO,便于单测)。

元数据从容器 label 级联读取,兼容主流自托管生态的约定
(自有 sh.echo.* 优先,其次 CasaOS / homepage / Unraid / OCI)。
Web 端口只返回端口号,完整 URL 由前端用浏览器可见的主机名拼装——
后端不知道 NAS 对外的地址。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

_LEGACY_LABEL_NAMESPACE = "sh.octo" + "pus"

NAME_LABELS = (
    "sh.echo.name",
    f"{_LEGACY_LABEL_NAMESPACE}.name",
    "casaos.name",
    "homepage.name",
    "org.opencontainers.image.title",
)
ICON_LABELS = (
    "sh.echo.icon",
    f"{_LEGACY_LABEL_NAMESPACE}.icon",
    "casaos.icon",
    "icon",
    "homepage.icon",
    "net.unraid.docker.icon",
)
WEBUI_LABELS = (
    "sh.echo.webui",
    f"{_LEGACY_LABEL_NAMESPACE}.webui",
    "casaos.webui",
    "homepage.href",
    "net.unraid.docker.webui",
)
DESCRIPTION_LABELS = (
    "sh.echo.description",
    f"{_LEGACY_LABEL_NAMESPACE}.description",
    "casaos.description",
    "org.opencontainers.image.description",
)
HIDE_LABELS = ("sh.echo.hide", f"{_LEGACY_LABEL_NAMESPACE}.hide")

# 多端口容器挑"哪个是 Web UI"的启发式优先级。
WEB_PORT_PREFERENCE = (80, 443, 3000, 8080, 8096, 8123, 9000, 5000, 8000)


@dataclass
class ApplianceApp:
    id: str
    name: str
    description: str
    icon: str
    state: str  # running / exited / paused / …
    status: str  # e.g. "Up 3 hours"
    image: str
    web_port: int | None
    web_url: str | None  # label 显式给出的完整地址(优先于 web_port)
    ports: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_label(labels: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = labels.get(key, "").strip()
        if value:
            return value
    return ""


def _published_ports(container: dict[str, Any]) -> list[int]:
    ports: list[int] = []
    for entry in container.get("Ports") or []:
        public = entry.get("PublicPort")
        if entry.get("Type") == "tcp" and isinstance(public, int):
            ports.append(public)
    return sorted(set(ports))


def _pick_web_port(ports: list[int]) -> int | None:
    for preferred in WEB_PORT_PREFERENCE:
        if preferred in ports:
            return preferred
    return ports[0] if ports else None


def _safe_web_url(labels: dict[str, str]) -> str | None:
    raw = _first_label(labels, WEBUI_LABELS).strip()
    if not raw:
        return None
    if len(raw) > 2_048 or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        return None
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return raw


def container_to_app(container: dict[str, Any]) -> ApplianceApp | None:
    """单个容器的映射;被 sh.echo.hide 标记的返回 None。"""
    labels: dict[str, str] = container.get("Labels") or {}
    if any(labels.get(key, "").lower() in ("1", "true", "yes") for key in HIDE_LABELS):
        return None

    names = container.get("Names") or []
    raw_name = names[0].lstrip("/") if names else container.get("Id", "")[:12]
    ports = _published_ports(container)

    return ApplianceApp(
        id=str(container.get("Id", ""))[:12],
        name=_first_label(labels, NAME_LABELS) or raw_name,
        description=_first_label(labels, DESCRIPTION_LABELS) or str(container.get("Image", "")),
        icon=_first_label(labels, ICON_LABELS),
        state=str(container.get("State", "unknown")),
        status=str(container.get("Status", "")),
        image=str(container.get("Image", "")),
        web_port=_pick_web_port(ports),
        web_url=_safe_web_url(labels),
        ports=ports,
    )


def build_catalog(containers: list[dict[str, Any]]) -> list[ApplianceApp]:
    """运行中的排前面,其余按名称;隐藏项剔除。"""
    apps = [app for c in containers if (app := container_to_app(c)) is not None]
    apps.sort(key=lambda a: (a.state != "running", a.name.lower()))
    return apps
