from __future__ import annotations

from runtime.core.graph_runtime import GraphRuntime
from runtime.platform.models import ArmId, SkillId

from .base import Worker

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


_ECHO_SOUL = """You are a general-purpose assistant. \
You handle everyday tasks — writing, planning, research, summarization, Q&A. \
Prefer concise answers. When unsure, ask one focused clarifying question."""

_CODER_SOUL = """You are a pragmatic software engineer. \
You read code carefully before changing it, prefer small reversible edits, \
and run tests before declaring a fix complete. You value working software \
over premature abstraction."""

_VIBE_SELLING_SOUL = """You are an e-commerce growth operator who thinks in \
conversion funnels, content hooks, and creator-style copy. \
You draft product pages, social posts, and campaign briefs with tight, \
scannable language and a clear call to action."""

_ECOMMERCE_MIND_SOUL = """You are an e-commerce operations advisor covering \
category strategy, supply chain, traffic, CRO, and fulfillment. \
You give structured, data-oriented recommendations with explicit trade-offs, \
not generic marketing fluff."""

_MOBILE_OPERATOR_SOUL = """You operate a real Android device on the user's behalf. \
You read the screen carefully before each action, prefer the smallest gesture \
that gets the job done, and confirm with the user before destructive operations \
(payments, sharing, deletion). When the screen state is ambiguous, you take a \
screenshot and re-plan."""

_MOBILE_BROWSER_OPERATOR_SOUL = """You drive a mobile browser (GeckoView) on \
Android for web automation. You favor stable selectors, respect anti-bot \
boundaries, and check page-load state before interacting. When a flow breaks, \
you describe what you saw and propose a different selector strategy rather \
than retrying blindly."""

#

_WEB_READ = [
    SkillId("fetch_url"),
    SkillId("web_search"),
    SkillId("web_fetch"),
    SkillId("crawl_site"),
    SkillId("platform_search"),
    SkillId("platform_read"),
    SkillId("platform_collect"),
    SkillId("platform_monitor"),
    SkillId("reach_doctor"),
]

_BROWSER_READ = [
    SkillId("browser_get"),
    SkillId("browser_extract"),
    SkillId("browser_navigate"),
    SkillId("browser_screenshot"),
]

_BROWSER_INTERACT = [
    SkillId("browser_click"),
    SkillId("browser_type"),
    SkillId("browser_upload"),
    SkillId("browser_scroll"),
    SkillId("browser_wait"),
]

_WRITE_FS = [
    SkillId("write_text_file"),
    SkillId("append_text_file"),
    SkillId("edit_text_file"),
]

_GIT_ALL = [
    SkillId("git_status"),
    SkillId("git_diff"),
    SkillId("git_log"),
    SkillId("git_add"),
    SkillId("git_commit"),
    SkillId("git_branch"),
]

_CODE_QUALITY = [
    SkillId("run_tests"),
    SkillId("lint_check"),
    SkillId("format_code"),
]

_SHELL_EXEC = [
    SkillId("exec_shell"),
    SkillId("background_exec"),
    SkillId("read_background_output"),
    SkillId("read_shell_output"),
    SkillId("kill_background_exec"),
    SkillId("kill_shell"),
]

# Echo Mobile · Phase 0 概念验证
# 详见 docs/mobile/skills.md 与 docs/adr/011-echo-mobile.md
_MOBILE_OPS = [
    # 基础操作
    SkillId("android.tap"),
    SkillId("android.swipe"),
    SkillId("android.input_text"),
    SkillId("android.long_press"),
    SkillId("android.system_key"),
    # 屏幕感知
    SkillId("android.get_screen_info"),
    SkillId("android.take_screenshot"),
    SkillId("android.find_node"),
    SkillId("android.find_text"),
    # 应用管理
    SkillId("android.open_app"),
    SkillId("android.install_app"),
    SkillId("android.get_installed_apps"),
    SkillId("android.wait"),
    # 智能复合
    SkillId("android.scroll_to_find"),
    SkillId("android.detect_dialog"),
    SkillId("android.find_and_tap"),
    SkillId("android.get_current_app"),
    # 任务控制
    SkillId("android.finish"),
    SkillId("android.fail"),
    # 文件 / 剪贴板
    SkillId("android.read_file"),
    SkillId("android.write_file"),
    SkillId("android.get_clipboard"),
    SkillId("android.set_clipboard"),
    # 浏览器（Phase 7，远期）
    SkillId("android.browser.navigate"),
    SkillId("android.browser.get_dom"),
    SkillId("android.browser.click"),
    SkillId("android.browser.type"),
    SkillId("android.browser.screenshot"),
    SkillId("android.browser.evaluate"),
    SkillId("android.browser.install_extension"),
]

