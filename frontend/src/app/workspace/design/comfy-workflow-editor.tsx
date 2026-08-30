"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRightIcon,
  CirclePlayIcon,
  ExternalLinkIcon,
  Link2Icon,
  Loader2Icon,
  PlusIcon,
  SaveIcon,
  Trash2Icon,
  UnlinkIcon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

type WorkflowNode = {
  class_type: string;
  inputs?: Record<string, unknown>;
  _meta?: { title?: string };
};
type Workflow = Record<string, WorkflowNode>;
type Position = { x: number; y: number };
type NodeSpec = {
  class_type: string;
  title: string;
  category: string;
  inputs: Array<{
    name: string;
    type: unknown;
    optional: boolean;
    default?: unknown;
  }>;
};
type EditorState =
  | "loading"
  | "ready"
  | "saving"
  | "saved"
  | "conflict"
  | "error";

const NODE_WIDTH = 220;
const FALLBACK_NODE_SPECS: NodeSpec[] = [
  {
    class_type: "KSampler",
    title: "KSampler 采样器",
    category: "sampling",
    inputs: [
      { name: "seed", type: "INT", optional: false, default: 0 },
      { name: "steps", type: "INT", optional: false, default: 20 },
      { name: "cfg", type: "FLOAT", optional: false, default: 8 },
      {
        name: "sampler_name",
        type: ["euler"],
        optional: false,
        default: "euler",
      },
      {
        name: "scheduler",
        type: ["normal"],
        optional: false,
        default: "normal",
      },
      { name: "denoise", type: "FLOAT", optional: false, default: 1 },
    ],
  },
  {
    class_type: "CLIPTextEncode",
    title: "CLIP 文本编码",
    category: "conditioning",
    inputs: [{ name: "text", type: "STRING", optional: false, default: "" }],
  },
  {
    class_type: "SaveImage",
    title: "保存图片",
    category: "image",
    inputs: [
      {
        name: "filename_prefix",
        type: "STRING",
        optional: false,
        default: "Echo",
      },
    ],
  },
];

function isConnection(value: unknown): value is [string, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    ["string", "number"].includes(typeof value[0]) &&
    typeof value[1] === "number"
  );
}

function initialPositions(
  workflow: Workflow,
  saved?: Record<string, Position>,
): Record<string, Position> {
  return Object.fromEntries(
    Object.keys(workflow).map((id, index) => [
      id,
      saved?.[id] ?? {
        x: 80 + (index % 4) * 280,
        y: 80 + Math.floor(index / 4) * 220,
      },
    ]),
  );
}

function nodeAccent(classType: string): string {
  const accents = ["#8b5cf6", "#3b82f6", "#10b981", "#f59e0b", "#ec4899"];
  const hash = [...classType].reduce(
    (total, char) => total + char.charCodeAt(0),
    0,
  );
  return accents[hash % accents.length] ?? accents[0]!;
}

