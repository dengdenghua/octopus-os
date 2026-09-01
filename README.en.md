# Echo OS

Echo OS is the Agent operating system for personal devices: Agent runtime,
desktop, launcher, files, and system capabilities ship from this repository as
one versioned distribution. The visible workbench belongs to the single Echo
frontend, and development and release use this checkout as their source of truth.

中文读者：[README.md](README.md)

## Project family

| Project | Responsibility |
| --- | --- |
| `echo-agent` | Agent capability name and compatibility command embedded in Echo OS |
| `echo-os` | Single release unit: Agent runtime, desktop, files, and system capabilities |
| `echo-mobile` | Android automation client |
| `echo-enterprise` | Enterprise AI project management |
| `echo-storage` | Local secure data coprocessor (File Agent) |

## Repository layout

| Path | Purpose |
| --- | --- |
| `appliance/` | OS device layer: app registry, authentication, extensions, files, and system services |
| `frontend/` | Desktop UI shell |
| `tests/appliance/` | Authoritative OS appliance tests |
| `deploy/` | Image, Docker, Kubernetes, host, recovery, and delivery tooling |
| `docs/` | Active OS architecture, onboarding, operations, and archived Agent references |

## echo-storage (File Agent) integration

Echo OS does not duplicate document indexing. It talks to the sibling
`echo-storage` project through a narrow HTTP API:

- The desktop launcher opens the File Agent workspace.
- The NAS file manager can call `/v1/search` and `/v1/answer`.
- Uploads are staged and atomically committed; copy, move, directory creation,
  multi-file selection, downloads, and trash are supported.
- Set `ECHO_STORAGE_AUTOSTART=1` to start `echo-storage serve` in the
  background. Failure is isolated from the rest of the OS.
- Override the endpoint with `ECHO_STORAGE_URL`,
  `ECHO_STORAGE_HOST`, or `ECHO_STORAGE_PORT`.

## Quick Start

```bash
# Install the unified development environment from this repository.
make install

# Build a source-verified Agent bundle, then start the appliance stack.
make agent-bundle
make up

# Run the appliance contract and integration tests.
make test
```

For local QA snapshots, device deployment, and recovery procedures, see the
[NAS deployment guide](deploy/appliance/README.md).

## CLI

The unified installation exposes `echo` as the primary command and keeps
`echo-agent` as a compatibility alias for existing automation.

## Reflection closure

Agent execution, verification, replay evidence, memory promotion, and rollback
all live inside this repository and release as one Echo OS version.

## License and upstream components

Echo OS is distributed under Apache-2.0. Bundled OpenAI components retain their
own notices and license files in the release artifacts.

To run the embedded workbench with the OS backend:

```bash
cd frontend
pnpm dev:with-agent
```

This starts the Agent backend on `127.0.0.1:8000` and the only frontend on port
3000. The full setup is documented in
[Echo Agent workbench integration](docs/ECHO_AGENT_INTEGRATION.md).

Start with the [documentation ownership map](docs/README.md), the
[OS architecture](docs/architecture.md), and the
[Echo OS ↔ Echo Agent engineering boundary](docs/AGENT_OS_BOUNDARY.md).

The NAS product-delivery boundary and remaining physical acceptance gates are
tracked in [NAS_DELIVERY_STATUS.md](docs/NAS_DELIVERY_STATUS.md).
