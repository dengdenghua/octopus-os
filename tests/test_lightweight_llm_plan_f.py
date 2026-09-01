"""方案 F · LightweightLlmClient + LightweightReAct 端到端测试.

覆盖：

- 客户端：单次 chat / 多轮 / OpenAI 工具格式
- Manifest：扫真实 SKILL.md 目录 + description 续行 + parameters list-of-objects → JSON Schema
- ReAct：DONE / MAX_STEPS / STUCK 三种结局
- ReAct：上下文压缩不崩
"""

from __future__ import annotations

from pathlib import Path

from runtime.tentacle.llm import (
    ChatMessage,
    FakeTransport,
    LightweightLlmClient,
    LightweightReAct,
    LlmConfig,
    SkillManifestLoader,
    TaskOutcome,
    TokenUsage,
    ToolCall,
    ToolCallResult,
)

# ── 工具类 ──────────────────────────────────────────────────


class _FakeExec:
    """测试用 ToolExecutor —— 啥都成功，content 写死."""

    def __init__(self, content: str = "ok") -> None:
        self.calls: list[ToolCall] = []
        self.content = content

    def execute(self, tc: ToolCall) -> ToolCallResult:
        self.calls.append(tc)
        return ToolCallResult(tc.id, tc.name, True, self.content)


def _client(responses: list[dict]) -> tuple[LightweightLlmClient, FakeTransport]:
    fake = FakeTransport(responses)
    return LightweightLlmClient(LlmConfig.deepSeek("test-key"), transport=fake), fake


# ── 客户端测试 ──────────────────────────────────────────────


