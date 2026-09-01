"""runtime.core · 核心器官（Core Organs）

仿生名 → 工程名 映射：
  cerebrum     → Planner（规划器）—— 任务图生成 + 规则匹配
  ganglia      → Runtime（图运行时）—— DAG 分层并行执行
  hearts       → Heartbeat（心跳监控）—— 分支/系统级健康检查
  nerves       → EventBus（事件总线）—— 类型安全发布/订阅
  spinal_cord  → Reflex（反射层）—— 缓存 + 规则匹配快速响应
"""
