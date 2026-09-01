"""Regression tests for ``react_guards._final_answer_requests_user_help``.

The function is the early-out for the completion guards: when it
returns True, the guards short-circuit and the agent's Final Answer
is accepted without checking whether todos are complete or whether
verification ran. Bug it has masked in the wild:

  * Research reports about API security / auth / permissions naturally
    mention "权限"/"token"/"permission"/"批准" all over their body.
    The previous implementation matched any substring → reports got
    accepted on the FIRST emitted ``Final Answer:`` even though the
    checklist was empty and no real research had finished.

The current implementation tightens the matcher: only the LAST 400
characters are scanned, tight markers must include the action verb
("please confirm", not just "confirm"), and short single-word
markers (``token``/``permission``) only count when the answer is
short enough to be a sign-off rather than a report.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _final_answer_requests_user_help,
)


class TestNotAHelpRequest:
    """Long-form report content that incidentally mentions auth-related
    words is NOT a help request. These would have triggered the bug."""

    def test_research_report_about_api_security(self) -> None:
        report = (
            "# API Security Research Report\n\n"
            "## 1. Authentication Methods\n\n"
            "Modern APIs typically use OAuth 2.0 tokens for authentication. "
            "The token-based flow grants the client temporary access and "
            "the server validates each request's permission claims. "
            "Misconfigured CORS combined with weak token validation can "
            "expose data to unauthorized callers.\n\n"
            "## 2. Common Vulnerabilities\n\n"
            "Stolen API keys remain the leading cause of breaches. "
            "Permission escalation through JWT confusion attacks is "
            "the second most reported vector. Password-based login flows "
            "lack the device-binding modern OIDC offers.\n\n"
            "## 3. Recommendations\n\n"
            "- Rotate API keys every 30 days\n"
            "- Use scoped permission tokens, not master credentials\n"
            "- Enforce MFA on every admin login path\n"
            "- Audit blocked requests and confirm rate limits engage."
        )
        assert _final_answer_requests_user_help(report) is False

    def test_research_report_in_chinese_about_oauth(self) -> None:
        report = (
            "# OAuth 2.0 调研报告\n\n"
            "## 1. 协议概述\n\n"
            "OAuth 2.0 是当前主流的授权协议。它通过 token 实现委托访问，"
            "客户端在获得用户批准后可以代表用户调用受保护资源。"
            "权限范围由 scope 字段约束。\n\n"
            "## 2. 常见漏洞\n\n"
            "- 重定向 URI 校验缺失会被钓鱼攻击利用\n"
            "- 弱权限边界让普通 token 也能访问管理员接口\n"
            "- 密码模式（password grant）已被官方弃用\n\n"
            "## 3. 实施建议\n\n"
            "建议所有新接入方采用 PKCE，确认后再发布到生产。"
        )
        assert _final_answer_requests_user_help(report) is False

    def test_long_technical_doc_does_not_trigger(self) -> None:
        # 1500-char essay heavy on auth vocabulary — must not match.
        body = (
            "Tokens, permissions, and login flows are the three pillars "
            "of any auth system. A token without scoped permissions is "
            "a credential leak waiting to happen. Login flows that don't "
            "rotate credentials extend the blast radius of any breach. "
        ) * 5
        assert _final_answer_requests_user_help(body) is False


class TestActualHelpRequest:
    """Genuine sign-off / hand-off messages — guard MUST trigger."""

    def test_short_zh_help_request(self) -> None:
        msg = "我无法继续，请你提供 API key 后我再继续执行。"
        assert _final_answer_requests_user_help(msg) is True

    def test_short_en_help_request(self) -> None:
        msg = "I cannot continue. Please provide your API key."
        assert _final_answer_requests_user_help(msg) is True

    def test_pls_confirm_pattern(self) -> None:
        msg = "Plan ready. Please confirm to proceed with destructive operations."
        assert _final_answer_requests_user_help(msg) is True

    def test_blocked_by_pattern(self) -> None:
        msg = "Stopped: blocked by missing credential. Restart after providing one."
        assert _final_answer_requests_user_help(msg) is True

    def test_short_token_signoff(self) -> None:
        # Short answer mentioning token in a sign-off context should
        # still trigger via the loose-marker fallback.
        msg = "需要权限 token，请补充后继续。"
        assert _final_answer_requests_user_help(msg) is True

    def test_long_help_request_with_tail(self) -> None:
        # Tail-only scan: a long report that ENDS with a help ask
        # should still trip the guard.
        msg = (
            "# Diagnostics\n\n"
            "Network latency is normal, the registry is healthy, the "
            "build pipeline ran clean. Caches are warm.\n\n"
            "## Outcome\n\n"
            "Cannot complete the deploy without production credentials. "
            "Please provide the production API key so I can finish."
        )
        assert _final_answer_requests_user_help(msg) is True


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert _final_answer_requests_user_help("") is False

    def test_none_safe(self) -> None:
        # Defensive: parser may pass None during refactors.
        assert _final_answer_requests_user_help(None) is False  # type: ignore[arg-type]

    def test_whitespace_only(self) -> None:
        assert _final_answer_requests_user_help("   \n\t  ") is False
