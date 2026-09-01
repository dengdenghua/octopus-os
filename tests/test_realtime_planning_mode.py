from runtime.protocol.items import TurnParams
from runtime.sensing.gateway.realtime_cerebrum import _should_default_planning_mode


def test_agent_react_mode_does_not_default_to_planning_mode():
    params = TurnParams(
        threadId="t1",
        input=[
            {
                "type": "input_text",
                "text": "测试工具链：请调用 list_cwd 查看当前目录",
                "metadata": {"context": {"mode": "react", "agent_name": "general"}},
            }
        ],
    )

    assert not _should_default_planning_mode("测试工具链：请调用 list_cwd 查看当前目录", params)


def test_deep_mode_still_defaults_to_planning_mode_for_complex_tasks():
    params = TurnParams(
        threadId="t1",
        input=[
            {
                "type": "input_text",
                "text": "请完整调研 NAS 市场并输出报告",
                "metadata": {"context": {"mode": "deep", "agent_name": "general"}},
            }
        ],
    )

    assert _should_default_planning_mode("请完整调研 NAS 市场并输出报告", params)
