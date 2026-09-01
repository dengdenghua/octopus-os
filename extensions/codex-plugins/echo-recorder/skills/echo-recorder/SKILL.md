---
name: echo-recorder
description: Record an explicitly requested human demonstration or Agent workflow in EchoAI and turn the captured event stream into a reviewable reusable workflow or skill.
---

# Echo Recorder

Use the recorder only when the user asks to record, demonstrate, teach, replay,
or turn the current workflow into a reusable skill.

## Recording

- Call `recording_start` only when the user is ready. Starting a recording is
  an explicit-consent action; never enable it from an inferred preference.
- Prefer `provider: hybrid`: it combines semantic human UI events with Agent
  tool trajectories in one session. Use `human` or `agent` only when the user
  explicitly wants a single source.
- Before a browser demonstration, call `recording_provider_status`. If the
  browser provider is offline, explain that REC will safely fall back to Agent
  trajectory capture; do not block the user's task.
- Inner-browser events are collected only while REC is active. Chrome Relay
  contributes privacy-minimised semantic actions and never sends typed values.
- After starting, tell the user recording lasts at most 30 minutes. Do not poll.
  When they return, use `recording_status` once.
- Call `recording_stop` when the user says the demonstration is complete.
- Only one recording may be active per task. A repeated start returns the
  existing session rather than discarding it.

## Privacy and review

Password, OTP, payment, token, cookie, and authorization-shaped values are
redacted before persistence. Do not reconstruct or repeat redacted values.
Treat the resulting workflow as a draft: actions with broad filesystem,
browser, desktop, shell, account, publishing, or payment effects require the
normal approval policy when replayed.

The output may be a promoted skill, a quarantined candidate, or a captured
event workflow awaiting review. Report that distinction plainly. Do not claim
that a captured workflow is already safe to replay.

For event fields or provider integration, read
[references/event-schema.md](references/event-schema.md).

