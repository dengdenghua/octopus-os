# Root Layout

The repository root is a product entrance. Keep it small, predictable, and easy
to scan.

> **Enforcement**: [`tools/lint/root_hygiene.py`](tools/lint/root_hygiene.py)
> runs in CI (`python tools/lint/root_hygiene.py --strict`) and as a
> pre-commit hook. It cross-checks the allow-list below against
> `git ls-files` and fails on any non-allowlisted top-level entry that is
> actually tracked. Add a new root entry only by updating both this file
> and the `ROOT_ALLOWLIST` set in the linter.


## Source

| Path | Purpose |
|---|---|
| `runtime/` | Python runtime and API surface |
| `frontend/` | React/Electron workspace |
| `tests/` | Test suite |
| `appliance/` | Octopus OS appliance layer (octopus-os fork; see docs/OCTOPUS_OS_PLAN.md) |

## Product Assets

| Path | Purpose |
|---|---|
| `agents/` | Agent definitions and presets |
| `skills/` | Skills shipped with the product |
| `protocols/` | Protocol specs and compatibility assets |
| `prompts/` | Prompt templates and evaluation assets |
| `extensions/` | Browser / IDE extensions |

Android mobile client source lives as a sibling checkout at `../octopus-mobile/`, not
as a repository-root child.

## Project Support

| Path | Purpose |
|---|---|
| `docs/` | Current docs plus archived notes |
| `demos/` | Small runnable examples |
| `benchmarks/` | Repeatable benchmark assets |
| `deploy/` | Deployment manifests |
| `tools/` | Developer utilities |
| `scripts/` | Automation scripts |
| `packaging/` | Packaging helpers |

## Configuration

| Path | Rule |
|---|---|
| `config.example.yaml` | Commit-safe example config |
| `config.yaml` / `config.local.yaml` | Local-only config |
| `.env.example` | Commit-safe environment template |
| `.env` | Local-only secret file |

## Local State

These paths are not source. They may exist during development, but should not be
treated as part of the product surface.

| Path | Rule |
|---|---|
| `.octopus/` | Local runtime state |
| `data/` | Local journals, DBs, and generated runtime state |
| `logs/` | Local process logs |
| `workspace/` | Generated workspaces |
| `test-results/` | Local test artifacts |
| `.venv/`, `frontend/node_modules/` | Installed dependencies |
| `build/`, `dist/`, `*.egg-info/` | Build output |

## Hygiene Rules

1. Add a root directory only when it is a stable source or product boundary.
2. Put experiments under `demos/`, `benchmarks/`, or `docs/archive/`.
3. Keep generated state under ignored local-state paths.
4. If a root item cannot be explained here, it probably should not live at root.
