# Engine import performance

Measured on the Windows development host on 2026-09-07, using Python 3.12.
Each sample starts a fresh interpreter; values below are medians of three
processes. Timing starts immediately before import and excludes interpreter
startup. Filesystem cache was not deliberately flushed.

| Import | Before | After | Loaded modules before / after |
| --- | ---: | ---: | ---: |
| `runtime.core.cerebrum` | 0.7547 s | 0.0076 s | 744 / 75 |
| `runtime.core.cerebrum.react_types` | 0.7369 s | 0.0233 s | 745 / 91 |
| `runtime.execution.codex_backend.types` | 0.8579 s | 0.0242 s | 850 / 91 |

The package initializers previously imported the planner, account management,
execution and proxy modules even when a caller only requested a lightweight
type. Public exports now resolve on first access and are cached in the package.
Type-checking imports, `__all__`, discovery through `dir`, and star imports are
retained. No execution engine or recovery functionality was removed.

This measures lightweight import latency, not full application startup,
steady-state task latency, memory consumption, or model throughput. A caller
that needs the full planner/executor still pays its import cost on first use.

`tests/test_engine_lazy_imports.py` uses isolated interpreters to check that
lightweight imports do not load unused engines and that every public export
retains its original object identity. Performance assertions intentionally do
not impose machine-dependent millisecond thresholds.
