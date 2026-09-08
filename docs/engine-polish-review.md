# Engine and system model integration review

## Result

Echo's native executor and embedded Codex App Server remain available. Built-in
roles select Codex explicitly; native roles retain their existing route. This
review improves shared policy, terminal outcomes, Windows startup, and system
model/account presentation. It does not claim a measured throughput improvement.

## Changes and evidence

| Area | Result | Regression evidence |
| --- | --- | --- |
| Shared policy | Realtime uses the role runner's deployment and login-source rules; engine selection uses one strictly validated flag snapshot. | `test_codex_shared_policy.py`, `test_drive_codex_app_server.py` |
| Background termination | Fatal errors finish promptly; retryable errors remain live; stop/timeout publishes cancellation and closes the execution scope. | `test_codex_role_lifecycle.py` |
| Realtime termination | Fatal errors stop polling; late terminal messages cannot overwrite an accepted cancellation. | `test_drive_codex_app_server.py` |
| Recovery | Existing bindings resume; only an exact missing-thread response permits replacement. Other errors preserve the binding and fail. | `test_codex_execution_backend.py` |
| Windows startup | New SQLite directories use a compact full-digest path under protected state storage. Realm, tenant and thread separation remain intact; task retries reuse the same directory. | `test_security.py`, real App Server test in `test_codex_responses_proxy.py` |
| Existing databases | Existing legacy SQLite directories are retained, including WAL files; no automatic live database migration is attempted. | `test_windows_sqlite_keeps_existing_legacy_database_and_wal` |
| System model UI | Independent settings entry and top-bar selector share default settings and principal-scoped account caches. Missing account, API key and failed account lookup do not fetch subscription usage; logout hides cached usage. | `system-model-status.test.tsx`, `account-security-panel.test.tsx` |

## Follow-up redundancy cleanup

The four remaining items identified in the follow-up review are resolved:

- `timeouts.py` owns defaults, bounds, environment parsing and invalid-value
  handling. Background tasks retain their explicit per-task override; realtime
  retains its environment-only entry point. Non-finite values use the default.
- The unused `_partner_command` wrapper and duplicate timeout constants were
  removed. Current production and test sources contain no references to them.
- `query-options.ts` owns account, rate-limit and usage query functions, cache
  freshness and retry policy. Both UI surfaces reuse it; each controls when its
  observer is enabled. Cumulative usage no longer has a separate minute poll.
- Both request adapters share `codex_request_policy`, executable selection and
  pinned App Server arguments. The stack-free embedding path retains its own
  identity/model/tool inputs; the normal path still resolves and checks the
  executable on disk. These differences are intentional, not duplicate policy.

Follow-up verification: Codex/Coder, native recovery, rewind and task execution
passed together (**426 passed, 3 POSIX-only skips**). The four affected frontend
suites passed (**56 tests**), as did TypeScript and modified Python static checks.
The shared-policy test checks that parent read-only restrictions survive a
server-approved execution request and that non-boolean approval cannot grant
full access.

## Retained execution paths

The native ReAct executor still supplies an active execution path and cannot be
removed as dead code. The stack-free realtime request adapter explicitly supports
older embeddings and is covered by standalone driver tests. Retaining it does not
enable a silent CLI retry when a Codex startup fails. Engine-specific transports
remain separate while both project outcomes into Echo's existing event vocabulary.

## Verification on Windows, 2026-09-07

- Codex/Coder suite: **272 passed, 3 skipped**. Skips are POSIX permission checks.
  Live/provider integration files were excluded; the deterministic real App Server
  text-and-tool test in the Responses proxy suite was included and passed.
- Native recovery, rejection, task execution and rewind: **141 passed**.
- System model panel, account settings, menu bar and Codex controls: **56 passed**.
- Frontend TypeScript check and static checks on modified Python modules passed.

The Windows fix was verified by first reproducing SQLite initialization failure
with the original nested directory, then passing the same real-process test with
the compact directory. It does not migrate existing databases or establish behavior
for arbitrary state roots that are already near Windows path limits. No paid live
provider execution or cross-platform GUI verification is claimed.
