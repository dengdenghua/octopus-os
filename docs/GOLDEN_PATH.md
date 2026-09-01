# Golden Path

This is the 10-minute path for a new user. It avoids internal architecture and
proves one thing first: Echo can run an agent task and show you what happened.

## Goal

By the end, you should have:

- installed the local runtime;
- run one deterministic agent demo;
- opened the UI;
- inspected the journal or task result.

## 1. Install

From the repository root:

```bash
pip install -e ".[dev]"
```

If you only want the smallest backend demo:

```bash
pip install -e ".[minimal]"
```

## 2. Check The Runtime

```bash
python -m runtime status
```

You should see a capability summary for FastAPI, MCP, Playwright, model
providers, and related optional integrations. Missing optional integrations are
fine for the first run.

## 3. Run A Real Demo

Use the deterministic bugfix demo first. It does not need an LLM key.

```bash
python -m runtime bugfix-demo
```

What to look for:

- the agent reads files;
- runs tests;
- edits code;
- verifies the fix;
- records the process.

This is the first "aha": Echo is not only a chat box. It supervises work.

## 4. Open The Workspace

Start the local UI:

```bash
python -m runtime ui --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

Useful first pages:

| Page | What to inspect |
|---|---|
| `/` | Legacy dashboard and runtime status |
| `/ui/` | React workspace if `frontend/dist` exists |
| `/docs` | FastAPI routes |
| `/api/health` | Health probe |
| `/api/status` | Capability probe |
| `/api/journal` | Recent events |

## 5. Run A Simple Goal

```bash
python -m runtime run "list files in the current directory"
```

Then inspect:

```bash
python -m runtime reflect --from-journal data/events.jsonl
```

If your run used an in-memory journal or a different journal path, use that path
instead.

## 6. Understand The Result

Read the output in this order:

1. Did the task complete?
2. Which skills/tools were used?
3. Were any limits or safety checks triggered?
4. What was written to the journal?
5. Did reflection propose anything useful?

Do not start by reading the biomimetic architecture. Start with the work trace.

## Common First Problems

| Symptom | Try |
|---|---|
| `fastapi` missing | `pip install -e ".[dev]"` |
| UI starts but `/ui/` is empty | run `cd frontend && corepack enable && pnpm install --frozen-lockfile && pnpm build` |
| model call fails | run deterministic demos first, then configure provider keys |
| browser automation unavailable | install Playwright or use the browser relay path |
| journal looks empty | pass an explicit `--journal-file` or `--journal` path |

## What To Read After This

- Plain concepts: [CONCEPTS.md](CONCEPTS.md)
- Setup details: [getting-started.md](getting-started.md)
- Architecture: [architecture.md](architecture.md)
- Naming rules: [naming.md](naming.md)