_MOBILE_BROWSER_OPS = [
    SkillId("android.browser.navigate"),
    SkillId("android.browser.get_dom"),
    SkillId("android.browser.click"),
    SkillId("android.browser.type"),
    SkillId("android.browser.screenshot"),
    SkillId("android.browser.evaluate"),
]


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def make_web_read_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("web_read_arm"),
        affinity=["web", "fetch", "search", "research"],
        allowed_skills=[*_WEB_READ],
        runtime=runtime,
        display_name="Web Reader",
        description="Fetches URLs and searches the web.",
        icon="🌐",
    )


def make_browser_read_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("browser_read_arm"),
        affinity=["browser", "scrape", "render", "capture"],
        allowed_skills=[*_BROWSER_READ],
        runtime=runtime,
        display_name="Browser Reader",
        description="Renders pages (JS-capable), extracts content, takes screenshots.",
        icon="🔍",
    )


def make_browser_interact_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("browser_interact_arm"),
        affinity=["browser", "interact", "click", "type", "automation"],
        allowed_skills=[*_BROWSER_INTERACT],
        runtime=runtime,
        display_name="Browser Operator",
        description="Clicks, types, scrolls, and waits on rendered pages.",
        icon="🖱️",
    )


def make_fs_writer_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("fs_writer_arm"),
        affinity=["file", "write", "edit", "author"],
        allowed_skills=[*_WRITE_FS],
        runtime=runtime,
        display_name="Filesystem Writer",
        description="Writes, appends, and edits text files (sandbox-enforced).",
        icon="✏️",
    )


def make_git_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("git_arm"),
        affinity=["git", "vcs", "version", "commit"],
        allowed_skills=[*_GIT_ALL],
        runtime=runtime,
        display_name="Git Operator",
        description="Runs git status / diff / log / add / commit / branch.",
        icon="🔀",
    )


def make_shell_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("shell_arm"),
        affinity=["shell", "exec", "run", "test"],
        allowed_skills=[*_SHELL_EXEC],
        runtime=runtime,
        display_name="Shell Executor",
        description="Executes shell commands, including background jobs.",
        icon="🐚",
    )


def make_desktop_operator_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("desktop_operator_arm"),
        affinity=["desktop", "gui", "vision", "automation"],
        allowed_skills=[
            SkillId("screen_capture"),
            SkillId("screen_info"),
            SkillId("mouse_click"),
            SkillId("mouse_move"),
            SkillId("keyboard_type"),
            SkillId("keyboard_press"),
            SkillId("computer_observe"),
            SkillId("computer_plan_next"),
            SkillId("computer_preview_action"),
            SkillId("computer_execute_token"),
            SkillId("computer_use_loop"),
            SkillId("computer_uia_status"),
            SkillId("computer_uia_tree"),
            SkillId("computer_uia_find"),
        ],
        runtime=runtime,
        display_name="Desktop Operator",
        description=(
            "Drives the desktop GUI via screen capture + mouse/keyboard · "
            "vision-guided through computer_use_loop."
        ),
        icon="🖥️",
    )


# ═══════════════════════════════════════════════════════════
#  Echo Mobile · 移动操作 Arm（Phase 0 概念验证）
#  详见 docs/mobile/architecture.md 与 docs/adr/011-echo-mobile.md
# ═══════════════════════════════════════════════════════════


