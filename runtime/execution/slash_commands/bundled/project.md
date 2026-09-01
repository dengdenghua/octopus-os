---
description: 项目管理（Project OS）——里程碑式驱动，查看 PM 驾驶舱/复盘/恢复/干预任务
argument-hint: report | retro | recover [tasks=T1,T2] [run] | task <id> <reassign|reset|complete|skip> [agent=..]
---
Project OS 控制命令（项目模式对话内可用）：

- /project report —— PM 驾驶舱：里程碑健康度 / 风险 / 下一步动作 / 指派
- /project retro —— 项目复盘：交付、失败、重试、耗时、建议
- /project recover [tasks=T1,T2] [run] —— 恢复被阻塞的项目
- /project task <task_id> <reassign|reset|complete|skip> [agent=agent-id] [reason=...] [run] —— 干预单个任务
