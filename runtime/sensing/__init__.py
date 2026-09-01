"""runtime.sensing · 感知识别（Perception）

子模块速查：
  model_router → ModelRouter —— Anthropic/OpenAI/openrouter 模型适配
  normalize    → SensorManager —— 输入归一化 + 事件驱动监控
  gateway      → FastAPI 路由聚合层
  server       → 后端隔离层 (Local/Docker/K8s/SSH/Subprocess Backend)
"""
