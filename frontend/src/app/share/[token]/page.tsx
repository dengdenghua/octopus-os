import { useEffect, useMemo, useState } from "react";
import {
  BotIcon,
  FileIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useParams } from "react-router-dom";

import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import {
  getPublicThreadShare,
  type PublicThreadShare,
} from "@/core/sharing/public-thread-share";
import { useStreamdownPlugins } from "@/core/streamdown";

type LoadState =
  | { status: "loading" }
  | { status: "ready"; share: PublicThreadShare }
  | { status: "error"; message: string };

function artifactBasename(value: string): string {
  return (
    value
      .trim()
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .pop() || "附件"
  );
}

function createdAtLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function PublicShareLoading() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/35 px-6 text-foreground">
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Loader2Icon className="size-4 animate-spin" />
        正在打开分享内容…
      </div>
    </main>
  );
}

function PublicShareError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/35 px-6 text-foreground">
      <section
        role="alert"
        className="w-full max-w-sm rounded-2xl border border-border-default bg-card p-6 text-center shadow-[var(--shadow-card)]"
      >
        <div className="mx-auto flex size-11 items-center justify-center rounded-xl bg-muted text-muted-foreground">
          <MessageSquareTextIcon className="size-5" />
        </div>
        <h1 className="mt-4 text-lg font-semibold">无法打开分享内容</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {message || "链接可能已失效、被取消或输入不完整。"}
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-5 inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-border-default bg-background px-3 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        >
          <RefreshCwIcon className="size-4" />
          重新加载
        </button>
      </section>
    </main>
  );
}

function PublicShareView({ share }: { share: PublicThreadShare }) {
  const plugins = useStreamdownPlugins();
  const createdAt = createdAtLabel(share.created_at);
  const artifacts = useMemo(
    () =>
      Array.from(
        new Set(
          (share.artifacts ?? [])
            .filter((item): item is string => typeof item === "string")
            .map(artifactBasename)
            .filter(Boolean),
        ),
      ),
    [share.artifacts],
  );

  return (
    <div className="min-h-screen bg-muted/35 text-foreground">
      <header className="border-b border-border-subtle bg-background/95 px-4 backdrop-blur sm:px-6">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-foreground text-background">
              <BotIcon className="size-4" />
            </div>
            <span className="truncate text-sm font-semibold">EchoAI</span>
          </div>
          <span className="shrink-0 rounded-full border border-border-default bg-muted/50 px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
            公开分享 · 只读
          </span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
        <section aria-labelledby="share-title">
          <p className="text-xs font-medium text-muted-foreground">分享任务</p>
          <h1
            id="share-title"
            className="mt-2 text-balance text-2xl font-semibold tracking-tight sm:text-3xl"
          >
            {share.title || "EchoAI 分享任务"}
          </h1>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
            <span>{share.stats?.turns ?? 0} 轮对话</span>
            <span>{share.stats?.messages ?? share.messages.length} 条消息</span>
            {artifacts.length > 0 ? (
              <span>{artifacts.length} 个产物</span>
            ) : null}
            {createdAt ? (
              <time dateTime={share.created_at}>{createdAt}</time>
            ) : null}
          </div>
        </section>

        <section aria-label="公开对话" className="mt-8 space-y-5">
          {share.messages.map((message, index) =>
            message.role === "user" ? (
              <article
                key={`user-${index}`}
                className="ml-auto w-fit max-w-[88%] rounded-2xl rounded-br-md bg-foreground px-4 py-3 text-background"
              >
                <p className="whitespace-pre-wrap break-words text-sm leading-6">
                  {message.content}
                </p>
              </article>
            ) : (
              <article
                key={`assistant-${index}`}
                className="rounded-2xl border border-border-subtle bg-card p-4 shadow-[var(--shadow-card)] sm:p-5"
              >
                <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <span className="flex size-6 items-center justify-center rounded-md bg-muted">
                    <BotIcon className="size-3.5" />
                  </span>
                  EchoAI
                </div>
                <MarkdownContent
                  content={message.content}
                  isLoading={false}
                  remarkPlugins={plugins.remarkPlugins}
                  rehypePlugins={plugins.rehypePlugins}
                  chatFontSize="medium"
                  className="max-w-none text-sm leading-6"
                />
              </article>
            ),
          )}
        </section>

        {artifacts.length > 0 ? (
          <section
            aria-labelledby="share-artifacts-title"
            className="mt-8 rounded-2xl border border-border-subtle bg-card p-4 sm:p-5"
          >
            <h2
              id="share-artifacts-title"
              className="text-sm font-semibold text-foreground"
            >
              任务产物
            </h2>
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {artifacts.map((artifact) => (
                <li
                  key={artifact}
                  className="flex min-w-0 items-center gap-2 rounded-lg bg-muted/55 px-3 py-2 text-sm"
                >
                  <FileIcon className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate" title={artifact}>
                    {artifact}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <footer className="mt-10 border-t border-border-subtle pt-5 text-xs leading-5 text-muted-foreground">
          此页面是创建分享时生成的只读快照，不会随原任务继续更新。AI
          生成内容可能存在错误，请结合实际情况核验；敏感信息请勿通过公开链接传播。
        </footer>
      </main>
    </div>
  );
}

export default function PublicThreadSharePage() {
  const { token = "" } = useParams<{ token: string }>();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    const robots = document.head.querySelector<HTMLMetaElement>(
      'meta[name="robots"]',
    );
    const previousContent = robots?.getAttribute("content") ?? null;
    const meta = robots ?? document.createElement("meta");
    if (!robots) {
      meta.name = "robots";
      document.head.appendChild(meta);
    }
    meta.content = "noindex, nofollow, noarchive";
    return () => {
      if (!robots) meta.remove();
      else if (previousContent === null) meta.removeAttribute("content");
      else meta.content = previousContent;
    };
  }, []);

  useEffect(() => {
    const previousTitle = document.title;
    document.title =
      state.status === "ready"
        ? `${state.share.title} · EchoAI 分享`
        : "EchoAI 公开分享";
    return () => {
      document.title = previousTitle;
    };
  }, [state]);

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    getPublicThreadShare(token)
      .then((share) => {
        if (active) setState({ status: "ready", share });
      })
      .catch((error: unknown) => {
        if (!active) return;
        setState({
          status: "error",
          message:
            error instanceof Error
              ? error.message
              : "链接可能已失效、被取消或输入不完整。",
        });
      });
    return () => {
      active = false;
    };
  }, [attempt, token]);

  if (state.status === "loading") return <PublicShareLoading />;
  if (state.status === "error") {
    return (
      <PublicShareError
        message={state.message}
        onRetry={() => setAttempt((value) => value + 1)}
      />
    );
  }
  return <PublicShareView share={state.share} />;
}
