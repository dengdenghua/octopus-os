# Custom Models · 接入任意 LLM 厂商

Echo 的 "自定义模型" 机制允许你在 UI 里直接接入 **任何 OpenAI-compat / Anthropic-native 协议** 的 LLM 服务，**零代码**。新接入的模型自动获得：

- 真流式（token-by-token）
- 思考过程折叠（thinking-capable 模型）
- **原生 tool_use**（skill 工具调用）
- 取消生命周期（点 Stop 真停）
- 协议统一 delta 帧（不重复不丢内容）

这文档给你常见厂商的 **即插即用配置**。

---

## 添加方式

UI 路径：`设置 → 模型 → 自定义模型 → 添加`

填 4 个字段：

| 字段 | 说明 |
| :--- | :--- |
| **Provider** | 下拉里挑预设（OpenAI / Kimi / GLM / Qwen / ... ），会自动回填 base URL |
| **Model ID** | 厂商的模型标识（如 `kimi-k2-0711-preview`、`qwen-max-latest`） |
| **Display Name** | 聊天框里展示的短名（如 "Kimi K2"） |
| **API Key** | 在厂商控制台申请的 key |

保存后，聊天框模型选择器的 **自定义** 标签会出现这个模型。

---

## 常见厂商 · 抄作业表

### 🌏 国际

#### OpenAI · 官方

```
Protocol  : OpenAI
Base URL  : https://api.openai.com/v1
Key 来源  : https://platform.openai.com/api-keys
推荐模型  :
  - gpt-4o-mini          便宜快
  - gpt-4o                旗舰
  - o1                    推理模式
  - o3-mini               最新推理
Tool use  : ✅ 完全支持
```

#### Anthropic · Claude 官方

```
Protocol  : Anthropic
Base URL  : https://api.anthropic.com/v1
Key 来源  : https://console.anthropic.com/
推荐模型  :
  - claude-sonnet-4-6-20250514         主力（思考 + 工具）
  - claude-haiku-4-5-20251001          便宜快
  - claude-opus-4-7-20250805           最强
Tool use  : ✅ 原生 tool_use + extended thinking
```

#### Google Gemini

```
Protocol  : OpenAI (用 Google 的 OpenAI compat 模式)
Base URL  : https://generativelanguage.googleapis.com/v1beta/openai
Key 来源  : https://aistudio.google.com/apikey
推荐模型  :
  - gemini-2.5-flash
  - gemini-2.5-pro
Tool use  : ✅ 支持（compat 层自动把 OpenAI tools 翻成 functionDeclarations）
```

#### xAI · Grok

```
Protocol  : OpenAI
Base URL  : https://api.x.ai/v1
Key 来源  : https://console.x.ai/
推荐模型  : grok-4-mini / grok-4
Tool use  : ✅
```

#### DeepSeek

```
Protocol  : OpenAI
Base URL  : https://api.deepseek.com/v1
Key 来源  : https://platform.deepseek.com/
推荐模型  :
  - deepseek-chat (DeepSeek-V3.2)
  - deepseek-reasoner
Tool use  : ✅
```

---

### 🇨🇳 中国

#### Moonshot · Kimi

```
Protocol  : OpenAI
Base URL  : https://api.moonshot.cn/v1
Key 来源  : https://platform.moonshot.cn/console/api-keys
推荐模型  :
  - kimi-k2-0711-preview      最新 K2
  - moonshot-v1-128k          长上下文
  - moonshot-v1-32k           标准
Tool use  : ✅ 严格遵循 OpenAI 规范 · 开箱即用
```

#### 智谱 · GLM

```
Protocol  : OpenAI
Base URL  : https://open.bigmodel.cn/api/paas/v4
Key 来源  : https://bigmodel.cn/usercenter/apikeys
推荐模型  :
  - glm-4.6                   主力
  - glm-4-flash               便宜
  - glm-4-plus                旗舰
  - glm-4v-plus               视觉
Tool use  : ✅ 标准 function calling
小坑      : ``arguments`` 字段偶尔是 python-repr 风格 · 我们已做 JSON 解析容错
```

#### MiniMax

```
Protocol  : OpenAI
Base URL  : https://api.minimaxi.com/v1
Key 来源  : https://platform.minimaxi.com/user-center/basic-information/interface-key
推荐模型  :
  - MiniMax-M2             最新 M2.5
  - abab7-chat-preview
Tool use  : ✅
```

#### 阿里云 · 通义千问 (Qwen)

```
Protocol  : OpenAI      ← ⚠️ 必须用 compatible-mode
Base URL  : https://dashscope.aliyuncs.com/compatible-mode/v1
Key 来源  : https://bailian.console.aliyun.com/?tab=model#/api-key
推荐模型  :
  - qwen-max-latest               旗舰
  - qwen-plus                     平衡
  - qwen-turbo                    便宜快
  - qwen3-max                     Qwen3 最强
  - qvq-max-latest                推理模式
Tool use  : ✅（一定要走 compatible-mode · 原生 DashScope 协议不兼容）
```