def make_mobile_operator_arm(runtime: GraphRuntime) -> Worker:
    """移动操作 Arm —— 控制 Android 设备.

    与 desktop_operator_arm 并列，是 Echo Mobile 的核心 Arm。
    通过 Tentacle 抽象（runtime/tentacle/mobile.py）连接 Echo Mobile 客户端。
    工具调用通过 WebSocket + JSON-RPC 2.0 路由到真机（详见 docs/mobile/protocol.md）。
    """
    return Worker(
        arm_id=ArmId("mobile_operator_arm"),
        affinity=[
            "mobile",
            "android",
            "gui",
            "automation",
            "app",
            "手机",
            "安卓",
            "触手",
            "打开app",
            "自动操作",
            "打开",
        ],
        allowed_skills=[*_MOBILE_OPS],
        runtime=runtime,
        display_name="Mobile Operator",
        description=(
            "Drives an Android device via accessibility service + gestures · "
            "30 mobile skills covering tap/swipe/input/感知/管理."
        ),
        soul=_MOBILE_OPERATOR_SOUL,
        icon="📱",
    )


def make_mobile_browser_operator_arm(runtime: GraphRuntime) -> Worker:
    """移动浏览器操作 Arm —— 在 Android 上用 GeckoView 自动化.

    集成 GeckoView（主力）· WebExtension API 桥接 · 反爬免疫。
    后备：Chromium AAR（仅当需要 Chromium 指纹时）。
    """
    return Worker(
        arm_id=ArmId("mobile_browser_operator_arm"),
        affinity=[
            "mobile",
            "android",
            "browser",
            "anti_bot",
            "scraping",
            "浏览器",
            "网页",
            "反爬",
            "抓取",
            "网站自动化",
        ],
        allowed_skills=[*_MOBILE_BROWSER_OPS],
        runtime=runtime,
        display_name="Mobile Browser Operator",
        description=(
            "GeckoView on Android · WebExtension API · "
            "anti-bot fingerprint · supports Firefox extensions."
        ),
        soul=_MOBILE_BROWSER_OPERATOR_SOUL,
        icon="🌐",
    )


# ═══════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════


def make_general_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("general_arm"),
        affinity=["general", "assistant", "io", "research", "writing"],
        allowed_skills=[*_WEB_READ],
        runtime=runtime,
        display_name="Eve",
        description=("AI 办公首席助理，负责会议、邮件、文档、日程、汇报和复杂任务推进。"),
        soul=_ECHO_SOUL,
        icon="🐙",
    )


def make_coder_arm_v2(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("coder_arm_v2"),
        affinity=["code", "git", "test", "debug", "refactor", "shell", "quality"],
        allowed_skills=[
            *_WRITE_FS,
            *_GIT_ALL,
            *_CODE_QUALITY,
            *_SHELL_EXEC,
        ],
        runtime=runtime,
        display_name="Kane",
        description=("AI 应用架构师与全栈工程师，负责模型、Agent、工具、数据与生产交付。"),
        soul=_CODER_SOUL,
        icon="💻",
    )


def make_vibe_selling_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("vibe_selling_arm"),
        affinity=[
            "ecommerce",
            "content",
            "social",
            "marketing",
            "copywriting",
            "browser",
            "shopify",
            "storefront",
            "listing",
        ],
        allowed_skills=[
            *_WEB_READ,
            *_BROWSER_READ,
            *_WRITE_FS,
        ],
        runtime=runtime,
        display_name="Luna",
        description=("AI 漫剧制片人与内容增长导演，负责剧本、分镜、生成制作和平台增长。"),
        soul=_VIBE_SELLING_SOUL,
        icon="✨",
    )


def make_ecommerce_mind_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("ecommerce_mind_arm"),
        affinity=[
            "ecommerce",
            "operations",
            "analytics",
            "strategy",
            "supply",
            "traffic",
            "cro",
            "shopify",
            "storefront",
            "listing",
        ],
        allowed_skills=[
            *_WEB_READ,
            *_BROWSER_READ,
        ],
        runtime=runtime,
        display_name="Shion",
        description=("跨境电商增长负责人，负责选品、内容、投放、定价、库存、履约与利润。"),
        soul=_ECOMMERCE_MIND_SOUL,
        icon="📊",
    )


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


PRESET_FACTORIES = [
    make_general_arm,
    make_coder_arm_v2,
    make_vibe_selling_arm,
    make_ecommerce_mind_arm,
    make_mobile_operator_arm,
    make_mobile_browser_operator_arm,
]


def make_all_presets(runtime: GraphRuntime) -> list[Worker]:
    return [factory(runtime) for factory in PRESET_FACTORIES]
