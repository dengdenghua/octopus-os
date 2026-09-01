"""Echo Mobile 集成检查脚本 —— 方案 F Kotlin 端自检.

在跑 `./gradlew test` 之前用这个脚本做静态检查：
  1. 30 SKILL.md 都同步到 assets/
  2. echo_mobile/ 9 个 Kotlin 文件齐全
  3. 3 个 test 文件齐全
  4. build.gradle.kts 加了 testImplementation 依赖
  5. libs.versions.toml 有 mockwebserver / coroutines-test / robolectric
  6. ClawApplication 引用了 BrainModeSelector
  7. KVUtils 提供了 echo 相关方法

跑法：``python examples/verify_apkclaw_setup.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_APK = _REPO.parent / "echo-mobile"
_OK = "\033[92m✓\033[0m"
_FAIL = "\033[91m✗\033[0m"


def _skill_id(path: Path) -> str | None:
    """Extract the ``name:`` frontmatter value from a SKILL.md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip()
    return None


def _display_path(path: Path) -> str:
    """Return a readable path even when the target is a sibling checkout."""
    try:
        return str(path.relative_to(_REPO))
    except ValueError:
        try:
            return str(path.relative_to(_REPO.parent))
        except ValueError:
            return str(path)


def check(condition: bool, msg: str, errors: list[str]) -> None:
    if condition:
        print(f"  {_OK} {msg}")
    else:
        print(f"  {_FAIL} {msg}")
        errors.append(msg)


def section(name: str) -> None:
    print()
    print(f"\033[1m【{name}】\033[0m")


