"""Echo Mobile 集成子包.

提供 Echo Mobile（Android 客户端）↔ echo-agent 之间的桥梁：

- :mod:`skill_export` —— 把 Echo Mobile 的 BaseTool 导出为 SKILL.md
- :mod:`tool_bridge` —— 工具调用桥接（JSON-RPC envelope）
- :mod:`version` —— Echo Mobile 端版本兼容矩阵

详见 ``docs/mobile/architecture.md`` 第 2.2 节。
"""
