# Echo Concepts

Echo has a biomimetic kernel, but new readers should not have to learn the
biomimetic names first. This page is the plain-language map.

## One Sentence

Echo is an Agent OS for running, supervising, and evolving AI agents on your
computer.

## The Mental Model

Use this model before learning the internal organ names:

```text
Goal -> Plan -> Execute -> Observe -> Remember -> Improve
```

What each part means:

| Plain term | What it does | Internal name |
|---|---|---|
| Agent OS | Runs and supervises agents as long-lived workers | Echo runtime |
| Workspace | The IDE-like control surface for humans | Frontend / UI |
| Planner | Breaks a goal into steps or a task graph | Cerebrum |
| Reflex Layer | Answers known/simple intents without an LLM round trip | SpinalCord |
| Scheduler | Runs task graphs and coordinates execution | Ganglia（未实装）|
| Agent / Worker | A role-specific AI worker | Arm |
| Skill / Tool | A callable capability used by agents | Sucker / Beak |
| Event Bus | Carries typed events through the runtime | Nerves |
| Memory Store | Persists facts, threads, journal events, and checkpoints | Genome |
| Shared Context | Lets agents coordinate through short-term state | Hemolymph / Blackboard |
| Safety Guard | Checks trust, scope, policy, and risky behavior | Immunity |
| Budget Guard | Stops runaway token, cost, or latency usage | Ink |
| Evolution Loop | Learns from journal history and proposes improvements | Regeneration |
| Strategy Selector | Tests and shifts between prompt/skill variants | Camouflage |
| Model Router | Selects and calls model providers | Eyes |
| IO Gateway | Exposes HTTP, streaming, channels, and UI APIs | Siphon |

## Three-Layer Language Rule

Echo uses three vocabularies for three audiences:

| Audience | Use this language | Example |
|---|---|---|
| Users | Outcome language | "Run an agent task and review the result" |
| Developers | Engineering language | "Planner sends a task graph to the scheduler" |
| Kernel contributors | Biomimetic language | "Cerebrum and Ganglia coordinate through Nerves" |

Default to the leftmost language that works. Biomimetic terms are useful once a
reader wants the deeper architecture, but they should not block the first task.

## What Echo Is Not

Echo is not just an IDE, browser, desktop app, or agent framework.

Those are surfaces:

| Surface | Role |
|---|---|
| Desktop App | Delivery form |
| Workspace | Human control room |
| Browser / Extension | A place agents can act |
| OpenAI-compatible API | Integration surface |
| MCP / tools | Capability ecosystem |

The core is the Agent OS: long-running agents, memory, supervision, safety,
budgeting, and improvement loops.

## First Principles

Keep these in mind when reading the code:

1. Fast path before slow path. Known work should use reflexes before LLM calls.
2. Every meaningful action should leave an event trail in the journal.
3. Agents should be supervised: scoped, budgeted, cancellable, and inspectable.
4. Skills are capabilities, not magic. They should be testable and auditable.
5. Memory should improve future work without hiding what changed.
6. Evolution is proposed and reviewed; it is not blind self-modification.

## Where To Go Next

- First hands-on task: [GOLDEN_PATH.md](GOLDEN_PATH.md)
- Existing quick start: [../QUICKSTART.md](../QUICKSTART.md)
- Naming contract: [naming.md](naming.md)
- Full architecture: [architecture.md](architecture.md)
