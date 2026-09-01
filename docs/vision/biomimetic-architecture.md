# Biomimetic Architecture (Vision) — Echo OS

> This document describes the **full biomimetic vision** for Echo OS.
> For what is actually built today, see [guide/architecture.md](../guide/architecture.md).
> For the organ-to-code mapping, see [biomimetic/README.md](../biomimetic/README.md).

---

## OS as Organism

The core thesis of Echo OS: **Agent IS the session, not an app installed on the system.**

In a traditional OS, you open an AI assistant as one of many applications.
In Echo OS, the Agent is the nervous system of the entire device:
- Desktop / files / apps / screen are all operable by the Agent
- OS-level permission model (agent's approval/audit upgraded to system permissions, like apps requesting camera access)
- Killer experience: speak to the device, it operates across all your apps/files/storage

---

## Organ Status in OS Context

| Organ | Agent Status | OS-Specific Notes |
|---|---|---|
| Cerebrum | **Implemented** | Same as agent; planner drives desktop/file/app operations |
| Ganglia | **Not implemented** | Even more valuable in OS: each app could have its own Ganglion |
| Arms | **Partial** | OS adds: file_arm, app_arm (launcher), media_arm |
| Tentacle | **Implemented** | OS: HDMI output is a Tentacle; each Docker app is a Tentacle |
| Suckers | **Implemented** | OS adds: app-specific skills (qBittorrent, Jellyfin, Immich, etc.) |
| Beak | **Implemented** | Same as agent |
| Mantle | **Implemented** | OS: every Docker app runs in its own Mantle |
| Siphon | **Implemented** | OS: kiosk rendering is a Siphon output |
| Eyes | **Implemented** | OS: screen capture as input |
| Skin | **Not implemented** | OS: system metrics, disk health, network status — ideal Skin signals |
| Nerves | **Implemented** | OS: file watcher events, Docker events, systemd events |
| Chromatophores | **Implemented** | OS: app status broadcasts (app.started, app.stopped, app.crashed) |
| Ink Sac | **Implemented** | OS: local + cloud inference budget control |
| Immunity | **Partial** | OS: app permission model (app requests access to files/network) |
| Hearts | **Partial** | OS: single device, HA less relevant; process health monitoring more relevant |
| Genome | **Partial** | OS: device-specific DNA (local-first policies, ollama defaults) |
| Hemolymph | **Implemented** | Same as agent |
| Camouflage | **Implemented** | OS: A/B test different desktop layouts or agent strategies |
| Regeneration | **Implemented** | OS: auto-heal crashed apps, auto-rollback failed updates |

---

## OS-Specific Vision Mechanisms

### Agent as System Nerve

> ⚠️ Status: **Partially implemented** (approval gate exists, OS-level permission model not yet built)

The Agent doesn't just chat — it operates the entire device:
- **File operations**: "Find all invoices from 2023" → semantic search + organize
- **App orchestration**: "Download complete → identify episode → rename → notify Jellyfin"
- **System management**: "Disk space low" → Agent auto-cleans, notifies user
- **Update management**: "Update failed" → Agent auto-rollbacks via A/B partition

### Desktop as Product

> ⚠️ Status: **P2 in progress**

Three primitives:
1. **Chat** (Agent conversation)
2. **Window** (App Web UI in iframe)
3. **File** (File manager with recycle bin)

The Agent can open/close windows, manage files, and interact with apps — all through natural language.

### Local Sovereignty

> ⚠️ Status: **Partially implemented** (ollama support exists, local-first not yet default)

- Single-user, local-first, data never leaves the device
- ollama as default model provider
- Private RAG over local documents
- Sensitive content forced to local inference

### Self-Healing Appliance

> ⚠️ Status: **Not implemented**

- Disk alarm → Agent auto-handles
- App crash → Agent auto-restarts
- Update failure → Agent auto-rollbacks (A/B atomic updates)
- "Self-healing home appliance" model

---

## See Also

- [guide/architecture.md](../guide/architecture.md) — Engineering-only architecture
- [biomimetic/README.md](../biomimetic/README.md) — Organ → code mapping
- [ECHO_OS_PLAN.md](../ECHO_OS_PLAN.md) — Full OS plan
- [OS_DIFFERENTIATION.md](../OS_DIFFERENTIATION.md) — OS vs Agent differentiation
- [implementation-status.md](../implementation-status.md) — Per-mechanism status
