/* Implementation note. */

import {
  Loader2Icon,
  SendIcon,
  SparklesIcon,
  XIcon,
  FileTextIcon,
  ClipboardCheckIcon,
  DownloadIcon,
  LanguagesIcon,
  ListIcon,
  ChevronDownIcon,
  StopCircleIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { swallow } from "@/core/utils/log";
import { useThreadStream } from "@/core/threads/hooks";
import { isAIMessage, isHumanMessage } from "@/core/api/types";
import { useI18n } from "@/core/i18n/hooks";
import { useActiveAgentId } from "@/core/agents/active";
import { useAgents } from "@/core/agents/hooks";
import { isPrimaryPersonaAgentId } from "@/core/agents/persona-policy";
import { copyTextToClipboard } from "@/core/clipboard";
import { emitAgentChanged } from "@/core/events";
import { useCapabilitySurface } from "@/core/plugins/use-capability-surface";
import {
  appendRecordingEvents,
  getRecordingStatus,
  startRecording,
  stopRecording,
} from "@/core/teach-repeat/api";
import {
  browserRecorderDrainScript,
  normalizeBrowserRecordingEvents,
} from "@/core/teach-repeat/browser-events";
import {
  subscribeBrowserRelayStatus,
  type BrowserRelayStatus,
} from "@/core/settings/automation-status-api";
import type { RecordingEvent } from "@/core/teach-repeat/types";
import {
  BROWSER_AGENT_POLICY_EVENT,
  browserHttpOrigin,
  getBrowserAgentPermission,
  recordBrowserAgentAudit,
  setBrowserAgentPermission,
} from "@/core/browser/agent-permissions";
import { cn } from "@/lib/utils";

import {
  BROWSER_ACTION_PROTOCOL,
  formatResults,
  parseActions,
  runActionWithRetry,
  runBrowserActionWithControl,
  runBrowserHandleActionWithControl,
  withActionTimeout,
  type AgentAction,
  type ActionResult,
  type BrowserControlOptions,
} from "./agentic-actions";
import { useBrowserStore } from "./browser-store";

import type { WebviewTabHandle } from "./webview-tab";

interface Props {
  webviewHandle: WebviewTabHandle | null;
}

interface PendingConfirmation {
  id: string;
  action: AgentAction;
  error?: string;
  detail?: Record<string, unknown>;
  createdAt: number;
}

interface PendingSiteAccess {
  origin: string;
  aiMessageId: string;
}

interface ResearchPlatform {
  name: string;
  url: string;
  hint: string;
}

interface ResearchLogEntry {
  id: string;
  createdAt: number;
  platform: string;
  title: string;
  note: string;
  url?: string;
}

export function AssistantPanel({ webviewHandle }: Props) {
  const { t } = useI18n();
  const recorderPluginEnabled = useCapabilitySurface("browser.recorder");
  const { activeTab, state, setCopilotOpen, setCopilotWidth } =
    useBrowserStore();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pendingConfirmations, setPendingConfirmations] = useState<
    PendingConfirmation[]
  >([]);
  const [pendingSiteAccess, setPendingSiteAccess] =
    useState<PendingSiteAccess | null>(null);
  const [policyVersion, setPolicyVersion] = useState(0);
  const [recorderMode, setRecorderMode] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem("echo:browser-recorder-mode") === "1";
  });
  const [recorderProviderState, setRecorderProviderState] = useState<
    "idle" | "embedded" | "relay" | "agent-only"
  >("idle");
  useEffect(() => {
    if (!recorderPluginEnabled) setRecorderMode(false);
  }, [recorderPluginEnabled]);
  const [researchGoal, setResearchGoal] = useState("");
  const [researchLog, setResearchLog] = useState<ResearchLogEntry[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const raw = sessionStorage.getItem("echo:browser-research-log");
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      swallow(e);
      return [];
    }
  });
  const [briefCopied, setBriefCopied] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const researchPlatforms = useMemo<ResearchPlatform[]>(
    () => [
      {
        name: "Gemini",
        url: "https://gemini.google.com/app",
        hint: t.browser.assistant.researchPlatformHintGemini,
      },
      {
        name: "NotebookLM",
        url: "https://notebooklm.google.com/",
        hint: t.browser.assistant.researchPlatformHintNotebookLM,
      },
      {
        name: t.browser.assistant.researchPlatformNameDoubao,
        url: "https://www.doubao.com/chat/",
        hint: t.browser.assistant.researchPlatformHintDoubao,
      },
      {
        name: "Perplexity",
        url: "https://www.perplexity.ai/",
        hint: t.browser.assistant.researchPlatformHintPerplexity,
      },
    ],
    [t],
  );

  // Implementation note.
  // Implementation note.
  const activeAgentId = useActiveAgentId();
  const agentName = activeAgentId ?? "general";
  const { agents } = useAgents();
  const primaryAgents = useMemo(
    () => agents.filter((agent) => isPrimaryPersonaAgentId(agent.name)),
    [agents],
  );
  const activeAgent = useMemo(
    () => primaryAgents.find((a) => a.name === agentName) ?? null,
    [agentName, primaryAgents],
  );
  const activeOrigin = useMemo(
    () => browserHttpOrigin(activeTab?.url),
    [activeTab?.url],
  );
  const [activeSitePermission, setActiveSitePermission] = useState(() =>
    getBrowserAgentPermission(activeTab?.url),
  );

  useEffect(() => {
    setActiveSitePermission(getBrowserAgentPermission(activeTab?.url));
  }, [activeTab?.url, policyVersion]);

  useEffect(() => {
    const refresh = () => setPolicyVersion((value) => value + 1);
    window.addEventListener(BROWSER_AGENT_POLICY_EVENT, refresh);
    return () =>
      window.removeEventListener(BROWSER_AGENT_POLICY_EVENT, refresh);
  }, []);

  // Implementation note.
  // Implementation note.
  const threadId = useMemo(
    () => `assistant:${activeTab?.id ?? "none"}:${agentName}`,
    [activeTab?.id, agentName],
  );

  const [thread, sendMessage] = useThreadStream({
    threadId,
    context: { agent_name: agentName, mode: "chat" },
  });

  useEffect(() => {
    if (!recorderPluginEnabled) return;
    let cancelled = false;
    void getRecordingStatus(threadId)
      .then((status) => {
        if (!cancelled) setRecorderMode(status.recording);
      })
      .catch((error) => swallow(error, "browser-recorder-status"));
    return () => {
      cancelled = true;
    };
  }, [recorderPluginEnabled, threadId]);

  useEffect(() => {
    if (!recorderMode) {
      setRecorderProviderState("idle");
      return;
    }

    let disposed = false;
    let flushing = false;
    const queue: RecordingEvent[] = [];
    const seenRelayEvents = new Set<string>();
    const embeddedAvailable = Boolean(webviewHandle && window.echo);
    setRecorderProviderState(embeddedAvailable ? "embedded" : "agent-only");

    const flush = async () => {
      if (disposed || flushing || queue.length === 0) return;
      flushing = true;
      const batch = queue.splice(0, 100);
      try {
        await appendRecordingEvents(threadId, batch);
      } catch (error) {
        if (!disposed) {
          queue.unshift(...batch);
          if (queue.length > 300) queue.splice(0, queue.length - 300);
          swallow(error, "browser-recorder-events");
        }
      } finally {
        flushing = false;
        if (!disposed && queue.length > 0) window.setTimeout(flush, 500);
      }
    };
    const enqueue = (events: RecordingEvent[]) => {
      if (disposed || events.length === 0) return;
      queue.push(...events);
      if (queue.length > 300) queue.splice(0, queue.length - 300);
      void flush();
    };

    const drainWebview = async () => {
      if (!webviewHandle || !embeddedAvailable || disposed) return;
      const result = await webviewHandle.executeJS(
        browserRecorderDrainScript(),
      );
      const events = normalizeBrowserRecordingEvents(result);
      if (events.length > 0) enqueue(events);
    };
    void drainWebview();
    const drainTimer = window.setInterval(() => void drainWebview(), 650);

    const consumeRelay = (status: BrowserRelayStatus) => {
      if (disposed) return;
      if (status.connected) setRecorderProviderState("relay");
      else if (!embeddedAvailable) setRecorderProviderState("agent-only");
      const events: RecordingEvent[] = [];
      for (const activity of status.recent_human_activity ?? []) {
        const at = Number(activity.at || 0);
        const key = [
          at,
          activity.kind || "activity",
          activity.tabId ?? "",
          activity.url || "",
        ].join(":");
        if (!at || seenRelayEvents.has(key)) continue;
        seenRelayEvents.add(key);
        events.push({
          ts: new Date(at * 1000).toISOString(),
          source: "browser",
          kind: String(activity.kind || "activity"),
          app: "Chrome",
          window: String(activity.url || ""),
          target: activity.target,
          data: {
            ...(activity.data ?? {}),
            title: String(activity.title || ""),
            tab_id: activity.tabId,
          },
        });
      }
      enqueue(events);
    };
    const unsubscribeRelay = subscribeBrowserRelayStatus(consumeRelay);

    return () => {
      disposed = true;
      window.clearInterval(drainTimer);
      unsubscribeRelay();
    };
  }, [recorderMode, threadId, webviewHandle]);

  // Implementation note.
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [thread.messages.length, thread.isLoading]);

  // ── Agentic loop ──────────────────────────────────────
  // Implementation note.
  // Implementation note.
  // Implementation note.
  const [autoBrowse, setAutoBrowse] = useState(true);
  const toggleRecorderMode = useCallback(async () => {
    setErrorMsg(null);
    try {
      if (recorderMode) {
        await stopRecording({ thread_id: threadId, use_llm: true });
        setRecorderMode(false);
        return;
      }
      await startRecording({
        thread_id: threadId,
        name: researchGoal.trim() || t.browser.assistant.recorderTitle,
        description: "AI 浏览器中的真人示范与 Agent 操作轨迹。",
        provider: "hybrid",
      });
      setRecorderMode(true);
      setAutoBrowse(true);
    } catch (error) {
      setErrorMsg(error instanceof Error ? error.message : "REC 操作失败");
    }
  }, [recorderMode, researchGoal, t, threadId]);
  const lastProcessedAiIdRef = useRef<string | null>(null);
  const protocolInjectedRef = useRef<Set<string>>(new Set());
  const loopCountRef = useRef(0);
  // Implementation note.
  const stopRequestedRef = useRef(false);
  const activeTabIdRef = useRef<string | null>(activeTab?.id ?? null);
  // Implementation note.
  // Implementation note.
  const [agentLoopActive, setAgentLoopActive] = useState(false);
  const MAX_AGENT_LOOP = 8;

  useEffect(() => {
    activeTabIdRef.current = activeTab?.id ?? null;
  }, [activeTab?.id]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(
      "echo:browser-recorder-mode",
      recorderMode ? "1" : "0",
    );
  }, [recorderMode]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      sessionStorage.setItem(
        "echo:browser-research-log",
        JSON.stringify(researchLog.slice(0, 80)),
      );
    } catch (e) {
      swallow(e);
    }
  }, [researchLog]);

  useEffect(() => {
    if (recorderMode) {
      setAutoBrowse(true);
    }
  }, [recorderMode]);

  // Implementation note.
  useEffect(() => {
    lastProcessedAiIdRef.current = null;
    loopCountRef.current = 0;
    stopRequestedRef.current = false;
    setAgentLoopActive(false);
  }, [threadId]);

  // Implementation note.
  // Implementation note.
  // Implementation note.
  const stopAgentLoop = useCallback(() => {
    stopRequestedRef.current = true;
    setAgentLoopActive(false);
    webviewHandle?.setControlIndicator?.("paused", {
      reason: "operator_stop",
    });
    // Implementation note.
    void sendMessage(threadId, {
      text: t.browser.assistant.stopAgentMessage,
      files: [],
    });
  }, [sendMessage, threadId, t, webviewHandle]);

  const addPendingConfirmation = useCallback(
    (action: AgentAction, result: ActionResult) => {
      const detail =
        result.detail && typeof result.detail === "object"
          ? (result.detail as Record<string, unknown>)
          : undefined;
      setPendingConfirmations((prev) => [
        ...prev.filter(
          (item) => actionIdentity(item.action) !== actionIdentity(action),
        ),
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          action,
          error: result.error,
          detail,
          createdAt: Date.now(),
        },
      ]);
    },
    [],
  );

  const buildBrowserControl = useCallback(
    (loopTabId: string | null): BrowserControlOptions => ({
      sessionId: `browser-${loopTabId || "active"}`,
      ownerId: "browser-assistant",
      ownerLabel: "Browser Assistant",
      surface: window.echo?.isElectron
        ? "electron_webview"
        : "backend_preview",
      targetId: loopTabId,
      getStopped: () =>
        stopRequestedRef.current || activeTabIdRef.current !== loopTabId,
      setIndicator: (mode, detail) =>
        webviewHandle?.setControlIndicator?.(mode, detail),
    }),
    [webviewHandle],
  );

  // Implementation note.
  // Implementation note.
  useEffect(() => {
    if (thread.isLoading && autoBrowse) setAgentLoopActive(true);
  }, [thread.isLoading, autoBrowse]);

  // Implementation note.
  useEffect(() => {
    if (!autoBrowse) return;
    if (thread.isLoading) return; // Implementation note.
    if (busy) return; // Implementation note.
    // Implementation note.
    if (stopRequestedRef.current) {
      stopRequestedRef.current = false;
      loopCountRef.current = 0;
      setAgentLoopActive(false);
      return;
    }
    const last = thread.messages[thread.messages.length - 1];
    if (!last || !isAIMessage(last)) return;
    const aiId = last.id ?? "";
    if (!aiId || lastProcessedAiIdRef.current === aiId) return;

    const text =
      typeof last.content === "string"
        ? last.content
        : last.content
            .filter(
              (c): c is { type: "text"; text: string } => c.type === "text",
            )
            .map((c) => c.text)
            .join("");
    const actions = parseActions(text);
    if (actions.length === 0) {
      // Implementation note.
      lastProcessedAiIdRef.current = aiId;
      loopCountRef.current = 0;
      setAgentLoopActive(false);
      return;
    }
    if (!activeOrigin || activeSitePermission === "block") {
      lastProcessedAiIdRef.current = aiId;
      const origin = activeOrigin ?? activeTab?.url ?? "internal-page";
      recordBrowserAgentAudit({
        origin,
        action: "site-access",
        outcome: "blocked",
        detail: "Agent access is blocked for this site",
      });
      void sendMessage(threadId, {
        text: `[浏览器权限] 已阻止 Agent 操作 ${origin}。可在浏览器数据与隐私中修改站点权限。`,
        files: [],
      });
      setAgentLoopActive(false);
      return;
    }
    if (activeSitePermission === "ask") {
      setPendingSiteAccess({ origin: activeOrigin, aiMessageId: aiId });
      setAgentLoopActive(false);
      return;
    }
    lastProcessedAiIdRef.current = aiId;

    if (webviewHandle && !window.echo) {
      const loopTabId = activeTabIdRef.current;
      const control = buildBrowserControl(loopTabId);
      if (loopCountRef.current >= MAX_AGENT_LOOP) {
        void sendMessage(threadId, {
          text: t.browser.assistant.maxLoopReached(MAX_AGENT_LOOP),
          files: [],
        });
        loopCountRef.current = 0;
        return;
      }
      loopCountRef.current += 1;

      void (async () => {
        setBusy(true);
        setErrorMsg(null);
        try {
          const results: ActionResult[] = [];
          for (const action of actions) {
            if (control.getStopped?.()) {
              results.push({
                action,
                ok: false,
                error: "browser control interrupted: operator_stop",
              });
              break;
            }
            const preflight = confirmationPreflight(action, t);
            if (preflight) {
              results.push(preflight);
              addPendingConfirmation(action, preflight);
              stopRequestedRef.current = true;
              setAgentLoopActive(false);
              break;
            }
            const r = await withActionTimeout(
              runBrowserHandleActionWithControl(webviewHandle, action, {
                control,
              }),
              action.type,
            );
            results.push(r);
            recordBrowserAgentAudit({
              origin: activeOrigin,
              action: actionIdentity(action),
              outcome: r.ok ? "allowed" : "failed",
              detail: r.error,
            });
            if (needsUserConfirmation(r)) {
              addPendingConfirmation(action, r);
              stopRequestedRef.current = true;
              setAgentLoopActive(false);
              break;
            }
            if (action.type === "wait" || action.type === "navigate") {
              await new Promise((res) => setTimeout(res, 300));
            }
          }
          const pageInfo = await withActionTimeout(
            webviewHandle.extractText(),
            "extractText",
          ).catch(() => null);
          const pageInfoLite = pageInfo
            ? {
                url: pageInfo.url,
                title: pageInfo.title,
                text: pageInfo.text,
                pageAgent: pageInfo.pageAgent,
              }
            : undefined;
          const summary = formatResults(results, pageInfoLite);
          void sendMessage(threadId, { text: summary, files: [] });
        } catch (err) {
          swallow(err);
          setErrorMsg(err instanceof Error ? err.message : String(err));
        } finally {
          setBusy(false);
        }
      })();
      return;
    }

    const api = window.echo;
    const wcId = webviewHandle?.getWebContentsId();
    if (!api || wcId == null) {
      void sendMessage(threadId, {
        text: t.browser.assistant.webviewNotReadyError,
        files: [],
      });
      return;
    }

    if (loopCountRef.current >= MAX_AGENT_LOOP) {
      void sendMessage(threadId, {
        text: t.browser.assistant.maxLoopReached(MAX_AGENT_LOOP),
        files: [],
      });
      loopCountRef.current = 0;
      return;
    }
    loopCountRef.current += 1;
    const loopTabId = activeTabIdRef.current;
    const control = buildBrowserControl(loopTabId);

    void (async () => {
      setBusy(true);
      setErrorMsg(null);
      try {
        const results: ActionResult[] = [];
        for (const action of actions) {
          if (control.getStopped?.()) {
            results.push({
              action,
              ok: false,
              error: "browser control interrupted: operator_stop",
            });
            break;
          }
          const preflight = confirmationPreflight(action, t);
          if (preflight) {
            results.push(preflight);
            addPendingConfirmation(action, preflight);
            stopRequestedRef.current = true;
            setAgentLoopActive(false);
            break;
          }
          const r = await withActionTimeout(
            runBrowserActionWithControl(
              action,
              () =>
                runActionWithRetry(api, wcId, action, {
                  navigate: (url: string) => {
                    webviewHandle?.loadURL(url);
                  },
                }),
              control,
            ),
            action.type,
          );
          results.push(r);
          recordBrowserAgentAudit({
            origin: activeOrigin,
            action: actionIdentity(action),
            outcome: r.ok ? "allowed" : "failed",
            detail: r.error,
          });
          // Implementation note.
          if (action.type === "wait" || action.type === "navigate") {
            await new Promise((res) => setTimeout(res, 300));
          }
        }
        // Implementation note.
        const pageInfo = await withActionTimeout(
          api.browser.extractText(wcId),
          "extractText",
        ).catch(() => null);
        const pageInfoLite = pageInfo
          ? {
              url: pageInfo.url,
              title: pageInfo.title,
              text: pageInfo.text,
            }
          : undefined;
        const summary = formatResults(results, pageInfoLite);
        // Implementation note.
        void sendMessage(threadId, { text: summary, files: [] });
      } catch (err) {
        swallow(err);
        setErrorMsg(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    })();
  }, [
    autoBrowse,
    busy,
    sendMessage,
    t,
    thread.isLoading,
    thread.messages,
    threadId,
    webviewHandle,
    addPendingConfirmation,
    buildBrowserControl,
    activeOrigin,
    activeSitePermission,
    activeTab?.url,
  ]);

  const resolveSiteAccess = useCallback(
    (permission: "allow" | "block") => {
      if (!pendingSiteAccess) return;
      setBrowserAgentPermission(pendingSiteAccess.origin, permission);
      recordBrowserAgentAudit({
        origin: pendingSiteAccess.origin,
        action: "site-access",
        outcome: permission === "allow" ? "confirmed" : "blocked",
      });
      if (permission === "block") {
        lastProcessedAiIdRef.current = pendingSiteAccess.aiMessageId;
        stopRequestedRef.current = true;
      }
      setPendingSiteAccess(null);
      setPolicyVersion((value) => value + 1);
    },
    [pendingSiteAccess],
  );

  const confirmPendingAction = useCallback(
    async (pending: PendingConfirmation) => {
      if (!webviewHandle) return;
      setBusy(true);
      setErrorMsg(null);
      const loopTabId = activeTabIdRef.current;
      try {
        const result = await withActionTimeout(
          runBrowserHandleActionWithControl(webviewHandle, pending.action, {
            confirmDangerous: true,
            control: buildBrowserControl(loopTabId),
          }),
          pending.action.type,
        );
        recordBrowserAgentAudit({
          origin: activeOrigin ?? activeTab?.url ?? "internal-page",
          action: actionIdentity(pending.action),
          outcome: result.ok ? "confirmed" : "failed",
          detail: result.error,
        });
        setPendingConfirmations((prev) =>
          prev.filter((item) => item.id !== pending.id),
        );
        const pageInfo = await withActionTimeout(
          webviewHandle.extractText(),
          "extractText",
        ).catch(() => null);
        const pageInfoLite = pageInfo
          ? {
              url: pageInfo.url,
              title: pageInfo.title,
              text: pageInfo.text,
              pageAgent: pageInfo.pageAgent,
            }
          : undefined;
        const summary = `${t.browser.assistant.confirmedRiskyOperation}\n${formatResults([result], pageInfoLite)}`;
        void sendMessage(threadId, { text: summary, files: [] });
      } catch (err) {
        swallow(err);
        setErrorMsg(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [
      sendMessage,
      threadId,
      webviewHandle,
      t,
      buildBrowserControl,
      activeOrigin,
      activeTab?.url,
    ],
  );

  const dismissPendingAction = useCallback((id: string) => {
    setPendingConfirmations((prev) => prev.filter((item) => item.id !== id));
  }, []);

  // Implementation note.
  // Implementation note.
  const buildOutgoingText = useCallback(
    (raw: string): string => {
      const recorderHeader = recorderMode
        ? `${t.browser.assistant.recorderProtocol}\n\n---\n\n`
        : "";
      if (!autoBrowse) return `${recorderHeader}${raw}`;
      if (protocolInjectedRef.current.has(threadId)) return raw;
      protocolInjectedRef.current.add(threadId);
      return `${recorderHeader}${BROWSER_ACTION_PROTOCOL}\n\n---\n\n${raw}`;
    },
    [autoBrowse, recorderMode, threadId, t],
  );

  const send = useCallback(
    (text: string) => {
      const t = text.trim();
      if (!t) return;
      void sendMessage(threadId, { text: buildOutgoingText(t), files: [] });
      setInput("");
    },
    [buildOutgoingText, sendMessage, threadId],
  );

  const onKey = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send(input);
      }
    },
    [input, send],
  );

  const researchBrief = useMemo(
    () => buildResearchBrief(researchLog, t),
    [researchLog, t],
  );

  const buildRecorderTask = useCallback(
    (goal: string) => {
      const trimmed = goal.trim();
      const c = t.browser.assistant;
      return [
        c.recorderProtocol,
        "",
        c.researchMissionLabel,
        trimmed,
        "",
        c.researchPlatformDivisionLabel,
        ...researchPlatforms.map(
          (platform, index) =>
            `${index + 1}. ${platform.name}: ${platform.hint} (${platform.url})`,
        ),
        "",
        c.researchExecutionRequirementsLabel,
        c.researchRequirementOpenFirstPlatform,
        c.researchRequirementExtractHighDensity,
        c.researchRequirementDoNotFeedBack,
        c.researchRequirementLogPerPlatform,
        c.researchRequirementPauseForSensitive,
      ].join("\n");
    },
    [t, researchPlatforms],
  );

  const startRecorderResearch = useCallback(() => {
    const goal = researchGoal.trim() || input.trim();
    if (!goal) return;
    setRecorderMode(true);
    setAutoBrowse(true);
    const c = t.browser.assistant;
    setResearchLog((prev) =>
      [
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          createdAt: Date.now(),
          platform: c.researchLogDispatchLabel,
          title: c.researchStartTitle,
          note: `${goal}\n${c.researchPlatformsPrefix} ${researchPlatforms.map((p) => p.name).join(", ")}`,
          url: activeTab?.url,
        },
        ...prev,
      ].slice(0, 80),
    );
    send(buildRecorderTask(goal));
    setResearchGoal("");
    setInput("");
  }, [
    activeTab?.url,
    buildRecorderTask,
    input,
    researchGoal,
    send,
    t,
    researchPlatforms,
  ]);

  const addPageToResearchLog = useCallback(async () => {
    setBusy(true);
    setErrorMsg(null);
    const c = t.browser.assistant;
    try {
      const page = webviewHandle ? await webviewHandle.extractText() : null;
      const title = page?.title || activeTab?.title || c.currentPageFallback;
      const url = page?.url || activeTab?.url;
      const text = page?.text ?? "";
      setResearchLog((prev) =>
        [
          {
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            createdAt: Date.now(),
            platform: guessPlatformName(url, t),
            title,
            note: text ? text.slice(0, 600) : c.recordedPageNote,
            url,
          },
          ...prev,
        ].slice(0, 80),
      );
    } catch (err) {
      swallow(err);
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [activeTab?.title, activeTab?.url, webviewHandle, t]);

  const copyResearchBrief = useCallback(async () => {
    if (!researchBrief) return;
    try {
      await copyTextToClipboard(researchBrief);
      setBriefCopied(true);
      window.setTimeout(() => setBriefCopied(false), 1200);
    } catch (err) {
      swallow(err);
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  }, [researchBrief]);

  const downloadResearchBrief = useCallback(() => {
    if (!researchBrief) return;
    const blob = new Blob([researchBrief], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.href = url;
    a.download = `ai-research-brief-${stamp}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [researchBrief]);

  // Implementation note.
  const askWithPage = useCallback(
    async (instruction: string) => {
      const c = t.browser.assistant;
      if (webviewHandle && !window.echo) {
        setBusy(true);
        setErrorMsg(null);
        try {
          const page = await webviewHandle.extractText();
          const pageAgent = page.pageAgent
            ? `\n\n${c.pageAgentCapabilityLabel}\n${JSON.stringify(page.pageAgent).slice(0, 12000)}`
            : "";
          const prefix = `${c.currentPageLabel}\n${c.urlLabel} ${page.url}\n${c.titleLabel} ${page.title}\n\n${page.text}${pageAgent}\n${page.truncated ? `\n${c.truncatedSuffix(page.textLength ?? 0)}` : ""}`;
          send(`${prefix}\n\n${instruction}`);
        } catch (err) {
          swallow(err);
          setErrorMsg(err instanceof Error ? err.message : String(err));
        } finally {
          setBusy(false);
        }
        return;
      }
      if (!webviewHandle || !window.echo) {
        setErrorMsg(c.needElectronError);
        return;
      }
      const wcId = webviewHandle.getWebContentsId();
      if (wcId == null) {
        setErrorMsg(c.tabNotReadyError);
        return;
      }
      setBusy(true);
      setErrorMsg(null);
      try {
        const page = await window.echo.browser.extractText(wcId);
        const prefix = `${c.currentPageLabel}\n${c.urlLabel} ${page.url}\n${c.titleLabel} ${page.title}\n\n${page.text}\n${page.truncated ? `\n${c.truncatedSuffix(page.textLength ?? 0)}` : ""}`;
        send(`${prefix}\n\n${instruction}`);
      } catch (err) {
        swallow(err);
        setErrorMsg(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [send, webviewHandle, t],
  );

  // Implementation note.
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const onResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, startW: state.copilotWidth };
      const onMove = (ev: MouseEvent) => {
        if (!dragRef.current) return;
        const delta = dragRef.current.startX - ev.clientX;
        setCopilotWidth(dragRef.current.startW + delta);
      };
      const onUp = () => {
        dragRef.current = null;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [setCopilotWidth, state.copilotWidth],
  );

  return (
    <div
      // Implementation note.
      // Implementation note.
      // Implementation note.
      // Implementation note.
      // Implementation note.
      className={cn(
        "relative flex h-full w-full min-w-[280px] flex-1 flex-col bg-transparent",
      )}
    >
      {/* Implementation note. */}
      <div
        onMouseDown={onResizeStart}
        className="absolute left-0 top-0 z-10 h-full w-1 cursor-col-resize hover:bg-primary/30"
      />

      {/* Implementation note. */}
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-white/24 bg-white/[0.06] px-3">
        <AgentPicker
          activeAgent={activeAgent}
          agents={primaryAgents}
          activeAgentId={agentName}
        />
        {activeTab?.title && (
          <span
            className="min-w-0 flex-1 truncate text-mini text-muted-foreground"
            title={activeTab.title}
          >
            · {activeTab.title}
          </span>
        )}
        {/* Implementation note. */}
        {agentLoopActive && (
          <button
            onClick={stopAgentLoop}
            className="flex shrink-0 items-center gap-1 rounded bg-destructive/10 px-1.5 py-0.5 text-micro font-medium text-destructive transition-colors hover:bg-destructive/20 dark:text-destructive"
            title={t.browser.assistant.stopAgentTooltip}
          >
            <StopCircleIcon className="size-3" />
            {t.browser.assistant.stopAgent}
          </button>
        )}
        {/* Implementation note. */}
        <button
          onClick={() => setAutoBrowse((v) => !v)}
          className={cn(
            "shrink-0 rounded px-1.5 py-0.5 text-micro font-medium transition-colors",
            autoBrowse
              ? "bg-primary/10 text-primary"
              : "border border-white/28 text-muted-foreground hover:bg-white/18",
          )}
          title={
            autoBrowse
              ? t.browser.assistant.autoBrowseOnTooltip
              : t.browser.assistant.autoBrowseOffTooltip
          }
        >
          {autoBrowse ? "AUTO" : "READ"}
        </button>
        {recorderPluginEnabled ? (
          <button
            onClick={() => void toggleRecorderMode()}
            className={cn(
              "shrink-0 rounded px-1.5 py-0.5 text-micro font-medium transition-colors",
              recorderMode
                ? "bg-success/10 text-success"
                : "border border-white/28 text-muted-foreground hover:bg-white/18",
            )}
            title={t.browser.assistant.recorderTitle}
          >
            REC
          </button>
        ) : null}
        <button
          onClick={() => setCopilotOpen(false)}
          className="grid size-7 shrink-0 place-items-center rounded text-muted-foreground hover:bg-white/18 hover:text-foreground"
          title={t.common.close}
        >
          <XIcon className="size-4" />
        </button>
      </div>

      {/* quick actions */}
      <div className="flex shrink-0 flex-wrap gap-1.5 border-b border-white/20 bg-white/[0.05] px-3 py-2">
        <QuickAction
          icon={FileTextIcon}
          label={t.browser.assistant.summarizePage}
          onClick={() => askWithPage(t.browser.assistant.summarizePagePrompt)}
          disabled={busy}
        />
        <QuickAction
          icon={ListIcon}
          label={t.browser.assistant.extractKeyPoints}
          onClick={() =>
            askWithPage(t.browser.assistant.extractKeyPointsPrompt)
          }
          disabled={busy}
        />
        <QuickAction
          icon={LanguagesIcon}
          label={t.browser.assistant.translateToChinese}
          onClick={() =>
            askWithPage(t.browser.assistant.translateToChinesePrompt)
          }
          disabled={busy}
        />
      </div>

      {recorderMode && (
        <div className="shrink-0 border-b border-white/20 bg-success/50/[0.04] px-3 py-2">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-mini font-medium text-success">
                {t.browser.assistant.recorderTitle}
              </div>
              <div className="truncate text-micro text-muted-foreground">
                {t.browser.assistant.recorderDesc}
              </div>
              <div className="mt-0.5 text-micro text-muted-foreground">
                {recorderProviderState === "embedded"
                  ? "内置页面已接入"
                  : recorderProviderState === "relay"
                    ? "Chrome Relay 已接入"
                    : "页面采集离线，仅记录 Agent 轨迹"}
              </div>
            </div>
          </div>
          <div className="mt-2 flex gap-1.5">
            <input
              value={researchGoal}
              onChange={(e) => setResearchGoal(e.target.value)}
              placeholder={t.browser.assistant.researchGoalPlaceholder}
              aria-label={t.browser.assistant.researchGoalPlaceholder}
              className={cn(
                "min-w-0 flex-1 rounded px-2 py-1 text-mini outline-none focus:ring-1 focus:ring-success/40",
                "bg-white/10",
              )}
            />
            <button
              type="button"
              onClick={startRecorderResearch}
              disabled={
                busy ||
                thread.isLoading ||
                (!researchGoal.trim() && !input.trim())
              }
              className="rounded bg-success px-2 py-1 text-mini font-medium text-white hover:bg-success disabled:opacity-40"
            >
              {t.browser.assistant.start}
            </button>
          </div>
          <div className="mt-2 flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={() => void addPageToResearchLog()}
              disabled={busy}
              className={cn(
                "rounded px-2 py-1 text-micro text-muted-foreground disabled:opacity-40",
                "bg-white/10",
              )}
            >
              {t.browser.assistant.recordCurrentPage}
            </button>
            {researchLog.length > 0 && (
              <button
                type="button"
                onClick={() => setResearchLog([])}
                className="text-micro text-muted-foreground hover:text-foreground"
              >
                {t.browser.assistant.clearLog}
              </button>
            )}
          </div>
          {researchLog.length > 0 && (
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              <button
                type="button"
                onClick={() => void copyResearchBrief()}
                className={cn(
                  "inline-flex items-center justify-center gap-1 rounded px-2 py-1 text-micro font-medium text-muted-foreground",
                  "bg-white/10",
                )}
              >
                <ClipboardCheckIcon className="size-3" />
                {briefCopied
                  ? t.browser.assistant.copied
                  : t.browser.assistant.copyBrief}
              </button>
              <button
                type="button"
                onClick={downloadResearchBrief}
                className={cn(
                  "inline-flex items-center justify-center gap-1 rounded px-2 py-1 text-micro font-medium text-muted-foreground",
                  "bg-white/10",
                )}
              >
                <DownloadIcon className="size-3" />
                {t.browser.assistant.exportMd}
              </button>
            </div>
          )}
          {researchLog.length > 0 && (
            <div
              className={cn(
                "mt-2 max-h-28 space-y-1 overflow-y-auto rounded p-1.5",
                "bg-white/10",
              )}
            >
              {researchLog.slice(0, 5).map((entry) => (
                <div key={entry.id} className="rounded bg-white/18 px-2 py-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-micro font-medium">
                      {entry.platform} · {entry.title}
                    </span>
                    <span className="shrink-0 text-[9px] text-muted-foreground">
                      {new Date(entry.createdAt).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-micro text-muted-foreground">
                    {entry.note}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {errorMsg && (
        <div className="shrink-0 border-b border-white/20 bg-destructive/10 px-3 py-1.5 text-mini text-destructive">
          {errorMsg}
        </div>
      )}

      {pendingSiteAccess && (
        <div className="shrink-0 border-b border-white/20 bg-primary/8 px-3 py-2">
          <div className="rounded-md border border-primary/25 bg-white/10 p-2 text-mini">
            <div className="font-medium text-foreground">
              允许 Agent 操作此网站？
            </div>
            <div className="mt-1 break-all text-muted-foreground">
              {pendingSiteAccess.origin}
            </div>
            <div className="mt-1 text-muted-foreground">
              允许后，Agent
              可以读取页面并点击、输入和滚动；提交、支付、删除等敏感操作仍需单独确认。
            </div>
            <div className="mt-2 flex gap-2">
              <button
                onClick={() => resolveSiteAccess("allow")}
                className="rounded bg-primary px-2 py-1 font-medium text-primary-foreground hover:bg-primary/90"
              >
                允许此网站
              </button>
              <button
                onClick={() => resolveSiteAccess("block")}
                className="rounded border border-white/28 px-2 py-1 text-muted-foreground hover:bg-white/18"
              >
                阻止
              </button>
            </div>
          </div>
        </div>
      )}

      {pendingConfirmations.length > 0 && (
        <div className="shrink-0 space-y-2 border-b border-white/20 bg-warning/10 px-3 py-2">
          {pendingConfirmations.map((pending) => (
            <div
              key={pending.id}
              className={cn(
                "rounded-md border-warning/30 p-2 text-mini",
                "bg-white/10",
              )}
            >
              <div className="font-medium text-warning">
                {t.browser.assistant.needsUserConfirmationTitle}
              </div>
              <div className="mt-1 text-muted-foreground">
                {describePendingAction(pending)}
              </div>
              {pending.error && (
                <div className="mt-1 break-words text-warning">
                  {pending.error}
                </div>
              )}
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => void confirmPendingAction(pending)}
                  disabled={busy}
                  className="rounded bg-warning px-2 py-1 font-medium text-white hover:bg-warning disabled:opacity-50"
                >
                  {t.browser.assistant.confirmExecute}
                </button>
                <button
                  onClick={() => dismissPendingAction(pending.id)}
                  disabled={busy}
                  className="rounded border border-white/28 px-2 py-1 text-muted-foreground hover:bg-white/18 disabled:opacity-50"
                >
                  {t.common.cancel}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Implementation note. */}
      <div
        ref={listRef}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3"
      >
        {thread.messages.length === 0 && !thread.isLoading && (
          <div className="flex h-full flex-col items-center justify-center text-center text-xs text-muted-foreground">
            <SparklesIcon className="mb-2 size-6 opacity-50" />
            <div>{t.browser.assistant.emptyHint}</div>
          </div>
        )}
        {thread.messages.map((m) => {
          const isUser = isHumanMessage(m);
          const isAi = isAIMessage(m);
          if (!isUser && !isAi) return null;
          const text =
            typeof m.content === "string"
              ? m.content
              : m.content
                  .filter(
                    (c): c is { type: "text"; text: string } =>
                      c.type === "text",
                  )
                  .map((c) => c.text)
                  .join("");
          return (
            <div
              key={m.id}
              className={cn(
                "rounded-lg px-3 py-2 text-[13px] leading-relaxed",
                isUser
                  ? cn("ml-6 text-foreground", "bg-white/10")
                  : cn("mr-6 text-foreground", "bg-white/10"),
              )}
            >
              {/* Implementation note. */}
              <div className="whitespace-pre-wrap break-words">
                {text.length > 1500 ? `${text.slice(0, 1500)}…` : text}
              </div>
            </div>
          );
        })}
        {thread.isLoading && (
          <div
            className={cn(
              "mr-6 flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] text-muted-foreground",
              "bg-white/10",
            )}
          >
            <Loader2Icon className="size-3.5 animate-spin" />
            {t.browser.assistant.thinking}
          </div>
        )}
      </div>

      {/* input */}
      <div className="shrink-0 border-t border-white/20 p-2">
        <div
          className={cn(
            "flex items-end gap-2 p-2 focus-within:ring-2 focus-within:ring-primary/30",
            "bg-white/10",
          )}
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            placeholder={t.browser.assistant.inputPlaceholder}
            aria-label={t.browser.assistant.inputPlaceholder}
            rows={1}
            className="max-h-32 min-h-[24px] flex-1 resize-none bg-transparent text-sm outline-none"
          />
          <button
            type="button"
            onClick={() => send(input)}
            disabled={!input.trim() || thread.isLoading}
            className="grid size-7 place-items-center rounded bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
            title={t.codeMode.send}
            aria-label={t.codeMode.send}
          >
            <SendIcon className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

function needsUserConfirmation(result: ActionResult): boolean {
  if (result.ok) return false;
  if (!result.detail || typeof result.detail !== "object") return false;
  return (
    (result.detail as Record<string, unknown>).requiresConfirmation === true
  );
}

const SENSITIVE_ACTION_RE =
  /(submit|confirm|delete|remove|pay|payment|checkout|order|purchase|buy|login|signin|sign-in|logout|post|publish|send|save|upload|授权|确认|提交|删除|移除|支付|付款|下单|购买|登录|登出|发布|发送|保存|上传)/i;

function confirmationPreflight(
  action: AgentAction,
  t: {
    browser: {
      assistant: {
        confirmInputContent: string;
        confirmSubmitForm: string;
        confirmSensitiveClick: string;
        confirmSensitiveAction: string;
      };
    };
  },
): ActionResult | null {
  const reasons: string[] = [];
  if (action.type === "type" || action.type === "pageInput") {
    reasons.push(t.browser.assistant.confirmInputContent);
  }
  if (action.type === "press" && /^(enter|return)$/i.test(action.key)) {
    reasons.push(t.browser.assistant.confirmSubmitForm);
  }
  if (action.type === "click" && SENSITIVE_ACTION_RE.test(action.selector)) {
    reasons.push(t.browser.assistant.confirmSensitiveClick);
  }
  if (
    (action.type === "pageAction" || action.type === "pageCapability") &&
    SENSITIVE_ACTION_RE.test(action.id)
  ) {
    reasons.push(t.browser.assistant.confirmSensitiveAction);
  }
  if (reasons.length === 0) return null;
  return {
    action,
    ok: false,
    error: "requires user confirmation",
    detail: {
      requiresConfirmation: true,
      riskReasons: reasons,
      source: "echo-preflight",
    },
  };
}

function actionIdentity(action: AgentAction): string {
  if ("id" in action && typeof action.id === "string") {
    return `${action.type}:${action.id}`;
  }
  if ("selector" in action && typeof action.selector === "string") {
    return `${action.type}:${action.selector}`;
  }
  if (action.type === "press") return `${action.type}:${action.key}`;
  if (action.type === "navigate") return `${action.type}:${action.url}`;
  return action.type;
}

function describePendingAction(pending: PendingConfirmation): string {
  const action = pending.action;
  const reasons = Array.isArray(pending.detail?.riskReasons)
    ? pending.detail.riskReasons.filter(
        (item): item is string => typeof item === "string",
      )
    : [];
  const target =
    action.type === "pageCapability"
      ? `capability: ${action.id}`
      : action.type === "pageAction"
        ? `page action: ${action.id}`
        : action.type === "pageInput"
          ? `page input: ${action.id}`
          : action.type === "click"
            ? `click: ${action.selector}`
            : action.type === "type"
              ? `type: ${action.selector}`
              : action.type === "press"
                ? `press: ${action.key}`
                : action.type;
  const input =
    action.type === "pageCapability" && action.input
      ? ` · input=${JSON.stringify(action.input).slice(0, 180)}`
      : action.type === "pageInput"
        ? ` · text=${action.text.slice(0, 80)}`
        : action.type === "type"
          ? ` · text=${action.text.slice(0, 80)}`
          : "";
  const reasonText = reasons.length ? ` · risk=${reasons.join(", ")}` : "";
  return `${target}${input}${reasonText}`;
}

function QuickAction({
  icon: Icon,
  label,
  onClick,
  disabled,
}: {
  icon: typeof FileTextIcon;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1 rounded-full px-2.5 py-1 text-mini text-foreground disabled:opacity-50",
        "bg-white/10",
      )}
    >
      <Icon className="size-3" />
      {label}
    </button>
  );
}

function buildResearchBrief(
  entries: ResearchLogEntry[],
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (entries.length === 0) return "";
  const c = t.browser.assistant;
  const ordered = [...entries].reverse();
  const lines = [
    c.researchBriefTitle,
    "",
    c.researchBriefGeneratedAt(new Date().toLocaleString()),
    c.researchBriefRecordCount(entries.length),
    "",
    c.researchBriefAbstractRecords,
    "",
  ];

  for (const entry of ordered) {
    lines.push(`### ${entry.platform} · ${entry.title}`);
    lines.push(
      c.researchBriefEntryTime(new Date(entry.createdAt).toLocaleString()),
    );
    if (entry.url) lines.push(`- URL: ${entry.url}`);
    lines.push(c.researchBriefEntryRecordLabel);
    for (const line of entry.note
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)) {
      lines.push(`  - ${line}`);
    }
    lines.push("");
  }

  lines.push(c.researchBriefPendingVerification);
  lines.push(c.researchBriefVerifyCrossPlatform);
  lines.push(c.researchBriefKeepEvidence);
  lines.push(c.researchBriefConfirmSensitive);
  return lines.join("\n");
}

function guessPlatformName(
  url: string | null | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const c = t.browser.assistant;
  if (!url) return c.unknownPlatform;
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host.includes("gemini.google")) return "Gemini";
    if (host.includes("notebooklm.google")) return "NotebookLM";
    if (host.includes("doubao")) return c.researchPlatformNameDoubao;
    if (host.includes("perplexity")) return "Perplexity";
    if (host.includes("chatgpt") || host.includes("openai")) return "ChatGPT";
    return host.replace(/^www\./, "");
  } catch (e) {
    swallow(e);
    return c.unknownPlatform;
  }
}

interface AgentLite {
  name: string;
  display_name?: string | null;
  icon?: string | null;
}

/* Implementation note. */
function AgentPicker({
  activeAgent,
  agents,
  activeAgentId,
}: {
  activeAgent: AgentLite | null;
  agents: AgentLite[];
  activeAgentId: string;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Implementation note.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  const select = (name: string) => {
    if (!isPrimaryPersonaAgentId(name)) return;
    emitAgentChanged(name);
    setOpen(false);
  };

  const display =
    activeAgent?.display_name || activeAgent?.name || activeAgentId;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-sm font-semibold transition-colors hover:bg-white/18"
      >
        {activeAgent?.icon ? (
          <span className="text-base leading-none">{activeAgent.icon}</span>
        ) : (
          <SparklesIcon className="size-4 text-primary" />
        )}
        <span className="max-w-[140px] truncate">{display}</span>
        <ChevronDownIcon
          className={cn(
            "size-3 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div
          className={cn(
            "absolute left-0 top-full z-50 mt-1 max-h-72 w-56 overflow-y-auto rounded-md bg-popover p-1 text-popover-foreground shadow-lg",
          )}
        >
          {agents.length === 0 ? (
            <div className="px-2 py-1.5 text-xs text-muted-foreground">
              {t.browser.assistant.noAgents}
            </div>
          ) : (
            agents.map((a) => {
              const active = a.name === activeAgentId;
              return (
                <button
                  key={a.name}
                  onClick={() => select(a.name)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors hover:bg-white/18",
                    active && "bg-white/24 font-semibold",
                  )}
                >
                  {a.icon ? (
                    <span className="text-base leading-none">{a.icon}</span>
                  ) : (
                    <span className="grid size-4 place-items-center text-muted-foreground">
                      <SparklesIcon className="size-3" />
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate">
                    {a.display_name || a.name}
                  </span>
                  {active && <span className="text-micro text-primary">●</span>}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
