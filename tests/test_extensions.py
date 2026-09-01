"""扩展发现机制:消费者经环境变量挂路由/注册技能,不 fork agent。"""

from __future__ import annotations

import sys
import types

from runtime.platform.extensions import (
    AppExtensionContext,
    load_app_extensions,
    load_skill_extensions,
)


def _fake_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def test_app_extension_called_with_app_and_context(monkeypatch):
    seen = []
    _fake_module(
        "octo_test_appext",
        register_app=lambda app, ctx: seen.append((app, ctx)),
    )
    monkeypatch.setenv("ECHO_APP_EXTENSIONS", "octo_test_appext")
    n = load_app_extensions("APP", AppExtensionContext(identity_store="ID"))
    assert n == 1
    assert seen[0][0] == "APP"
    assert seen[0][1].identity_store == "ID"


def test_skill_extension_custom_func_and_count(monkeypatch):
    _fake_module(
        "octo_test_skillext",
        my_register=lambda registry: (registry.append("pm"), 3)[1],
    )
    monkeypatch.setenv("ECHO_SKILL_EXTENSIONS", "octo_test_skillext:my_register")
    reg: list = []
    assert load_skill_extensions(reg) == 3
    assert reg == ["pm"]


def test_multiple_comma_separated(monkeypatch):
    _fake_module("octo_ext_a", register_skills=lambda r: 1)
    _fake_module("octo_ext_b", register_skills=lambda r: 2)
    monkeypatch.setenv("ECHO_SKILL_EXTENSIONS", "octo_ext_a, octo_ext_b")
    assert load_skill_extensions([]) == 3


def test_unconfigured_is_noop(monkeypatch):
    monkeypatch.delenv("ECHO_APP_EXTENSIONS", raising=False)
    monkeypatch.delenv("ECHO_SKILL_EXTENSIONS", raising=False)
    assert load_app_extensions("APP", AppExtensionContext()) == 0
    assert load_skill_extensions([]) == 0


def test_failing_extension_is_isolated(monkeypatch):
    def boom(registry):
        raise RuntimeError("boom")

    _fake_module("octo_bad_ext", register_skills=boom)
    monkeypatch.setenv("ECHO_SKILL_EXTENSIONS", "octo_bad_ext")
    # 扩展抛错只记日志、不传播,返回 0。
    assert load_skill_extensions([]) == 0


def test_missing_module_isolated(monkeypatch):
    monkeypatch.setenv("ECHO_APP_EXTENSIONS", "nonexistent_module_xyz")
    assert load_app_extensions("APP", AppExtensionContext()) == 0

