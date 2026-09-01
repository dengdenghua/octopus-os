---
name: agent-generator
description: Generate a complete Echo Agent from a short natural-language brief. Use when creating, bootstrapping, or refining an Agent from a one-line description, role idea, use case, or persona concept; expands the brief into SOUL, ARM/tool groups, private_skills whitelist, permissions, behavior boundaries, default workflow, and setup_agent-ready configuration.
---

# Agent Generator

## Goal

Turn a short conversational brief into a usable Echo Agent configuration. Prefer a complete first draft over asking the user to write a long persona document.

## Workflow

1. Read the user's brief and infer:
   - role identity and job-to-be-done
   - target users and usage scenes
   - tone, interaction style, and output shape
   - required tools, ARM/tool groups, and Skill whitelist
   - permissions, confirmation gates, and safety boundaries
   - first-run tasks and reusable memory the Agent should maintain

2. If the brief is vague, make reasonable assumptions and state them briefly. Ask at most two clarifying questions only when missing information would make the Agent unsafe or unusable.

3. Produce a concise Agent blueprint:
   - **Agent ID**: lowercase kebab-case suggestion if the caller has not provided one
   - **Positioning**: one paragraph explaining what the Agent is for
   - **SOUL.md Draft**: system behavior, personality, responsibilities, boundaries, and default output style
   - **ARM / Tool Groups**: tool groups the Agent should be allowed to use
   - **private_skills**: include `agent-generator` plus task-specific skills
   - **Permissions**: what can be done automatically, what needs preview, and what needs explicit confirmation
   - **Default Workflow**: how the Agent handles a normal request
   - **First Task**: a useful starter action after creation

4. When the user explicitly asks to save/create the Agent, or the surrounding flow says this is confirmed, call `setup_agent` with the generated configuration. Do not wait for another confirmation if the user has already clicked a save/create action.

## Configuration Rules

- Always include `agent-generator` in `private_skills` so the Agent can refine itself later.
- Keep `SOUL.md` readable and operational. It should feel like a strong role card, not a legal policy document.
- Do not overgrant permissions. Prefer narrow default rights plus explicit confirmation for irreversible, external, financial, destructive, or message-sending actions.
- Match ARM/tool groups to the actual job:
  - research and market analysis: `browser`, `search`, `knowledge`, `files`
  - workspace coordination: `tasks`, `calendar`, `team`, `knowledge`
  - local execution: `computer`, `filesystem`, and command execution only with confirmation gates
  - content creation: writing, knowledge, files, and optional image/media skills when relevant
- If the UI provides selected scenes, capability packs, permissions, or template references, treat them as strong hints but let the user's brief override mismatched presets.

## Output Style

Be compact, specific, and implementation-ready. Avoid generic phrases such as "help users improve efficiency" unless paired with concrete duties, tools, and examples.
