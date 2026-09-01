# Deployment Quickstart

This page is the shortest path from a fresh checkout to a running Echo
service. Use the longer `docs/deployment.md` when you need Docker, k8s, or
systemd details.

## Python Local

```bash
pip install -e ".[dev,serve,web]"
python -m runtime quickstart --non-interactive
python -m runtime quickstart --non-interactive --serve
```

Open:

```text
http://127.0.0.1:8000
```

The first command creates `config.yaml` if it is missing and runs `doctor`.
The second command repeats the checks and starts the FastAPI service.

## Docker

```bash
cp .env.example .env
cp config.example.yaml config.yaml
docker compose up -d
docker compose logs -f echo-agent
```

Stop it with:

```bash
docker compose down
```

## Desktop Build (Optional)

The desktop shell is **opt-in** — it lives in `extras/desktop/` and wraps the
web frontend in Electron for users who want a single-file installer.

```bash
cd extras/desktop
corepack enable
pnpm install --frozen-lockfile
pnpm electron:build:win    # or :mac / :linux
```

For development (Backend + Vite + Electron with hot reload):

```bash
cd extras/desktop
pnpm electron:dev
```

> Self-hosted or developer use — skip this; the web frontend (`frontend/`) is
> the canonical UI and is ~30 MB vs ~200 MB for the desktop bundle.
> See [`extras/desktop/README.md`](../extras/desktop/README.md).

## Health Checks

```bash
python -m runtime doctor --config config.yaml
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/status
```

If you do not have an LLM key yet, keep the generated static config and run the
deterministic demos first.
