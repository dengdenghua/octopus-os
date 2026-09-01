# Architecture (Engineering Reference) — Echo OS

> This document describes **what is actually implemented** in the Echo OS codebase.
> For the biomimetic vision, see [vision/biomimetic-architecture.md](../vision/biomimetic-architecture.md).
> For the organ-to-code mapping, see [biomimetic/README.md](../biomimetic/README.md).

---

## OS-Specific Layer

Echo OS adds an **appliance layer** on top of the agent runtime:

```
Display (HDMI)                                  ← P2.5
  ↑ Full-screen rendering
kiosk (cage + Chromium or Electron)             ← Host process, needs GPU
  ↑
Desktop Shell (Next.js, modified frontend/)      ← Chat / Window manager / File manager / App launcher
  ↑ JSON-RPC WebSocket
Agent Runtime (runtime/)                         ← Agent OS: skills/approval/memory/model routing (incl. ollama)
  ↑ docker.sock + reverse proxy
Docker container runtime                         ← Third-party app ecosystem (CasaOS compatible)
  ↑
NAS base (Debian + OMV storage packages)         ← P3; P1/P2 hosted on existing NAS systems
```

### Appliance Module (`appliance/`)

| Path | Purpose |
|---|---|
| `appliance/app_registry/` | Read docker.sock: container list/icons/ports → launcher data source |
| `appliance/files/` | File manager + recycle bin |
| `appliance/agent_ui.py` | Agent UI integration for desktop |
| `appliance/auth.py` | Single-user authentication (OS-specific) |
| `appliance/extension.py` | Extension points for OS-specific features |
| `appliance/pm_skills.py` | Product management skills |
| `appliance/security.py` | OS-level security policies |

### Desktop Shell (`frontend/src/appliance/`)

| Path | Purpose |
|---|---|
| `appliance/apps.ts` | App launcher API |
| `appliance/auth.ts` | Login flow |
| `appliance/dock.tsx` | Desktop dock/taskbar |
| `appliance/files.ts` | File manager API |
| `appliance/login.tsx` | Login page |

---

## Runtime Module Structure

The Agent runtime is built into this repository under `runtime/` and ships in the same
`echo-os` distribution. The device layer accesses it through `appliance/agent_api/`.

### Appliance Profile (Disabled by Default)

The following agent modules are **disabled** in the appliance profile:

| Module | Path | Disposition |
|---|---|---|
| Multi-agent cluster | `runtime/execution/swarm/` | Off by default |
| HA heartbeat/election | `runtime/core/hearts/` | Off by default (single device) |
| K8s/SSH sandbox backends | `runtime/safety/sandboxing/` | Off, keep local/docker |
| Self-evolution/skill forge | `runtime/safety/recovery/` (skill_forge) | Off; requires approval gate when enabled |
| Company/research/tentacle | `runtime/company/` etc. | Under evaluation |

Memory target: runtime resident < 1.5GB (excluding ollama).

---

## Key OS Workflows

### 1. Natural Language File Search

```
"Find all invoices from 2023 and organize by month"
    → Cerebrum (planner)
    → File search skill (semantic index)
    → File organization skill
    → Approval gate (destructive operation)
    → Execute in sandbox
```

### 2. Watchdog Automation

```
Download complete → File watcher event
    → Episode identification skill
    → Rename + organize skill
    → Notify Jellyfin refresh skill
    → All orchestrated across applications
```

### 3. Local Document Q&A

```
User asks about private contract
    → RAG over local documents
    → Sensitive content → force ollama local inference
    → Never leaves the device
```

---

## Security Model

1. **Primary attack surface**: Web content → Agent input prompt injection. All text from app windows/web/downloads is untrusted input.
2. **Delete = Recycle bin** (P2 hard constraint)
3. Safety rules fully configurable (upgraded from "suggestion" to "must" in OS context)
4. Journal as system-level audit log, UI can replay "what AI did to this machine"
5. Budget breaker manages local + cloud inference cost quotas

---

## Deployment

- **P1**: Docker app on existing NAS (CasaOS / OMV / 飞牛)
- **P2**: Windowed desktop + app skills + semantic index
- **P2.5**: HDMI local desktop (kiosk mode)
- **P3**: Full OS image (Debian + OMV, immutable system + A/B atomic updates)

---

## Related Documents

| Document | Purpose |
|---|---|
| [ECHO_OS_PLAN.md](ECHO_OS_PLAN.md) | Full OS transformation plan |
| [OS_DIFFERENTIATION.md](OS_DIFFERENTIATION.md) | How OS differs from Agent |
| [implementation-status.md](implementation-status.md) | Per-mechanism implementation status |
| [biomimetic/README.md](biomimetic/README.md) | Organ → code mapping table |
| [vision/biomimetic-architecture.md](../vision/biomimetic-architecture.md) | Full biomimetic vision |
