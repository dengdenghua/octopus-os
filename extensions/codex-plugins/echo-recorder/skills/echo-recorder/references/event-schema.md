# Echo recording event contract

Sessions use `echo.recording.session.v1`; JSONL rows use
`echo.recording.event.v1`.

Required event fields:

- `ts`: ISO-8601 timestamp.
- `source`: `human`, `agent`, `browser`, or `desktop`.
- `kind`: semantic action or observation type.

Portable optional fields:

- `app` and `window`: attribution for the active surface.
- `target`: stable semantic identity such as tag, role, accessible label, name,
  test id, or short visible text. Coordinates are supplemental, never the only
  replay identity when a semantic target exists.
- `data`: bounded action-specific values. Sensitive keys and fields are stored
  as `[REDACTED]`.

Providers append events to the active task session in batches of at most 100.
Keep each serialized event below 32 KiB. Provider failures are side-channel
failures and must not interrupt the task being demonstrated.

Browser providers:

- Electron webview capture is installed and drained only while REC is active.
  Non-sensitive field values may be included to make the draft replayable.
- Chrome Relay reports a privacy-minimised target descriptor and control keys;
  it does not transmit typed field values.
- A relay disconnect degrades capture to Agent trajectory recording. It must
  not pause or fail the demonstrated task.