class TestLightweightLlmClient:
    def test_chat_basic(self):
        """单次 chat：解析 content + usage + finish_reason."""
        client, _ = _client(
            [
                {
                    "choices": [
                        {
                            "message": {"content": "hi there", "tool_calls": []},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    "model": "deepseek-chat",
                }
            ]
        )
        resp = client.chat([ChatMessage.user("hello")])
        assert resp.content == "hi there"
        assert resp.usage == TokenUsage(10, 5, 15)
        assert not resp.has_tool_calls
        assert resp.model == "deepseek-chat"

    def test_chat_with_tool_calls(self):
        """解析 tool_calls（arguments 是 JSON 字符串 → dict）."""
        client, _ = _client(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "android.tap",
                                            "arguments": '{"x":540,"y":1200}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
                    "model": "deepseek-chat",
                }
            ]
        )
        # 实际用 SkillSpec
        from runtime.tentacle.llm import SkillSpec

        resp = client.chat(
            [ChatMessage.user("tap")],
            skills=[SkillSpec(name="android.tap", description="click", parameters={})],
        )
        assert resp.has_tool_calls
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc.id == "call_1"
        assert tc.name == "android.tap"
        assert tc.arguments == {"x": 540, "y": 1200}

    def test_chat_empty_arguments_string(self):
        """DeepSeek 偶发返回 arguments='' —— 兜底为 {}."""
        client, _ = _client(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {"name": "android.tap", "arguments": ""},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "model": "x",
                }
            ]
        )
        from runtime.tentacle.llm import SkillSpec

        resp = client.chat(
            [ChatMessage.user("x")],
            skills=[SkillSpec(name="android.tap", description="", parameters={})],
        )
        assert resp.tool_calls[0].arguments == {}

    def test_request_body_format(self):
        """检查请求体格式（OpenAI 兼容）."""
        client, fake = _client(
            [
                {
                    "choices": [
                        {"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "model": "x",
                }
            ]
        )
        from runtime.tentacle.llm import SkillSpec

        client.chat(
            [ChatMessage.system("sys"), ChatMessage.user("hi")],
            skills=[
                SkillSpec(
                    name="android.tap",
                    description="click",
                    parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
                )
            ],
            temperature=0.5,
        )
        body = fake.calls[0]["body"]
        assert body["model"] == "deepseek-chat"
        assert body["temperature"] == 0.5
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        assert body["tools"][0]["function"]["name"] == "android.tap"


# ── Manifest 测试 ──────────────────────────────────────────


class TestSkillManifestLoader:
    def test_load_real_directory(self):
        """扫真实 mobile 技能目录（项目内） —— 30 个 SKILL.md."""
        loader = SkillManifestLoader()
        skills = loader.load_directory(Path("runtime/tentacle/mobile/skills"))
        assert len(skills) == 30, f"expected 30 skills, got {len(skills)}"
        names = {s.name for s in skills}
        # 核心移动技能
        expected = {
            "android.tap",
            "android.swipe",
            "android.input_text",
            "android.open_app",
            "android.finish",
            "android.get_screen_info",
            "android.take_screenshot",
            "android.wait",
        }
        assert expected.issubset(names), f"missing: {expected - names}"
        # 30 个技能名都在（与 runtime/tentacle/mobile/skills/ 对齐）
        required_30 = {
            "android.tap",
            "android.swipe",
            "android.input_text",
            "android.long_press",
            "android.system_key",
            "android.get_screen_info",
            "android.take_screenshot",
            "android.find_node",
            "android.find_text",
            "android.open_app",
            "android.install_app",
            "android.get_installed_apps",
            "android.wait",
            "android.scroll_to_find",
            "android.detect_dialog",
            "android.find_and_tap",
            "android.get_current_app",
            "android.finish",
            "android.fail",
            "android.browser.navigate",
            "android.browser.get_dom",
            "android.browser.click",
            "android.browser.type",
            "android.browser.screenshot",
            "android.browser.evaluate",
            "android.browser.install_extension",
            "android.read_file",
            "android.write_file",
            "android.get_clipboard",
            "android.set_clipboard",
        }
        assert required_30.issubset(names), f"missing: {required_30 - names}"

    def test_tap_parameters_to_json_schema(self):
        """android.tap 的 parameters 应当正确转成 JSON Schema（list-of-objects → schema）."""
        loader = SkillManifestLoader()
        skills = loader.load_directory(Path("runtime/tentacle/mobile/skills"))
        tap = next(s for s in skills if s.name == "android.tap")
        assert "properties" in tap.parameters
        assert "x" in tap.parameters["properties"]
        assert "y" in tap.parameters["properties"]
        assert tap.parameters["required"] == ["x", "y"]

    def test_description_merged_from_pipe_block(self):
        """description: | 块的多行内容应合并成单字符串."""
        loader = SkillManifestLoader()
        skills = loader.load_directory(Path("runtime/tentacle/mobile/skills"))
        tap = next(s for s in skills if s.name == "android.tap")
        # tap/SKILL.md description 是英文多行（Tap at coordinate ...），包含必要关键词
        assert "Tap" in tap.description
        assert "(x, y)" in tap.description
        assert "get_screen_info" in tap.description

    def test_to_openai_tools(self):
        """SkillSpec → OpenAI tools 数组."""
        loader = SkillManifestLoader()
        skills = loader.load_directory(Path("runtime/tentacle/mobile/skills"))
        tools = loader.to_openai_tools(skills)
        assert len(tools) == len(skills)
        for t in tools:
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "description" in t["function"]

    def test_parse_inline_text(self):
        """parse() 接受字符串输入（不依赖文件）."""
        loader = SkillManifestLoader()
        text = """---
name: test.foo
description: 测试
risk: low
parameters: {"type": "object", "properties": {"x": {"type": "integer"}}}
---
# body
"""
        spec = loader.parse(text)
        assert spec.name == "test.foo"
        assert spec.description == "测试"
        assert spec.risk == "low"
        assert spec.parameters["properties"]["x"]["type"] == "integer"
        assert "body" in spec.body


# ── ReAct 测试 ──────────────────────────────────────────────


class TestLightweightReAct:
    def _skills(self):
        from runtime.tentacle.llm import SkillSpec

        return [SkillSpec(name="android.tap", description="click", parameters={})]

    def test_done_in_two_steps(self):
        """2 步：调 1 个工具 → finish."""
        client, _ = _client(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "tc1",
                                        "type": "function",
                                        "function": {"name": "android.tap", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
                    "model": "x",
                },
                {
                    "choices": [
                        {"message": {"content": "done", "tool_calls": []}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 150, "completion_tokens": 5, "total_tokens": 155},
                    "model": "x",
                },
            ]
        )
        exec_ = _FakeExec()
        react = LightweightReAct(client, exec_, max_steps=5)
        result = react.run("tap something", skills=self._skills())
        assert result.outcome == TaskOutcome.DONE
        assert result.steps == 2
        assert result.total_tokens == 285
        assert result.final_message == "done"
        assert len(exec_.calls) == 1
        assert exec_.calls[0].name == "android.tap"

    def test_max_steps(self):
        """max_steps=3 时强制停."""
        responses = [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"tc{i}",
                                    "type": "function",
                                    "function": {
                                        "name": "android.tap",
                                        "arguments": '{"x":' + str(i) + ',"y":' + str(i) + "}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
                "model": "x",
            }
            for i in range(100)
        ]
        client, _ = _client(responses)
        react = LightweightReAct(client, _FakeExec(), max_steps=3, stuck_window=4)
        result = react.run("loop", skills=self._skills())
        assert result.outcome == TaskOutcome.MAX_STEPS
        assert result.steps == 3

    def test_stuck_detection(self):
        """连续 4 轮同 fingerprint 触发 STUCK."""
        same = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "type": "function",
                                "function": {
                                    "name": "android.tap",
                                    "arguments": '{"x":540,"y":1200}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            "model": "x",
        }
        client, _ = _client([same] * 10)
        react = LightweightReAct(client, _FakeExec(), max_steps=10, stuck_window=4)
        result = react.run("stuck", skills=self._skills())
        assert result.outcome == TaskOutcome.STUCK
        assert result.steps <= 5  # 4 步就触发

    def test_different_args_not_stuck(self):
        """同名工具但参数不同 —— 不是死循环."""
        responses = [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"tc{i}",
                                    "type": "function",
                                    "function": {
                                        "name": "android.tap",
                                        "arguments": '{"x":' + str(i) + ',"y":' + str(i + 1) + "}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "model": "x",
            }
            for i in range(10)
        ]
        responses.append(
            {
                "choices": [
                    {"message": {"content": "done", "tool_calls": []}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "model": "x",
            }
        )
        client, _ = _client(responses)
        react = LightweightReAct(client, _FakeExec(), max_steps=20, stuck_window=4)
        result = react.run("explore", skills=self._skills())
        assert result.outcome == TaskOutcome.DONE

    def test_callbacks_fire(self):
        """回调函数按预期触发."""
        client, _ = _client(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "tc1",
                                        "type": "function",
                                        "function": {"name": "android.tap", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "model": "x",
                },
                {
                    "choices": [
                        {"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "model": "x",
                },
            ]
        )
        events: list[str] = []
        from runtime.tentacle.llm import ReActCallbacks

        cb = ReActCallbacks(
            on_step_start=lambda s: events.append(f"start:{s}"),
            on_tool_call=lambda tc: events.append(f"tool:{tc.name}"),
            on_finish=lambda r: events.append(f"finish:{r.outcome.value}"),
        )
        react = LightweightReAct(client, _FakeExec(), callbacks=cb, max_steps=5)
        react.run("x", skills=self._skills())
        assert "start:0" in events
        assert "start:1" in events
        assert "tool:android.tap" in events
        assert "finish:done" in events

    def test_long_history_does_not_crash(self):
        """超长 messages 触发压缩时不崩."""
        client, _ = _client(
            [
                {
                    "choices": [
                        {"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "model": "x",
                }
            ]
        )
        react = LightweightReAct(
            client, _FakeExec(), max_steps=1, compress_trigger_ratio=0.01
        )  # 极低阈值，强制压缩
        result = react.run("x" * 500, skills=[])
        assert result.outcome == TaskOutcome.DONE


# ── 端到端：客户端 + Manifest + ReAct 串联 ──────────────────


class TestEnd2End:
    def test_real_skill_directory_end2end(self, tmp_path):
        """真实 SKILL.md 目录 + 客户端 + ReAct 全链路."""
        # 写一个临时 SKILL.md
        skill = tmp_path / "android.snapshot.md"
        skill.write_text(
            """---
name: android.snapshot
description: |
  Take a snapshot of current screen state.
  Returns structured JSON.
risk: low
parameters: {"type": "object", "properties": {}, "required": []}
---
""",
            encoding="utf-8",
        )
        loader = SkillManifestLoader()
        skills = loader.load_directory(tmp_path)
        assert len(skills) == 1
        assert skills[0].name == "android.snapshot"
        assert "Take a snapshot" in skills[0].description

        # 用这个 SkillSpec 跑一次 ReAct
        client, _ = _client(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "tc1",
                                        "type": "function",
                                        "function": {"name": "android.snapshot", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "model": "x",
                },
                {
                    "choices": [
                        {"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "model": "x",
                },
            ]
        )
        exec_ = _FakeExec(content='{"app": "com.x"}')
        react = LightweightReAct(client, exec_, max_steps=5)
        result = react.run("snapshot", skills=skills)
        assert result.outcome == TaskOutcome.DONE
        assert exec_.calls[0].name == "android.snapshot"
