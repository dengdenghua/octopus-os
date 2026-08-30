import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { AllProviders } from "@/test/harness";

import { AGENT_WORKBENCH_FOCUS_EVENT } from "./agent-workbench-events";
import type { LiveToolEvent } from "./live-tool-timeline";
import {
  SwarmRunOverview,
  buildSwarmReplayPackage,
  buildSwarmRunOverview,
} from "./swarm-run-overview";

function event(partial: Partial<LiveToolEvent>): LiveToolEvent {
  return {
    id: "evt-1",
    name: "call_agent_parallel",
    status: "running",
    startedAt: 1000,
    iteration: 1,
    ...partial,
  };
}

describe("SwarmRunOverview", () => {
  test("derives running lanes from parallel dispatch specs", () => {
    const overview = buildSwarmRunOverview([
      event({
        input: {
          specs: [
            { agent_id: "researcher", task: "collect market evidence" },
            { agent_id: "critic", task: "challenge weak claims" },
            { agent_id: "synthesizer", task: "merge final answer" },
          ],
        },
      }),
    ]);

    expect(overview).not.toBeNull();
    expect(overview?.counts).toMatchObject({
      total: 3,
      running: 3,
      done: 0,
      error: 0,
    });
    expect(overview?.status).toBe("running");
    expect(overview?.phases.map((phase) => [phase.id, phase.status])).toEqual([
      ["dispatch", "done"],
      ["execute", "running"],
      ["aggregate", "pending"],
      ["synthesize", "pending"],
    ]);
    expect(overview?.activePhase).toMatchObject({
      id: "execute",
      status: "running",
    });
    expect(overview?.agents.map((agent) => agent.role)).toEqual([
      "researcher",
      "critic",
      "synthesizer",
    ]);
  });

  test("promotes cowork team swarm results into synthesis stages", () => {
    const overview = buildSwarmRunOverview([
      event({
        id: "team-1",
        name: "team_swarm",
        status: "done",
        input: {
          specs: [
            { agent_id: "db-agent", task: "check data model" },
            { agent_id: "ui-agent", task: "check interaction" },
          ],
        },
        output: {
          schema: "echo.group_fanout_result.v1",
          replies: [
            { agent_id: "db-agent", ok: true, reply: "schema is safe" },
            { agent_id: "ui-agent", ok: true, reply: "flow is clear" },
          ],
          arbitration: {
            outcomes: [
              { agent_id: "db-agent", status: "answered" },
              { agent_id: "ui-agent", status: "answered" },
            ],
          },
          synthesis: {
            schema: "echo.group_fanout_synthesis.v1",
            primary_agent_id: "db-agent",
            primary_reply: "schema is safe",
            supporting_agent_ids: ["ui-agent"],
            retry_agent_ids: [],
            answered_count: 2,
            total_count: 2,
            recommended_next_action: "use_primary_response",
            ready: true,
          },
        },
      }),
      event({
        id: "sub-db",
        name: "subagent",
        status: "done",
        agentId: "db-agent",
        subAgentRole: "cowork",
        parentToolUseId: "team-1",
        output: "schema is safe",
      }),
      event({
        id: "sub-ui",
        name: "subagent",
        status: "done",
        agentId: "ui-agent",
        subAgentRole: "cowork",
        parentToolUseId: "team-1",
        output: "flow is clear",
      }),
    ]);

    expect(overview).not.toBeNull();
    expect(overview?.counts).toMatchObject({
      total: 2,
      done: 2,
      running: 0,
      error: 0,
    });
    expect(overview?.resultCount).toBe(5);
    expect(overview?.evidenceCount).toBeGreaterThanOrEqual(3);
    expect(overview?.phases.map((phase) => [phase.id, phase.status])).toEqual([
      ["dispatch", "done"],
      ["execute", "done"],
      ["aggregate", "done"],
      ["synthesize", "done"],
    ]);
    expect(overview?.activePhase).toMatchObject({
      id: "synthesize",
      status: "done",
    });
    expect(overview?.agents.map((agent) => agent.id)).toEqual([
      "db-agent",
      "ui-agent",
    ]);
    expect(overview?.synthesis).toMatchObject({
      primaryAgentId: "db-agent",
      primaryReply: "schema is safe",
      supportingCount: 1,
      retryCount: 0,
      answeredCount: 2,
      totalCount: 2,
      nextAction: "use_primary_response",
      ready: true,
    });
  });

  test("builds an auditable swarm replay package", () => {
    const events = [
      event({
        id: "team-1",
        name: "team_swarm",
        status: "done",
        input: {
          specs: [
            { agent_id: "db-agent", task: "check data model" },
            { agent_id: "ui-agent", task: "check interaction" },
          ],
        },
        output: {
          synthesis: {
            schema: "echo.group_fanout_synthesis.v1",
            primary_agent_id: "db-agent",
            primary_reply: "schema is safe",
            supporting_agent_ids: ["ui-agent"],
            retry_agent_ids: [],
            answered_count: 2,
            total_count: 2,
            recommended_next_action: "use_primary_response",
            ready: true,
          },
        },
      }),
      event({
        id: "sub-db",
        name: "subagent",
        status: "done",
        agentId: "db-agent",
        subAgentRole: "cowork",
        parentToolUseId: "team-1",
        output: "schema is safe",
      }),
      event({
        id: "sub-ui",
        name: "subagent",
        status: "done",
        agentId: "ui-agent",
        subAgentRole: "cowork",
        parentToolUseId: "team-1",
        output: "flow is clear",
      }),
    ];
    const overview = buildSwarmRunOverview(events);

    expect(overview).not.toBeNull();
    const replayPackage = buildSwarmReplayPackage(overview!, events);

    expect(replayPackage).toMatchObject({
      schema: "echo.swarm_replay_package.v1",
      overview: {
        activePhase: "synthesize",
        status: "done",
        evidenceCount: expect.any(Number),
        resultCount: 1,
      },
      synthesis: {
        primaryAgentId: "db-agent",
        primaryReply: "schema is safe",
        ready: true,
      },
    });
    expect(replayPackage.agents.map((agent) => agent.id)).toEqual([
      "db-agent",
      "ui-agent",
    ]);
    expect(replayPackage.phases.map((phase) => phase.id)).toEqual([
      "dispatch",
      "execute",
      "aggregate",
      "synthesize",
    ]);
    expect(replayPackage.timeline).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          agentId: "db-agent",
          event: "subagent",
          status: "done",
          summary: "schema is safe",
        }),
        expect.objectContaining({
          event: "team_swarm",
          summary: "2 member dispatch",
        }),
      ]),
    );
    expect(replayPackage.events).toHaveLength(3);
  });

  test("preserves large swarm capacity in overview and replay export", () => {
    const specs = Array.from({ length: 32 }, (_, index) => ({
      agent_id: `agent-${index}`,
      task: `work item ${index}`,
    }));
    const overview = buildSwarmRunOverview([
      event({
        id: "team-large",
        name: "team_swarm",
        status: "running",
        input: {
          specs,
          capacity: {
            schema: "echo.group_fanout_capacity.v1",
            requested_members: 300,
            dispatched_members: 32,
            dropped_members: 268,
            max_members: 32,
            concurrency: 32,
            capacity_tier: "kimi_scale",
          },
        },
      }),
    ]);

    expect(overview).not.toBeNull();
    expect(overview?.counts.total).toBe(32);
    expect(overview?.counts.pending).toBe(20);
    expect(overview?.capacity).toMatchObject({
      requestedMembers: 300,
      dispatchedMembers: 32,
      droppedMembers: 268,
      maxMembers: 32,
      concurrency: 32,
      capacityTier: "kimi_scale",
    });

    const replayPackage = buildSwarmReplayPackage(overview!, []);
    expect(replayPackage.overview.capacity).toMatchObject({
      requestedMembers: 300,
      dispatchedMembers: 32,
      droppedMembers: 268,
      capacityTier: "kimi_scale",
    });
  });

  test("does not render for ordinary single-tool timelines", () => {
    expect(
      buildSwarmRunOverview([
        event({
          id: "read-1",
          name: "read_file",
          status: "done",
          input: { path: "src/app.tsx" },
        }),
      ]),
    ).toBeNull();
  });

  test("renders compact agent lanes and focuses the workbench on click", () => {
    const focusSpy = vi.fn();
    window.addEventListener(AGENT_WORKBENCH_FOCUS_EVENT, focusSpy);
    try {
      render(
        <AllProviders locale="en-US">
          <SwarmRunOverview
            events={[
              event({
                input: {
                  specs: [
                    { agent_id: "researcher", task: "collect market evidence" },
                    { agent_id: "critic", task: "challenge weak claims" },
                  ],
                },
              }),
            ]}
          />
        </AllProviders>,
      );

      expect(screen.getByLabelText("Agent Collaboration")).toBeInTheDocument();
      expect(screen.getByText("Dispatch")).toBeInTheDocument();
      expect(screen.getAllByText("Execute").length).toBeGreaterThan(0);
      expect(screen.getByText("Aggregate")).toBeInTheDocument();
      expect(screen.getByText("Synthesize")).toBeInTheDocument();
      expect(screen.getByText(/researcher is working/)).toBeInTheDocument();
      expect(screen.getByText(/0\/2 done/)).toBeInTheDocument();
      expect(screen.getAllByText("researcher").length).toBeGreaterThan(0);
      expect(screen.getAllByText("critic").length).toBeGreaterThan(0);
      fireEvent.click(
        screen.getByRole("button", { name: /collect market evidence/i }),
      );
      expect(focusSpy).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(AGENT_WORKBENCH_FOCUS_EVENT, focusSpy);
    }
  });

  test("renders the final synthesis delivery strip", () => {
    render(
      <AllProviders locale="en-US">
        <SwarmRunOverview
          events={[
            event({
              id: "team-1",
              name: "team_swarm",
              status: "done",
              input: {
                specs: [
                  { agent_id: "db-agent", task: "check schema" },
                  { agent_id: "ui-agent", task: "check flow" },
                ],
              },
              output: {
                synthesis: {
                  schema: "echo.group_fanout_synthesis.v1",
                  primary_agent_id: "db-agent",
                  primary_reply: "schema is safe",
                  supporting_agent_ids: ["ui-agent"],
                  retry_agent_ids: [],
                  answered_count: 2,
                  total_count: 2,
                  recommended_next_action: "use_primary_response",
                  ready: true,
                },
              },
            }),
            event({
              id: "sub-db",
              name: "subagent",
              status: "done",
              agentId: "db-agent",
              subAgentRole: "cowork",
              parentToolUseId: "team-1",
              output: "schema is safe",
            }),
            event({
              id: "sub-ui",
              name: "subagent",
              status: "done",
              agentId: "ui-agent",
              subAgentRole: "cowork",
              parentToolUseId: "team-1",
              output: "flow is clear",
            }),
          ]}
        />
      </AllProviders>,
    );

    expect(screen.getByText("Delivery ready")).toBeInTheDocument();
    expect(screen.getByText(/Collaboration delivered/)).toBeInTheDocument();
    expect(screen.getByText(/2\/2 done/)).toBeInTheDocument();
    expect(screen.getByText("Primary:")).toBeInTheDocument();
    expect(screen.getByText("db-agent")).toBeInTheDocument();
    expect(screen.getByText("1 supporting")).toBeInTheDocument();
    expect(screen.getByText("Coverage: 2/2 members")).toBeInTheDocument();
    expect(screen.getByText(/Use the primary response/)).toBeInTheDocument();
    expect(screen.getByText(/Main answer/)).toBeInTheDocument();
    expect(screen.getByText(/schema is safe/)).toBeInTheDocument();
    expect(screen.queryByText(/use_primary_response/)).not.toBeInTheDocument();
  });

  test("copies the final synthesized answer from the delivery strip", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const originalClipboard = Object.getOwnPropertyDescriptor(
      navigator,
      "clipboard",
    );
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    try {
      render(
        <AllProviders locale="en-US">
          <SwarmRunOverview
            events={[
              event({
                id: "team-1",
                name: "team_swarm",
                status: "done",
                input: {
                  specs: [
                    { agent_id: "db-agent", task: "check schema" },
                    { agent_id: "ui-agent", task: "check flow" },
                  ],
                },
                output: {
                  synthesis: {
                    schema: "echo.group_fanout_synthesis.v1",
                    primary_agent_id: "db-agent",
                    primary_reply: "schema is safe",
                    supporting_agent_ids: ["ui-agent"],
                    retry_agent_ids: [],
                    answered_count: 2,
                    total_count: 2,
                    recommended_next_action: "use_primary_response",
                    ready: true,
                  },
                },
              }),
              event({
                id: "sub-db",
                name: "subagent",
                status: "done",
                agentId: "db-agent",
                subAgentRole: "cowork",
                parentToolUseId: "team-1",
                output: "schema is safe",
              }),
              event({
                id: "sub-ui",
                name: "subagent",
                status: "done",
                agentId: "ui-agent",
                subAgentRole: "cowork",
                parentToolUseId: "team-1",
                output: "flow is clear",
              }),
            ]}
          />
        </AllProviders>,
      );

      fireEvent.click(screen.getByRole("button", { name: "Copy main answer" }));

      await waitFor(() => {
        expect(writeText).toHaveBeenCalledWith("schema is safe");
      });
      expect(
        screen.getByRole("button", { name: "Copied main answer" }),
      ).toBeInTheDocument();
    } finally {
      if (originalClipboard) {
        Object.defineProperty(navigator, "clipboard", originalClipboard);
      } else {
        Reflect.deleteProperty(navigator, "clipboard");
      }
    }
  });

  test("exports the collaboration replay package from the delivery strip", async () => {
    const createObjectURL = vi.fn(() => "blob:swarm-replay");
    const revokeObjectURL = vi.fn();
    const click = vi.fn();
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ ok: true, session: {}, evidence: {} }),
      text: async () => "",
    }));
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    const originalFetch = globalThis.fetch;
    const originalCreateElement = document.createElement.bind(document);
    const createdAnchors: HTMLAnchorElement[] = [];

    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    globalThis.fetch = fetchMock as typeof fetch;
    vi.spyOn(document, "createElement").mockImplementation((tagName) => {
      const element = originalCreateElement(tagName);
      if (tagName.toLowerCase() === "a") {
        createdAnchors.push(element as HTMLAnchorElement);
        vi.spyOn(element as HTMLAnchorElement, "click").mockImplementation(
          click,
        );
      }
      return element;
    });

    try {
      render(
        <AllProviders locale="en-US">
          <SwarmRunOverview
            controlSessionId="ctrl-swarm-1"
            events={[
              event({
                id: "team-1",
                name: "team_swarm",
                status: "done",
                input: {
                  specs: [
                    { agent_id: "db-agent", task: "check schema" },
                    { agent_id: "ui-agent", task: "check flow" },
                  ],
                },
                output: {
                  synthesis: {
                    schema: "echo.group_fanout_synthesis.v1",
                    primary_agent_id: "db-agent",
                    primary_reply: "schema is safe",
                    supporting_agent_ids: ["ui-agent"],
                    retry_agent_ids: [],
                    answered_count: 2,
                    total_count: 2,
                    recommended_next_action: "use_primary_response",
                    ready: true,
                  },
                },
              }),
              event({
                id: "sub-db",
                name: "subagent",
                status: "done",
                agentId: "db-agent",
                subAgentRole: "cowork",
                parentToolUseId: "team-1",
                output: "schema is safe",
              }),
              event({
                id: "sub-ui",
                name: "subagent",
                status: "done",
                agentId: "ui-agent",
                subAgentRole: "cowork",
                parentToolUseId: "team-1",
                output: "flow is clear",
              }),
            ]}
          />
        </AllProviders>,
      );

      fireEvent.click(
        screen.getByRole("button", { name: "Export replay package" }),
      );

      await waitFor(() => {
        expect(createObjectURL).toHaveBeenCalledTimes(1);
      });
      expect(click).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:swarm-replay");
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          expect.stringContaining("/api/control-sessions"),
          expect.objectContaining({ method: "POST" }),
        );
        expect(fetchMock).toHaveBeenCalledWith(
          expect.stringContaining(
            "/api/control-sessions/ctrl-swarm-1/evidence",
          ),
          expect.objectContaining({
            method: "POST",
            body: expect.stringContaining("echo.swarm_replay_package.v1"),
          }),
        );
        expect(fetchMock).toHaveBeenCalledWith(
          expect.stringContaining("/api/control-sessions/ctrl-swarm-1/actions"),
          expect.objectContaining({
            method: "POST",
            body: expect.stringContaining("swarm_replay_export"),
          }),
        );
        expect(fetchMock).toHaveBeenCalledWith(
          expect.stringContaining(
            "/api/control-sessions/ctrl-swarm-1/actions/ctrl-swarm-1-swarm-replay-export-",
          ),
          expect.objectContaining({
            method: "PATCH",
            body: expect.stringContaining('"status":"done"'),
          }),
        );
      });
      const evidenceRequest = fetchMock.mock.calls.find(([url]) =>
        String(url).includes("/api/control-sessions/ctrl-swarm-1/evidence"),
      );
      const body = JSON.parse(String(evidenceRequest?.[1]?.body));
      expect(body).toMatchObject({
        action_id: expect.stringContaining("ctrl-swarm-1-swarm-replay-export-"),
        action: "swarm_replay_export",
        kind: "log",
        ok: true,
        detail: {
          schema: "echo.swarm_replay_package.v1",
          synthesis: {
            primaryReply: "schema is safe",
          },
        },
      });
      expect(createdAnchors[0]?.download).toMatch(
        /^echo-swarm-replay-.+\.json$/,
      );
      expect(
        screen.getByRole("button", { name: "Replay package exported" }),
      ).toBeInTheDocument();
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
      URL.revokeObjectURL = originalRevokeObjectURL;
      globalThis.fetch = originalFetch;
      vi.restoreAllMocks();
    }
  });

  test("renders retry note when synthesis still needs member follow-up", () => {
    render(
      <AllProviders locale="en-US">
        <SwarmRunOverview
          events={[
            event({
              id: "team-1",
              name: "team_swarm",
              status: "done",
              input: {
                specs: [
                  { agent_id: "db-agent", task: "check schema" },
                  { agent_id: "qa-agent", task: "check edge cases" },
                ],
              },
              output: {
                synthesis: {
                  schema: "echo.group_fanout_synthesis.v1",
                  primary_agent_id: "db-agent",
                  primary_reply: "ship schema guard first",
                  supporting_agent_ids: [],
                  retry_agent_ids: ["qa-agent"],
                  answered_count: 1,
                  total_count: 2,
                  recommended_next_action:
                    "use_primary_and_retry_failed_members",
                  ready: false,
                },
              },
            }),
            event({
              id: "sub-db",
              name: "subagent",
              status: "done",
              agentId: "db-agent",
              subAgentRole: "cowork",
              parentToolUseId: "team-1",
              output: "ship schema guard first",
            }),
            event({
              id: "sub-qa",
              name: "subagent",
              status: "error",
              agentId: "qa-agent",
              subAgentRole: "cowork",
              parentToolUseId: "team-1",
              output: { error: "timeout" },
            }),
          ]}
        />
      </AllProviders>,
    );

    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByText("1 to retry")).toBeInTheDocument();
    expect(screen.getByText("Coverage: 1/2 members")).toBeInTheDocument();
    expect(screen.getByText(/Use primary and retry/)).toBeInTheDocument();
    expect(screen.getByText(/1 member\(s\) need a retry/)).toBeInTheDocument();
  });

  test("falls back to replies and arbitration when synthesis is missing", () => {
    render(
      <AllProviders locale="en-US">
        <SwarmRunOverview
          events={[
            event({
              id: "team-legacy",
              name: "team_swarm",
              status: "done",
              input: {
                specs: [
                  { agent_id: "db-agent", task: "check schema" },
                  { agent_id: "ui-agent", task: "check flow" },
                ],
              },
              output: {
                schema: "echo.group_fanout_result.v1",
                replies: [
                  { agent_id: "db-agent", ok: true, reply: "legacy primary" },
                  { agent_id: "ui-agent", ok: true, reply: "legacy support" },
                ],
                arbitration: {
                  primary_agent_id: "db-agent",
                  recommended_next_action: "use_primary_response",
                  answered_agent_ids: ["db-agent", "ui-agent"],
                  failed_agent_ids: [],
                  empty_agent_ids: [],
                },
              },
            }),
            event({
              id: "sub-db",
              name: "subagent",
              status: "done",
              agentId: "db-agent",
              subAgentRole: "cowork",
              parentToolUseId: "team-legacy",
              output: "legacy primary",
            }),
            event({
              id: "sub-ui",
              name: "subagent",
              status: "done",
              agentId: "ui-agent",
              subAgentRole: "cowork",
              parentToolUseId: "team-legacy",
              output: "legacy support",
            }),
          ]}
        />
      </AllProviders>,
    );

    expect(screen.getByText("Delivery ready")).toBeInTheDocument();
    expect(screen.getByText("Coverage: 2/2 members")).toBeInTheDocument();
    expect(screen.getByText(/legacy primary/)).toBeInTheDocument();
    expect(screen.queryByText(/use_primary_response/)).not.toBeInTheDocument();
  });
});