def main() -> int:
    errors: list[str] = []

    # ── 1. 30 SKILL.md 同步 ───────────────────────────────
    section("1. assets/skills/mobile/ SKILL.md")
    skills_dir = _APK / "app/src/main/assets/skills/mobile"
    check(skills_dir.exists(), f"目录存在: {_display_path(skills_dir)}", errors)
    if skills_dir.exists():
        skill_files = [f for f in skills_dir.glob("*.md")
                       if f.name.lower() not in ("readme.md", "skill.md")]
        check(len(skill_files) == 30, f"30 个 SKILL.md 完整（实际 {len(skill_files)}）", errors)
        # 验证精简版格式
        sample = skills_dir / "tap.md"
        if sample.exists():
            content = sample.read_text(encoding="utf-8")
            check('"type": "object"' in content, "tap.md 用 JSON Schema 单行 parameters", errors)
            check("android.tap" in content, "tap.md 包含正确 name", errors)
            check("description:" in content, "tap.md 包含 description 字段", errors)
        # 与源目录对齐。源目录是 ``skill-name/SKILL.md``，Android assets
        # 是 ``skill.name.md``，所以用 frontmatter name 做事实源比较。
        src_dir = _REPO / "runtime/tentacle/mobile/skills"
        src_ids = {
            sid for sid in (_skill_id(f) for f in src_dir.glob("*/SKILL.md"))
            if sid
        }
        asset_ids = {
            sid for sid in (_skill_id(f) for f in skills_dir.glob("*.md")
                            if f.name.lower() not in ("readme.md", "skill.md"))
            if sid
        }
        check(src_ids == asset_ids,
              f"assets 与源目录 SKILL.md 名单一致（{len(src_ids)} 个）", errors)
        if src_ids != asset_ids:
            print(f"    源独有: {src_ids - asset_ids}")
            print(f"    assets 独有: {asset_ids - src_ids}")

    # ── 2. echo_mobile/ Kotlin 文件 ──────────────────────
    section("2. echo_mobile/ 核心 Kotlin 文件")
    om_dir = _APK / "app/src/main/java/com/apk/claw/android/echo_mobile"
    required_main = [
        "LightweightLlmClient.kt",
        "LightweightReAct.kt",
        "SkillManifest.kt",
        "BrainModeSelector.kt",
        "ChatTypes.kt",
        "EchoMobileClient.kt",
        "Protocol.kt",
        "StartupMode.kt",
    ]
    for fname in required_main:
        check((om_dir / fname).exists(), f"{fname} 存在", errors)

    # 行数检查（方案 F 承诺）
    for fname in ["LightweightLlmClient.kt", "LightweightReAct.kt", "SkillManifest.kt"]:
        f = om_dir / fname
        if f.exists():
            lines = len(f.read_text(encoding="utf-8").splitlines())
            check(lines < 400, f"{fname} < 400 行（实际 {lines} 行）", errors)

    # ── 3. 测试文件 ─────────────────────────────────────
    section("3. test/ Kotlin 测试文件")
    test_dir = _APK / "app/src/test/java/com/apk/claw/android/echo_mobile"
    required_tests = [
        "SkillManifestTest.kt",
        "LightweightLlmClientTest.kt",
        "LightweightReActTest.kt",
    ]
    for fname in required_tests:
        check((test_dir / fname).exists(), f"{fname} 存在", errors)

    # ── 4. build.gradle.kts 依赖 ─────────────────────────
    section("4. build.gradle.kts 测试依赖")
    gradle = _APK / "app/build.gradle.kts"
    gradle_text = gradle.read_text(encoding="utf-8") if gradle.exists() else ""
    for dep in ["libs.junit", "libs.coroutines.test", "libs.mockwebserver",
                "libs.robolectric", "libs.mockito.core"]:
        check(f"testImplementation({dep})" in gradle_text,
              f"testImplementation({dep}) 已添加", errors)

    # ── 5. libs.versions.toml ──────────────────────────
    section("5. gradle/libs.versions.toml")
    toml = _APK / "gradle/libs.versions.toml"
    toml_text = toml.read_text(encoding="utf-8") if toml.exists() else ""
    for lib in ["mockwebserver", "coroutines-test", "robolectric", "mockito-core"]:
        check(lib in toml_text, f"包含 {lib}", errors)
    check('coroutines = "1.7.3"' in toml_text, 'coroutines = "1.7.3" 版本', errors)

    # ── 6. ClawApplication 接入 ────────────────────────
    section("6. ClawApplication 接入")
    app = _APK / "app/src/main/java/com/apk/claw/android/ClawApplication.kt"
    app_text = app.read_text(encoding="utf-8") if app.exists() else ""
    check("BrainModeSelector" in app_text, "ClawApplication 引用 BrainModeSelector", errors)
    check("EchoMobileClient" in app_text, "ClawApplication 引用 EchoMobileClient", errors)
    check("initEchoMobile" in app_text, "ClawApplication 调 initEchoMobile()", errors)
    check("SkillManifest.loadFromAssets" in app_text, "启动时加载 30 SKILL.md", errors)
    check("brainSelector.start" in app_text, "启动 30s 健康检查", errors)
    check('companion object' in app_text, "brainSelector 暴露成 companion 单例", errors)

    # ── 7. KVUtils echo 方法 ───────────────────────
    section("7. KVUtils echo 配置方法")
    kv = _APK / "app/src/main/java/com/apk/claw/android/utils/KVUtils.kt"
    kv_text = kv.read_text(encoding="utf-8") if kv.exists() else ""
    check("KEY_ECHO_RPC_URL" in kv_text, "KEY_ECHO_RPC_URL 定义", errors)
    check("KEY_ECHO_AUTH_TOKEN" in kv_text, "KEY_ECHO_AUTH_TOKEN 定义", errors)
    check("getEchoRpcUrl()" in kv_text, "getEchoRpcUrl() 方法", errors)
    check("setEchoRpcUrl(value" in kv_text, "setEchoRpcUrl(...) setter", errors)
    check("getEchoAuthToken()" in kv_text, "getEchoAuthToken() 方法", errors)

    # ── 总结 ─────────────────────────────────────────
    print()
    print("=" * 60)
    if errors:
        print(f"\033[91m❌ {len(errors)} 个问题\033[0m")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\033[92m✅ 所有检查通过（0 个问题）\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
