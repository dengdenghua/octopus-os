# ECHO Task Projection v1

ECHO Task Projection is the device-owner view of work already managed by Echo Agent.
It gives the desktop a native task surface without creating another scheduler, task store or
approval system inside Echo OS.

## Authority boundary

| Concern | Authority |
| --- | --- |
| Task creation, lifecycle and recovery | Echo Agent `TaskSupervisor` |
| Conversation and thread state | Echo Agent |
| System capability discovery and policy | Echo Capability Contract |
| Password step-up and single-use approval | Echo appliance approval service |
| File, app and storage execution | Existing Echo provider routers |
| Device-owner presentation | Echo OS Task Space |

The projection does not copy task records to an Echo-owned database and it does not infer
lifecycle state from UI state. Its two recovery actions are an explicitly confirmed lease
takeover and a separately confirmed checkpoint-resume handoff. Both re-enter Echo Agent's
public lifecycle routes; Echo OS never becomes an executor.

## Endpoints

```text
GET /api/appliance/tasks?status=<status>&limit=<1..200>
GET /api/appliance/tasks/<task-id>
POST /api/appliance/tasks/<task-id>/takeover
POST /api/appliance/tasks/<task-id>/resume-execution
```

The endpoint requires the same device session as other appliance APIs and returns schema
`echo.task_projection.v1`. When the Agent supervisor is not mounted, the response remains
well formed with `available: false` and an empty task list.

On a native Echo OS image, the Agent listens only on loopback behind the PAM/logind desktop
session boundary. Its native extension exposes the same schema without introducing a second
browser login. NAS appliance deployments continue to require the appliance session. The detail
endpoint uses schema `echo.task_projection.detail.v1`; takeover returns `echo.task_action.v1`.

Takeover is deliberately narrow. It is accepted only when Agent reports an expired or missing
lease as takeover-eligible. It reacquires the authoritative Agent lease but does not execute the
task, replay a checkpoint or synthesize progress. The response therefore always includes
`requiresWorkspaceResume: true`, and the desktop directs the owner back to the original thread.
NAS deployments record attempted and successful/conflicting takeover events in the HMAC audit
chain using the task ID as the intent ID. If that audit cannot be written or verified, takeover
fails closed. Native loopback mode currently retains Agent's own takeover metadata while the
native OS audit provider is completed.

Resume is a distinct second action. The projection asks the live Agent runtime for a real ReAct
checkpoint; a stored `latestCheckpointId` by itself is not sufficient. After any required lease
takeover, the desktop can submit one intent-bound recovery request. Echo records the attempted
handoff, calls Agent's `echo.task_run_resume_execution.v1` endpoint in the same ASGI process,
and records the accepted turn coordinate. Agent then starts a normal server-resident turn on the
original thread with a stable user-item ID. The existing thread claim, checkpoint loader,
`TaskSupervisor`, interruption path and approval policy remain authoritative. The synthetic
handoff connection cannot answer approvals, so any new protected operation still waits for a
real client in the original task. NAS audit failure blocks the handoff before execution.

## Correlation contract

The stable join key is:

```text
Agent task.task_id == capability request.intentId == X-Echo-Intent
```

Capability decisions, approval events and provider execution audit records carrying that
intent are projected into the matching task. File and application providers add the verified
intent header to their audit metadata automatically. A caller cannot replace the authenticated
actor, and a high-risk approval signed for one intent cannot be replayed for another intent.

## Projected state

Each task includes:

- identity, parent task and thread references;
- Agent title, goal summary, mode and lifecycle status;
- optional progress and latest checkpoint;
- live checkpoint recovery availability, iteration and phase;
- authoritative Agent lease health and recovery advice;
- runtime capability groups declared by the Agent task;
- the latest capability policy decisions;
- waiting-approval context;
- correlated approval and provider execution activity.

The desktop orders waiting approvals first, then active work, paused work and terminal tasks.
Within each group the newest task is shown first.

A non-terminal record whose Agent lease is expired or missing keeps its original lifecycle
status for diagnostics, but is presented as disconnected and counted under “recovery needed”.
The desktop never converts that stale record into a successful or currently-running task.

## Bounded device view

The current appliance profile is a single-device-owner surface, so the projection requests the
supervisor's device-wide records. It reads at most 1,000 task records before applying the API
filter and limit, and joins the latest 200 appliance audit entries. These are presentation bounds,
not retention policy; the Agent task store and appliance audit remain authoritative.

If audit integrity verification fails, the endpoint fails closed with HTTP 503 instead of showing
an apparently trustworthy partial history. If no audit is configured, task state remains visible
and the response marks audit integrity as unavailable.

## Desktop behavior

The Task Space Dock item polls the projection every five seconds while the authenticated desktop
is active. It shows a badge for waiting approvals and presents status, progress, approval reason,
latest capability decision and audit-chain state. Selecting a task opens its native detail drawer.
An interrupted task first requests an explicit lease takeover. When Agent then verifies a real
checkpoint, the drawer offers a second “Resume execution and open original task” confirmation.
An accepted handoff immediately opens the original Agent thread for live progress and later
approvals. Waiting approval is informative only: approval remains in the original Agent task and
system-capability approvals still require the device password and single-use capability policy.
Task Space does not embed a fallback Agent or bypass those controls.

## Deliberate v1 limits

- Task cancellation, manual pause and approval decisions remain in the Agent workbench.
- Historical pagination is not yet exposed in the desktop panel.
- The projection does not turn arbitrary Agent tools into system capabilities.
- The UI does not synthesize success from a policy `allow`; provider execution evidence remains
  distinct from the preflight decision.
