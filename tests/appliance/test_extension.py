"""P0 契约:appliance 经 agent 官方扩展点挂载(OCTOPUS_APP_EXTENSIONS),不再 fork app.py。

守护「消费而非 fork」:appliance.extension:register_app 在 OCTOPUS_APPLIANCE=1 时
把启动器/认证/文件路由挂到传入的 app 上;关掉则 no-op。见 appliance/extension.py。
"""

from __future__ import annotations

from fastapi import FastAPI

from appliance.extension import register_app
from runtime.platform.extensions import AppExtensionContext


def _routes(app: FastAPI) -> list[str]:
    return [getattr(r, "path", "") for r in app.router.routes]


def test_register_app_mounts_appliance_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOPUS_APPLIANCE", "1")
    monkeypatch.setenv("OCTOPUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OCTOPUS_ADMIN_PASSWORD", "test-pass-123456")
    nas_root = tmp_path / "nas"
    nas_root.mkdir()  # 真实部署里是已挂载的存储卷
    monkeypatch.setenv("OCTOPUS_NAS_ROOT", str(nas_root))

    app = FastAPI()
    register_app(app, AppExtensionContext(identity_store=None))

    paths = _routes(app)
    assert any(p.startswith("/api/appliance/apps") for p in paths), "启动器路由应挂载"
    assert any("/api/auth/local" in p for p in paths), "本地认证路由应挂载"
    assert any("/api/appliance/files" in p for p in paths), "文件管理器路由应挂载"


def test_register_app_is_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_APPLIANCE", raising=False)
    monkeypatch.setenv("OCTOPUS_DATA_DIR", str(tmp_path))

    app = FastAPI()
    before = len(_routes(app))
    register_app(app, AppExtensionContext(identity_store=None))
    # OCTOPUS_APPLIANCE 未开 → 不挂任何 appliance 路由(语义同原 fork 块)。
    assert len(_routes(app)) == before
