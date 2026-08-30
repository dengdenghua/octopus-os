# Cross-cutting configuration rename

Rename the canonical configuration key from `max_turns` to `turn_limit`
across the supplied repository.

Acceptance requirements:

- `normalize_config({"turn_limit": 12})` returns `turn_limit=12` and does
  not expose `max_turns` as a normalized output key.
- `max_turns` remains accepted as a deprecated compatibility alias.
- When both keys are supplied, `turn_limit` takes precedence.
- The CLI consumer reads the canonical `turn_limit` key.
- The example configuration uses only `turn_limit`.
- Documentation names `turn_limit` and explains that `max_turns` is deprecated.
- Add persistent targeted tests under `tests/test_*.py` covering the canonical
  key, compatibility alias, precedence/default behavior, and the CLI consumer.
- Run the targeted tests and leave all unrelated files unchanged.
