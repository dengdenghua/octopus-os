"""runtime.safety.guards · 顾问式防护(Advisory Guards)

与 approval 等「硬门」不同,guard 只观察、只提醒,永不拦截或改写
模型调用。当前家族:

  repeat_tool_reminder → dsh ``repeat-tool-reminder`` 防循环顾问
"""
