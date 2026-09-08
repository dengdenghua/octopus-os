import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useCookbookPull } from "@/core/cookbook/use-cookbook";
import { useAuth } from "@/providers/AuthProvider";

interface Plan {
  plan_id: string;
  tag: string;
  label: string;
  runtime_download_bytes: number;
  model_estimate_bytes: number;
  required_disk_bytes: number;
  free_disk_bytes: number;
  storage_path: string;
  model_memory_gb: number;
}
interface Job {
  job_id?: string;
  tag?: string;
  stage: string;
  error?: string;
  downloaded_bytes?: number;
  total_bytes?: number;
}
const stages: Record<string, string> = {
  preparing: "准备部署",
  installing: "安装运行环境",
  starting: "启动本机服务",
  pulling: "下载模型",
  verifying: "验证模型能力",
  ready: "模型已就绪",
  error: "部署未完成",
};
const gib = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GiB`;

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/cookbook/deployment${path}`,
    {
      method: body ? "POST" : "GET",
      headers: jsonAuthHeaders(),
      ...(body ? { body: JSON.stringify(body) } : {}),
    },
  );
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string" ? data.detail : "无法读取部署状态",
    );
  return data as T;
}

function readIntent(key: string) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function LocalAiDeployment({
  tag,
  disabled,
}: {
  tag?: string;
  disabled: boolean;
}) {
  const { user } = useAuth();
  const principal = user?.actor_id || user?.user_id || "local";
  const currentPrincipal = useRef(principal);
  currentPrincipal.current = principal;
  const key = `local-ai-default:${principal}`;
  const qc = useQueryClient();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [setDefault, setSetDefault] = useState(true);
  const [intent, setIntent] = useState<string | null>(() => readIntent(key));
  const attempted = useRef<string | null>(null);
  const {
    activate,
    pendingTag,
    activatedTag,
    error: activationError,
  } = useCookbookPull();
  useEffect(() => {
    setPlan(null);
    setIntent(readIntent(key));
    attempted.current = null;
  }, [key]);
  const query = useQuery({
    queryKey: ["local-ai-deployment", principal],
    queryFn: () => request<Job>(""),
    retry: false,
    refetchInterval: (q) =>
      q.state.data && !["idle", "ready", "error"].includes(q.state.data.stage)
        ? 2000
        : 30000,
  });
  const job = query.data;
  const busy = !!job && !["idle", "ready", "error"].includes(job.stage);
  const review = useMutation({
    mutationFn: ({ tag }: { tag: string; principal: string }) =>
      request<Plan>("/plan", { tag }),
    onSuccess: (value, variables) => {
      if (variables.principal === currentPrincipal.current) setPlan(value);
    },
  });
  const deploy = useMutation({
    mutationFn: ({
      plan_id,
    }: {
      plan_id: string;
      principal: string;
      setDefault: boolean;
    }) => request<Job>("/start", { plan_id }),
    onSuccess: (value, variables) => {
      qc.setQueryData(["local-ai-deployment", variables.principal], value);
      if (variables.principal !== currentPrincipal.current) return;
      setPlan(null);
      attempted.current = null;
      const next = variables.setDefault ? value.job_id || null : null;
      setIntent(next);
      try {
        if (next) sessionStorage.setItem(key, next);
        else sessionStorage.removeItem(key);
      } catch {
        /* memory intent remains */
      }
    },
  });
  useEffect(() => {
    if (
      job?.stage !== "ready" ||
      !job.job_id ||
      attempted.current === job.job_id
    )
      return;
    attempted.current = job.job_id;
    void qc.invalidateQueries({ queryKey: ["cookbook-snapshot"] });
    if (intent === job.job_id && job.tag) {
      // Consume before sending: a failed save requires an explicit retry.
      try {
        sessionStorage.removeItem(key);
      } catch {
        /* no persistent intent */
      }
      setIntent(null);
      activate(job.tag);
    }
  }, [job, intent, key, qc, activate]);
  const error = review.error || deploy.error || activationError || query.error;
  return (
    <div className="mt-3 rounded-lg border border-border bg-background/60 p-3 text-xs">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-medium">部署本地 AI</p>
          <p className="mt-1 text-muted-foreground">
            独立安装 · 本机推理 · 随 Echo 启动
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          disabled={
            !tag ||
            disabled ||
            busy ||
            review.isPending ||
            deploy.isPending ||
            !!pendingTag
          }
          onClick={() => tag && review.mutate({ tag, principal })}
        >
          {review.isPending ? "检查中…" : "检查部署方案"}
        </Button>
      </div>
      {plan && (
        <div className="mt-3 space-y-2 border-t border-border pt-3">
          <p className="font-medium">
            {plan.label} · 预计内存 {plan.model_memory_gb} GiB
          </p>
          <p className="text-muted-foreground">
            环境下载 {gib(plan.runtime_download_bytes)} · 模型预计{" "}
            {gib(plan.model_estimate_bytes)}
          </p>
          <p className="text-muted-foreground">
            磁盘可用 {gib(plan.free_disk_bytes)} ·
            含解压、下载暂存及系统余量，需 {gib(plan.required_disk_bytes)}
          </p>
          <p className="break-all text-muted-foreground">
            存储位置：{plan.storage_path}
          </p>
          <p className="text-muted-foreground">
            将联网下载公开安装包和模型；验证只使用内置测试内容。独立服务使用本机端口
            11435，现有 Ollama 模型不会搬迁。
          </p>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={setDefault}
              onChange={(e) => setSetDefault(e.target.checked)}
            />
            验证后设为默认模型
          </label>
          <div className="flex gap-2">
            <Button
              size="sm"
              className="h-7 text-xs"
              disabled={deploy.isPending || busy}
              onClick={() =>
                deploy.mutate({ plan_id: plan.plan_id, principal, setDefault })
              }
            >
              确认部署
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs"
              onClick={() => setPlan(null)}
            >
              取消
            </Button>
          </div>
        </div>
      )}
      {job && job.stage !== "idle" && (
        <div role="status" className="mt-3 space-y-1 text-muted-foreground">
          <p>
            {activatedTag === job.tag
              ? "部署完成，已设为默认"
              : pendingTag
                ? "正在接入默认模型…"
                : stages[job.stage] || job.stage}{" "}
            · {job.tag}
          </p>
          {!!job.total_bytes && (
            <>
              <progress
                aria-label="运行环境下载进度"
                className="h-1.5 w-full"
                value={job.downloaded_bytes || 0}
                max={job.total_bytes}
              />
              <p>
                {gib(job.downloaded_bytes || 0)} / {gib(job.total_bytes)}
              </p>
            </>
          )}
          {busy && <p>可离开此设置页；后台继续准备，返回后接入默认模型。</p>}
          {job.stage === "error" && (
            <p role="alert" className="text-destructive">
              {job.error}
            </p>
          )}
          {job.stage === "ready" && !pendingTag && activatedTag !== job.tag && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => job.tag && activate(job.tag)}
            >
              设为默认
            </Button>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-destructive">
          {error.message}
        </p>
      )}
    </div>
  );
}