export function ComfyWorkflowEditor({
  workflowId,
  onClose,
  onOpenNative,
}: {
  workflowId: string;
  onClose: () => void;
  onOpenNative: () => void;
}) {
  const [name, setName] = useState("ComfyUI 工作流");
  const [workflow, setWorkflow] = useState<Workflow>({});
  const [positions, setPositions] = useState<Record<string, Position>>({});
  const [revision, setRevision] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [state, setState] = useState<EditorState>("loading");
  const [runState, setRunState] = useState<string | null>(null);
  const [nodeSpecs, setNodeSpecs] = useState<NodeSpec[]>(FALLBACK_NODE_SPECS);
  const dragRef = useRef<{
    id: string;
    startX: number;
    startY: number;
    origin: Position;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    fetch(
      `${getBackendBaseURL()}/api/design/comfyui/workflows/${encodeURIComponent(workflowId)}`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (response.status === 404)
          return {
            name: workflowId === "blank" ? "空白工作流" : workflowId,
            workflow: {},
            revision: 0,
            ui: {},
          };
        if (!response.ok) throw new Error(`load failed: ${response.status}`);
        return (await response.json()) as {
          name?: string;
          workflow?: Workflow;
          revision?: number;
          ui?: { positions?: Record<string, Position> };
        };
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        const next = payload.workflow ?? {};
        setName(payload.name || workflowId);
        setWorkflow(next);
        setPositions(initialPositions(next, payload.ui?.positions));
        setRevision(payload.revision ?? 0);
        setSelectedId(Object.keys(next)[0] ?? null);
        setState("ready");
      })
      .catch((error: unknown) => {
        if ((error as { name?: string }).name !== "AbortError")
          setState("error");
      });
    return () => controller.abort();
  }, [workflowId]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${getBackendBaseURL()}/api/design/comfyui/object-info`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) return null;
        return (await response.json()) as { items?: NodeSpec[] };
      })
      .then((payload) => {
        if (!controller.signal.aborted && payload?.items?.length) {
          setNodeSpecs(payload.items);
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      setPositions((current) => ({
        ...current,
        [drag.id]: {
          x: Math.max(16, drag.origin.x + event.clientX - drag.startX),
          y: Math.max(16, drag.origin.y + event.clientY - drag.startY),
        },
      }));
      setState((current) => (current === "conflict" ? current : "ready"));
    };
    const up = () => {
      dragRef.current = null;
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, []);

  const edges = useMemo(
    () =>
      Object.entries(workflow).flatMap(([target, node]) =>
        Object.entries(node.inputs ?? {}).flatMap(([input, value]) =>
          isConnection(value)
            ? [{ source: String(value[0]), target, input }]
            : [],
        ),
      ),
    [workflow],
  );
  const selected = selectedId ? workflow[selectedId] : undefined;

  const patchInput = (key: string, value: unknown) => {
    if (!selectedId) return;
    setWorkflow((current) => {
      const node = current[selectedId];
      if (!node) return current;
      return {
        ...current,
        [selectedId]: {
          ...node,
          inputs: { ...(node.inputs ?? {}), [key]: value },
        },
      };
    });
    setState("ready");
  };

  const save = async () => {
    setState("saving");
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/workflows/${encodeURIComponent(workflowId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            workflow,
            ui: { positions },
            expected_revision: revision,
          }),
        },
      );
      if (response.status === 409) {
        setState("conflict");
        return;
      }
      if (!response.ok) throw new Error(`save failed: ${response.status}`);
      const payload = (await response.json()) as { revision?: number };
      setRevision(payload.revision ?? revision + 1);
      setState("saved");
    } catch {
      setState("error");
    }
  };

  const run = async () => {
    setRunState("正在提交");
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/queue`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: workflow }),
        },
      );
      if (!response.ok) throw new Error(`queue failed: ${response.status}`);
      const payload = (await response.json()) as { prompt_id?: string };
      setRunState(
        payload.prompt_id
          ? `已排队 · ${payload.prompt_id.slice(0, 8)}`
          : "已排队",
      );
    } catch {
      setRunState("运行失败 · 请检查本机 ComfyUI");
    }
  };

  const addNode = (classType = "KSampler") => {
    const spec =
      nodeSpecs.find((item) => item.class_type === classType) ??
      FALLBACK_NODE_SPECS[0]!;
    const numeric = Object.keys(workflow).map(Number).filter(Number.isFinite);
    const id = String((numeric.length ? Math.max(...numeric) : 0) + 1);
    setWorkflow((current) => ({
      ...current,
      [id]: {
        class_type: spec.class_type,
        inputs: Object.fromEntries(
          spec.inputs.map((input) => {
            const choices = Array.isArray(input.type) ? input.type : [];
            return [input.name, input.default ?? choices[0] ?? ""];
          }),
        ),
        _meta: { title: spec.title },
      },
    }));
    setPositions((current) => ({
      ...current,
      [id]: {
        x: 120 + Object.keys(current).length * 24,
        y: 120 + Object.keys(current).length * 18,
      },
    }));
    setSelectedId(id);
    setState("ready");
  };

  const removeNode = () => {
    if (!selectedId) return;
    setWorkflow((current) =>
      Object.fromEntries(
        Object.entries(current)
          .filter(([id]) => id !== selectedId)
          .map(([id, node]) => [
            id,
            {
              ...node,
              inputs: Object.fromEntries(
                Object.entries(node.inputs ?? {}).filter(
                  ([, value]) =>
                    !isConnection(value) || String(value[0]) !== selectedId,
                ),
              ),
            },
          ]),
      ),
    );
    setSelectedId(null);
    setState("ready");
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#111318] text-zinc-100">
      <header className="flex h-11 shrink-0 items-center border-b border-white/10 bg-[#17191f] px-2.5">
        <Input
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setState("ready");
          }}
          className="h-7 w-56 border-white/10 bg-white/5 text-[11px] text-white"
        />
        <span className="ml-2 rounded bg-white/5 px-2 py-1 text-[9px] text-zinc-400">
          {Object.keys(workflow).length} 节点 · {edges.length} 连线 · r
          {revision}
        </span>
        <span className="flex-1" />
        <span
          className={cn(
            "mr-2 text-[9px]",
            state === "conflict" || state === "error"
              ? "text-red-400"
              : "text-zinc-500",
          )}
        >
          {state === "loading"
            ? "载入中"
            : state === "saving"
              ? "保存中"
              : state === "saved"
                ? "已保存"
                : state === "conflict"
                  ? "版本冲突，请重新打开"
                  : state === "error"
                    ? "操作失败"
                    : "有未保存修改"}
        </span>
        {runState ? (
          <span className="mr-2 text-[9px] text-zinc-400">{runState}</span>
        ) : null}
        <label className="mr-1 flex h-8 items-center gap-1.5 rounded-md px-2 text-[10px] text-zinc-300 hover:bg-white/5">
          <PlusIcon className="size-3.5" />
          <select
            aria-label="添加 ComfyUI 节点"
            value=""
            onChange={(event) => {
              if (event.target.value) addNode(event.target.value);
            }}
            className="max-w-36 bg-transparent outline-none"
          >
            <option value="">添加节点</option>
            {nodeSpecs.map((spec) => (
              <option key={spec.class_type} value={spec.class_type}>
                {spec.category} · {spec.title}
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 text-[10px] text-zinc-300"
          onClick={() => void save()}
          disabled={state === "loading" || state === "saving"}
        >
          {state === "saving" ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <SaveIcon className="size-3.5" />
          )}
          保存
        </Button>
        <Button
          size="sm"
          className="h-8 gap-1.5 bg-violet-500 text-[10px] hover:bg-violet-400"
          onClick={() => void run()}
          disabled={!Object.keys(workflow).length}
        >
          <CirclePlayIcon className="size-3.5" /> 运行
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 text-[10px] text-zinc-300"
          onClick={onOpenNative}
        >
          <ExternalLinkIcon className="size-3.5" /> 原生界面
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 text-zinc-300"
          onClick={onClose}
        >
          <XIcon className="size-4" />
        </Button>
      </header>
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto bg-[radial-gradient(circle,#343842_1px,transparent_1px)] [background-size:20px_20px]">
          <div className="relative h-[1100px] w-[1800px]">
            <svg className="pointer-events-none absolute inset-0 size-full overflow-visible">
              {edges.map((edge) => {
                const source = positions[edge.source];
                const target = positions[edge.target];
                if (!source || !target) return null;
                const x1 = source.x + NODE_WIDTH;
                const y1 = source.y + 48;
                const x2 = target.x;
                const y2 = target.y + 48;
                const bend = Math.max(70, Math.abs(x2 - x1) * 0.45);
                return (
                  <path
                    key={`${edge.source}-${edge.target}-${edge.input}`}
                    d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke="#8b5cf6"
                    strokeOpacity="0.78"
                    strokeWidth="2"
                  />
                );
              })}
            </svg>
            {Object.entries(workflow).map(([id, node]) => {
              const position = positions[id] ?? { x: 80, y: 80 };
              const inputs = Object.entries(node.inputs ?? {});
              return (
                <div
                  key={id}
                  className={cn(
                    "absolute overflow-hidden rounded-[10px] border bg-[#20232b] shadow-[0_14px_30px_rgba(0,0,0,.32)]",
                    selectedId === id
                      ? "border-violet-400 ring-1 ring-violet-400/50"
                      : "border-white/10",
                  )}
                  style={{
                    left: position.x,
                    top: position.y,
                    width: NODE_WIDTH,
                  }}
                  onClick={() => setSelectedId(id)}
                >
                  <div
                    className="flex h-8 cursor-grab items-center px-2.5 text-[10px] font-semibold active:cursor-grabbing"
                    style={{
                      backgroundColor: `${nodeAccent(node.class_type)}cc`,
                    }}
                    onPointerDown={(event) => {
                      dragRef.current = {
                        id,
                        startX: event.clientX,
                        startY: event.clientY,
                        origin: position,
                      };
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {node._meta?.title || node.class_type}
                    </span>
                    <span className="ml-2 text-[8px] opacity-70">#{id}</span>
                  </div>
                  <div className="space-y-1 px-2.5 py-2 text-[8px] text-zinc-400">
                    {inputs.length ? (
                      inputs.slice(0, 5).map(([key, value]) => (
                        <div key={key} className="flex items-center gap-1.5">
                          <span className="size-1.5 rounded-full bg-violet-400" />
                          <span className="min-w-0 flex-1 truncate">{key}</span>
                          <span className="max-w-24 truncate text-zinc-500">
                            {isConnection(value)
                              ? `← #${value[0]}`
                              : String(value)}
                          </span>
                        </div>
                      ))
                    ) : (
                      <span>暂无输入</span>
                    )}
                  </div>
                  <span className="absolute right-[-4px] top-11 size-2 rounded-full bg-violet-400" />
                </div>
              );
            })}
          </div>
        </div>
        <aside className="w-[310px] shrink-0 overflow-y-auto border-l border-white/10 bg-[#17191f] p-3">
          {selected && selectedId ? (
            <>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] font-semibold">
                    {selected._meta?.title || selected.class_type}
                  </div>
                  <div className="mt-0.5 text-[9px] text-zinc-500">
                    {selected.class_type} · #{selectedId}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-zinc-400 hover:text-red-400"
                  onClick={removeNode}
                >
                  <Trash2Icon className="size-3.5" />
                </Button>
              </div>
              <div className="mt-4 space-y-3">
                {Object.entries(selected.inputs ?? {}).map(([key, value]) => (
                  <div key={key}>
                    <label className="mb-1.5 flex items-center gap-1.5 text-[9px] text-zinc-400">
                      {isConnection(value) ? (
                        <Link2Icon className="size-3" />
                      ) : null}
                      {key}
                    </label>
                    {isConnection(value) ? (
                      <div className="flex gap-1.5">
                        <select
                          value={String(value[0])}
                          onChange={(event) =>
                            patchInput(key, [event.target.value, value[1]])
                          }
                          className="h-8 min-w-0 flex-1 rounded-md border border-white/10 bg-white/5 px-2 text-[10px] outline-none"
                        >
                          {Object.keys(workflow)
                            .filter((id) => id !== selectedId)
                            .map((id) => {
                              const node = workflow[id];
                              return node ? (
                                <option key={id} value={id}>
                                  #{id} {node._meta?.title || node.class_type}
                                </option>
                              ) : null;
                            })}
                        </select>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8 text-zinc-400"
                          onClick={() => patchInput(key, "")}
                        >
                          <UnlinkIcon className="size-3.5" />
                        </Button>
                      </div>
                    ) : (
                      <div className="flex gap-1.5">
                        <Input
                          value={
                            typeof value === "object"
                              ? JSON.stringify(value)
                              : String(value ?? "")
                          }
                          onChange={(event) =>
                            patchInput(
                              key,
                              typeof value === "number"
                                ? Number(event.target.value)
                                : event.target.value,
                            )
                          }
                          className="h-8 border-white/10 bg-white/5 text-[10px] text-white"
                        />
                        {Object.keys(workflow).length > 1 ? (
                          <select
                            value=""
                            aria-label={`连接 ${key}`}
                            onChange={(event) => {
                              if (event.target.value)
                                patchInput(key, [event.target.value, 0]);
                            }}
                            className="size-8 rounded-md border border-white/10 bg-white/5 text-[10px]"
                          >
                            <option value="">↗</option>
                            {Object.keys(workflow)
                              .filter((id) => id !== selectedId)
                              .map((id) => (
                                <option key={id} value={id}>
                                  #{id}
                                </option>
                              ))}
                          </select>
                        ) : null}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="mt-5 rounded-lg border border-white/10 bg-white/[0.03] p-3 text-[9px] leading-4 text-zinc-500">
                <ArrowRightIcon className="mb-1 size-3" />
                参数与连线会写回 Comfy API prompt；节点坐标只保存在 Echo UI
                元数据中。
              </div>
            </>
          ) : (
            <div className="grid h-40 place-items-center text-center text-[10px] text-zinc-500">
              选择节点查看和编辑参数
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

export default ComfyWorkflowEditor;
