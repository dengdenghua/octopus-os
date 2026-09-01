# Echo Agent 快速上手

这份文档只回答三个问题：

1. 它是什么？
2. 怎么跑起来？
3. 跑起来后先看哪里？

更完整的架构说明见 [README.md](README.md)、[docs/GOLDEN_PATH.md](docs/GOLDEN_PATH.md)、[docs/architecture.md](docs/architecture.md)。

## 30 秒理解

| 问题 | 答案 |
|---|---|
| 它是什么 | 一个 Python Agent OS runtime，外加 React/Electron 工作台 |
| 它解决什么 | 把 agent 的规划、执行、记忆、安全、成本、审计、反思组织到一条可观测链路里 |
| 它不是什么 | 不是 ChatGPT 替代品，不是只封装 LangChain，也不绑定某一个 LLM |
| 核心依赖 | `pydantic>=2.12`，其余能力大多是 optional extras |
| 成熟度 | Beta v0.2.0 |
| License | Apache-2.0 |

## 1. 安装

最小 demo，不需要 LLM key：

```bash
pip install -e ".[minimal]"
python -m runtime bugfix-demo
```

开发环境：

```bash
pip install -e ".[dev,serve,web]"
python -m runtime status
```

完整能力环境：

```bash
pip install -e ".[dev,all]"
python -m playwright install chromium
python -m runtime status
```

## 2. 跑第一个真实 demo

推荐先跑确定性 bugfix demo：

```bash
python -m runtime bugfix-demo
```

它会在临时目录里完成一条真实链路：

1. 创建一个带 bug 的小 Python 项目
2. 读取文件
3. 运行失败测试
4. 定位并修改代码
5. 再次运行测试
6. 通过后写入 git commit
7. 把步骤写入 journal / trajectory

这个 demo 不依赖外部 LLM，是判断本地 runtime 是否工作正常的第一条路径。

## 3. 查看本机能力

```bash
python -m runtime status
```

你会看到类似：

```text
Runtime
  pydantic
  opentelemetry

LLM
  MockModelRouter
  AnthropicModelRouter

External
  httpx web skills
  MCP stdio
  playwright browser
  FastAPI web UI
```

缺少 optional integration 不一定是错误。比如只跑 `bugfix-demo`，不需要 FastAPI、MCP、Playwright 或 Anthropic。

## 4. 启动 Web UI

```bash
python -m runtime ui --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

常用入口：

| 页面 | 作用 |
|---|---|
| `/` | 内置 dashboard |
| `/ui/` | React workspace，前提是已有 `frontend/dist` |
| `/docs` | FastAPI Swagger |
| `/api/health` | 健康检查 |
| `/api/status` | 能力检查 |
| `/api/journal` | 最近事件 |

## 5. 启动完整服务

准备配置：

```bash
cp .env.example .env
cp config.example.yaml config.local.yaml
python -m runtime quickstart --non-interactive
```

启动：

```bash
python -m runtime quickstart --non-interactive --serve
```

如果没有真实 LLM key，可以先使用 mock/static planner 路径做本地功能验证。

## 6. 前端开发

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

默认 Vite dev server 会把 `/api` 代理到后端。

常用检查：

```bash
pnpm typecheck
pnpm test
pnpm build
```

Electron：

```bash
pnpm electron:dev
```

## 7. 后端开发

```bash
python -m pytest -q
python -m ruff check runtime tests tools
python -m ruff format --check runtime tests tools
```

只跑 CLI smoke：

```bash
python -m pytest tests/test_cli_status.py tests/test_cli_smoke.py -q
```

## 8. Docker

```bash
cp .env.example .env
cp config.example.yaml config.yaml
docker compose up -d
docker compose logs -f echo-agent
```

服务地址：

```text
http://127.0.0.1:8000
```

## 9. 下一步读什么

- [docs/GOLDEN_PATH.md](docs/GOLDEN_PATH.md)：10 分钟上手路径
- [docs/CONCEPTS.md](docs/CONCEPTS.md)：概念地图
- [docs/architecture/module-map.md](docs/architecture/module-map.md)：仿生命名到工程目录的映射
- [ROOT_LAYOUT.md](ROOT_LAYOUT.md)：根目录边界
- [docs/invariants.md](docs/invariants.md)：工程不变量
