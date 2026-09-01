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
import {
  ACTIVE_AGENT_EVENT,
  ACTIVE_AGENT_KEY,
  useActiveAgentId,
} from "@/core/agents/active";
import { useAgents } from "@/core/agents/hooks";
import { copyTextToClipboard } from "@/core/clipboard";
import { cn } from "@/lib/utils";

import {
  BROWSER_ACTION_PROTOCOL,
  formatResults,
  parseActions,
  runActionWithRetry,
  withActionTimeout,
  type AgentAction,
  type ActionResult,
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

const RESEARCH_PLATFORMS: ResearchPlatform[] = [
  {
    name: "Gemini",
    url: "https://gemini.google.com/app",
    hint: "综合搜索、长问题、多轮分析",
  },
  {
    name: "NotebookLM",
    url: "https://notebooklm.google.com/",
    hint: "资料库、引用、文档内研究",
  },
  {
    name: "豆包",
    url: "https://www.doubao.com/chat/",
    hint: "中文调研、中文改写、国内语境",
  },
  {
    name: "Perplexity",
    url: "https://www.perplexity.ai/",
    hint: "网页检索、来源线索、事实核查",
  },
];

const RECORDER_PROTOCOL = `\
[外部 AI 调研 · 记录员模式]
目标: 尽量少消耗本地模型 token。你是浏览器调度员和记录员,不是主研究模型。
策略:
1. 优先打开/操控外部 AI 平台完成重推理,例如 Gemini、NotebookLM、豆包、Perplexity。
2. 本地只做短步骤规划、页面操作、等待结果、摘录关键结论、保存证据日志。
3. 不要把整页长文本回灌给本地模型;只抽取标题、URL、3-8 条关键结论、明显引用和待核查点。
4. 每个平台输出后,记录: 平台、使用的 prompt、结果摘要、URL、时间、证据/截图线索。
5. 遇到登录、上传文件、发送敏感数据、发帖/提交表单/付费操作,必须停下请用户确认。
6. 最终报告只基于各平台结果做合并、去重、冲突标注和下一步建议。\
`;

export function CopilotPanel({ webviewHandle }: Props) {
  const { t } = useI18n();
  const { activeTab, state, setCopilotOpen, setCopilotWidth } =
    useBrowserStore();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pendingConfirmations, setPendingConfirmations] = useState<
    PendingConfirmation[]
  >([]);
  const [recorderMode, setRecorderMode] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem("echo:browser-recorder-mode") === "1";
  });
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

  // Implementation note.
  // Implementation note.
  const activeAgentId = useActiveAgentId();
  const agentName = activeAgentId ?? "general";
  const { agents } = useAgents();
  const activeAgent = useMemo(
    () => agents.find((a) => a.name === agentName) ?? null,
    [agents, agentName],
  );

  // Implementation note.
  // Implementation note.
  const threadId = useMemo(
    () => `copilot:${activeTab?.id ?? "none"}:${agentName}`,
    [activeTab?.id, agentName],
  );

  const [thread, sendMessage] = useThreadStream({
    threadId,
    context: { agent_name: agentName, mode: "chat" },
  });

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
  const lastProcessedAiIdRef = useRef<string | null>(null);
  const protocolInjectedRef = useRef<Set<string>>(new Set());
  const loopCountRef = useRef(0);
  // Implementation note.
  const stopRequestedRef = useRef(false);
  // Implementation note.
  // Implementation note.
  const [agentLoopActive, setAgentLoopActive] = useState(false);
  const MAX_AGENT_LOOP = 8;

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
    // Implementation note.
    void sendMessage(threadId, {
      text: "[用户已手动停止 agent · 不再继续自动操作]",
      files: [],
    });
  }, [sendMessage, threadId]);

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
    lastProcessedAiIdRef.current = aiId;

    if (webviewHandle && !window.echo) {
      if (loopCountRef.current >= MAX_AGENT_LOOP) {
        void sendMessage(threadId, {
          text: `[已达到最大 agent 循环 ${MAX_AGENT_LOOP} 次,自动停止防止失控]`,
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
            const preflight = confirmationPreflight(action, t);
            if (preflight) {
              results.push(preflight);
              addPendingConfirmation(action, preflight);
              stopRequestedRef.current = true;
              setAgentLoopActive(false);
              break;
            }
            const r = await withActionTimeout(
              runBrowserHandleAction(webviewHandle, action),
              action.type,
            );
            results.push(r);
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
        text: "[执行失败:webview 没准备好(可能在浏览器里跑而不是 Electron)]",
        files: [],
      });
      return;
    }

    if (loopCountRef.current >= MAX_AGENT_LOOP) {
      void sendMessage(threadId, {
        text: `[已达到最大 agent 循环 ${MAX_AGENT_LOOP} 次,自动停止防止失控]`,
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
          const preflight = confirmationPreflight(action, t);
          if (preflight) {
            results.push(preflight);
            addPendingConfirmation(action, preflight);
            stopRequestedRef.current = true;
            setAgentLoopActive(false);
            break;
          }
          const r = await withActionTimeout(
            runActionWithRetry(api, wcId, action, {
              navigate: (url: string) => {
                webviewHandle?.loadURL(url);
              },
            }),
            action.type,
          );
          results.push(r);
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
    thread.isLoading,
    thread.messages,
    threadId,
    webviewHandle,
    addPendingConfirmation,
    t,
  ]);

  const confirmPendingAction = useCallback(
    async (pending: PendingConfirmation) => {
      if (!webviewHandle) return;
      setBusy(true);
      setErrorMsg(null);
      try {
        const result = await withActionTimeout(
          runBrowserHandleAction(webviewHandle, pending.action, {
            confirmDangerous: true,
          }),
          pending.action.type,
        );
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
        const summary = `[用户已确认高风险操作]\n${formatResults([result], pageInfoLite)}`;
        void sendMessage(threadId, { text: summary, files: [] });
      } catch (err) {
        swallow(err);
        setErrorMsg(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [sendMessage, threadId, webviewHandle],
  );

  const dismissPendingAction = useCallback((id: string) => {
    setPendingConfirmations((prev) => prev.filter((item) => item.id !== id));
  }, []);

  // Implementation note.
  // Implementation note.
  const buildOutgoingText = useCallback(
    (raw: string): string => {
      const recorderHeader = recorderMode
        ? `${RECORDER_PROTOCOL}\n\n---\n\n`
        : "";
      if (!autoBrowse) return `${recorderHeader}${raw}`;
      if (protocolInjectedRef.current.has(threadId)) return raw;
      protocolInjectedRef.current.add(threadId);
      return `${recorderHeader}${BROWSER_ACTION_PROTOCOL}\n\n---\n\n${raw}`;
    },
    [autoBrowse, recorderMode, threadId],
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
    () => buildResearchBrief(researchLog),
    [researchLog],
  );

  const buildRecorderTask = useCallback((goal: string) => {
    const trimmed = goal.trim();
    const platforms = RESEARCH_PLATFORMS;
    return [
      RECORDER_PROTOCOL,
      "",
      "[本次调研任务]",
      trimmed,
      "",
      "[外部平台分工]",
      ...platforms.map(
        (platform, index) =>
          `${index + 1}. ${platform.name}: ${platform.hint} (${platform.url})`,
      ),
      "",
      "[执行要求]",
      "- 先打开第一个平台并输入适合该平台的 prompt。",
      "- 每个平台只摘录高密度结果: 结论、来源线索、争议点、下一步。",
      "- 不要把外部平台完整长回答反复喂回本地模型。",
      "- 每个平台完成后,用一条简短研究日志汇报: 平台 / URL / 5 条以内要点 / 待核查。",
      "- 需要登录、上传文件、提交敏感信息时暂停并请用户确认。",
    ].join("\n");
  }, []);

  const startRecorderResearch = useCallback(() => {
    const goal = researchGoal.trim() || input.trim();
    if (!goal) return;
    setRecorderMode(true);
    setAutoBrowse(true);
    const platforms = RESEARCH_PLATFORMS;
    setResearchLog((prev) =>
      [
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          createdAt: Date.now(),
          platform: "调度",
          title: "开始外部 AI 调研",
          note: `${goal}\n平台: ${platforms.map((p) => p.name).join(", ")}`,
          url: activeTab?.url,
        },
        ...prev,
      ].slice(0, 80),
    );
    send(buildRecorderTask(goal));
    setResearchGoal("");
    setInput("");
  }, [activeTab?.url, buildRecorderTask, input, researchGoal, send]);

  const addPageToResearchLog = useCallback(async () => {
    setBusy(true);
    setErrorMsg(null);
    try {
      const page = webviewHandle ? await webviewHandle.extractText() : null;
      const title = page?.title || activeTab?.title || "当前页面";
      const url = page?.url || activeTab?.url;
      const text = page?.text ?? "";
      setResearchLog((prev) =>
        [
          {
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            createdAt: Date.now(),
            platform: guessPlatformName(url),
            title,
            note: text ? text.slice(0, 600) : "已记录当前页面。",
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
  }, [activeTab?.title, activeTab?.url, webviewHandle]);

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
      if (webviewHandle && !window.echo) {
        setBusy(true);
        setErrorMsg(null);
        try {
          const page = await webviewHandle.extractText();
          const pageAgent = page.pageAgent
            ? `\n\n[页面语义能力 pageAgent]\n${JSON.stringify(page.pageAgent).slice(0, 12000)}`
            : "";
          const prefix = `[当前页面]\nURL: ${page.url}\n标题: ${page.title}\n\n${page.text}${pageAgent}\n${page.truncated ? `\n[已截断 · 完整 ${page.textLength} 字符]` : ""}`;
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
        setErrorMsg("Need Electron · 当前在浏览器(非 Electron)里运行");
        return;
      }
      const wcId = webviewHandle.getWebContentsId();
      if (wcId == null) {
        setErrorMsg("当前 tab 没准备好");
        return;
      }
      setBusy(true);
      setErrorMsg(null);
      try {
        const page = await window.echo.browser.extractText(wcId);
        const prefix = `[当前页面]\nURL: ${page.url}\n标题: ${page.title}\n\n${page.text}\n${page.truncated ? `\n[已截断 · 完整 ${page.textLength} 字符]` : ""}`;
        send(`${prefix}\n\n${instruction}`);
      } catch (err) {
        swallow(err);
        setErrorMsg(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [send, webviewHandle],
  );

  // Implementation note.
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const onResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, startW: state.copilotWidth };
      const onMove = (ev: MouseEvent) => {
        if (!dragRef.current) return;
        const delta = ev.clientX - dragRef.current.startX;
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
      className="relative flex h-full w-full min-w-[280px] flex-1 flex-col border-r bg-background"
    >
      {/* Implementation note. */}
      <div
        onMouseDown={onResizeStart}
        className="absolute right-0 top-0 z-10 h-full w-1 cursor-col-resize hover:bg-primary/30"
      />

      {/* Implementation note. */}
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b px-3">
        <AgentPicker
          activeAgent={activeAgent}
          agents={agents}
          activeAgentId={agentName}
        />
        {activeTab?.title && (
          <span
            className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground"
            title={activeTab.title}
          >
            · {activeTab.title}
          </span>
        )}
        {/* Implementation note. */}
        {agentLoopActive && (
          <button
            onClick={stopAgentLoop}
            className="flex shrink-0 items-center gap-1 rounded bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-600 transition-colors hover:bg-rose-500/20 dark:text-rose-400"
            title={t.browser.copilot.stopAgentTooltip}
          >
            <StopCircleIcon className="size-3" />
            {t.browser.copilot.stopAgent}
          </button>
        )}
        {/* Implementation note. */}
        <button
          onClick={() => setAutoBrowse((v) => !v)}
          className={cn(
            "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
            autoBrowse
              ? "bg-primary/10 text-primary"
              : "border border-border text-muted-foreground hover:bg-muted",
          )}
          title={
            autoBrowse
              ? t.browser.copilot.autoBrowseOnTooltip
              : t.browser.copilot.autoBrowseOffTooltip
          }
        >
          {autoBrowse ? "AUTO" : "READ"}
        </button>
        <button
          onClick={() => setRecorderMode((v) => !v)}
          className={cn(
            "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
            recorderMode
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "border border-border text-muted-foreground hover:bg-muted",
          )}
          title={t.browser.copilot.recorderTitle}
        >
          REC
        </button>
        <button
          onClick={() => setCopilotOpen(false)}
          className="grid size-7 shrink-0 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          title={t.common.close}
        >
          <XIcon className="size-4" />
        </button>
      </div>

      {/* quick actions */}
      <div className="flex shrink-0 flex-wrap gap-1.5 border-b bg-muted/30 px-3 py-2">
        <QuickAction
          icon={FileTextIcon}
          label={t.browser.copilot.summarizePage}
          onClick={() =>
            askWithPage("请用中文简明总结这个页面的核心内容,3-5 个要点。")
          }
          disabled={busy}
        />
        <QuickAction
          icon={ListIcon}
          label={t.browser.copilot.extractKeyPoints}
          onClick={() =>
            askWithPage("从这个页面提取所有事实性要点,以有序列表给出。")
          }
          disabled={busy}
        />
        <QuickAction
          icon={LanguagesIcon}
          label={t.browser.copilot.translateToChinese}
          onClick={() => askWithPage("把这个页面的主要内容完整翻译成中文。")}
          disabled={busy}
        />
      </div>

      {recorderMode && (
        <div className="shrink-0 border-b bg-emerald-500/[0.04] px-3 py-2">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
                {t.browser.copilot.recorderTitle}
              </div>
              <div className="truncate text-[10px] text-muted-foreground">
                {t.browser.copilot.recorderDesc}
              </div>
            </div>
          </div>
          <div className="mt-2 flex gap-1.5">
            <input
              value={researchGoal}
              onChange={(e) => setResearchGoal(e.target.value)}
              placeholder={t.browser.copilot.researchGoalPlaceholder}
              className="min-w-0 flex-1 rounded border bg-background px-2 py-1 text-[11px] outline-none focus:ring-1 focus:ring-emerald-500/40"
            />
            <button
              type="button"
              onClick={startRecorderResearch}
              disabled={
                busy ||
                thread.isLoading ||
                (!researchGoal.trim() && !input.trim())
              }
              className="rounded bg-emerald-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:opacity-40"
            >
              {t.browser.copilot.start}
            </button>
          </div>
          <div className="mt-2 flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={() => void addPageToResearchLog()}
              disabled={busy}
              className="rounded border border-border bg-background px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted disabled:opacity-40"
            >
              {t.browser.copilot.recordCurrentPage}
            </button>
            {researchLog.length > 0 && (
              <button
                type="button"
                onClick={() => setResearchLog([])}
                className="text-[10px] text-muted-foreground hover:text-foreground"
              >
                {t.browser.copilot.clearLog}
              </button>
            )}
          </div>
          {researchLog.length > 0 && (
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              <button
                type="button"
                onClick={() => void copyResearchBrief()}
                className="inline-flex items-center justify-center gap-1 rounded border border-border bg-background px-2 py-1 text-[10px] font-medium text-muted-foreground hover:bg-muted"
              >
                <ClipboardCheckIcon className="size-3" />
                {briefCopied
                  ? t.browser.copilot.copied
                  : t.browser.copilot.copyBrief}
              </button>
              <button
                type="button"
                onClick={downloadResearchBrief}
                className="inline-flex items-center justify-center gap-1 rounded border border-border bg-background px-2 py-1 text-[10px] font-medium text-muted-foreground hover:bg-muted"
              >
                <DownloadIcon className="size-3" />
                {t.browser.copilot.exportMd}
              </button>
            </div>
          )}
          {researchLog.length > 0 && (
            <div className="mt-2 max-h-28 space-y-1 overflow-y-auto rounded border border-border/60 bg-background/70 p-1.5">
              {researchLog.slice(0, 5).map((entry) => (
                <div key={entry.id} className="rounded bg-muted/40 px-2 py-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[10px] font-medium">
                      {entry.platform} · {entry.title}
                    </span>
                    <span className="shrink-0 text-[9px] text-muted-foreground">
                      {new Date(entry.createdAt).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">
                    {entry.note}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {errorMsg && (
        <div className="shrink-0 border-b bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">
          {errorMsg}
        </div>
      )}

      {pendingConfirmations.length > 0 && (
        <div className="shrink-0 space-y-2 border-b bg-amber-500/10 px-3 py-2">
          {pendingConfirmations.map((pending) => (
            <div
              key={pending.id}
              className="rounded-md border border-amber-500/30 bg-background/70 p-2 text-[11px]"
            >
              <div className="font-medium text-amber-700 dark:text-amber-300">
                需要用户确认
              </div>
              <div className="mt-1 text-muted-foreground">
                {describePendingAction(pending)}
              </div>
              {pending.error && (
                <div className="mt-1 break-words text-amber-700 dark:text-amber-300">
                  {pending.error}
                </div>
              )}
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => void confirmPendingAction(pending)}
                  disabled={busy}
                  className="rounded bg-amber-600 px-2 py-1 font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                >
                  确认执行
                </button>
                <button
                  onClick={() => dismissPendingAction(pending.id)}
                  disabled={busy}
                  className="rounded border border-border px-2 py-1 text-muted-foreground hover:bg-muted disabled:opacity-50"
                >
                  取消
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
            <div>{t.browser.copilot.emptyHint}</div>
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
                  ? "ml-6 bg-primary/10 text-foreground"
                  : "mr-6 bg-muted/60 text-foreground",
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
          <div className="mr-6 flex items-center gap-2 rounded-lg bg-muted/60 px-3 py-2 text-[13px] text-muted-foreground">
            <Loader2Icon className="size-3.5 animate-spin" />
            {t.browser.copilot.thinking}
          </div>
        )}
      </div>

      {/* input */}
      <div className="shrink-0 border-t p-2">
        <div className="flex items-end gap-2 rounded-lg border bg-muted/40 p-2 focus-within:ring-2 focus-within:ring-primary/30">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            placeholder={t.browser.copilot.inputPlaceholder}
            rows={1}
            className="max-h-32 min-h-[24px] flex-1 resize-none bg-transparent text-sm outline-none"
          />
          <button
            onClick={() => send(input)}
            disabled={!input.trim() || thread.isLoading}
            className="grid size-7 place-items-center rounded bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
            title={t.codeMode.send}
          >
            <SendIcon className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

async function runBrowserHandleAction(
  handle: WebviewTabHandle,
  action: AgentAction,
  options: { confirmDangerous?: boolean } = {},
): Promise<ActionResult> {
  const base: ActionResult = { action, ok: false };
  try {
    switch (action.type) {
      case "navigate":
        handle.loadURL(action.url);
        return { ...base, ok: true, detail: { url: action.url } };
      case "extract":
        return { ...base, ok: true, detail: await handle.extractText() };
      case "snapshot":
        return { ...base, ok: true, detail: await handle.capturePage() };
      case "click":
        return browserActionResult(
          base,
          await handle.runAction("click", {
            selector: action.selector,
          }),
        );
      case "type":
        return browserActionResult(
          base,
          await handle.runAction("type", {
            selector: action.selector,
            text: action.text,
            clear: action.clear,
          }),
        );
      case "hover":
        return browserActionResult(
          base,
          await handle.runAction("hover", {
            selector: action.selector,
          }),
        );
      case "scroll":
        return browserActionResult(
          base,
          await handle.runAction("scroll", {
            selector: action.selector,
            deltaX: action.deltaX,
            deltaY: action.deltaY,
            y: action.deltaY,
          }),
        );
      case "wait":
        return browserActionResult(
          base,
          await handle.runAction("wait", {
            selector: action.selector,
            timeout: action.timeout,
          }),
        );
      case "press":
        return browserActionResult(
          base,
          await handle.runAction("press", {
            key: action.key,
          }),
        );
      case "pageAction":
        return browserActionResult(
          base,
          await handle.runAction("pageAction", {
            id: action.id,
            confirm: options.confirmDangerous === true,
          }),
        );
      case "pageInput":
        return browserActionResult(
          base,
          await handle.runAction("pageInput", {
            id: action.id,
            text: action.text,
            clear: action.clear,
          }),
        );
      case "pageCapability":
        return browserActionResult(
          base,
          await handle.runAction("pageCapability", {
            id: action.id,
            input: action.input,
            confirm: options.confirmDangerous === true,
          }),
        );
      case "aria":
        return browserActionResult(
          base,
          await handle.runAction("aria", {
            maxDepth: action.maxDepth,
          }),
        );
      default:
        return { ...base, ok: false, error: "unknown action type" };
    }
  } catch (e) {
    swallow(e);
    return {
      ...base,
      ok: false,
      error: e instanceof Error ? e.message : String(e),
    };
  }
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
      copilot: {
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
    reasons.push(t.browser.copilot.confirmInputContent);
  }
  if (action.type === "press" && /^(enter|return)$/i.test(action.key)) {
    reasons.push(t.browser.copilot.confirmSubmitForm);
  }
  if (action.type === "click" && SENSITIVE_ACTION_RE.test(action.selector)) {
    reasons.push(t.browser.copilot.confirmSensitiveClick);
  }
  if (
    (action.type === "pageAction" || action.type === "pageCapability") &&
    SENSITIVE_ACTION_RE.test(action.id)
  ) {
    reasons.push(t.browser.copilot.confirmSensitiveAction);
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

function browserActionResult(
  base: ActionResult,
  detail: Record<string, unknown>,
): ActionResult {
  const error = typeof detail.error === "string" ? detail.error : undefined;
  return { ...base, ok: detail.ok !== false, detail, error };
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
      className="flex items-center gap-1 rounded-full border bg-background px-2.5 py-1 text-[11px] text-foreground transition-colors hover:bg-muted disabled:opacity-50"
    >
      <Icon className="size-3" />
      {label}
    </button>
  );
}

function buildResearchBrief(entries: ResearchLogEntry[]): string {
  if (entries.length === 0) return "";
  const ordered = [...entries].reverse();
  const lines = [
    "# 外部 AI 调研简报",
    "",
    `生成时间: ${new Date().toLocaleString()}`,
    `记录数量: ${entries.length}`,
    "",
    "## 摘要记录",
    "",
  ];

  for (const entry of ordered) {
    lines.push(`### ${entry.platform} · ${entry.title}`);
    lines.push(`- 时间: ${new Date(entry.createdAt).toLocaleString()}`);
    if (entry.url) lines.push(`- URL: ${entry.url}`);
    lines.push("- 记录:");
    for (const line of entry.note
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)) {
      lines.push(`  - ${line}`);
    }
    lines.push("");
  }

  lines.push("## 待核查");
  lines.push("- 对不同平台结论做交叉验证。");
  lines.push("- 保留原始页面 URL 或截图作为证据线索。");
  lines.push("- 对需要登录、上传、提交、付费的动作单独确认。");
  return lines.join("\n");
}

function guessPlatformName(url?: string | null): string {
  if (!url) return "页面";
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host.includes("gemini.google")) return "Gemini";
    if (host.includes("notebooklm.google")) return "NotebookLM";
    if (host.includes("doubao")) return "豆包";
    if (host.includes("perplexity")) return "Perplexity";
    if (host.includes("chatgpt") || host.includes("openai")) return "ChatGPT";
    return host.replace(/^www\./, "");
  } catch (e) {
    swallow(e);
    return "页面";
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
    try {
      window.localStorage.setItem(ACTIVE_AGENT_KEY, name);
    } catch (e) {
      swallow(e);
    }
    window.dispatchEvent(
      new CustomEvent(ACTIVE_AGENT_EVENT, { detail: { name } }),
    );
    setOpen(false);
  };

  const display =
    activeAgent?.display_name || activeAgent?.name || activeAgentId;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-sm font-semibold transition-colors hover:bg-muted"
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
        <div className="absolute left-0 top-full z-50 mt-1 max-h-72 w-56 overflow-y-auto rounded-md border bg-popover p-1 shadow-lg">
          {agents.length === 0 ? (
            <div className="px-2 py-1.5 text-xs text-muted-foreground">
              {t.browser.copilot.noAgents}
            </div>
          ) : (
            agents.map((a) => {
              const active = a.name === activeAgentId;
              return (
                <button
                  key={a.name}
                  onClick={() => select(a.name)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted",
                    active && "bg-muted font-semibold",
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
                  {active && (
                    <span className="text-[10px] text-primary">●</span>
                  )}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
