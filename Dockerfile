# syntax=docker/dockerfile:1.7
# octopus-agent · 多阶段构建
# 阶段 1: node:20-alpine → Vite 构建前端
# 阶段 2: python:3.12-slim → pip install 后端依赖
# 阶段 3: python:3.12-slim → 运行时（最小镜像）
#
# 构建:
#   docker build -t octopus-agent .
#
# 快速启动（无持久化）:
#   docker run --rm -p 8000:8000 octopus-agent
# 生产部署（持久化 + 配置):
#   docker run --rm -p 8000:8000 \
#     -v $(pwd)/data:/data \
#     -v $(pwd)/config.yaml:/etc/octopus/config.yaml:ro \
#     -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
#     octopus-agent

# ═══════════════════════════════════════════════════════════
# 阶段 1 · webui-builder · Vite + React 前端构建
# ═══════════════════════════════════════════════════════════

FROM node:20-alpine AS webui-builder

WORKDIR /webui

# 利用 Docker 层缓存: 先复制 package.json → npm ci → 再复制源码
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# 源码变更不影响 npm ci 缓存层
COPY frontend/ ./

RUN npm run build
# 产物在 /webui/dist · 运行时阶段复制到 /app/webui


# ═══════════════════════════════════════════════════════════
# 阶段 2 · py-builder · pip install 后端依赖
# ═══════════════════════════════════════════════════════════

FROM python:3.12-slim AS py-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# P1 去 fork:runtime/tools 不再随 os 仓库,改由 pinned agent 依赖提供。
# agent 仓库私有 → 构建前先跑 deploy/appliance/prepare-agent-wheel.sh 预构建
# agent wheel 到 deploy/appliance/agent-dist/,此处本地投喂(无需 GitHub token)。
COPY pyproject.toml README.md ./
COPY deploy/appliance/agent-dist/ ./agent-dist/
COPY appliance/ ./appliance/

# 1) 装 agent(=runtime/tools)+ serve/web/tracing extra:octopus-agent 取自本地
#    find-links 的 wheel,其余依赖(fastapi 等)走 PyPI。
# 2) 装 os 自身(仅 appliance 层),--no-deps 不再去 git 拉 agent(已装)。
RUN pip install --prefix=/install --no-warn-script-location \
        --find-links agent-dist/ "octopus-agent[serve,tracing,web]" \
 && pip install --prefix=/install --no-warn-script-location --no-deps .


# ═══════════════════════════════════════════════════════════
# 阶段 3 · runtime · 最小运行时镜像
# ═══════════════════════════════════════════════════════════

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/install/lib/python3.12/site-packages:${PYTHONPATH}" \
    OCTOPUS_DATA_DIR=/data \
    OCTOPUS_CONFIG=/etc/octopus/config.yaml \
    OCTOPUS_WEBUI_DIST=/app/webui \
    OCTOPUS_RESOURCES_DIR=/app/resources

RUN groupadd -r octopus && \
    useradd -r -g octopus -d /data -s /bin/false octopus && \
    mkdir -p /data /etc/octopus /app/webui /app/resources && \
    chown -R octopus:octopus /data /etc/octopus /app

# 只复制已安装的依赖（不含构建工具链）
COPY --from=py-builder  /install     /install
COPY --from=webui-builder /webui/dist /app/webui

# 运行时资源目录 · planner / skills / prompts / protocols 读取
# 必须存在于镜像中，否则 agent fallback 到空目录
COPY agents/    /app/resources/agents/
COPY skills/    /app/resources/skills/
COPY prompts/   /app/resources/prompts/
COPY protocols/ /app/resources/protocols/
COPY teams/     /app/resources/teams/
COPY config.example.yaml /etc/octopus/config.example.yaml
RUN chown -R octopus:octopus /app/resources /etc/octopus

USER octopus
WORKDIR /data

EXPOSE 8000

# 入口点支持任意子命令:
#   docker run octopus-agent octopus-agent run "帮我做X"
#   docker run octopus-agent octopus-agent loop "目标" --config /etc/octopus/config.yaml
ENTRYPOINT ["octopus-agent"]
CMD ["serve", "--config", "/etc/octopus/config.yaml", "--host", "0.0.0.0", "--port", "8000"]