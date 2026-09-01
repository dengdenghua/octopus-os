# 👁️ Eyes · 眼睛

**生物原型**：章鱼眼睛有 W 形水平瞳孔，视野接近 360°，是已知最发达的无脊椎动物视觉系统。

## 职责
显式输入感知：
- 用户请求解析
- 多模态输入（图像 / 文件）
- **LLM Provider 适配**（`models/`，fork 自 echo，10+ provider）

## 子目录
```
eyes/
└── models/       [fork]  Anthropic / OpenAI / DeepSeek / Kimi / ...
```

## 核心接口
```python
class Eyes:
    def perceive(self, raw_input) -> Perception: ...
    def call_model(self, provider, messages, **opts) -> ModelResponse: ...
```

## Prompt Caching 强制规范
- system prompt + sucker 注册表前缀**必须稳定**
- 任何 cache 破坏操作（插入、重排）一律拦截在 Eyes 层
- 每次调用记录 cache hit rate，低于阈值告警

## 进化关联
作为所有模型调用的唯一出口，是 **⑥ 成本治理** 的关键枢纽 —— model 分层路由在此落地。