#### 腾讯云 · 混元 (Hunyuan)

```
Protocol  : OpenAI
Base URL  : https://api.hunyuan.cloud.tencent.com/v1
Key 来源  : https://console.cloud.tencent.com/hunyuan/api-key
推荐模型  :
  - hunyuan-turbos-latest
  - hunyuan-large
  - hunyuan-lite
Tool use  : ✅
小坑      : 部分模型对 ``additionalProperties: true`` 敏感 · 如果报 400,
            把 input_schema 改成 ``{"type":"object", "properties":{"_":{"type":"string"}}}``
```

#### 火山引擎 · 豆包 (Doubao / Ark)

```
Protocol  : OpenAI
Base URL  : https://ark.cn-beijing.volces.com/api/v3
Key 来源  : https://console.volcengine.com/ark
推荐模型  :
  - doubao-pro-256k              长上下文
  - doubao-1-5-pro-256k          1.5 系
  - doubao-pro-32k               标准
Tool use  : ✅
注意      : model ID 在火山控制台要单独"创建推理接入点"后才能用，
            填的是接入点 ID 而非模型名（如 ``ep-20250123-xxxx``）
```

---

## 本地 / 私有部署

#### Ollama（本地）

```
Protocol  : OpenAI
Base URL  : http://localhost:11434/v1
API Key   : ollama （随便填不检查）
推荐模型  :
  - llama3.2:3b / llama3.3:70b
  - qwen3:7b / qwen3-coder:32b
  - deepseek-r1:7b
Tool use  : ✅（llama3.x、qwen3、deepseek 等都支持 function calling）
```

#### LM Studio

```
Protocol  : OpenAI
Base URL  : http://localhost:1234/v1
API Key   : (任意)
Tool use  : 取决于加载的模型
```

#### vLLM / LocalAI / TGI

```
Protocol  : OpenAI
Base URL  : http://<host>:<port>/v1
API Key   : 按你的部署设
Tool use  : 取决于后端模型
```

#### OpenRouter（一站式 200+ 模型）

```
Protocol  : OpenAI
Base URL  : https://openrouter.ai/api/v1
Key 来源  : https://openrouter.ai/keys
Extra Headers:
  HTTP-Referer: https://your-app.example
  X-Title: Echo
Tool use  : ✅（取决于选的模型）
```

---

## 工作原理简述

1. **UI → 后端**：PUT `/api/config/custom-models/{id}` 写入 `base_url + api_key + model + provider`
2. **后端 dispatch**：请求到达时，`ModelDispatchRouter` 按 model_id 找到对应的 provider router
3. **协议翻译**（每个 router 内部）：
   - Echo 通用 `ToolSpec` / `ToolCall` / 块式消息
   - → OpenAI shape: `tools=[{type:function, function:{...}}]`
   - → Anthropic shape: `tools=[{name, description, input_schema}]`
   - → Gemini shape: `tools=[{functionDeclarations:[...]}]`
4. **响应解析**：
   - OpenAI/兼容：`choices[0].message.tool_calls[]`
   - Anthropic：`content[]` 里的 `tool_use` 块
   - Gemini：`candidates[0].content.parts[]` 里的 `functionCall`
5. **统一成 `ModelResponse.tool_calls`** → Echo 的 agentic loop 一视同仁

**只要厂商支持 OpenAI 的 `tools` 字段（或 Anthropic / Gemini 的原生形），Echo 就能无脑接入**。

---

## 排查

**Q · 模型已添加但工具从来不触发？**

先在聊天框输入 **工具意图** 关键词（"列出目录"、"读 README"、"搜索"、"查"），确认走进 agentic 路径。纯聊天（"讲个笑话"）**不会**触发工具 —— 这是设计，不是 bug。

**Q · API 测试连通但 tool_use 返空？**

- 有些小模型**不支持 function calling**（如 glm-4-air、qwen-lite 旧版）· 换旗舰
- 检查厂商控制台是否开通了"工具使用"特性（某些厂是独立计费）

**Q · "no current_actor set" 报错？**

账户型 fallback router 需要登录态。用 custom model 时请求直接走注册表，所以正常使用不会触发此错误。如果仍然报，说明 model_id 没命中 custom model 注册表、落到了需要 actor 的 fallback 上 · 检查 model id 拼写。

**Q · 响应里出现原始 `<thinking>` XML？**

说明代理剥了 `thinking={type:enabled}` 参数、Claude 退化为 prose thinking。我们有正则把它抽出来放到 `💭 思考过程` 折叠里，正常情况下看不到裸 XML。如果出现了，**刷新**会消失（代理缓存问题）。

---

## 未来扩展

- ✅ OpenAI / Anthropic / Gemini / OpenAI-compat 全支持
- 🔮 DashScope 原生协议（阿里云非 compatible-mode）· 低优先级
- 🔮 百度文心 ERNIE 自有协议 · 用户基数小
- 🔮 本地 MLX / llama.cpp 直连（无需 OpenAI 层）· 有需求再加
