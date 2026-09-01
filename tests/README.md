# Test ownership

All tests in this directory are owned by the unified Echo OS repository.

- `tests/appliance/` covers the NAS appliance, delivery, update, recovery, and
  public-source release contracts.
- Root-level `tests/test_*.py` files cover the embedded Agent runtime,
  integrations, safety boundaries, tools, and desktop-facing APIs.

The runtime is no longer tested or released from a sibling Agent repository.
During the final migration cleanup, pytest's default collection remains the
appliance suite so existing release checks stay stable. Run the complete
OS-owned suite explicitly with:

```bash
uv run pytest tests
```

The migration is complete only when this full command is green and the runtime
suite is enforced by CI alongside the appliance suite.
