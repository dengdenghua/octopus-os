"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type SetStateAction,
  type WheelEvent as ReactWheelEvent,
} from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ArchiveIcon,
  ArrowRightIcon,
  AudioLinesIcon,
  BookOpenIcon,
  BotIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleHelpIcon,
  CirclePlayIcon,
  CopyIcon,
  CopyPlusIcon,
  DownloadIcon,
  FolderInputIcon,
  FolderIcon,
  FolderOpenIcon,
  Grid2X2Icon,
  GroupIcon,
  HandIcon,
  ImageIcon,
  LayoutPanelLeftIcon,
  LibraryIcon,
  ListIcon,
  Loader2Icon,
  Maximize2Icon,
  MessageSquareIcon,
  MessageSquarePlusIcon,
  MinusIcon,
  MousePointer2Icon,
  PanelLeftCloseIcon,
  PanelRightIcon,
  PencilIcon,
  PlusIcon,
  PuzzleIcon,
  Redo2Icon,
  SearchIcon,
  SendIcon,
  Settings2Icon,
  SparklesIcon,
  TagIcon,
  Trash2Icon,
  UngroupIcon,
  VideoIcon,
  WandSparklesIcon,
  WorkflowIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useAgents } from "@/core/agents/hooks";
import { useActiveAgentId } from "@/core/agents/active";
import { DEFAULT_PRIMARY_AGENT_ID } from "@/core/agents/persona-policy";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useModels } from "@/core/models/hooks";
import { useStreamdownPlugins } from "@/core/streamdown";
import {
  CREATIVE_PROJECTS_CHANGED_EVENT,
  createLocalCreativeProject,
  creativeCanvasStorageKey,
  readLocalCreativeProjects,
  type LocalCreativeProject,
} from "@/core/design/local-projects";
import {
  useEnableMarketSkill,
  useEnableSkill,
  useSkills,
} from "@/core/skills/hooks";
import type { SkillInfo } from "@/core/skills/types";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/AuthProvider";

import {
  appendDesignNode,
  connectDesignNodes,
  copyDesignSelection,
  deleteDesignSelection,
  DESIGN_CANVAS_STORAGE_KEY,
  designCanvasRunPrompt,
  disconnectDesignEdge,
  fitDesignMediaNodeDimensions,
  groupDesignNodes,
  mergeDesignCanvases,
  parseDesignCanvas,
  pasteDesignSelection,
  planDesignSelectionDeletion,
  switchDesignCanvasMode,
  tidyDesignCanvas,
  ungroupDesignNode,
  type DesignCanvasDocument,
  type DesignCanvasClipboard,
  type DesignCanvasDeletionPlan,
  type DesignCanvasNode,
  type DesignCanvasTidyMode,
  type DesignNodeKind,
} from "./canvas-model";
import {
  COMFY_WORKFLOWS,
  CREATIVE_SKILL_COLLECTION,
  NATIVE_NODE_TEMPLATES,
  type DesignSection,
  type WorkspaceLayout,
} from "./design-catalog";
import { DirectorStage } from "./director-stage";
import { ComfyWorkflowEditor } from "./comfy-workflow-editor";
import { PluginNodeFrame } from "./plugin-node-frame";

type ToolMode = "select" | "hand";
type EmbeddedSurface = "director" | "editor" | "comfyui" | null;
type DesignModelTab = "agent" | "image" | "video" | "audio";

const DESIGN_MODEL_SELECTION_KEY = "echo-design-enabled-models-v1";
const DESIGN_MEDIA_CAPABILITIES: Array<{
  id: string;
  tab: Exclude<DesignModelTab, "agent">;
  name: string;
  detail: string;
  badge: string;
}> = [
  {
    id: "image-generation",
    tab: "image",
    name: "图像生成",
    detail: "跟随当前环境配置",
    badge: "基座",
  },
  {
    id: "comfyui-image",
    tab: "image",
    name: "ComfyUI 图像工作流",
    detail: "调用本地已安装模型",
    badge: "本地",
  },
  {
    id: "comfyui-video",
    tab: "video",
    name: "ComfyUI 视频工作流",
    detail: "调用本地视频节点与模型",
    badge: "本地",
  },
  {
    id: "clip-studio",
    tab: "video",
    name: "AI 剪辑工坊",
    detail: "剪辑、转场与特效包装",
    badge: "内置",
  },
  {
    id: "comfyui-audio",
    tab: "audio",
    name: "ComfyUI 音频工作流",
    detail: "调用本地音频节点与模型",
    badge: "本地",
  },
];

const CREATIVE_SKILL_COVERS = [
  "/community/game-guide(1).jpg",
  "/community/weekly-highlights.jpg",
  "/community/memory-video(1).jpg",
  "/community/daily-album.jpg",
  "/community/study-paper(1).jpg",
  "/community/travel-plan(1).jpg",
  "/community/food-delivery(1).jpg",
  "/community/web-summary.jpg",
  "/community/mock-interview.jpg",
  "/community/gacha.jpg",
  "/community/smart-home.jpg",
  "/community/language-coach.jpg",
  "/community/voice-reply.jpg",
  "/community/weekend.jpg",
  "/community/meeting-notes.jpg",
  "/community/price-watch(1).jpg",
] as const;

const COMFY_WORKFLOW_COVERS = [
  "/images/browser-wallpapers/aurora-lab.png",
  "/images/browser-wallpapers/sky-studio.png",
  "/images/browser-wallpapers/forest-calm.png",
  "/images/browser-wallpapers/ember-dusk.png",
  "/images/browser-wallpapers/mist-glass.png",
  "/images/browser-wallpapers/focus-nocturne.png",
  "/images/browser-wallpapers/clear-productivity.png",
] as const;
const CANVAS_VIEW_STORAGE_KEY = "echo.design.canvas-view.v1";
const WORKSPACE_LAYOUT_STORAGE_KEY = "echo.design.workspace-layout.v1";

type CanvasBackgroundPattern = "dots" | "grid" | "none";
type CanvasBackgroundTone =
  | "default"
  | "paper"
  | "cool-gray"
  | "warm-gray"
  | "mist-blue"
  | "sage"
  | "lavender"
  | "blush"
  | "sand";

const CANVAS_BACKGROUND_TONES: Array<{
  id: CanvasBackgroundTone;
  label: string;
  color: string;
}> = [
  { id: "default", label: "默认", color: "#fafafa" },
  { id: "paper", label: "纸白", color: "#f8f5ee" },
  { id: "cool-gray", label: "冷灰", color: "#f0f2f4" },
  { id: "warm-gray", label: "暖灰", color: "#f4f0ec" },
  { id: "mist-blue", label: "雾蓝", color: "#edf4f7" },
  { id: "sage", label: "雾绿", color: "#eef3ec" },
  { id: "lavender", label: "淡紫", color: "#f2eff8" },
  { id: "blush", label: "浅粉", color: "#f8eff1" },
  { id: "sand", label: "沙色", color: "#f5f0e6" },
];
type CanvasSyncState =
  | "local"
  | "loading"
  | "saving"
  | "saved"
  | "conflict"
  | "error";
type CanvasServerPayload = {
  revision?: number;
  document?: Record<string, unknown> | null;
  updated_at?: string | null;
};
type PendingCanvasConflict = {
  revision: number;
  remote: DesignCanvasDocument;
  merged: DesignCanvasDocument;
  conflicts: string[];
};
type CanvasPresenceMember = {
  id: string;
  client_id: string;
  display_name: string;
  x: number | null;
  y: number | null;
  section: DesignSection;
  color: string;
  updated_at: string;
};
type ProjectArtifact = {
  id: string;
  name: string;
  category?: string;
  kind?: string;
  path?: string;
  url?: string;
  summary?: string;
  task_id?: string;
  milestone_id?: string;
};
type DesignLibraryAsset = ProjectArtifact & {
  category: "角色" | "场景" | "风格包" | "道具" | "自定义";
  description?: string;
  tags?: string[];
  filename?: string;
  size?: number;
  created_at?: string;
};

const NODE_WIDTH = 236;
const NODE_HEIGHT = 122;
const MIN_ZOOM = 0.35;
const MAX_ZOOM = 1.8;

const KIND_STYLE: Record<
  DesignNodeKind,
  { label: string; tint: string; accent: string }
> = {
  brief: { label: "需求", tint: "bg-amber-50", accent: "bg-amber-400" },
  agent: { label: "角色", tint: "bg-violet-50", accent: "bg-violet-500" },
  skill: { label: "Skill", tint: "bg-blue-50", accent: "bg-blue-500" },
  plugin: { label: "插件", tint: "bg-emerald-50", accent: "bg-emerald-500" },
  text: { label: "文本", tint: "bg-zinc-50", accent: "bg-zinc-500" },
  table: { label: "表格", tint: "bg-cyan-50", accent: "bg-cyan-500" },
  image: { label: "图片", tint: "bg-pink-50", accent: "bg-pink-500" },
  video: { label: "视频", tint: "bg-indigo-50", accent: "bg-indigo-500" },
  audio: { label: "音频", tint: "bg-orange-50", accent: "bg-orange-500" },
  file: { label: "文件", tint: "bg-slate-50", accent: "bg-slate-500" },
  placeholder: { label: "生成中", tint: "bg-zinc-50", accent: "bg-zinc-300" },
  group: { label: "分组", tint: "bg-purple-50", accent: "bg-purple-400" },
  sticker: { label: "贴纸", tint: "bg-yellow-50", accent: "bg-yellow-400" },
  director: { label: "3D", tint: "bg-lime-50", accent: "bg-lime-500" },
  editor: { label: "剪辑", tint: "bg-rose-50", accent: "bg-rose-500" },
  comfyui: { label: "ComfyUI", tint: "bg-sky-50", accent: "bg-sky-500" },
  output: { label: "交付", tint: "bg-purple-50", accent: "bg-purple-500" },
};

const GROUP_TONES = {
  red: {
    frame: "border-red-300/80 bg-red-100/10 dark:border-red-400/45",
    label: "text-red-700 dark:text-red-300",
    swatch: "bg-red-400",
  },
  orange: {
    frame: "border-orange-300/80 bg-orange-100/10 dark:border-orange-400/45",
    label: "text-orange-700 dark:text-orange-300",
    swatch: "bg-orange-400",
  },
  yellow: {
    frame: "border-yellow-300/80 bg-yellow-100/10 dark:border-yellow-400/45",
    label: "text-yellow-700 dark:text-yellow-300",
    swatch: "bg-yellow-400",
  },
  green: {
    frame: "border-green-300/80 bg-green-100/10 dark:border-green-400/45",
    label: "text-green-700 dark:text-green-300",
    swatch: "bg-green-400",
  },
  cyan: {
    frame: "border-cyan-300/80 bg-cyan-100/10 dark:border-cyan-400/45",
    label: "text-cyan-700 dark:text-cyan-300",
    swatch: "bg-cyan-400",
  },
  blue: {
    frame: "border-blue-300/80 bg-blue-100/10 dark:border-blue-400/45",
    label: "text-blue-700 dark:text-blue-300",
    swatch: "bg-blue-400",
  },
  purple: {
    frame: "border-purple-300/80 bg-purple-100/10 dark:border-purple-400/45",
    label: "text-purple-700 dark:text-purple-300",
    swatch: "bg-purple-400",
  },
} as const;
type GroupTone = keyof typeof GROUP_TONES;

function nextNodeId(kind: DesignNodeKind): string {
  return `${kind}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

function artifactNodeKind(artifact: ProjectArtifact): DesignNodeKind {
  const marker =
    `${artifact.kind ?? ""} ${artifact.path ?? ""} ${artifact.url ?? ""}`.toLowerCase();
  if (/\.(mp4|mov|webm|mkv)\b|\bvideo\b/.test(marker)) return "video";
  if (/\.(mp3|wav|m4a|aac|flac)\b|\baudio\b/.test(marker)) return "audio";
  if (/\.(png|jpe?g|webp|gif|svg)\b|\bimage\b/.test(marker)) return "image";
  if (/\.(csv|xlsx?|ods)\b|\btable\b/.test(marker)) return "table";
  return "file";
}

function EdgeLayer({
  document,
  connectionSourceId,
  pointer,
}: {
  document: DesignCanvasDocument;
  connectionSourceId?: string | null;
  pointer?: { x: number; y: number } | null;
}) {
  if (document.mode !== "workflow") return null;
  const connectionSource = connectionSourceId
    ? document.nodes.find((node) => node.id === connectionSourceId)
    : null;
  return (
    <svg className="pointer-events-none absolute inset-0 size-full overflow-visible">
      {document.edges.map((edge) => {
        const source = document.nodes.find((node) => node.id === edge.source);
        const target = document.nodes.find((node) => node.id === edge.target);
        if (!source || !target) return null;
        const sx = source.x + (source.width ?? NODE_WIDTH);
        const sy = source.y + (source.height ?? NODE_HEIGHT) / 2;
        const tx = target.x;
        const ty = target.y + (target.height ?? NODE_HEIGHT) / 2;
        const bend = Math.max(64, Math.abs(tx - sx) * 0.45);
        return (
          <path
            key={edge.id}
            d={`M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`}
            fill="none"
            className="stroke-[#c4c4c4] dark:stroke-[#525252]"
            strokeWidth="1.5"
          />
        );
      })}
      {connectionSource && pointer ? (
        <path
          d={`M ${connectionSource.x + (connectionSource.width ?? NODE_WIDTH)} ${connectionSource.y + (connectionSource.height ?? NODE_HEIGHT) / 2} C ${connectionSource.x + (connectionSource.width ?? NODE_WIDTH) + 80} ${connectionSource.y + (connectionSource.height ?? NODE_HEIGHT) / 2}, ${pointer.x - 80} ${pointer.y}, ${pointer.x} ${pointer.y}`}
          fill="none"
          className="stroke-violet-500"
          strokeWidth="1.5"
          strokeDasharray="5 4"
        />
      ) : null}
    </svg>
  );
}

function CanvasNode({
  node,
  selected,
  zoom,
  mode,
  onSelect,
  onMove,
  onMoveStart,
  onMoveEnd,
  onContextMenu,
  onDownload,
  onMediaDimensions,
  showPorts,
  connecting,
  onStartConnection,
  onCompleteConnection,
}: {
  node: DesignCanvasNode;
  selected: boolean;
  zoom: number;
  mode: ToolMode;
  onSelect: (additive: boolean) => void;
  onMove: (x: number, y: number) => void;
  onMoveStart: () => void;
  onMoveEnd: () => void;
  onContextMenu: (event: ReactMouseEvent<HTMLDivElement>) => void;
  onDownload: () => void;
  onMediaDimensions: (width: number, height: number) => void;
  showPorts: boolean;
  connecting: boolean;
  onStartConnection: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onCompleteConnection: () => void;
}) {
  const style = KIND_STYLE[node.kind] ?? KIND_STYLE.text;
  const assetPreviewUrl = node.asset?.url
    ? node.asset.url.startsWith("/")
      ? `${getBackendBaseURL()}${node.asset.url}`
      : node.asset.url
    : null;
  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (mode !== "select") return;
    event.stopPropagation();
    onSelect(event.shiftKey || event.metaKey);
    onMoveStart();
    const startX = event.clientX;
    const startY = event.clientY;
    const originX = node.x;
    const originY = node.y;
    const move = (next: PointerEvent) =>
      onMove(
        originX + (next.clientX - startX) / zoom,
        originY + (next.clientY - startY) / zoom,
      );
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      onMoveEnd();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end, { once: true });
  };

  if (node.kind === "sticker") {
    return (
      <div
        data-testid={`design-node-${node.id}`}
        onPointerDown={startDrag}
        onContextMenu={onContextMenu}
        className={cn(
          "pointer-events-auto absolute grid select-none place-items-center rounded-[14px] text-[34px] drop-shadow-sm transition hover:scale-105",
          mode === "select" && "cursor-grab active:cursor-grabbing",
          selected &&
            "bg-white/75 ring-[3px] ring-foreground/10 backdrop-blur dark:bg-black/45",
        )}
        style={{
          width: node.width ?? 56,
          height: node.height ?? 56,
          transform: `translate(${node.x}px, ${node.y}px)`,
        }}
        title={node.attachedTo ? "跟随目标" : "自由贴纸"}
      >
        {node.emoji || "✨"}
      </div>
    );
  }

  if (node.kind === "group") {
    const tone =
      GROUP_TONES[(node.color as GroupTone) ?? "purple"] ?? GROUP_TONES.purple;
    return (
      <div
        data-testid={`design-node-${node.id}`}
        onPointerDown={startDrag}
        onContextMenu={onContextMenu}
        className={cn(
          "pointer-events-auto absolute rounded-[18px] border border-dashed transition",
          tone.frame,
          mode === "select" && "cursor-grab active:cursor-grabbing",
          selected && "ring-[3px] ring-purple-500/12",
        )}
        style={{
          width: node.width ?? NODE_WIDTH,
          height: node.height ?? NODE_HEIGHT,
          transform: `translate(${node.x}px, ${node.y}px)`,
        }}
      >
        <span
          className={cn(
            "absolute left-3 top-2 rounded-md bg-white/90 px-2 py-1 text-[10px] font-medium shadow-sm backdrop-blur dark:bg-[#1a1a1a]/90",
            tone.label,
          )}
        >
          {node.title} · {node.childIds?.length ?? 0}
        </span>
      </div>
    );
  }

  const isMediaNode =
    Boolean(assetPreviewUrl) &&
    (node.kind === "image" || node.kind === "video" || node.kind === "audio");

  if (isMediaNode) {
    const width = node.width ?? NODE_WIDTH;
    const height = node.height ?? (node.kind === "audio" ? 88 : 240);
    const bodyHeight = Math.max(64, height - 25);
    return (
      <div
        data-testid={`design-node-${node.id}`}
        data-media-node={node.kind}
        onPointerDown={startDrag}
        onContextMenu={onContextMenu}
        className={cn(
          "group/media pointer-events-auto absolute select-none",
          mode === "select" && "cursor-grab active:cursor-grabbing",
        )}
        style={{
          width,
          height,
          transform: `translate(${node.x}px, ${node.y}px)`,
        }}
      >
        <div className="flex h-[21px] items-start gap-1.5 px-0.5 text-[11px] leading-4">
          {node.kind === "image" ? (
            <ImageIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
          ) : node.kind === "video" ? (
            <VideoIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
          ) : (
            <AudioLinesIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate font-medium text-foreground/85">
            {node.title}
          </span>
          <span className="shrink-0 text-[9px] text-muted-foreground opacity-0 transition-opacity group-hover/media:opacity-100">
            {style.label}
          </span>
        </div>
        <div
          className={cn(
            "relative overflow-hidden rounded-[12px] border border-black/[0.07] bg-[#ededed] shadow-[0_2px_7px_rgba(0,0,0,.08)] transition dark:border-white/10 dark:bg-[#202020]",
            selected
              ? "ring-[3px] ring-foreground/12"
              : "group-hover/media:border-foreground/20",
          )}
          style={{ height: bodyHeight }}
        >
          {node.kind === "image" ? (
            <img
              src={assetPreviewUrl ?? undefined}
              alt={node.title}
              draggable={false}
              onLoad={(event) =>
                onMediaDimensions(
                  event.currentTarget.naturalWidth,
                  event.currentTarget.naturalHeight,
                )
              }
              className="size-full object-cover"
            />
          ) : node.kind === "video" ? (
            <video
              src={assetPreviewUrl ?? undefined}
              controls
              preload="metadata"
              onLoadedMetadata={(event) =>
                onMediaDimensions(
                  event.currentTarget.videoWidth,
                  event.currentTarget.videoHeight,
                )
              }
              onPointerDown={(event) => event.stopPropagation()}
              className="size-full bg-black object-cover"
            />
          ) : (
            <div className="flex size-full items-center gap-3 bg-gradient-to-br from-white/80 to-zinc-100/75 px-3 dark:from-white/[0.07] dark:to-black/10">
              <span
                className={cn(
                  "grid size-8 shrink-0 place-items-center rounded-[9px]",
                  style.tint,
                )}
              >
                <CirclePlayIcon className="size-4" />
              </span>
              <audio
                src={assetPreviewUrl ?? undefined}
                controls
                preload="metadata"
                onPointerDown={(event) => event.stopPropagation()}
                className="h-8 min-w-0 flex-1"
              />
            </div>
          )}
          <button
            type="button"
            aria-label={`下载 ${node.title}`}
            title="另存为"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              onDownload();
            }}
            className="absolute right-2 top-2 grid size-7 place-items-center rounded-[8px] border border-black/[0.08] bg-white/90 text-zinc-700 opacity-0 shadow-sm backdrop-blur transition hover:bg-white group-hover/media:opacity-100 focus-visible:opacity-100 dark:border-white/10 dark:bg-black/70 dark:text-zinc-100"
          >
            <DownloadIcon className="size-3.5" />
          </button>
        </div>
        {showPorts ? (
          <button
            type="button"
            data-connection-target={node.id}
            aria-label={`连接到 ${node.title}`}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              onCompleteConnection();
            }}
            className="absolute -left-1.5 z-10 size-3 rounded-full border-2 border-white bg-foreground/35 hover:scale-125 hover:bg-violet-500 dark:border-[#1a1a1a]"
            style={{ top: height / 2 - 6 }}
          />
        ) : null}
        {showPorts ? (
          <button
            type="button"
            aria-label={`从 ${node.title} 开始连接`}
            onPointerDown={(event) => {
              event.stopPropagation();
              onStartConnection(event);
            }}
            onClick={(event) => event.stopPropagation()}
            className={cn(
              "absolute -right-1.5 z-10 size-3 rounded-full border-2 border-white bg-foreground/55 hover:scale-125 hover:bg-violet-500 dark:border-[#1a1a1a]",
              connecting && "scale-125 bg-violet-500 ring-4 ring-violet-500/15",
            )}
            style={{ top: height / 2 - 6 }}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div
      data-testid={`design-node-${node.id}`}
      onPointerDown={startDrag}
      onContextMenu={onContextMenu}
      className={cn(
        "pointer-events-auto absolute rounded-[16px] border border-[#e3e3e3] bg-white shadow-[0_2px_5px_rgba(0,0,0,.08)] transition dark:border-[#4a4a4a] dark:bg-[#1a1a1a] dark:shadow-[0_2px_5px_rgba(0,0,0,.15)]",
        mode === "select" && "cursor-grab active:cursor-grabbing",
        selected
          ? "border-foreground/45 ring-[3px] ring-foreground/8"
          : "hover:border-foreground/25",
      )}
      style={{
        width: node.width ?? NODE_WIDTH,
        minHeight: NODE_HEIGHT,
        height: node.height,
        transform: `translate(${node.x}px, ${node.y}px)`,
      }}
    >
      <div className={cn("h-1 rounded-t-[15px]", style.accent)} />
      <div className="p-3.5">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              "grid size-8 place-items-center rounded-lg",
              style.tint,
            )}
          >
            <span className="text-xs font-semibold text-zinc-700">
              {style.label.slice(0, 2)}
            </span>
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-semibold">
              {node.title}
            </div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {style.label}
            </div>
          </div>
          <span className="size-1.5 rounded-full bg-emerald-500" />
        </div>
        {node.kind === "image" && assetPreviewUrl ? (
          <img
            src={assetPreviewUrl}
            alt=""
            className="mt-2.5 h-24 w-full rounded-[10px] border border-black/[0.06] object-cover"
          />
        ) : null}
        <p className="mt-2.5 line-clamp-2 text-[11px] leading-[17px] text-muted-foreground">
          {node.description}
        </p>
      </div>
      {showPorts ? (
        <button
          type="button"
          data-connection-target={node.id}
          aria-label={`连接到 ${node.title}`}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            onCompleteConnection();
          }}
          className="absolute -left-1.5 z-10 size-3 rounded-full border-2 border-white bg-foreground/35 hover:scale-125 hover:bg-violet-500 dark:border-[#1a1a1a]"
          style={{ top: (node.height ?? NODE_HEIGHT) / 2 - 6 }}
        />
      ) : null}
      {showPorts ? (
        <button
          type="button"
          aria-label={`从 ${node.title} 开始连接`}
          onPointerDown={(event) => {
            event.stopPropagation();
            onStartConnection(event);
          }}
          onClick={(event) => event.stopPropagation()}
          className={cn(
            "absolute -right-1.5 z-10 size-3 rounded-full border-2 border-white bg-foreground/55 hover:scale-125 hover:bg-violet-500 dark:border-[#1a1a1a]",
            connecting && "scale-125 bg-violet-500 ring-4 ring-violet-500/15",
          )}
          style={{ top: (node.height ?? NODE_HEIGHT) / 2 - 6 }}
        />
      ) : null}
    </div>
  );
}

function ChatPanel({
  chatUrl,
  onRun,
  onNew,
  onClose,
  surface,
  side = "right",
}: {
  chatUrl: string | null;
  onRun: (prompt?: string) => void;
  onNew: () => void;
  onClose: () => void;
  surface?: EmbeddedSurface;
  side?: "left" | "right";
}) {
  const [prompt, setPrompt] = useState("");
  const profile =
    surface === "editor"
      ? {
          title: "剪辑 Agent",
          intro:
            "告诉我想处理的片段、字幕或画面效果，我会直接修改当前剪辑工程并复核成片。",
          skill: "剪辑 Skill",
        }
      : surface === "director"
        ? {
            title: "导演台 Agent",
            intro:
              "告诉我镜头、角色走位和空间关系，我会直接修改当前 3D 场景并做多视角检查。",
            skill: "导演 Skill",
          }
        : surface === "comfyui"
          ? {
              title: "ComfyUI Agent",
              intro:
                "描述目标和本地依赖，我会检查当前工作流、修改节点并提交本机队列。",
              skill: "工作流 Skill",
            }
          : {
              title: "个人工作台",
              intro: "把想法说给我，产物会直接落在画布上。",
              skill: "Skill",
            };
  if (chatUrl) {
    return (
      <aside
        className={cn(
          "h-full w-[clamp(380px,32vw,440px)] min-w-[380px] shrink-0 bg-background",
          side === "left"
            ? "border-r border-border-subtle"
            : "border-l border-border-subtle",
        )}
      >
        <iframe
          title="Echo 个人工作台"
          data-echo-design-chat="true"
          src={chatUrl}
          className="size-full border-0 bg-background"
          allow="clipboard-read; clipboard-write"
        />
      </aside>
    );
  }
  return (
    <aside
      className={cn(
        "flex h-full w-[clamp(380px,32vw,420px)] min-w-[380px] shrink-0 flex-col bg-background",
        side === "left"
          ? "border-r border-border-subtle"
          : "border-l border-border-subtle",
      )}
    >
      <div className="flex h-12 items-center gap-2 border-b border-border-subtle px-3.5">
        <div className="min-w-0 flex-1 truncate text-[13px] font-semibold">
          {profile.title}
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          aria-label="新建对话"
          onClick={onNew}
        >
          <PlusIcon className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          aria-label="收起对话"
          onClick={onClose}
        >
          <PanelRightIcon className="size-4" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 text-[13px] leading-6">
        <div className="flex gap-2.5">
          <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-violet-100 text-violet-600">
            <SparklesIcon className="size-3.5" />
          </span>
          <div>
            <p className="font-medium">{profile.intro}</p>
            <p className="mt-2 text-muted-foreground">
              我会先拆解任务，再调用画布里绑定的角色、Skill
              和插件。工作流模式按连线执行，自由画布模式由我自主编排。
            </p>
          </div>
        </div>
      </div>
      <div className="p-3">
        <div className="rounded-[16px] border border-border-default bg-background p-2 shadow-[0_12px_32px_-24px_rgba(0,0,0,.45)]">
          <Textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="描述你想创作的内容…"
            className="min-h-20 resize-none border-0 bg-transparent px-2 py-1 text-xs shadow-none focus-visible:ring-0"
          />
          <div className="flex items-center gap-1 pt-1">
            <Button variant="ghost" size="icon" className="size-8 rounded-full">
              <PlusIcon className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 rounded-lg text-[11px]"
            >
              <PuzzleIcon className="size-3.5" /> {profile.skill}
            </Button>
            <span className="flex-1" />
            <Button
              size="icon"
              className="size-8 rounded-full"
              onClick={() => onRun(prompt)}
            >
              <SendIcon className="size-3.5" />
            </Button>
          </div>
        </div>
        <p className="pt-1.5 text-center text-[9px] text-muted-foreground">
          内容会自动保存到当前项目
        </p>
      </div>
    </aside>
  );
}

function AddNodePopover({
  position,
  onAdd,
}: {
  position?: { x: number; y: number } | null;
  onAdd: (
    kind: DesignNodeKind,
    title: string,
    description: string,
    binding?: DesignCanvasNode["binding"],
  ) => void;
}) {
  const items = NATIVE_NODE_TEMPLATES.filter((item) => item.kind !== "output");

  return (
    <div
      data-add-node-menu
      className={cn(
        "absolute z-40 w-[236px] overflow-hidden rounded-[14px] border border-[#e6e6e6] bg-white p-2 shadow-[0_8px_32px_rgba(0,0,0,.08),0_2px_8px_rgba(0,0,0,.04)] dark:border-[#454545] dark:bg-[#1a1a1a] dark:shadow-[0_8px_32px_rgba(0,0,0,.15),0_2px_8px_rgba(0,0,0,.1)]",
        position ? "left-0 top-0" : "bottom-[72px] left-1/2 -translate-x-1/2",
      )}
      style={
        position
          ? { transform: `translate(${position.x}px, ${position.y}px)` }
          : undefined
      }
    >
      <div className="px-2 pb-1 pt-1 text-[12px] font-semibold text-muted-foreground">
        添加节点
      </div>
      <div className="max-h-[440px] overflow-y-auto">
        <div className="space-y-0.5">
          {items.map((item, index) => {
            const Icon = item.icon;
            return (
              <button
                key={`${item.title}-${index}`}
                type="button"
                onClick={() =>
                  onAdd(item.kind, item.title, item.description, undefined)
                }
                className="group flex h-[51px] w-full items-center gap-2.5 rounded-xl px-2 text-left hover:bg-muted/65"
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted">
                  <Icon className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 text-[12px] font-semibold">
                    {item.title}
                    {"badge" in item && item.badge ? (
                      <span className="rounded bg-foreground px-1 py-px text-[8px] text-background">
                        {item.badge}
                      </span>
                    ) : null}
                  </span>
                </span>
                {item.kind === "comfyui" ? (
                  <ChevronDownIcon className="size-3.5 -rotate-90 text-muted-foreground" />
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function CreativeProjectSelector({
  personaId,
  projects,
  currentProjectId,
  onSelect,
  className,
}: {
  personaId: string;
  projects: LocalCreativeProject[];
  currentProjectId: string | null;
  onSelect: (projectId: string | null) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const current = projects.find((project) => project.id === currentProjectId);

  const createProject = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      const project = createLocalCreativeProject(personaId, trimmed);
      setName("");
      setCreateOpen(false);
      setOpen(false);
      onSelect(project.id);
      toast.success(`已创建本地项目「${project.name}」`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建项目失败");
    }
  };

  return (
    <>
      <div className={cn("relative", className)}>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex h-9 w-full min-w-0 items-center gap-2 rounded-lg px-2.5 text-left text-[11px] font-medium text-foreground hover:bg-muted/55"
          aria-label="选择本地创作项目"
          aria-expanded={open}
        >
          {current ? (
            <FolderIcon className="size-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <SparklesIcon className="size-3.5 shrink-0 text-violet-500" />
          )}
          <span className="min-w-0 flex-1 truncate">
            {current?.name || "创作空间"}
          </span>
          <ChevronDownIcon className="size-3 shrink-0 text-muted-foreground" />
        </button>
        {open ? (
          <div className="absolute left-0 top-10 z-50 w-72 overflow-hidden rounded-xl border border-border-default bg-background p-1.5 shadow-xl">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setCreateOpen(true);
              }}
              className="flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[11px] font-semibold hover:bg-muted"
            >
              <PlusIcon className="size-3.5" />
              新建本地项目
            </button>
            {projects.length ? (
              <>
                <div className="my-1 border-t border-border-subtle" />
                <div className="max-h-52 overflow-y-auto">
                  {projects.map((project) => (
                    <button
                      key={project.id}
                      type="button"
                      onClick={() => {
                        setOpen(false);
                        onSelect(project.id);
                      }}
                      className="flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[11px] hover:bg-muted"
                    >
                      <FolderIcon className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate">
                        {project.name}
                      </span>
                      {project.id === currentProjectId ? (
                        <CheckIcon className="size-3.5" />
                      ) : null}
                    </button>
                  ))}
                </div>
              </>
            ) : null}
            <div className="my-1 border-t border-border-subtle" />
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onSelect(null);
              }}
              className="flex h-10 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[11px] hover:bg-muted"
            >
              <SparklesIcon className="size-3.5 shrink-0 text-violet-500" />
              <span className="min-w-0 flex-1">
                <span className="block font-medium">创作空间</span>
                <span className="block truncate text-[9px] text-muted-foreground">
                  当前角色的独立创作房间
                </span>
              </span>
              {!currentProjectId ? <CheckIcon className="size-3.5" /> : null}
            </button>
          </div>
        ) : null}
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-[460px]">
          <DialogHeader>
            <DialogTitle>新建本地项目</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <label
              className="text-sm font-semibold"
              htmlFor="creative-project-name"
            >
              项目名称
            </label>
            <Input
              id="creative-project-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && name.trim()) createProject();
              }}
              placeholder="例如：品牌宣传片"
              maxLength={120}
              autoFocus
            />
          </div>
          <div className="rounded-xl bg-muted/55 px-4 py-3 text-[11px] leading-5 text-muted-foreground">
            本地项目用于整理当前角色的创作页面和资产。项目内容保存在这个角色的创作空间中，不会与工作空间、协作项目或其他角色自动共享。
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button disabled={!name.trim()} onClick={createProject}>
              创建项目
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function DesignHomeView({
  onStart,
  onOpenSkills,
  onAddFiles,
  personaId,
  projects,
  currentProjectId,
  onSelectProject,
}: {
  onStart: (prompt: string, enabledModels: string[]) => void;
  onOpenSkills: () => void;
  onAddFiles: (files: FileList) => void;
  personaId: string;
  projects: LocalCreativeProject[];
  currentProjectId: string | null;
  onSelectProject: (projectId: string | null) => void;
}) {
  const { models, isLoading: modelsLoading } = useModels();
  const { agents, isLoading: agentsLoading } = useAgents();
  const [prompt, setPrompt] = useState("");
  const [category, setCategory] = useState("精选");
  const [modelOpen, setModelOpen] = useState(false);
  const [modelTab, setModelTab] = useState<DesignModelTab>("agent");
  const [enabledModels, setEnabledModels] = useState<Set<string>>(() => {
    try {
      const stored = window.localStorage.getItem(DESIGN_MODEL_SELECTION_KEY);
      return new Set(stored ? (JSON.parse(stored) as string[]) : []);
    } catch {
      return new Set();
    }
  });
  const modelDefaultsAppliedRef = useRef(
    typeof window !== "undefined" &&
      window.localStorage.getItem(DESIGN_MODEL_SELECTION_KEY) !== null,
  );
  const [previewTitle, setPreviewTitle] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modelItems = useMemo(
    () => [
      ...agents.map((agent) => ({
        id: `role:${agent.name}`,
        tab: "agent" as const,
        name: agent.display_name || agent.name,
        detail: agent.description || "Echo Agent",
        badge: "Agent",
      })),
      ...models.map((model) => ({
        id: `agent:${model.selection_id || model.entry_id || model.name}`,
        tab: "agent" as const,
        name: model.display_name || model.name,
        detail:
          [
            model.supports_vision ? "视觉" : null,
            model.supports_tool_use ? "工具调用" : null,
            model.context_window
              ? `${Math.round(model.context_window / 1000)}K 上下文`
              : null,
          ]
            .filter(Boolean)
            .join(" · ") || "Echo 模型路由",
        badge: model.provider || "Agent",
      })),
      ...DESIGN_MEDIA_CAPABILITIES,
    ],
    [agents, models],
  );
  const visibleModelItems = modelItems.filter((item) => item.tab === modelTab);
  const allModelIds = modelItems.map((item) => item.id);
  const allModelsEnabled =
    allModelIds.length > 0 && allModelIds.every((id) => enabledModels.has(id));

  useEffect(() => {
    if (
      modelsLoading ||
      agentsLoading ||
      !modelItems.length ||
      modelDefaultsAppliedRef.current
    )
      return;
    modelDefaultsAppliedRef.current = true;
    setEnabledModels(new Set(modelItems.map((item) => item.id)));
  }, [agentsLoading, modelItems, modelsLoading]);

  useEffect(() => {
    if (!modelDefaultsAppliedRef.current) return;
    window.localStorage.setItem(
      DESIGN_MODEL_SELECTION_KEY,
      JSON.stringify(Array.from(enabledModels)),
    );
  }, [enabledModels]);
  const showcases = [
    {
      category: "官方 Skill",
      title: "产品发布视觉套件",
      description: "从卖点、主视觉到横竖版短片，完成一套统一的发布内容。",
      duration: "0:18",
      cover: "/community/weekly-highlights.jpg",
      prompt:
        "为一款新产品制作完整发布视觉套件，包括主视觉、分镜、短片和发布清单。",
    },
    {
      category: "特效包装",
      title: "手绘实拍融合短片",
      description: "让实拍空间与手绘线条发生接触、变形和节奏化响应。",
      duration: "0:16",
      cover: "/community/memory-video(1).jpg",
      prompt:
        "制作一条实拍与手绘线条融合的16秒创意短片，先给出视觉锚点和镜头方案。",
    },
    {
      category: "MV",
      title: "复古拼贴音乐 MV",
      description: "用纸张纹理、海报墙与卡点剪辑建立完整的音乐视觉系统。",
      duration: "0:24",
      cover: "/community/daily-album.jpg",
      prompt:
        "根据音乐结构制作复古拼贴MV，保持角色一致，并输出镜头、字幕和剪辑节奏。",
    },
    {
      category: "UI动效",
      title: "数字产品电影感演示",
      description: "把真实界面、交互路径和动效参考组织成可审片的产品宣传片。",
      duration: "0:20",
      cover: "/community/web-summary.jpg",
      prompt:
        "把数字产品界面制作成电影感宣传片，准确展示交互、运镜、节奏和声音。",
    },
    {
      category: "红人带货",
      title: "美妆达人种草短片",
      description: "围绕真实使用过程组织口播、产品特写、字幕与转化节奏。",
      duration: "0:15",
      cover: "/community/food-delivery(1).jpg",
      prompt:
        "制作一条15秒美妆达人种草短片，强调真实体验、产品细节与行动引导。",
    },
    {
      category: "影视片头",
      title: "未来档案电影片头",
      description: "以档案排版、扫描纹理和空间镜头建立克制的悬疑开场。",
      duration: "0:22",
      cover: "/community/study-paper(1).jpg",
      prompt: "制作一段未来档案风格电影片头，输出字体、镜头、转场和声音设计。",
    },
    {
      category: "二次元PV",
      title: "角色觉醒动画 PV",
      description: "围绕角色能力与情绪转折建立统一的分镜、色彩和音乐动机。",
      duration: "0:18",
      cover: "/community/game-guide(1).jpg",
      prompt: "为原创动画角色制作18秒觉醒PV，保持角色一致并给出完整镜头节奏。",
    },
    {
      category: "品牌广告",
      title: "便携科技产品短广告",
      description: "用极简空间、结构拆解和生活场景呈现产品的核心卖点。",
      duration: "0:16",
      cover: "/community/smart-home.jpg",
      prompt:
        "制作一条便携科技产品短广告，包含主视觉、结构特写、使用场景和收尾。",
    },
    {
      category: "官方 Skill",
      title: "角色一致性分镜",
      description: "锁定人物外观、服装与空间关系，生成可继续制作的连续镜头。",
      duration: "0:20",
      cover: "/community/mock-interview.jpg",
      prompt: "根据角色设定生成一组角色外观和空间关系一致的连续电影分镜。",
    },
    {
      category: "特效包装",
      title: "舞者轨迹视觉包装",
      description: "让几何框线、粒子与排版跟随舞者动作和节拍响应。",
      duration: "0:19",
      cover: "/community/voice-reply.jpg",
      prompt:
        "为一段舞蹈视频设计动作追踪视觉包装，包括轨迹、排版、粒子与节奏。",
    },
    {
      category: "品牌广告",
      title: "城市旅行品牌短片",
      description: "把地点、人物和路线组织成具有品牌识别度的旅行叙事。",
      duration: "0:24",
      cover: "/community/travel-plan(1).jpg",
      prompt: "制作一条城市旅行品牌短片，规划地点、人物、路线、镜头和声音。",
    },
    {
      category: "UI动效",
      title: "智能工作台交互演示",
      description: "准确展示拖拽、编排、协作和结果预览的完整界面路径。",
      duration: "0:17",
      cover: "/community/todo.jpg",
      prompt: "将智能工作台的拖拽、编排、协作和结果预览制作成17秒UI动效演示。",
    },
  ];
  const categories = [
    "精选",
    "官方 Skill",
    "特效包装",
    "红人带货",
    "影视片头",
    "MV",
    "二次元PV",
    "品牌广告",
    "UI动效",
  ];
  const visible =
    category === "精选"
      ? showcases
      : showcases.filter((item) => item.category === category);
  const preview = previewTitle
    ? (showcases.find((item) => item.title === previewTitle) ?? null)
    : null;

  const submit = () => {
    const value = prompt.trim();
    if (value) {
      const labels = modelItems
        .filter((item) => enabledModels.has(item.id))
        .map((item) => item.name);
      onStart(value, labels);
    }
  };

  return (
    <div className="relative h-full overflow-y-auto bg-[#fafafa] dark:bg-[#0a0a0a]">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-45"
        style={{
          backgroundImage:
            "radial-gradient(circle, color-mix(in oklch, var(--foreground) 14%, transparent) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      />
      <div className="relative mx-auto w-full max-w-[920px] px-8 pb-14 pt-16">
        <div className="text-center">
          <button
            type="button"
            className="mb-7 inline-flex h-8 items-center gap-2 rounded-full border border-black/[0.07] bg-background/90 px-3 text-[10px] text-muted-foreground shadow-sm backdrop-blur-sm hover:text-foreground"
            onClick={() =>
              setPrompt("体验 Echo 多模态创作基座，生成一套完整作品。")
            }
          >
            <SparklesIcon className="size-3 text-violet-500" />
            Echo 创作基座已就绪
            <ArrowRightIcon className="size-3" />
          </button>
          <br />
          <div className="inline-flex items-center gap-2.5">
            <span className="grid size-11 place-items-center rounded-[13px] bg-[#111] text-white shadow-sm dark:bg-white dark:text-black">
              <WandSparklesIcon className="size-5" />
            </span>
            <h1 className="text-[34px] font-semibold tracking-[-0.045em]">
              Echo Design
            </h1>
          </div>
          <p className="mt-2 text-[15px] text-muted-foreground">
            属于你的多模态 Agent 团队
          </p>
        </div>

        <div className="relative mx-auto mt-10 max-w-[760px] rounded-[24px] border border-black/[0.08] bg-background shadow-[0_12px_34px_rgba(0,0,0,.08)] dark:border-white/10">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              if (event.target.files?.length) onAddFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <div className="flex items-center gap-1 px-5 pt-4 text-[10px] text-muted-foreground">
            <span>描述你要生成的内容，或查看</span>
            <button
              type="button"
              className="border-b border-muted-foreground/40 hover:text-foreground"
              onClick={() =>
                toast.info("从目标、素材、风格和交付规格开始描述即可")
              }
            >
              Design 使用指南
            </button>
            <span>·</span>
            <button
              type="button"
              className="border-b border-muted-foreground/40 hover:text-foreground"
              onClick={() =>
                toast.info("可在模型设置中配置图像、视频和语音模型")
              }
            >
              模型使用指南
            </button>
          </div>
          <Textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                submit();
              }
            }}
            className="min-h-[82px] resize-none border-0 bg-transparent px-5 pt-3 text-[14px] shadow-none focus-visible:ring-0"
            placeholder=""
          />
          <div className="flex h-[52px] items-center gap-1.5 px-3 pb-3">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-full"
              onClick={() => fileInputRef.current?.click()}
              aria-label="添加文件"
            >
              <PlusIcon className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 rounded-lg text-[11px]"
              onClick={() => {
                setModelOpen((value) => !value);
              }}
              aria-expanded={modelOpen}
            >
              <BotIcon className="size-3.5" /> 模型
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 rounded-lg text-[11px]"
              onClick={onOpenSkills}
            >
              <PuzzleIcon className="size-3.5" /> Skill
            </Button>
            <span className="flex-1" />
            <Button
              size="icon"
              className="size-9 rounded-full"
              disabled={!prompt.trim()}
              onClick={submit}
              aria-label="开始创作"
            >
              <SendIcon className="size-4" />
            </Button>
          </div>
          {modelOpen ? (
            <div className="absolute left-0 top-[calc(100%+8px)] z-50 w-[330px] overflow-hidden rounded-[16px] border border-border-default bg-background text-left shadow-[0_16px_40px_rgba(0,0,0,.16)]">
              <div className="flex h-10 items-center gap-1 px-3">
                <span className="text-[11px] font-semibold">模型</span>
                <CircleHelpIcon
                  className="size-3 text-muted-foreground"
                  aria-label="勾选后，Agent 可在任务中调用这些能力"
                />
                <span className="flex-1" />
                <span className="text-[10px] text-muted-foreground">全选</span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={allModelsEnabled}
                  onClick={() =>
                    setEnabledModels(
                      allModelsEnabled ? new Set() : new Set(allModelIds),
                    )
                  }
                  className={cn(
                    "relative h-5 w-9 rounded-full transition",
                    allModelsEnabled ? "bg-violet-600" : "bg-muted",
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-0.5 size-4 rounded-full bg-white shadow-sm transition-all",
                      allModelsEnabled ? "left-[18px]" : "left-0.5",
                    )}
                  />
                </button>
              </div>
              <div className="mx-2 grid grid-cols-4 rounded-[9px] bg-muted/70 p-0.5">
                {(
                  [
                    ["agent", "Agent"],
                    ["image", "图片"],
                    ["video", "视频"],
                    ["audio", "音频"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setModelTab(id)}
                    className={cn(
                      "h-7 rounded-[7px] text-[10px] text-muted-foreground",
                      modelTab === id &&
                        "bg-background font-medium text-foreground shadow-sm",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="max-h-[300px] overflow-y-auto p-2">
                {(modelsLoading || agentsLoading) && modelTab === "agent" ? (
                  <div className="grid h-24 place-items-center">
                    <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
                  </div>
                ) : visibleModelItems.length ? (
                  visibleModelItems.map((item) => {
                    const selected = enabledModels.has(item.id);
                    return (
                      <button
                        key={item.id}
                        type="button"
                        role="switch"
                        aria-checked={selected}
                        onClick={() =>
                          setEnabledModels((current) => {
                            const next = new Set(current);
                            if (next.has(item.id)) next.delete(item.id);
                            else next.add(item.id);
                            return next;
                          })
                        }
                        className="flex w-full items-center gap-2.5 rounded-[10px] px-2.5 py-2 text-left hover:bg-muted/65"
                      >
                        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-muted text-[9px] font-semibold">
                          {item.name.slice(0, 1).toUpperCase()}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1.5 truncate text-[11px] font-medium">
                            {item.name}
                            <span className="rounded bg-violet-100 px-1 py-px text-[8px] text-violet-700 dark:bg-violet-950 dark:text-violet-300">
                              {item.badge}
                            </span>
                          </span>
                          <span className="mt-0.5 block truncate text-[9px] text-muted-foreground">
                            {item.detail}
                          </span>
                        </span>
                        {selected ? (
                          <CheckIcon className="size-3.5 shrink-0" />
                        ) : null}
                      </button>
                    );
                  })
                ) : (
                  <div className="p-8 text-center text-[10px] text-muted-foreground">
                    当前没有可用能力
                  </div>
                )}
              </div>
              <p className="border-t border-border-subtle px-3 py-2 text-[9px] leading-4 text-muted-foreground">
                勾选后，Agent 可在任务中调用这些模型与本地能力。
              </p>
            </div>
          ) : null}
        </div>

        <div className="relative mx-auto mt-2 flex w-full max-w-[730px] items-center rounded-b-[15px] bg-black/[0.035] px-3 dark:bg-white/[0.05]">
          <CreativeProjectSelector
            personaId={personaId}
            projects={projects}
            currentProjectId={currentProjectId}
            onSelect={onSelectProject}
            className="w-full"
          />
        </div>

        <div className="mt-7 flex flex-wrap items-center justify-center gap-2">
          {categories.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setCategory(item)}
              className={cn(
                "h-8 rounded-full border border-black/[0.07] bg-background px-4 text-[11px] text-muted-foreground transition",
                category === item &&
                  "border-foreground bg-foreground font-medium text-background",
              )}
            >
              {item}
            </button>
          ))}
        </div>

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((item) => (
            <article
              key={item.title}
              className="group overflow-hidden rounded-[14px] border border-black/[0.07] bg-background text-left shadow-[0_2px_5px_rgba(0,0,0,.04)] transition hover:-translate-y-0.5 hover:shadow-[0_8px_24px_rgba(0,0,0,.10)] dark:border-white/10"
            >
              <div className="relative h-[148px] overflow-hidden bg-muted">
                <img
                  src={item.cover}
                  alt=""
                  className="size-full object-cover transition duration-300 group-hover:scale-[1.025]"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/35 via-transparent to-transparent" />
                <span className="absolute bottom-2 left-2 rounded bg-black/65 px-1.5 py-0.5 text-[9px] text-white">
                  {item.duration}
                </span>
                <div className="absolute bottom-2 right-2 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                  <button
                    type="button"
                    onClick={() => setPrompt(item.prompt)}
                    className="rounded-md bg-black/65 px-2 py-1 text-[8px] text-white backdrop-blur-sm"
                  >
                    使用提示词
                  </button>
                  <button
                    type="button"
                    onClick={() => setPreviewTitle(item.title)}
                    aria-label={`预览 ${item.title}`}
                    className="grid size-6 place-items-center rounded-md bg-black/65 text-white backdrop-blur-sm"
                  >
                    <Maximize2Icon className="size-3" />
                  </button>
                </div>
              </div>
              <div className="p-3.5">
                <div className="truncate text-[12px] font-semibold">
                  {item.title}
                </div>
                <p className="mt-1.5 line-clamp-2 text-[10px] leading-[16px] text-muted-foreground">
                  {item.description}
                </p>
                <div className="mt-3 text-[9px] text-muted-foreground">
                  Echo Design
                </div>
              </div>
            </article>
          ))}
        </div>
      </div>
      <Dialog
        open={Boolean(preview)}
        onOpenChange={(open) => {
          if (!open) setPreviewTitle(null);
        }}
      >
        <DialogContent className="overflow-hidden p-0 sm:max-w-[820px]">
          {preview ? (
            <>
              <div className="aspect-video bg-black">
                <img
                  src={preview.cover}
                  alt={preview.title}
                  className="size-full object-contain"
                />
              </div>
              <div className="px-5 py-4 pr-12">
                <DialogTitle className="text-[15px]">
                  {preview.title}
                </DialogTitle>
                <DialogDescription className="mt-1 text-[11px] leading-5">
                  {preview.description}
                </DialogDescription>
                <Button
                  className="mt-4 rounded-lg"
                  size="sm"
                  onClick={() => {
                    setPrompt(preview.prompt);
                    setPreviewTitle(null);
                  }}
                >
                  使用提示词
                </Button>
              </div>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

const ASSET_DISPLAY_BATCH_SIZE = 24;

function AssetsView({
  personaId,
  projectId,
  onUseArtifact,
}: {
  personaId: string;
  projectId: string | null;
  onUseArtifact: (artifact: ProjectArtifact) => void;
}) {
  const [grid, setGrid] = useState(true);
  const [category, setCategory] = useState("所有类型");
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(ASSET_DISPLAY_BATCH_SIZE);
  const [artifacts, setArtifacts] = useState<ProjectArtifact[]>([]);
  const [libraryAssets, setLibraryAssets] = useState<DesignLibraryAsset[]>([]);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [assetDialogOpen, setAssetDialogOpen] = useState(false);
  const [assetSaving, setAssetSaving] = useState(false);
  const [assetPackImporting, setAssetPackImporting] = useState(false);
  const [assetSort, setAssetSort] = useState<"recent" | "name">("recent");
  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [assetName, setAssetName] = useState("");
  const [assetCategory, setAssetCategory] =
    useState<DesignLibraryAsset["category"]>("角色");
  const [assetDescription, setAssetDescription] = useState("");
  const [assetTags, setAssetTags] = useState("");
  const assetInputRef = useRef<HTMLInputElement>(null);
  const assetPackInputRef = useRef<HTMLInputElement>(null);
  const categories = [
    "所有类型",
    "项目产物",
    "角色",
    "场景",
    "风格包",
    "道具",
    "自定义",
  ];
  useEffect(() => {
    const controller = new AbortController();
    void fetch(
      `${getBackendBaseURL()}/api/design/assets?persona_id=${encodeURIComponent(personaId)}`,
      {
        headers: authHeaders(),
        signal: controller.signal,
      },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`design asset library failed: ${response.status}`);
        return (await response.json()) as { items?: DesignLibraryAsset[] };
      })
      .then((payload) => setLibraryAssets(payload.items ?? []))
      .catch((error: unknown) => {
        if ((error as { name?: string }).name !== "AbortError")
          setLibraryAssets([]);
      });
    return () => controller.abort();
  }, [personaId]);
  useEffect(() => {
    if (!projectId) {
      setArtifacts([]);
      return;
    }
    const controller = new AbortController();
    setArtifactsLoading(true);
    void fetch(
      `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}`,
      { headers: authHeaders(), signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`project assets failed: ${response.status}`);
        return (await response.json()) as { artifacts?: ProjectArtifact[] };
      })
      .then((payload) => setArtifacts(payload.artifacts ?? []))
      .catch((error: unknown) => {
        if ((error as { name?: string }).name !== "AbortError")
          setArtifacts([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setArtifactsLoading(false);
      });
    return () => controller.abort();
  }, [projectId]);
  const needle = query.trim().toLowerCase();
  const visibleProjectArtifacts =
    category === "所有类型" || category === "项目产物"
      ? artifacts.filter((artifact) =>
          `${artifact.name} ${artifact.kind ?? ""} ${artifact.summary ?? ""}`
            .toLowerCase()
            .includes(needle),
        )
      : [];
  const visibleLibraryAssets = libraryAssets.filter(
    (asset) =>
      (category === "所有类型" || category === asset.category) &&
      `${asset.name} ${asset.category} ${asset.description ?? ""} ${(asset.tags ?? []).join(" ")}`
        .toLowerCase()
        .includes(needle),
  );
  const visibleArtifacts: ProjectArtifact[] = [
    ...visibleProjectArtifacts,
    ...visibleLibraryAssets.map((asset) => ({
      ...asset,
      summary: asset.description || asset.tags?.join(" · ") || asset.filename,
    })),
  ].sort((left, right) =>
    assetSort === "name" ? left.name.localeCompare(right.name, "zh-CN") : 0,
  );
  const displayedArtifacts = visibleArtifacts.slice(0, visibleCount);
  const hasMoreAssets = displayedArtifacts.length < visibleArtifacts.length;

  useEffect(() => {
    setVisibleCount(ASSET_DISPLAY_BATCH_SIZE);
  }, [assetSort, category, query]);
  const createLibraryAsset = async () => {
    if (!assetFile || !assetName.trim()) return;
    const body = new FormData();
    body.append("file", assetFile);
    body.append("name", assetName.trim());
    body.append("category", assetCategory);
    body.append("description", assetDescription.trim());
    body.append("tags", assetTags.trim());
    body.append("persona_id", personaId);
    setAssetSaving(true);
    try {
      const response = await fetch(`${getBackendBaseURL()}/api/design/assets`, {
        method: "POST",
        headers: authHeaders(),
        body,
      });
      const payload = (await response.json()) as { item?: DesignLibraryAsset };
      if (!response.ok || !payload.item)
        throw new Error(`design asset create failed: ${response.status}`);
      setLibraryAssets((current) => [
        payload.item!,
        ...current.filter((item) => item.id !== payload.item!.id),
      ]);
      setAssetDialogOpen(false);
      setAssetFile(null);
      setAssetName("");
      setAssetDescription("");
      setAssetTags("");
      toast.success("资产已保存，可在其他项目复用");
    } catch {
      toast.error("资产保存失败，请检查文件大小或登录状态");
    } finally {
      setAssetSaving(false);
    }
  };
  const importAssetPack = async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    body.append("persona_id", personaId);
    setAssetPackImporting(true);
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/assets/import-pack`,
        { method: "POST", headers: authHeaders(), body },
      );
      const payload = (await response.json()) as {
        items?: DesignLibraryAsset[];
        detail?: string;
      };
      if (!response.ok || !payload.items?.length)
        throw new Error(payload.detail || "asset pack import failed");
      const imported = payload.items;
      setLibraryAssets((current) => [
        ...imported,
        ...current.filter(
          (item) => !imported.some((created) => created.id === item.id),
        ),
      ]);
      toast.success(`已导入 ${imported.length} 个资产`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "资产包导入失败");
    } finally {
      setAssetPackImporting(false);
    }
  };
  return (
    <>
      <div className="h-full overflow-y-auto bg-background px-11 py-9">
        <div className="mx-auto max-w-5xl">
          <h1 className="text-2xl font-semibold tracking-tight">资产中心</h1>
          <p className="mt-1.5 text-xs text-muted-foreground">
            这里只展示当前角色主动添加、导入或从画布保存的资产，不会与其他角色自动共享
          </p>
          <div className="mt-7 flex flex-wrap gap-2">
            <input
              ref={assetPackInputRef}
              type="file"
              accept="application/zip,.zip,.echo-assets"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void importAssetPack(file);
                event.target.value = "";
              }}
            />
            <Button
              className="rounded-xl"
              onClick={() => setAssetDialogOpen(true)}
            >
              <PlusIcon className="mr-1.5 size-4" />
              添加资产
            </Button>
            <Button
              variant="outline"
              className="rounded-xl"
              onClick={() => assetPackInputRef.current?.click()}
              disabled={assetPackImporting}
            >
              {assetPackImporting ? (
                <Loader2Icon className="mr-1.5 size-4 animate-spin" />
              ) : (
                <ArchiveIcon className="mr-1.5 size-4" />
              )}
              导入资产包
            </Button>
          </div>
          <div className="mt-8 flex items-center border-t border-border-subtle pt-4">
            <div className="flex gap-1 text-xs">
              {categories.map((item) => (
                <button
                  key={item}
                  onClick={() => setCategory(item)}
                  className={cn(
                    "rounded-lg px-3 py-2",
                    category === item
                      ? "bg-muted font-medium"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {item}
                </button>
              ))}
            </div>
            <span className="flex-1" />
            <div className="relative w-60">
              <SearchIcon className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="h-9 rounded-xl pl-9 text-xs"
                placeholder="搜索资产"
              />
            </div>
            <select
              value={assetSort}
              onChange={(event) =>
                setAssetSort(event.target.value as "recent" | "name")
              }
              className="ml-2 h-8 rounded-lg border border-border-default bg-background px-2 text-[10px] text-muted-foreground outline-none"
              aria-label="排序方式"
            >
              <option value="recent">最近添加</option>
              <option value="name">按名称</option>
            </select>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setGrid(true)}
              className={cn("ml-2 size-8", grid && "bg-muted")}
            >
              <Grid2X2Icon className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setGrid(false)}
              className={cn("size-8", !grid && "bg-muted")}
            >
              <ListIcon className="size-4" />
            </Button>
          </div>
          {artifactsLoading ? (
            <div className="mt-6 flex items-center gap-2 text-[11px] text-muted-foreground">
              <Loader2Icon className="size-3.5 animate-spin" />
              正在读取项目产物
            </div>
          ) : null}
          {visibleArtifacts.length > 0 ? (
            <>
              <div className="mt-6 flex items-center gap-2">
                <h2 className="text-[13px] font-semibold">资产</h2>
                <span className="text-[10px] text-muted-foreground">
                  {visibleArtifacts.length}
                </span>
              </div>
              <div
                className={cn(
                  "mt-3",
                  grid
                    ? "grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4"
                    : "flex flex-col gap-2",
                )}
              >
                {displayedArtifacts.map((artifact) => {
                  const kind = artifactNodeKind(artifact);
                  const previewUrl = artifact.url
                    ? artifact.url.startsWith("/")
                      ? `${getBackendBaseURL()}${artifact.url}`
                      : artifact.url
                    : null;
                  return (
                    <article
                      key={artifact.id}
                      className={cn(
                        "group overflow-hidden rounded-[12px] border border-border-default bg-background transition hover:border-sky-300 hover:shadow-[0_14px_35px_-20px_rgba(0,0,0,.3)]",
                        !grid && "flex items-center p-2.5",
                      )}
                    >
                      <div
                        className={cn(
                          "relative grid shrink-0 place-items-center overflow-hidden bg-gradient-to-br from-sky-100 via-cyan-50 to-background",
                          grid ? "h-32 w-full" : "size-12 rounded-lg",
                        )}
                      >
                        {kind === "image" && previewUrl ? (
                          <img
                            src={previewUrl}
                            alt=""
                            className="size-full object-cover"
                          />
                        ) : kind === "video" ? (
                          <CirclePlayIcon
                            className={cn(grid ? "size-10" : "size-5")}
                          />
                        ) : kind === "image" ? (
                          <ImageIcon
                            className={cn(grid ? "size-10" : "size-5")}
                          />
                        ) : (
                          <ArchiveIcon
                            className={cn(grid ? "size-10" : "size-5")}
                          />
                        )}
                        {grid ? (
                          <span className="absolute left-2.5 top-2.5 rounded-md bg-black/65 px-1.5 py-0.5 text-[8px] font-medium text-white">
                            {artifact.category || KIND_STYLE[kind].label}
                          </span>
                        ) : null}
                      </div>
                      <div
                        className={cn("min-w-0 flex-1", grid ? "p-3" : "ml-3")}
                      >
                        <div className="truncate text-[12px] font-semibold">
                          {artifact.name}
                        </div>
                        <p className="mt-1 line-clamp-2 min-h-8 text-[10px] leading-4 text-muted-foreground">
                          {artifact.summary ||
                            artifact.path ||
                            artifact.kind ||
                            "项目交付产物"}
                        </p>
                        <div className="mt-2 flex items-center text-[9px] text-muted-foreground">
                          <span>
                            {artifact.category ||
                              (artifact.milestone_id
                                ? "里程碑产物"
                                : "项目产物")}
                          </span>
                          <button
                            onClick={() => onUseArtifact(artifact)}
                            className="ml-auto rounded-md bg-foreground px-2 py-1 text-background opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
                          >
                            加入画布
                          </button>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          ) : null}
          {visibleArtifacts.length === 0 && !artifactsLoading ? (
            <div className="grid min-h-[420px] place-items-center text-center">
              <div>
                <ArchiveIcon className="mx-auto size-7 text-muted-foreground/50" />
                <p className="mt-4 text-sm font-semibold">
                  {query
                    ? "没有匹配的资产"
                    : category === "所有类型"
                      ? "还没有资产"
                      : `还没有${category}资产`}
                </p>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  点击上方添加资产，或从画布节点保存为可复用资产
                </p>
              </div>
            </div>
          ) : null}
          {hasMoreAssets ? (
            <div className="mt-6 flex justify-center">
              <Button
                type="button"
                variant="outline"
                className="rounded-xl"
                onClick={() =>
                  setVisibleCount((count) => count + ASSET_DISPLAY_BATCH_SIZE)
                }
              >
                加载更多
                <span className="ml-1.5 text-xs text-muted-foreground">
                  {Math.min(visibleCount, visibleArtifacts.length)}/
                  {visibleArtifacts.length}
                </span>
              </Button>
            </div>
          ) : null}
        </div>
      </div>
      <Dialog open={assetDialogOpen} onOpenChange={setAssetDialogOpen}>
        <DialogContent className="gap-0 overflow-hidden p-0 sm:max-w-[500px]">
          <DialogHeader className="border-b border-border-subtle px-5 py-4 pr-12">
            <DialogTitle className="text-[15px]">添加资产</DialogTitle>
            <DialogDescription className="text-[11px] leading-5">
              填写清晰的名称、描述和标签，让 Agent 能搜索并在不同项目中复用。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 px-5 py-4">
            <div className="grid grid-cols-[1fr_120px] gap-2">
              <Input
                value={assetName}
                onChange={(event) => setAssetName(event.target.value)}
                placeholder="资产名称"
                className="h-9 rounded-lg text-xs"
                maxLength={120}
              />
              <select
                value={assetCategory}
                onChange={(event) =>
                  setAssetCategory(
                    event.target.value as DesignLibraryAsset["category"],
                  )
                }
                className="h-9 rounded-lg border border-border-default bg-background px-3 text-xs outline-none focus:ring-2 focus:ring-ring"
              >
                {["角色", "场景", "风格包", "道具", "自定义"].map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </div>
            <Textarea
              value={assetDescription}
              onChange={(event) => setAssetDescription(event.target.value)}
              placeholder="输入清晰的描述，帮助 Agent 更好地搜索和复用…"
              className="min-h-20 resize-none rounded-lg text-xs leading-5"
              maxLength={1200}
            />
            <input
              ref={assetInputRef}
              type="file"
              hidden
              accept="image/*,video/*,audio/*,.pdf,.txt,.md,.csv,.xls,.xlsx,.ppt,.pptx,.doc,.docx"
              onChange={(event) => {
                const selected = event.target.files?.[0] ?? null;
                setAssetFile(selected);
                if (selected && !assetName.trim())
                  setAssetName(selected.name.replace(/\.[^.]+$/, ""));
                event.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => assetInputRef.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const selected = event.dataTransfer.files?.[0] ?? null;
                setAssetFile(selected);
                if (selected && !assetName.trim())
                  setAssetName(selected.name.replace(/\.[^.]+$/, ""));
              }}
              className="grid min-h-36 w-full place-items-center rounded-xl border border-dashed border-border-default bg-muted/20 text-center transition hover:border-foreground/40 hover:bg-muted/35"
            >
              <span>
                <ArchiveIcon className="mx-auto size-5 text-muted-foreground" />
                <span className="mt-2 block text-[11px] font-medium">
                  {assetFile ? assetFile.name : "将素材文件拖入 / 点击上传"}
                </span>
                <span className="mt-1 block text-[9px] text-muted-foreground">
                  单个文件最大 64 MB
                </span>
              </span>
            </button>
            <Input
              value={assetTags}
              onChange={(event) => setAssetTags(event.target.value)}
              placeholder="添加标签（可选），用逗号分隔"
              className="h-9 rounded-lg text-xs"
              maxLength={600}
            />
          </div>
          <DialogFooter className="border-t border-border-subtle px-5 py-3">
            <Button
              size="sm"
              disabled={!assetFile || !assetName.trim() || assetSaving}
              onClick={() => void createLibraryAsset()}
              className="rounded-lg px-4"
            >
              {assetSaving ? (
                <Loader2Icon className="mr-1.5 size-3.5 animate-spin" />
              ) : null}
              创建资产
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function CanvasAssetsPanel({
  personaId,
  projectId,
  document,
  onClose,
  onPick,
}: {
  personaId: string;
  projectId: string | null;
  document: DesignCanvasDocument;
  onClose: () => void;
  onPick: (artifact: ProjectArtifact) => void;
}) {
  const [tab, setTab] = useState<"canvas" | "assets">("canvas");
  const [grid, setGrid] = useState(false);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [projectArtifacts, setProjectArtifacts] = useState<ProjectArtifact[]>(
    [],
  );
  const [libraryAssets, setLibraryAssets] = useState<DesignLibraryAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [libraryResponse, projectResponse] = await Promise.all([
        fetch(
          `${getBackendBaseURL()}/api/design/assets?persona_id=${encodeURIComponent(personaId)}`,
          { headers: authHeaders() },
        ),
        projectId
          ? fetch(
              `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}`,
              {
                headers: authHeaders(),
              },
            )
          : Promise.resolve(null),
      ]);
      if (libraryResponse.ok) {
        const payload = (await libraryResponse.json()) as {
          items?: DesignLibraryAsset[];
        };
        setLibraryAssets(payload.items ?? []);
      }
      if (projectResponse?.ok) {
        const payload = (await projectResponse.json()) as {
          artifacts?: ProjectArtifact[];
        };
        setProjectArtifacts(payload.artifacts ?? []);
      } else if (!projectId) {
        setProjectArtifacts([]);
      }
    } finally {
      setLoading(false);
    }
  }, [personaId, projectId]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const canvasAssets = useMemo(() => {
    const byId = new Map<string, ProjectArtifact>();
    for (const artifact of projectArtifacts) byId.set(artifact.id, artifact);
    for (const node of document.nodes) {
      if (!node.asset) continue;
      byId.set(node.asset.id, {
        id: node.asset.id,
        name: node.title,
        kind: node.asset.kind,
        path: node.asset.path,
        url: node.asset.url,
        summary: node.description,
      });
    }
    return [...byId.values()];
  }, [document.nodes, projectArtifacts]);
  const source: ProjectArtifact[] =
    tab === "canvas" ? canvasAssets : libraryAssets;
  const needle = query.trim().toLowerCase();
  const items = source.filter((item) => {
    const kind = artifactNodeKind(item);
    return (
      (typeFilter === "all" || kind === typeFilter) &&
      `${item.name} ${item.summary ?? ""} ${item.path ?? ""} ${item.category ?? ""}`
        .toLowerCase()
        .includes(needle)
    );
  });
  const uploadProjectFiles = async (files: FileList) => {
    if (!projectId) {
      toast.info("当前画布尚未绑定项目");
      return;
    }
    const body = new FormData();
    Array.from(files)
      .slice(0, 12)
      .forEach((file) => body.append("files", file));
    setLoading(true);
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/assets`,
        { method: "POST", headers: authHeaders(), body },
      );
      if (!response.ok) throw new Error("project upload failed");
      const payload = (await response.json()) as { items?: ProjectArtifact[] };
      setProjectArtifacts((current) => [
        ...(payload.items ?? []),
        ...current.filter(
          (item) =>
            !(payload.items ?? []).some((added) => added.id === item.id),
        ),
      ]);
      toast.success(`已上传 ${(payload.items ?? []).length} 个项目文件`);
    } catch {
      toast.error("项目文件上传失败");
    } finally {
      setLoading(false);
    }
  };
  return (
    <aside className="flex w-[280px] shrink-0 flex-col border-l border-border-subtle bg-background">
      <div className="flex h-11 items-center border-b border-border-subtle px-2">
        <div className="grid flex-1 grid-cols-2 rounded-lg bg-muted/60 p-0.5 text-[11px]">
          {(["canvas", "assets"] as const).map((item) => (
            <button
              key={item}
              onClick={() => setTab(item)}
              className={cn(
                "rounded-md px-2 py-1.5",
                tab === item && "bg-background font-semibold shadow-sm",
              )}
            >
              {item === "canvas" ? "画布" : "资产"}
            </button>
          ))}
        </div>
        <button
          onClick={onClose}
          className="ml-2 grid size-7 place-items-center rounded-lg hover:bg-muted"
          aria-label="关闭项目资产"
        >
          <XIcon className="size-3.5" />
        </button>
      </div>
      <div className="flex items-center gap-1 border-b border-border-subtle px-2 py-2">
        <button
          onClick={() => setGrid(false)}
          className={cn(
            "grid size-7 place-items-center rounded-md",
            !grid && "bg-muted",
          )}
          title="树形视图"
        >
          <ListIcon className="size-3.5" />
        </button>
        <button
          onClick={() => setGrid(true)}
          className={cn(
            "grid size-7 place-items-center rounded-md",
            grid && "bg-muted",
          )}
          title="网格视图"
        >
          <Grid2X2Icon className="size-3.5" />
        </button>
        <span className="flex-1" />
        <button
          onClick={() => void refresh()}
          className="grid size-7 place-items-center rounded-md hover:bg-muted"
          title="刷新文件列表"
        >
          <Redo2Icon className={cn("size-3.5", loading && "animate-spin")} />
        </button>
      </div>
      <div className="space-y-2 px-2 py-2">
        <div className="relative">
          <SearchIcon className="absolute left-2.5 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文件"
            className="h-8 rounded-lg pl-8 text-[10px]"
          />
        </div>
        <div className="grid grid-cols-3 gap-1">
          <select
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
            className="h-7 rounded-md border border-border-default bg-background px-1.5 text-[9px]"
          >
            <option value="all">类型</option>
            <option value="image">图片</option>
            <option value="video">视频</option>
            <option value="audio">音频</option>
            <option value="file">文件</option>
          </select>
          <button className="h-7 rounded-md border border-border-default text-[9px] text-muted-foreground">
            标签
          </button>
          <button className="h-7 rounded-md border border-border-default text-[9px] text-muted-foreground">
            时间
          </button>
        </div>
      </div>
      <div
        className={cn(
          "min-h-0 flex-1 overflow-y-auto px-2 pb-2",
          grid ? "grid auto-rows-max grid-cols-2 gap-2" : "space-y-0.5",
        )}
      >
        {items.map((item) => {
          const kind = artifactNodeKind(item);
          return (
            <button
              key={item.id}
              onClick={() => onPick(item)}
              className={cn(
                "group text-left hover:bg-muted",
                grid
                  ? "overflow-hidden rounded-lg border border-border-subtle"
                  : "flex w-full items-center gap-2 rounded-md px-2 py-1.5",
              )}
              title="在画布中定位"
            >
              <div
                className={cn(
                  "grid shrink-0 place-items-center bg-muted",
                  grid ? "h-16 w-full" : "size-6 rounded",
                )}
              >
                {kind === "image" ? (
                  <ImageIcon className="size-3.5" />
                ) : kind === "video" ? (
                  <CirclePlayIcon className="size-3.5" />
                ) : (
                  <ArchiveIcon className="size-3.5" />
                )}
              </div>
              <span className={cn("min-w-0", grid && "block p-2")}>
                <span className="block truncate text-[10px] font-medium">
                  {item.name}
                </span>
                <span className="block truncate text-[8px] text-muted-foreground">
                  {item.category || item.kind || "文件"}
                </span>
              </span>
            </button>
          );
        })}
        {!loading && items.length === 0 ? (
          <div className="col-span-2 px-3 py-12 text-center text-[10px] text-muted-foreground">
            {query
              ? "没有匹配文件"
              : tab === "canvas"
                ? "画布还没有项目文件"
                : "资产库为空"}
          </div>
        ) : null}
      </div>
      {tab === "canvas" ? (
        <div className="border-t border-border-subtle p-2">
          <input
            ref={uploadRef}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              if (event.target.files?.length)
                void uploadProjectFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-full justify-center text-[10px]"
            onClick={() => uploadRef.current?.click()}
            disabled={!projectId}
          >
            <PlusIcon className="mr-1 size-3.5" />
            上传文件
          </Button>
        </div>
      ) : null}
    </aside>
  );
}

function SkillsView({
  onUse,
  installedSkills,
  loading,
}: {
  onUse: (id: string) => void;
  installedSkills: SkillInfo[];
  loading: boolean;
}) {
  const navigate = useNavigate();
  const enableSkill = useEnableSkill();
  const enableMarketSkill = useEnableMarketSkill();
  const streamdownPlugins = useStreamdownPlugins();
  const [category, setCategory] = useState("全部");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"market" | "mine">("market");
  const [onlyUninstalled, setOnlyUninstalled] = useState(false);
  const [skillSort, setSkillSort] = useState<"recent" | "popular">("recent");
  const [detailSkillId, setDetailSkillId] = useState<string | null>(null);
  const [detailFiles, setDetailFiles] = useState<
    Array<{ path: string; content: string }>
  >([]);
  const [detailFilePath, setDetailFilePath] = useState("SKILL.md");
  const [expandedSkillDirs, setExpandedSkillDirs] = useState(
    () => new Set(["references"]),
  );
  const [detailLoading, setDetailLoading] = useState(false);
  const categories = [
    "全部",
    "精选",
    "短剧漫剧",
    "专业影视",
    "动画",
    "商业广告",
    "电商",
    "教育",
    "创意实验",
    "音频音乐",
    "平台工具",
  ];
  const needle = query.trim().toLowerCase();
  const installedByName = new Map(
    installedSkills.map((skill) => [skill.name, skill]),
  );
  const featuredSkillIds = new Set(
    CREATIVE_SKILL_COLLECTION.slice(0, 30).map((item) => item.id),
  );
  const downloadScore = (value: string) => {
    if (value === "内置") return Number.MAX_SAFE_INTEGER;
    const score = Number.parseFloat(value);
    return Number.isFinite(score) ? score : 0;
  };
  const items = CREATIVE_SKILL_COLLECTION.filter((item) => {
    const installed = installedByName.get(item.id);
    return (
      (tab === "market" || Boolean(installed)) &&
      (!onlyUninstalled || !installed) &&
      (category === "全部" ||
        (category === "精选" && featuredSkillIds.has(item.id)) ||
        item.category === category) &&
      (!needle ||
        `${item.title} ${item.description} ${item.category}`
          .toLowerCase()
          .includes(needle))
    );
  }).sort((left, right) =>
    skillSort === "popular"
      ? downloadScore(right.downloads) - downloadScore(left.downloads)
      : 0,
  );
  const tieredMarket =
    tab === "market" &&
    category === "全部" &&
    !needle &&
    !onlyUninstalled &&
    skillSort === "recent";
  const detailSkill = detailSkillId
    ? (CREATIVE_SKILL_COLLECTION.find((item) => item.id === detailSkillId) ??
      null)
    : null;
  const detailDirectories = Array.from(
    new Set(
      detailFiles
        .map((file) => file.path.split("/")[0])
        .filter(
          (part): part is string =>
            Boolean(part) &&
            detailFiles.some((file) => file.path.startsWith(`${part}/`)),
        ),
    ),
  );
  const detailRootFiles = detailFiles.filter(
    (file) => !file.path.includes("/"),
  );
  const selectedDetailContent =
    detailFiles.find((file) => file.path === detailFilePath)?.content ?? "";
  const renderedDetailContent = (() => {
    if (
      detailFilePath !== "SKILL.md" ||
      !selectedDetailContent.startsWith("---")
    )
      return selectedDetailContent;
    const match = selectedDetailContent.match(
      /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/,
    );
    if (!match) return selectedDetailContent;
    return `\`\`\`yaml\n${match[1]}\n\`\`\`\n\n${selectedDetailContent.slice(match[0].length)}`;
  })();
  useEffect(() => {
    if (!detailSkillId) {
      setDetailFiles([]);
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    void fetch(
      `${getBackendBaseURL()}/api/design/skills/${encodeURIComponent(detailSkillId)}/files`,
      { headers: authHeaders(), signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`skill preview failed: ${response.status}`);
        return (await response.json()) as {
          items?: Array<{ path: string; content: string }>;
        };
      })
      .then((payload) => {
        const files = payload.items ?? [];
        setDetailFiles(files);
        setDetailFilePath(
          files.some((item) => item.path === "SKILL.md")
            ? "SKILL.md"
            : files[0]?.path || "",
        );
      })
      .catch((error: unknown) => {
        if ((error as { name?: string }).name !== "AbortError") {
          setDetailFiles([]);
          toast.error("Skill 文件读取失败");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [detailSkillId]);
  const ensureSkillEnabled = async (id: string) => {
    const installed = installedByName.get(id);
    if (!installed) {
      try {
        await enableMarketSkill.mutateAsync(id);
        toast.success("Skill 已安装并启用");
      } catch {
        toast.error("Skill 安装失败");
        return false;
      }
    }
    if (installed && !installed.enabled) {
      try {
        await enableSkill.mutateAsync({ skillName: id, enabled: true });
        toast.success("Skill 已启用");
      } catch {
        toast.error("Skill 启用失败");
        return false;
      }
    }
    return true;
  };
  const handleInstallSkill = async (id: string) => {
    await ensureSkillEnabled(id);
  };
  const handleUseSkill = async (id: string) => {
    if (!(await ensureSkillEnabled(id))) return;
    onUse(id);
  };

  return (
    <>
      <div className="h-full overflow-y-auto bg-background px-10 py-8">
        <div className="mx-auto max-w-[1120px]">
          <h1 className="text-[24px] font-semibold tracking-tight">Skill</h1>
          <p className="mt-1 text-[12px] text-muted-foreground">
            发现、安装并管理 Skill，扩展 Echo Design 的创作能力
          </p>
          <div className="mt-7 flex gap-2">
            <Button
              className="h-9 rounded-[10px] bg-foreground px-4 text-[11px] text-background"
              onClick={() =>
                toast.info("可在对话中让 Agent 为当前流程创建 Skill")
              }
            >
              <WandSparklesIcon className="mr-1.5 size-3.5" />
              通过 Echo Design 创建
            </Button>
            <Button
              variant="outline"
              className="h-9 rounded-[10px] px-4 text-[11px]"
              onClick={() => navigate("/workspace/skills")}
            >
              <PlusIcon className="mr-1.5 size-3.5" />
              安装 Skill
            </Button>
          </div>

          <div className="mt-7 border-t border-border-subtle pt-3">
            <div className="flex items-center gap-5">
              <button
                onClick={() => setTab("market")}
                className={cn(
                  "relative h-9 text-[12px] font-medium",
                  tab === "market"
                    ? "text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-foreground"
                    : "text-muted-foreground",
                )}
              >
                Skill
              </button>
              <button
                onClick={() => setTab("mine")}
                className={cn(
                  "relative h-9 text-[12px] font-medium",
                  tab === "mine"
                    ? "text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-foreground"
                    : "text-muted-foreground",
                )}
              >
                我的 Skill
              </button>
              <span className="flex-1" />
              <div className="relative w-60 shrink-0">
                <SearchIcon className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="h-9 rounded-[10px] pl-9 text-[11px]"
                  placeholder="搜索 Skill..."
                />
              </div>
            </div>
            <div className="mt-2 flex min-w-0 gap-1 overflow-x-auto text-[11px]">
              {categories.map((item) => (
                <button
                  key={item}
                  onClick={() => setCategory(item)}
                  className={cn(
                    "shrink-0 rounded-md px-3 py-2",
                    category === item
                      ? "bg-muted font-medium text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-6 flex items-center">
            <h2 className="text-[15px] font-semibold">
              {tieredMarket
                ? "官方精选"
                : tab === "market"
                  ? category === "精选"
                    ? "官方精选"
                    : "创作 Skill"
                  : "已安装 Skill"}
            </h2>
            <span className="ml-2 text-[10px] text-muted-foreground">
              {loading ? "…" : tieredMarket ? 30 : items.length}
            </span>
            {tab === "market" ? (
              <>
                <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-[10px] text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={onlyUninstalled}
                    onChange={(event) =>
                      setOnlyUninstalled(event.target.checked)
                    }
                    className="size-3 rounded border-border-default"
                  />
                  仅显示未安装
                </label>
                <select
                  value={skillSort}
                  onChange={(event) =>
                    setSkillSort(event.target.value as "recent" | "popular")
                  }
                  className="ml-3 h-7 rounded-lg border border-border-default bg-background px-2 text-[10px] text-muted-foreground outline-none"
                  aria-label="Skill 排序"
                >
                  <option value="recent">最近</option>
                  <option value="popular">最热门</option>
                </select>
              </>
            ) : null}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
            {items.map((item, itemIndex) => {
              const Icon = item.icon;
              const installed = installedByName.get(item.id);
              const catalogIndex = CREATIVE_SKILL_COLLECTION.findIndex(
                (skill) => skill.id === item.id,
              );
              const cover =
                CREATIVE_SKILL_COVERS[
                  Math.max(0, catalogIndex) % CREATIVE_SKILL_COVERS.length
                ];
              return (
                <div key={item.id} className="contents">
                  {tieredMarket && itemIndex === 30 ? (
                    <div className="col-span-full mt-5 flex items-end border-t border-border-subtle pt-6">
                      <div>
                        <h2 className="text-[15px] font-semibold">用户精选</h2>
                        <p className="mt-1 text-[10px] text-muted-foreground">
                          社区创作范式与实验方向的原创等价能力
                        </p>
                      </div>
                      <span className="ml-2 text-[10px] text-muted-foreground">
                        5
                      </span>
                    </div>
                  ) : null}
                  {tieredMarket && itemIndex === 35 ? (
                    <div className="col-span-full mt-5 flex items-center border-t border-border-subtle pt-6">
                      <h2 className="text-[15px] font-semibold">
                        其他 Skill · {Math.max(0, items.length - 35)}
                      </h2>
                      <span className="ml-auto text-[10px] text-muted-foreground">
                        按需安装
                      </span>
                    </div>
                  ) : null}
                  <article className="group overflow-hidden rounded-[12px] border border-border-default bg-background transition hover:-translate-y-0.5 hover:shadow-[0_14px_35px_-18px_rgba(0,0,0,.35)]">
                    <div
                      className={cn(
                        "relative h-28 overflow-hidden bg-gradient-to-br",
                        item.tone,
                      )}
                    >
                      <img
                        src={cover}
                        alt=""
                        className="absolute inset-0 size-full object-cover transition duration-300 group-hover:scale-[1.03]"
                      />
                      <div className="absolute inset-0 bg-gradient-to-t from-black/20 via-transparent to-white/5" />
                      <div className="absolute inset-0 bg-[radial-gradient(circle_at_25%_15%,rgba(255,255,255,.65),transparent_34%)]" />
                      <Icon className="absolute bottom-3 right-4 size-8 text-white/75 drop-shadow" />
                      <span className="absolute left-0 top-0 size-8 bg-violet-600 [clip-path:polygon(0_0,100%_0,0_100%)]">
                        <SparklesIcon className="ml-1 mt-1 size-2.5 text-white" />
                      </span>
                      {installed ? (
                        <span className="absolute right-2.5 top-2.5 rounded-md bg-black/60 px-1.5 py-0.5 text-[8px] font-medium text-white backdrop-blur-sm">
                          {installed.enabled ? "已启用" : "已安装"}
                        </span>
                      ) : null}
                      <div className="absolute inset-0 flex items-center justify-center gap-1.5 bg-black/40 opacity-0 backdrop-blur-[1px] transition-opacity group-hover:opacity-100">
                        <button
                          onClick={() => setDetailSkillId(item.id)}
                          className="rounded-lg bg-white/92 px-2.5 py-1.5 text-[9px] font-medium text-zinc-900"
                        >
                          查看详情
                        </button>
                        <button
                          onClick={() =>
                            installed?.enabled
                              ? void handleUseSkill(item.id)
                              : void handleInstallSkill(item.id)
                          }
                          disabled={
                            enableSkill.isPending || enableMarketSkill.isPending
                          }
                          className="rounded-lg bg-zinc-950/90 px-2.5 py-1.5 text-[9px] font-medium text-white"
                        >
                          {installed?.enabled
                            ? "加入画布"
                            : installed
                              ? "启用 Skill"
                              : "安装 Skill"}
                        </button>
                      </div>
                    </div>
                    <button
                      onClick={() => setDetailSkillId(item.id)}
                      className="block w-full p-3 text-left"
                    >
                      <div className="truncate text-[13px] font-semibold">
                        {item.title}
                      </div>
                      <p className="mt-1 line-clamp-2 min-h-8 text-[10px] leading-4 text-muted-foreground">
                        {item.description}
                      </p>
                      <div className="mt-3 flex items-center gap-1 text-[9px] text-muted-foreground">
                        <span>Echo Design</span>
                        {installed ? (
                          <CheckIcon
                            className={cn(
                              "size-3",
                              installed.enabled
                                ? "text-emerald-500"
                                : "text-amber-500",
                            )}
                          />
                        ) : null}
                        <span className="ml-auto inline-flex items-center gap-1">
                          <ArchiveIcon className="size-2.5" />
                          {item.downloads}
                        </span>
                      </div>
                    </button>
                  </article>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <Dialog
        open={Boolean(detailSkill)}
        onOpenChange={(open) => {
          if (!open) setDetailSkillId(null);
        }}
      >
        <DialogContent className="flex h-[78vh] max-h-[760px] flex-col gap-0 overflow-hidden p-0 sm:max-w-[920px]">
          {detailSkill ? (
            <>
              <DialogHeader className="shrink-0 border-b border-border-subtle px-6 py-4 pr-12">
                <div className="flex items-center gap-2">
                  <DialogTitle className="text-[15px]">
                    {detailSkill.title}
                  </DialogTitle>
                  <span className="rounded-md bg-muted px-2 py-1 text-[9px] text-muted-foreground">
                    {detailSkill.category}
                  </span>
                </div>
                <DialogDescription className="max-w-3xl text-[11px] leading-5">
                  {detailSkill.description}
                </DialogDescription>
              </DialogHeader>
              <div className="flex min-h-0 flex-1">
                <aside className="w-[230px] shrink-0 overflow-y-auto border-r border-border-subtle bg-muted/20 p-2">
                  {detailLoading ? (
                    <div className="grid h-32 place-items-center">
                      <Loader2Icon className="size-4 animate-spin" />
                    </div>
                  ) : (
                    <>
                      {detailDirectories.map((directory) => (
                        <div key={directory}>
                          <button
                            type="button"
                            onClick={() =>
                              setExpandedSkillDirs((current) => {
                                const next = new Set(current);
                                if (next.has(directory)) next.delete(directory);
                                else next.add(directory);
                                return next;
                              })
                            }
                            className="flex w-full items-center gap-1.5 rounded-md px-2 py-2 text-left text-[10px] hover:bg-muted"
                          >
                            <ChevronDownIcon
                              className={cn(
                                "size-3 shrink-0 transition-transform",
                                !expandedSkillDirs.has(directory) &&
                                  "-rotate-90",
                              )}
                            />
                            <FolderIcon className="size-3 shrink-0" />
                            <span className="truncate">{directory}</span>
                          </button>
                          {expandedSkillDirs.has(directory)
                            ? detailFiles
                                .filter((file) =>
                                  file.path.startsWith(`${directory}/`),
                                )
                                .map((file) => (
                                  <button
                                    key={file.path}
                                    onClick={() => setDetailFilePath(file.path)}
                                    className={cn(
                                      "flex w-full items-center gap-2 rounded-md py-2 pl-8 pr-2 text-left text-[10px] hover:bg-muted",
                                      detailFilePath === file.path &&
                                        "bg-muted font-medium",
                                    )}
                                  >
                                    <BookOpenIcon className="size-3 shrink-0" />
                                    <span className="truncate">
                                      {file.path.slice(directory.length + 1)}
                                    </span>
                                  </button>
                                ))
                            : null}
                        </div>
                      ))}
                      {detailRootFiles.map((file) => (
                        <button
                          key={file.path}
                          onClick={() => setDetailFilePath(file.path)}
                          className={cn(
                            "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-[10px] hover:bg-muted",
                            detailFilePath === file.path &&
                              "bg-muted font-medium",
                          )}
                        >
                          <ArchiveIcon className="size-3 shrink-0" />
                          <span className="truncate">{file.path}</span>
                        </button>
                      ))}
                    </>
                  )}
                </aside>
                <section className="flex min-w-0 flex-1 flex-col">
                  <div className="flex h-10 shrink-0 items-center border-b border-border-subtle px-4 text-[10px] font-medium">
                    {detailFilePath || "Skill 文件"}
                    <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[8px] text-muted-foreground">
                      {detailFilePath.endsWith(".json") ? "JSON" : "Markdown"}
                    </span>
                  </div>
                  <div className="chat-markdown min-h-0 flex-1 overflow-auto px-7 py-5">
                    {detailLoading ? (
                      <div className="text-[11px] text-muted-foreground">
                        正在读取…
                      </div>
                    ) : renderedDetailContent ? (
                      <MarkdownContent
                        content={renderedDetailContent}
                        isLoading={false}
                        remarkPlugins={streamdownPlugins.remarkPlugins}
                        rehypePlugins={streamdownPlugins.rehypePlugins}
                        chatFontSize="small"
                        className="text-[11px] leading-6"
                      />
                    ) : (
                      <div className="text-[11px] text-muted-foreground">
                        没有可预览的文本文件
                      </div>
                    )}
                  </div>
                </section>
              </div>
              <DialogFooter className="shrink-0 border-t border-border-subtle px-5 py-3">
                <span className="mr-auto self-center text-[9px] text-muted-foreground">
                  Echo 原创 · Apache-2.0
                </span>
                <Button
                  size="sm"
                  className="rounded-lg"
                  disabled={
                    enableSkill.isPending || enableMarketSkill.isPending
                  }
                  onClick={() => {
                    if (installedByName.get(detailSkill.id)?.enabled) {
                      void handleUseSkill(detailSkill.id);
                      setDetailSkillId(null);
                    } else {
                      void handleInstallSkill(detailSkill.id);
                    }
                  }}
                >
                  {installedByName.get(detailSkill.id)?.enabled
                    ? "加入画布"
                    : installedByName.get(detailSkill.id)
                      ? "启用 Skill"
                      : "安装 Skill"}
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

function formatModelSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function ComfyUIView({
  onUse,
}: {
  onUse: (id: string, title: string) => void;
}) {
  const [checking, setChecking] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);
  const [comfyProcess, setComfyProcess] = useState({
    owned: false,
    running: false,
  });
  const [dependencies, setDependencies] = useState<{
    detected: boolean;
    path: string | null;
    modelCounts: Record<string, number>;
    totalModels: number;
    customNodes: string[];
    totalCustomNodes: number;
    managed: boolean;
    manager: {
      installed: boolean;
      home: string;
      job: {
        running?: boolean;
        state?: string;
        phase?: string;
        action?: string;
        node_id?: string | null;
        model_group?: string | null;
        error?: string | null;
      };
      runtime?: { version?: string | null; commit?: string | null };
      logTail?: string[];
    };
  } | null>(null);
  const [environmentOpen, setEnvironmentOpen] = useState(false);
  const [workflowCreateOpen, setWorkflowCreateOpen] = useState(false);
  const workflowCreateMenuRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<"market" | "mine">("market");
  const [query, setQuery] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(
    null,
  );
  const [selectedWorkflowDetail, setSelectedWorkflowDetail] = useState<{
    workflow: Record<
      string,
      { class_type?: string; inputs?: Record<string, unknown> }
    >;
  } | null>(null);
  const [selectedWorkflowDiagnostics, setSelectedWorkflowDiagnostics] =
    useState<{
      compatible: boolean;
      fullyChecked: boolean;
      counts: {
        nodes: number;
        nodeTypes: number;
        modelReferences: number;
        errors: number;
        warnings: number;
      };
      issues: Array<{
        kind: string;
        severity: "error" | "warning";
        detail: string;
        nodeId?: string;
        classType?: string;
        value?: string;
      }>;
    } | null>(null);
  const [nodeQuery, setNodeQuery] = useState("");
  const [nodeLoading, setNodeLoading] = useState(false);
  const [registryNodes, setRegistryNodes] = useState<
    Array<{
      id: string;
      name: string;
      description: string;
      publisher: string;
      repository: string;
      downloads: number;
      stars: number;
      version: string;
      dependencies: string[];
      deprecated: boolean;
      installed: boolean;
      backups: Array<{ id: string; created_at?: string }>;
    }>
  >([]);
  const [modelUrl, setModelUrl] = useState("");
  const [modelGroup, setModelGroup] = useState("checkpoints");
  const [modelLoading, setModelLoading] = useState(false);
  const [localModels, setLocalModels] = useState<
    Array<{
      id: string;
      group: string;
      name: string;
      size_bytes: number;
      modified_at?: string;
    }>
  >([]);
  const [modelBackups, setModelBackups] = useState<
    Array<{
      id: string;
      group: string;
      name: string;
      size_bytes: number;
    }>
  >([]);
  const [modelGroups, setModelGroups] = useState<string[]>([
    "checkpoints",
    "diffusion_models",
    "loras",
    "vae",
    "controlnet",
  ]);
  const [remoteWorkflows, setRemoteWorkflows] = useState<
    Array<{
      id: string;
      name: string;
      description: string;
      tags: string[];
      source: "bundled" | "user";
    }>
  >([]);
  const [runState, setRunState] = useState<{
    workflowId: string;
    promptId: string;
    state: "queued" | "running" | "completed" | "error";
    outputs: Array<{ filename: string; url: string; kind: string }>;
    detail?: string;
  } | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const loadWorkflows = useCallback(async () => {
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/workflows`,
        { headers: authHeaders() },
      );
      if (!response.ok) return;
      const payload = (await response.json()) as {
        items?: Array<{
          id: string;
          name: string;
          description?: string;
          tags?: string[];
          source: "bundled" | "user";
        }>;
      };
      setRemoteWorkflows(
        (payload.items ?? []).map((item) => ({
          ...item,
          description: item.description ?? "",
          tags: item.tags ?? [],
        })),
      );
    } catch {
      // The static marketplace remains available when the local bridge is down.
    }
  }, []);
  const loadDependencies = useCallback(async () => {
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/dependencies`,
        { headers: authHeaders() },
      );
      if (!response.ok) return;
      const payload = (await response.json()) as {
        detected?: boolean;
        path?: string | null;
        model_counts?: Record<string, number>;
        total_models?: number;
        custom_nodes?: string[];
        total_custom_nodes?: number;
        managed?: boolean;
        manager?: {
          installed?: boolean;
          home?: string;
          job?: {
            running?: boolean;
            state?: string;
            phase?: string;
            action?: string;
            node_id?: string | null;
            model_group?: string | null;
            error?: string | null;
          };
          runtime?: { version?: string | null; commit?: string | null };
          log_tail?: string[];
        };
      };
      setDependencies({
        detected: payload.detected === true,
        path: payload.path ?? null,
        modelCounts: payload.model_counts ?? {},
        totalModels: payload.total_models ?? 0,
        customNodes: payload.custom_nodes ?? [],
        totalCustomNodes: payload.total_custom_nodes ?? 0,
        managed: payload.managed === true,
        manager: {
          installed: payload.manager?.installed === true,
          home: payload.manager?.home ?? "",
          job: payload.manager?.job ?? {},
          runtime: payload.manager?.runtime,
          logTail: payload.manager?.log_tail ?? [],
        },
      });
    } catch {
      // Dependency inventory is optional and never blocks the workflow market.
    }
  }, []);
  const loadRegistryNodes = useCallback(async (search = "") => {
    setNodeLoading(true);
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("query", search.trim());
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/custom-nodes/registry?${params}`,
        { headers: authHeaders() },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as {
        items?: typeof registryNodes;
      };
      setRegistryNodes(payload.items ?? []);
    } catch {
      toast.error("暂时无法读取 Comfy Registry");
    } finally {
      setNodeLoading(false);
    }
  }, []);
  const loadLocalModels = useCallback(async () => {
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/models`,
        { headers: authHeaders() },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as {
        items?: typeof localModels;
        backups?: typeof modelBackups;
        groups?: string[];
      };
      setLocalModels(payload.items ?? []);
      setModelBackups(payload.backups ?? []);
      if (payload.groups?.length) setModelGroups(payload.groups);
    } catch {
      // Model inventory remains optional until the managed engine exists.
    }
  }, []);
  useEffect(() => {
    void loadWorkflows();
    void loadDependencies();
  }, [loadDependencies, loadWorkflows]);
  useEffect(() => {
    if (!dependencies?.manager.job.running) return;
    const timer = window.setInterval(() => void loadDependencies(), 1400);
    return () => window.clearInterval(timer);
  }, [dependencies?.manager.job.running, loadDependencies]);
  useEffect(() => {
    if (dependencies?.manager.installed) {
      void loadRegistryNodes();
      void loadLocalModels();
    }
  }, [dependencies?.manager.installed, loadLocalModels, loadRegistryNodes]);
  useEffect(() => {
    if (
      dependencies?.manager.job.state === "completed" &&
      dependencies.manager.job.action?.startsWith("node_")
    ) {
      void loadRegistryNodes();
      void loadDependencies();
    }
  }, [
    dependencies?.manager.job.action,
    dependencies?.manager.job.state,
    loadDependencies,
    loadRegistryNodes,
  ]);
  useEffect(() => {
    if (
      dependencies?.manager.job.state === "completed" &&
      dependencies.manager.job.action === "model_download"
    ) {
      void loadLocalModels();
      void loadDependencies();
    }
  }, [
    dependencies?.manager.job.action,
    dependencies?.manager.job.state,
    loadDependencies,
    loadLocalModels,
  ]);
  const catalogWorkflows = useMemo(() => {
    const remoteById = new Map(remoteWorkflows.map((item) => [item.id, item]));
    const market = COMFY_WORKFLOWS.map((item) => {
      const remote = remoteById.get(item.id);
      return {
        ...item,
        description: remote?.description || item.description,
        tags: [...(remote?.tags?.length ? remote.tags : item.tags)],
        availability:
          remote?.source === "user" ? ("user" as const) : item.availability,
        source: remote?.source,
      };
    });
    const known = new Set<string>(market.map((item) => item.id));
    const user = remoteWorkflows
      .filter((item) => item.source === "user" && !known.has(item.id))
      .map((item) => ({
        id: item.id,
        title: item.name,
        description: item.description || "用户导入的 ComfyUI 工作流",
        tags: item.tags,
        availability: "user" as const,
        source: item.source,
      }));
    return [...market, ...user];
  }, [remoteWorkflows]);
  const needle = query.trim().toLowerCase();
  const visibleWorkflows = catalogWorkflows.filter(
    (workflow) =>
      (tab === "market" || workflow.source === "user") &&
      (!needle ||
        `${workflow.title} ${workflow.description} ${workflow.tags.join(" ")}`
          .toLowerCase()
          .includes(needle)),
  );
  const selectedWorkflow = selectedWorkflowId
    ? (catalogWorkflows.find(
        (workflow) => workflow.id === selectedWorkflowId,
      ) ?? null)
    : null;
  useEffect(() => {
    if (!selectedWorkflowId) {
      setSelectedWorkflowDetail(null);
      setSelectedWorkflowDiagnostics(null);
      return;
    }
    const controller = new AbortController();
    void Promise.all([
      fetch(
        `${getBackendBaseURL()}/api/design/comfyui/workflows/${encodeURIComponent(selectedWorkflowId)}`,
        { headers: authHeaders(), signal: controller.signal },
      ),
      fetch(
        `${getBackendBaseURL()}/api/design/comfyui/workflows/${encodeURIComponent(selectedWorkflowId)}/diagnostics`,
        { headers: authHeaders(), signal: controller.signal },
      ),
    ])
      .then(async ([detailResponse, diagnosticsResponse]) => {
        if (!detailResponse.ok)
          throw new Error(`HTTP ${detailResponse.status}`);
        const detail = (await detailResponse.json()) as {
          workflow?: Record<
            string,
            { class_type?: string; inputs?: Record<string, unknown> }
          >;
        };
        const diagnostics = diagnosticsResponse.ok
          ? ((await diagnosticsResponse.json()) as NonNullable<
              typeof selectedWorkflowDiagnostics
            >)
          : null;
        return { detail, diagnostics };
      })
      .then(({ detail, diagnostics }) => {
        setSelectedWorkflowDetail({ workflow: detail.workflow ?? {} });
        setSelectedWorkflowDiagnostics(diagnostics);
      })
      .catch((error: unknown) => {
        if ((error as { name?: string }).name !== "AbortError") {
          setSelectedWorkflowDetail(null);
          setSelectedWorkflowDiagnostics(null);
        }
      });
    return () => controller.abort();
  }, [selectedWorkflowId]);
  const selectedWorkflowNodes = Object.values(
    selectedWorkflowDetail?.workflow ?? {},
  );
  const selectedWorkflowNodeTypes = Array.from(
    new Set(
      selectedWorkflowNodes
        .map((node) => node.class_type?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  );
  const selectedWorkflowResources = selectedWorkflowNodes.flatMap((node) =>
    Object.entries(node.inputs ?? {})
      .filter(
        ([key, value]) =>
          typeof value === "string" &&
          /(?:ckpt|checkpoint|vae|lora|control.*net|unet|clip|image|video|audio).*name|^(?:image|video|audio)$/i.test(
            key,
          ),
      )
      .map(([key, value]) => ({
        key,
        value: String(value),
        nodeType: node.class_type || "ComfyUI 节点",
      })),
  );
  const check = async () => {
    setChecking(true);
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/status`,
        {
          headers: authHeaders(),
          signal: AbortSignal.timeout(2800),
        },
      );
      const payload = (await response.json()) as {
        online?: boolean;
        process?: { owned?: boolean; running?: boolean };
      };
      setOnline(response.ok && payload.online === true);
      setComfyProcess({
        owned: payload.process?.owned === true,
        running: payload.process?.running === true,
      });
    } catch {
      setOnline(false);
    } finally {
      void loadDependencies();
      setChecking(false);
    }
  };
  const controlLocalService = async (action: "start" | "stop") => {
    setChecking(true);
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/${action}`,
        { method: "POST", headers: authHeaders() },
      );
      const payload = (await response.json()) as {
        ok?: boolean;
        state?: string;
        process?: { owned?: boolean; running?: boolean };
      };
      if (!response.ok || !payload.ok) {
        throw new Error(payload.state || `HTTP ${response.status}`);
      }
      setComfyProcess({
        owned: payload.process?.owned === true,
        running: payload.process?.running === true,
      });
      if (action === "start") {
        toast.success("ComfyUI 正在启动");
        window.setTimeout(() => void check(), 900);
      } else {
        setOnline(false);
        toast.success("已停止由 Echo 启动的 ComfyUI");
      }
    } catch {
      toast.error(
        action === "start"
          ? "未能启动，请确认本地 ComfyUI 安装完整"
          : "该服务不是由 Echo 启动，无法代为停止",
      );
    } finally {
      setChecking(false);
    }
  };
  const controlManagedComfy = async (
    action: "install" | "update" | "manager/cancel",
  ) => {
    setChecking(true);
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/${action}`,
        { method: "POST", headers: authHeaders() },
      );
      const payload = (await response.json()) as {
        ok?: boolean;
        state?: string;
        detail?: string;
      };
      if (!response.ok || !payload.ok)
        throw new Error(payload.detail || payload.state || "操作失败");
      await loadDependencies();
      if (action === "manager/cancel") {
        toast.success("已取消 ComfyUI 安装任务");
      } else {
        toast.success(
          action === "install"
            ? "开始安装 ComfyUI；不会自动下载模型权重"
            : "开始更新 ComfyUI",
        );
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "ComfyUI 操作失败");
    } finally {
      setChecking(false);
    }
  };
  const controlCustomNode = async (
    action: "install" | "update" | "uninstall" | "rollback",
    nodeId: string,
    backupId?: string,
  ) => {
    const warning =
      action === "install"
        ? "该扩展来自 Comfy Registry，安装时可能执行第三方依赖脚本。确认安装？"
        : action === "update"
          ? "更新前会自动备份当前版本；扩展依赖也可能变化。确认更新？"
          : action === "uninstall"
            ? "扩展会移入可恢复区，不会永久删除。确认卸载？"
            : "当前扩展会先备份，再恢复到所选历史版本。确认回滚？";
    if (!window.confirm(warning)) return;
    setNodeLoading(true);
    try {
      const base = `${getBackendBaseURL()}/api/design/comfyui/custom-nodes`;
      let response: Response;
      if (action === "install" || action === "update") {
        response = await fetch(`${base}/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ node_id: nodeId }),
        });
      } else if (action === "uninstall") {
        response = await fetch(`${base}/${encodeURIComponent(nodeId)}`, {
          method: "DELETE",
          headers: authHeaders(),
        });
      } else {
        response = await fetch(
          `${base}/${encodeURIComponent(nodeId)}/rollback`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({ backup_id: backupId }),
          },
        );
      }
      const payload = (await response.json()) as {
        ok?: boolean;
        state?: string;
        detail?: string;
      };
      if (!response.ok || !payload.ok)
        throw new Error(payload.detail || payload.state || "扩展操作失败");
      await loadDependencies();
      await loadRegistryNodes(nodeQuery);
      toast.success(
        action === "install"
          ? "扩展开始安装"
          : action === "update"
            ? "扩展开始更新"
            : action === "uninstall"
              ? "扩展已移入可恢复区"
              : "扩展已回滚",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "扩展操作失败");
    } finally {
      setNodeLoading(false);
    }
  };
  const controlModel = async (
    action: "download" | "remove" | "restore",
    payload?: { group?: string; name?: string; backupId?: string },
  ) => {
    const warning =
      action === "download"
        ? `将从公开来源下载模型到 ${modelGroup}。模型通常体积较大，确认开始？`
        : action === "remove"
          ? "模型会移入可恢复区，不会永久删除。确认移除？"
          : "确认恢复该模型？若目标位置已有同名模型，恢复会被拒绝。";
    if (!window.confirm(warning)) return;
    setModelLoading(true);
    try {
      const body =
        action === "download"
          ? { url: modelUrl.trim(), group: modelGroup }
          : action === "remove"
            ? { group: payload?.group, name: payload?.name }
            : { backup_id: payload?.backupId };
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/models/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify(body),
        },
      );
      const result = (await response.json()) as {
        ok?: boolean;
        state?: string;
        detail?: string;
      };
      if (!response.ok || !result.ok)
        throw new Error(result.detail || result.state || "模型操作失败");
      if (action === "download") {
        setModelUrl("");
        await loadDependencies();
        toast.success("模型下载已开始，可在后台继续");
      } else {
        await loadLocalModels();
        await loadDependencies();
        toast.success(
          action === "remove" ? "模型已移入可恢复区" : "模型已恢复",
        );
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "模型操作失败");
    } finally {
      setModelLoading(false);
    }
  };
  const importWorkflow = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      const workflow =
        parsed.workflow && typeof parsed.workflow === "object"
          ? (parsed.workflow as Record<string, unknown>)
          : parsed;
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/workflows/import`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({
            name: file.name.replace(/\.json$/i, ""),
            workflow,
          }),
        },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await loadWorkflows();
      setTab("mine");
      toast.success("ComfyUI 工作流已导入");
    } catch {
      toast.error("工作流 JSON 无法导入，请检查文件格式");
    }
  };
  const runWorkflow = async (workflowId: string) => {
    setRunState({
      workflowId,
      promptId: "",
      state: "queued",
      outputs: [],
    });
    try {
      const response = await fetch(
        `${getBackendBaseURL()}/api/design/comfyui/queue`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ workflow_id: workflowId }),
        },
      );
      const payload = (await response.json()) as {
        prompt_id?: string;
        detail?: string;
      };
      if (!response.ok || !payload.prompt_id)
        throw new Error(payload.detail || `HTTP ${response.status}`);
      setRunState({
        workflowId,
        promptId: payload.prompt_id,
        state: "running",
        outputs: [],
      });
    } catch (error) {
      setRunState({
        workflowId,
        promptId: "",
        state: "error",
        outputs: [],
        detail: error instanceof Error ? error.message : "工作流运行失败",
      });
    }
  };
  useEffect(() => {
    if (!runState?.promptId || runState.state !== "running") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch(
          `${getBackendBaseURL()}/api/design/comfyui/history/${encodeURIComponent(runState.promptId)}`,
          { headers: authHeaders() },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = (await response.json()) as {
          state?: "pending" | "running" | "completed";
          outputs?: Array<{ filename: string; url: string; kind: string }>;
        };
        if (cancelled) return;
        setRunState((current) =>
          current?.promptId === runState.promptId
            ? {
                ...current,
                state: payload.state === "completed" ? "completed" : "running",
                outputs: payload.outputs ?? [],
              }
            : current,
        );
      } catch (error) {
        if (!cancelled)
          setRunState((current) =>
            current?.promptId === runState.promptId
              ? {
                  ...current,
                  state: "error",
                  detail:
                    error instanceof Error ? error.message : "结果查询失败",
                }
              : current,
          );
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runState?.promptId, runState?.state]);
  useEffect(() => {
    if (!workflowCreateOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !workflowCreateMenuRef.current?.contains(event.target)
      )
        setWorkflowCreateOpen(false);
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setWorkflowCreateOpen(false);
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", dismissWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", dismissWithEscape);
    };
  }, [workflowCreateOpen]);
  return (
    <div className="h-full overflow-y-auto bg-background px-11 py-9">
      <div className="mx-auto max-w-[1120px]">
        <div className="flex items-start">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              ComfyUI 工作流
            </h1>
            <p className="mt-1.5 text-xs text-muted-foreground">
              支持本地部署，可手动运行，也可作为画布节点由 Agent 调用
            </p>
          </div>
        </div>
        <div className="mt-7 flex gap-2">
          <div ref={workflowCreateMenuRef} className="relative">
            <Button
              className="h-9 rounded-[10px] bg-foreground px-4 text-[11px] text-background"
              aria-haspopup="menu"
              aria-expanded={workflowCreateOpen}
              onClick={() => setWorkflowCreateOpen((value) => !value)}
            >
              <PlusIcon className="mr-1.5 size-3.5" />
              导入/新建工作流
            </Button>
            {workflowCreateOpen ? (
              <div
                role="menu"
                aria-label="导入/新建工作流"
                className="absolute left-0 top-11 z-40 w-44 rounded-[10px] border border-black/[0.08] bg-background p-1 shadow-[0_8px_32px_rgba(0,0,0,.10)]"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setWorkflowCreateOpen(false);
                    importRef.current?.click();
                  }}
                  className="flex h-9 w-full items-center rounded-lg px-3 text-left text-[11px] font-medium hover:bg-muted"
                >
                  导入本地工作流
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setWorkflowCreateOpen(false);
                    onUse("blank", "空白 ComfyUI 工作流");
                  }}
                  className="flex h-9 w-full items-center rounded-lg px-3 text-left text-[11px] font-medium hover:bg-muted"
                >
                  在画布中创建
                </button>
              </div>
            ) : null}
          </div>
          <input
            ref={importRef}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importWorkflow(file);
              event.target.value = "";
            }}
          />
          <Button
            variant="outline"
            className="h-9 rounded-[10px] px-4 text-[11px]"
            onClick={() =>
              toast.info(
                "可从 Comfy Registry 安装节点，也可导入开源 workflow JSON",
              )
            }
          >
            <BookOpenIcon className="mr-1.5 size-4" />
            探索开源
          </Button>
          <Button
            variant="ghost"
            className="ml-auto h-9 rounded-[10px] px-3 text-[10px] text-muted-foreground"
            onClick={() => setEnvironmentOpen((value) => !value)}
          >
            <span
              className={cn(
                "mr-1.5 size-1.5 rounded-full",
                online === true
                  ? "bg-emerald-500"
                  : online === false
                    ? "bg-red-500"
                    : "bg-zinc-400",
              )}
            />
            <Settings2Icon className="mr-1.5 size-4" />
            本地环境
            <ChevronDownIcon
              className={cn(
                "ml-1 size-3.5 transition-transform",
                environmentOpen && "rotate-180",
              )}
            />
          </Button>
          {environmentOpen ? (
            <>
              <Button
                variant="outline"
                className="rounded-xl"
                onClick={check}
                disabled={checking}
              >
                {checking ? (
                  <Loader2Icon className="mr-1.5 size-4 animate-spin" />
                ) : (
                  <Redo2Icon className="mr-1.5 size-4" />
                )}
                检测本地服务
              </Button>
              {dependencies?.detected && online !== true ? (
                <Button
                  variant="outline"
                  className="rounded-xl"
                  onClick={() => void controlLocalService("start")}
                  disabled={checking || comfyProcess.running}
                >
                  <CirclePlayIcon className="mr-1.5 size-4" />
                  启动本地服务
                </Button>
              ) : null}
              {dependencies &&
              !dependencies.detected &&
              !dependencies.manager.job.running ? (
                <Button
                  variant="outline"
                  className="rounded-xl border-violet-200 text-violet-700"
                  onClick={() => void controlManagedComfy("install")}
                  disabled={checking}
                >
                  <ArchiveIcon className="mr-1.5 size-4" />
                  安装本地引擎
                </Button>
              ) : null}
              {dependencies?.manager.installed &&
              !dependencies.manager.job.running ? (
                <Button
                  variant="ghost"
                  className="rounded-xl text-muted-foreground"
                  onClick={() => void controlManagedComfy("update")}
                  disabled={checking || online === true}
                  title={online === true ? "请先停止本地服务再更新" : undefined}
                >
                  <Redo2Icon className="mr-1.5 size-4" />
                  更新引擎
                </Button>
              ) : null}
              {dependencies?.manager.job.running ? (
                <Button
                  variant="ghost"
                  className="rounded-xl text-amber-700"
                  onClick={() => void controlManagedComfy("manager/cancel")}
                  disabled={checking}
                >
                  <XIcon className="mr-1.5 size-4" />
                  取消
                  {dependencies.manager.job.action === "update" ||
                  dependencies.manager.job.action === "node_update"
                    ? "更新"
                    : dependencies.manager.job.action === "model_download"
                      ? "下载"
                      : "安装"}
                </Button>
              ) : null}
              {online === true && comfyProcess.owned ? (
                <Button
                  variant="ghost"
                  className="rounded-xl text-muted-foreground"
                  onClick={() => void controlLocalService("stop")}
                  disabled={checking}
                >
                  <XIcon className="mr-1.5 size-4" />
                  停止服务
                </Button>
              ) : null}
            </>
          ) : null}
        </div>
        {environmentOpen && dependencies ? (
          <div className="mt-4 grid grid-cols-[1.35fr_0.65fr_0.65fr] overflow-hidden rounded-2xl border bg-muted/20">
            <div className="min-w-0 px-4 py-3.5">
              <div className="flex items-center gap-2 text-[11px] font-medium">
                <FolderIcon className="size-3.5 text-sky-600" />
                本地创作环境
              </div>
              <p
                className="mt-1.5 truncate text-[10px] text-muted-foreground"
                title={dependencies.path ?? undefined}
              >
                {dependencies.detected
                  ? dependencies.path
                  : "尚未找到 ComfyUI 目录，工作流市场仍可浏览"}
              </p>
            </div>
            <div className="border-l px-4 py-3.5">
              <div className="text-lg font-semibold tabular-nums">
                {dependencies.totalModels}
              </div>
              <div className="mt-0.5 text-[9px] text-muted-foreground">
                本地模型
              </div>
            </div>
            <div className="border-l px-4 py-3.5">
              <div className="text-lg font-semibold tabular-nums">
                {dependencies.totalCustomNodes}
              </div>
              <div className="mt-0.5 text-[9px] text-muted-foreground">
                节点扩展
              </div>
            </div>
          </div>
        ) : null}
        {environmentOpen &&
          (dependencies?.manager.job.running ||
          dependencies?.manager.job.state === "failed" ? (
            <div
              className={cn(
                "mt-3 rounded-xl border px-4 py-3 text-[10px]",
                dependencies.manager.job.state === "failed"
                  ? "border-red-200 bg-red-50 text-red-800"
                  : "border-violet-200 bg-violet-50 text-violet-800",
              )}
            >
              <div className="flex items-center gap-2 font-medium">
                {dependencies.manager.job.running ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : null}
                {dependencies.manager.job.state === "failed"
                  ? "ComfyUI 安装未完成"
                  : dependencies.manager.job.phase === "creating_runtime"
                    ? "正在创建隔离运行环境"
                    : dependencies.manager.job.phase === "installing_cli"
                      ? "正在安装官方管理工具"
                      : dependencies.manager.job.action === "update"
                        ? "正在更新 ComfyUI"
                        : dependencies.manager.job.action === "node_update"
                          ? `正在更新扩展 ${dependencies.manager.job.node_id || ""}`
                          : dependencies.manager.job.action === "node_install"
                            ? `正在安装扩展 ${dependencies.manager.job.node_id || ""}`
                            : dependencies.manager.job.action ===
                                "model_download"
                              ? `正在下载模型到 ${dependencies.manager.job.model_group || "models"}`
                              : "正在下载并安装 ComfyUI"}
              </div>
              <p className="mt-1 opacity-80">
                {dependencies.manager.job.error ||
                  "可以离开此页面，安装任务会在后台继续；模型权重仍由你自行选择。"}
              </p>
            </div>
          ) : null)}
        {environmentOpen && dependencies?.manager.installed ? (
          <section className="mt-5 rounded-2xl border border-border-default bg-background p-4">
            <div className="flex items-start gap-3">
              <div>
                <h2 className="text-[13px] font-semibold">模型中心</h2>
                <p className="mt-0.5 text-[9px] text-muted-foreground">
                  仅支持 Hugging Face / Civitai 公开链接 · 每个模型单独授权
                </p>
              </div>
              <span className="flex-1" />
              <span className="rounded-md bg-muted px-2 py-1 text-[9px] text-muted-foreground">
                {localModels.length} 个模型 ·{" "}
                {formatModelSize(
                  localModels.reduce((sum, model) => sum + model.size_bytes, 0),
                )}
              </span>
            </div>
            <form
              className="mt-3 grid grid-cols-[minmax(0,1fr)_150px_auto] gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (modelUrl.trim()) void controlModel("download");
              }}
            >
              <Input
                value={modelUrl}
                onChange={(event) => setModelUrl(event.target.value)}
                placeholder="粘贴 Hugging Face 文件链接或 Civitai 模型链接"
                className="h-9 rounded-lg text-[10px]"
              />
              <select
                value={modelGroup}
                onChange={(event) => setModelGroup(event.target.value)}
                className="h-9 rounded-lg border border-input bg-background px-2 text-[10px] outline-none"
                aria-label="模型目录"
              >
                {modelGroups.map((group) => (
                  <option key={group} value={group}>
                    {group}
                  </option>
                ))}
              </select>
              <Button
                type="submit"
                className="h-9 rounded-lg px-3 text-[10px]"
                disabled={
                  modelLoading ||
                  !modelUrl.trim() ||
                  online === true ||
                  dependencies.manager.job.running
                }
              >
                {modelLoading ? (
                  <Loader2Icon className="mr-1 size-3.5 animate-spin" />
                ) : (
                  <ArchiveIcon className="mr-1 size-3.5" />
                )}
                下载模型
              </Button>
            </form>
            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
              {localModels.map((model) => (
                <div
                  key={model.id}
                  className="flex min-w-0 items-center gap-2 rounded-xl border border-border-subtle bg-muted/15 px-3 py-2.5"
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-sky-100 text-sky-700">
                    <ArchiveIcon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[10px] font-medium">
                      {model.name}
                    </div>
                    <div className="mt-0.5 text-[8px] text-muted-foreground">
                      {model.group} · {formatModelSize(model.size_bytes)}
                    </div>
                  </div>
                  <button
                    onClick={() =>
                      void controlModel("remove", {
                        group: model.group,
                        name: model.name,
                      })
                    }
                    disabled={modelLoading || online === true}
                    className="rounded px-2 py-1 text-[9px] text-red-600 hover:bg-red-50 disabled:opacity-40"
                  >
                    移除
                  </button>
                </div>
              ))}
            </div>
            {modelBackups.length > 0 ? (
              <div className="mt-3 border-t border-border-subtle pt-3">
                <div className="mb-2 text-[9px] font-medium text-muted-foreground">
                  可恢复模型
                </div>
                <div className="flex flex-wrap gap-2">
                  {modelBackups.map((backup) => (
                    <button
                      key={backup.id}
                      onClick={() =>
                        void controlModel("restore", { backupId: backup.id })
                      }
                      disabled={modelLoading || online === true}
                      className="rounded-lg border px-2.5 py-1.5 text-[9px] hover:bg-muted disabled:opacity-40"
                    >
                      恢复 {backup.name} · {formatModelSize(backup.size_bytes)}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </section>
        ) : null}
        {environmentOpen && dependencies?.manager.installed ? (
          <section className="mt-5 rounded-2xl border border-border-default bg-background p-4">
            <div className="flex items-center gap-3">
              <div>
                <h2 className="text-[13px] font-semibold">节点扩展</h2>
                <p className="mt-0.5 text-[9px] text-muted-foreground">
                  来自官方 Comfy Registry · 修改前需停止本地服务
                </p>
              </div>
              <span className="flex-1" />
              <form
                className="flex w-72 gap-1.5"
                onSubmit={(event) => {
                  event.preventDefault();
                  void loadRegistryNodes(nodeQuery);
                }}
              >
                <Input
                  value={nodeQuery}
                  onChange={(event) => setNodeQuery(event.target.value)}
                  placeholder="输入 Registry ID 精确搜索"
                  className="h-8 rounded-lg text-[10px]"
                />
                <Button
                  type="submit"
                  variant="outline"
                  size="sm"
                  className="h-8 rounded-lg px-2.5"
                  disabled={nodeLoading}
                >
                  {nodeLoading ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : (
                    <SearchIcon className="size-3.5" />
                  )}
                </Button>
              </form>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {registryNodes.map((node) => (
                <article
                  key={node.id}
                  className="rounded-xl border border-border-subtle bg-muted/15 p-3"
                >
                  <div className="flex items-start gap-2">
                    <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-violet-100 text-violet-700">
                      <PuzzleIcon className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-[11px] font-semibold">
                          {node.name}
                        </span>
                        {node.installed ? (
                          <span className="shrink-0 rounded bg-emerald-100 px-1 py-0.5 text-[8px] text-emerald-700">
                            已安装
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-0.5 truncate text-[8px] text-muted-foreground">
                        {node.id} · v{node.version || "latest"}
                      </div>
                    </div>
                  </div>
                  <p className="mt-2 line-clamp-2 min-h-7 text-[9px] leading-3.5 text-muted-foreground">
                    {node.description || "ComfyUI 节点扩展"}
                  </p>
                  <div className="mt-2 flex items-center gap-1.5 text-[8px] text-muted-foreground">
                    <span>{node.downloads.toLocaleString()} 下载</span>
                    <span>★ {node.stars.toLocaleString()}</span>
                    <span className="flex-1" />
                    {node.installed ? (
                      <>
                        {node.backups.length > 0 ? (
                          <button
                            onClick={() =>
                              void controlCustomNode(
                                "rollback",
                                node.id,
                                node.backups[0]?.id,
                              )
                            }
                            disabled={nodeLoading || online === true}
                            className="rounded px-1.5 py-1 hover:bg-muted disabled:opacity-40"
                          >
                            回滚
                          </button>
                        ) : null}
                        <button
                          onClick={() =>
                            void controlCustomNode("update", node.id)
                          }
                          disabled={nodeLoading || online === true}
                          className="rounded px-1.5 py-1 hover:bg-muted disabled:opacity-40"
                        >
                          更新
                        </button>
                        <button
                          onClick={() =>
                            void controlCustomNode("uninstall", node.id)
                          }
                          disabled={nodeLoading || online === true}
                          className="rounded px-1.5 py-1 text-red-600 hover:bg-red-50 disabled:opacity-40"
                        >
                          卸载
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() =>
                          void controlCustomNode("install", node.id)
                        }
                        disabled={nodeLoading || online === true}
                        className="rounded-md bg-foreground px-2 py-1 text-background disabled:opacity-40"
                      >
                        安装
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </section>
        ) : null}
        {runState ? (
          <div
            className={cn(
              "mt-4 rounded-xl border px-4 py-3 text-xs",
              runState.state === "error"
                ? "border-red-200 bg-red-50 text-red-800"
                : runState.state === "completed"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-violet-200 bg-violet-50 text-violet-800",
            )}
          >
            <div className="flex items-center gap-2 font-medium">
              {runState.state === "queued" || runState.state === "running" ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : runState.state === "completed" ? (
                <CheckIcon className="size-3.5" />
              ) : (
                <XIcon className="size-3.5" />
              )}
              {runState.state === "queued"
                ? "正在提交工作流"
                : runState.state === "running"
                  ? "ComfyUI 正在生成"
                  : runState.state === "completed"
                    ? `生成完成 · ${runState.outputs.length} 个输出`
                    : `运行失败 · ${runState.detail || "请检查本地服务和模型"}`}
            </div>
            {runState.outputs.length ? (
              <div className="mt-3 flex gap-2 overflow-x-auto">
                {runState.outputs.map((output) => (
                  <a
                    key={`${output.kind}:${output.filename}`}
                    href={output.url}
                    target="_blank"
                    rel="noreferrer"
                    className="block shrink-0 overflow-hidden rounded-lg border bg-background"
                  >
                    {output.kind === "images" ? (
                      <img
                        src={output.url}
                        alt={output.filename}
                        className="h-24 w-32 object-cover"
                      />
                    ) : (
                      <span className="block max-w-40 truncate px-3 py-2">
                        {output.filename}
                      </span>
                    )}
                  </a>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        <div className="mt-8 flex items-center border-t border-border-subtle pt-3">
          <button
            onClick={() => setTab("market")}
            className={cn(
              "relative h-9 px-0 text-[12px] font-medium",
              tab === "market"
                ? "text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-foreground"
                : "text-muted-foreground",
            )}
          >
            精选工作流
          </button>
          <button
            onClick={() => setTab("mine")}
            className={cn(
              "relative ml-7 h-9 px-0 text-[12px] font-medium",
              tab === "mine"
                ? "text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-foreground"
                : "text-muted-foreground",
            )}
          >
            我的工作流
          </button>
          <span className="flex-1" />
          <div className="relative w-60">
            <SearchIcon className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="h-9 rounded-[10px] pl-9 text-[11px]"
              placeholder="搜索工作流"
            />
          </div>
          {selectedWorkflow ? (
            <Button
              variant="ghost"
              size="icon"
              className="ml-2 size-9 rounded-xl bg-foreground text-background hover:bg-foreground/85 hover:text-background"
              onClick={() => setSelectedWorkflowId(null)}
              aria-label="退出详情"
            >
              <XIcon className="size-4" />
            </Button>
          ) : null}
        </div>
        {selectedWorkflow ? (
          <div className="mt-6 grid min-h-[520px] grid-cols-[minmax(0,1fr)_240px] gap-8">
            <section className="min-w-0">
              <h2 className="text-[20px] font-semibold leading-7">
                {selectedWorkflow.title}
              </h2>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {selectedWorkflow.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-md bg-muted px-2 py-1 text-[9px] text-muted-foreground"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <p className="mt-7 max-w-2xl whitespace-pre-line text-[12px] leading-6 text-muted-foreground">
                {selectedWorkflow.description}
                {"\n"}
                {selectedWorkflow.availability === "dependency"
                  ? "这是能力目录，需要先安装页面列出的模型或扩展，Echo 不会静默下载大模型。"
                  : "工作流可在本机手动运行，也可加入画布交给 Agent 调用。模型和输入文件始终由你选择。"}
              </p>
              <Button
                className="mt-7 h-11 w-full max-w-xl rounded-xl bg-violet-600 text-white hover:bg-violet-700"
                disabled={selectedWorkflow.availability === "dependency"}
                onClick={() =>
                  onUse(selectedWorkflow.id, selectedWorkflow.title)
                }
              >
                {selectedWorkflow.availability === "dependency"
                  ? "缺少本地依赖"
                  : "加入画布"}
              </Button>
              <div className="mt-7 max-w-xl rounded-2xl border border-border-default p-4">
                <div className="flex items-center gap-2">
                  <h3 className="text-[12px] font-semibold">资源文件</h3>
                  {selectedWorkflowDetail ? (
                    <span className="text-[9px] text-muted-foreground">
                      {selectedWorkflowNodes.length} 个节点 ·{" "}
                      {selectedWorkflowResources.length + 1} 个文件/输入
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-[9px] text-muted-foreground">
                  工作流文件以及运行时需要由用户选择的本地资源
                </p>
                <div className="mt-3 space-y-2 text-[10px]">
                  <div className="flex items-center gap-2 rounded-lg bg-muted/45 px-3 py-2">
                    <WorkflowIcon className="size-3.5" />
                    <span className="min-w-0 flex-1 truncate">
                      {selectedWorkflow.id}.json
                    </span>
                    <span className="text-muted-foreground">工作流</span>
                  </div>
                  {selectedWorkflowResources.length ? (
                    selectedWorkflowResources.map((resource) => (
                      <div
                        key={`${resource.nodeType}:${resource.key}:${resource.value}`}
                        className="flex items-center gap-2 rounded-lg bg-muted/45 px-3 py-2"
                      >
                        <ArchiveIcon className="size-3.5" />
                        <span className="min-w-0 flex-1 truncate">
                          {resource.value}
                        </span>
                        <span className="shrink-0 text-muted-foreground">
                          {resource.key}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="flex items-center gap-2 rounded-lg bg-muted/45 px-3 py-2">
                      <ArchiveIcon className="size-3.5" />
                      <span className="min-w-0 flex-1 truncate">
                        {selectedWorkflow.availability === "dependency"
                          ? selectedWorkflow.tags.join(" · ")
                          : "未声明额外模型文件"}
                      </span>
                      <span className="text-muted-foreground">本地资源</span>
                    </div>
                  )}
                </div>
                {selectedWorkflowNodeTypes.length ? (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {selectedWorkflowNodeTypes.map((nodeType) => (
                      <span
                        key={nodeType}
                        className="rounded bg-muted px-1.5 py-1 text-[8px] text-muted-foreground"
                      >
                        {nodeType}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
              {selectedWorkflowDiagnostics ? (
                <div
                  className={cn(
                    "mt-4 max-w-xl rounded-2xl border p-4",
                    selectedWorkflowDiagnostics.compatible
                      ? "border-emerald-200 bg-emerald-50/60 dark:border-emerald-900 dark:bg-emerald-950/25"
                      : selectedWorkflowDiagnostics.counts.errors
                        ? "border-red-200 bg-red-50/60 dark:border-red-900 dark:bg-red-950/25"
                        : "border-amber-200 bg-amber-50/60 dark:border-amber-900 dark:bg-amber-950/25",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <h3 className="text-[12px] font-semibold">兼容性诊断</h3>
                    <span className="text-[9px] text-muted-foreground">
                      {selectedWorkflowDiagnostics.compatible
                        ? "本机可运行"
                        : selectedWorkflowDiagnostics.fullyChecked
                          ? `${selectedWorkflowDiagnostics.counts.errors} 个错误 · ${selectedWorkflowDiagnostics.counts.warnings} 个警告`
                          : "检查未完成"}
                    </span>
                  </div>
                  <p className="mt-1 text-[9px] leading-4 text-muted-foreground">
                    已核对节点类型、必填输入、枚举值和本地模型文件；不会自动安装或下载。
                  </p>
                  {selectedWorkflowDiagnostics.issues.length ? (
                    <div className="mt-3 space-y-1.5">
                      {selectedWorkflowDiagnostics.issues
                        .slice(0, 8)
                        .map((issue, index) => (
                          <div
                            key={`${issue.kind}:${issue.nodeId ?? "global"}:${index}`}
                            className="flex gap-2 rounded-lg bg-background/75 px-2.5 py-2 text-[9px]"
                          >
                            <span
                              className={cn(
                                "mt-1 size-1.5 shrink-0 rounded-full",
                                issue.severity === "error"
                                  ? "bg-red-500"
                                  : "bg-amber-500",
                              )}
                            />
                            <span className="min-w-0 flex-1">
                              {issue.detail}
                            </span>
                          </div>
                        ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              <div className="mt-5 max-w-xl">
                <h3 className="text-[12px] font-semibold">来源与许可</h3>
                <div className="mt-2 rounded-xl border border-border-default px-3 py-2.5 text-[10px]">
                  <div className="font-medium">Echo 原创工作流模板</div>
                  <div className="mt-0.5 text-muted-foreground">
                    Apache-2.0 · 兼容 ComfyUI 工作流格式
                  </div>
                </div>
              </div>
            </section>
            <aside className="space-y-2">
              {visibleWorkflows.map((workflow) => (
                <button
                  key={workflow.id}
                  onClick={() => setSelectedWorkflowId(workflow.id)}
                  className={cn(
                    "w-full rounded-xl border p-3 text-left transition hover:bg-muted/50",
                    workflow.id === selectedWorkflow.id
                      ? "border-violet-300 bg-violet-50/60"
                      : "border-border-subtle",
                  )}
                >
                  <div className="truncate text-[10px] font-semibold">
                    {workflow.title}
                  </div>
                  <p className="mt-1 line-clamp-2 text-[9px] leading-4 text-muted-foreground">
                    {workflow.description}
                  </p>
                </button>
              ))}
            </aside>
          </div>
        ) : (
          <div className="mt-5 grid grid-cols-2 gap-5 md:grid-cols-4">
            {visibleWorkflows.map((workflow, index) => (
              <div
                key={workflow.id}
                className={cn(
                  "group overflow-hidden rounded-[12px] border border-border-subtle bg-background text-left transition",
                  workflow.availability !== "dependency"
                    ? "hover:-translate-y-0.5 hover:shadow-lg"
                    : "opacity-75 hover:border-amber-300",
                )}
              >
                <button
                  type="button"
                  onClick={() => setSelectedWorkflowId(workflow.id)}
                  className="block w-full text-left"
                >
                  <div
                    className={cn(
                      "relative h-28 overflow-hidden",
                      index % 4 === 0
                        ? "bg-[radial-gradient(circle_at_68%_28%,rgba(255,255,255,.9),transparent_18%),radial-gradient(circle_at_35%_70%,#b7d7ff,transparent_36%),linear-gradient(135deg,#4d6687,#101827)]"
                        : index % 4 === 1
                          ? "bg-[radial-gradient(circle_at_28%_30%,#dfd5ff,transparent_28%),radial-gradient(circle_at_72%_75%,#8ca6ff,transparent_35%),linear-gradient(135deg,#1f2937,#63558d)]"
                          : index % 4 === 2
                            ? "bg-[radial-gradient(circle_at_65%_22%,#ffe7c2,transparent_24%),radial-gradient(circle_at_28%_80%,#9fd1bb,transparent_38%),linear-gradient(135deg,#263b39,#80714d)]"
                            : "bg-[radial-gradient(circle_at_24%_35%,#ffc7df,transparent_25%),radial-gradient(circle_at_78%_75%,#bc9cff,transparent_35%),linear-gradient(135deg,#3b304f,#76647b)]",
                    )}
                  >
                    <div className="absolute inset-0 bg-[linear-gradient(115deg,transparent_20%,rgba(255,255,255,.22)_21%,transparent_22%,transparent_48%,rgba(255,255,255,.12)_49%,transparent_50%)] opacity-70" />
                    <img
                      src={
                        COMFY_WORKFLOW_COVERS[
                          index % COMFY_WORKFLOW_COVERS.length
                        ]
                      }
                      alt=""
                      className="absolute inset-0 size-full object-cover opacity-90 transition duration-300 group-hover:scale-[1.03]"
                    />
                    <div className="absolute inset-0 bg-gradient-to-r from-black/45 via-black/10 to-transparent" />
                    <div className="absolute left-4 top-4 text-white drop-shadow-sm">
                      <div className="text-[8px] font-semibold tracking-[0.22em] text-white/70">
                        ECHO FLOW
                      </div>
                      <div className="mt-1.5 max-w-36 text-[15px] font-semibold leading-[18px]">
                        {workflow.title}
                      </div>
                    </div>
                    <span className="absolute bottom-3 left-4 rounded-full border border-white/25 bg-black/20 px-2 py-1 text-[7px] font-medium text-white/85 backdrop-blur-sm">
                      {workflow.tags[0] || "WORKFLOW"}
                    </span>
                    <WorkflowIcon className="absolute bottom-3 right-3 size-8 text-white/65 transition-transform group-hover:scale-110" />
                    <span className="absolute inset-0 flex items-center justify-center gap-1.5 bg-black/40 opacity-0 backdrop-blur-[1px] transition-opacity group-hover:opacity-100">
                      <span
                        className={cn(
                          "rounded-lg px-3 py-1.5 text-[9px] font-medium",
                          workflow.availability === "dependency"
                            ? "bg-white/85 text-zinc-500"
                            : "bg-white text-zinc-950",
                        )}
                      >
                        {workflow.availability === "dependency"
                          ? "查看依赖"
                          : "查看详情"}
                      </span>
                    </span>
                  </div>
                  <div className="p-3 pb-2">
                    <div className="flex items-center gap-2 text-[12px] font-semibold">
                      <span className="min-w-0 flex-1 truncate">
                        {workflow.title}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-1.5 py-0.5 text-[8px] font-medium",
                          workflow.availability !== "dependency"
                            ? "bg-emerald-50 text-emerald-700"
                            : "bg-amber-50 text-amber-700",
                        )}
                      >
                        {workflow.availability === "bundled"
                          ? "已内置"
                          : workflow.availability === "user"
                            ? "已导入"
                            : "需依赖"}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-muted-foreground">
                      {workflow.description}
                    </p>
                    <div className="mt-2 flex items-center text-[8px] text-muted-foreground">
                      <span className="rounded bg-muted px-1.5 py-0.5">
                        {workflow.source === "user" ? "用户导入" : "Echo 原创"}
                      </span>
                      <span className="ml-auto">{workflow.tags[0]}</span>
                    </div>
                  </div>
                </button>
                <div className="flex gap-1 border-t border-border-subtle px-3 py-2 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 flex-1 rounded-lg text-[9px]"
                    disabled={workflow.availability === "dependency"}
                    onClick={() => onUse(workflow.id, workflow.title)}
                  >
                    加入画布
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 flex-1 rounded-lg text-[9px]"
                    disabled={
                      workflow.availability === "dependency" ||
                      (runState?.workflowId === workflow.id &&
                        ["queued", "running"].includes(runState.state))
                    }
                    onClick={() => void runWorkflow(workflow.id)}
                  >
                    直接运行
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
        {online === false ? (
          <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900">
            <div className="font-semibold">没有发现 ComfyUI</div>
            <p className="mt-1 leading-5 text-amber-800">
              启动本机 ComfyUI 并监听 127.0.0.1:8188 后再检测。Echo
              只连接你的本地服务，不会自动下载数十 GB 的模型。
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function DesignPage({
  embeddedProject,
}: {
  embeddedProject?: { id: string; name?: string | null };
} = {}) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const personaId = useActiveAgentId() ?? DEFAULT_PRIMARY_AGENT_ID;
  const [creativeProjects, setCreativeProjects] = useState<
    LocalCreativeProject[]
  >(() => readLocalCreativeProjects(personaId));
  useEffect(() => {
    const refresh = () =>
      setCreativeProjects(readLocalCreativeProjects(personaId));
    refresh();
    window.addEventListener(CREATIVE_PROJECTS_CHANGED_EVENT, refresh);
    return () =>
      window.removeEventListener(CREATIVE_PROJECTS_CHANGED_EVENT, refresh);
  }, [personaId]);
  const projectId = embeddedProject?.id || null;
  const projectName = embeddedProject?.name || null;
  const creativeProjectId = embeddedProject
    ? null
    : searchParams.get("creative_project")?.trim() || null;
  const currentCreativeProject = creativeProjects.find(
    (project) => project.id === creativeProjectId,
  );
  const canvasScopeName =
    projectName || currentCreativeProject?.name || "创作空间";
  const storageKey = projectId
    ? `${DESIGN_CANVAS_STORAGE_KEY}:project:${projectId}`
    : creativeCanvasStorageKey(
        DESIGN_CANVAS_STORAGE_KEY,
        personaId,
        creativeProjectId,
      );
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasFileInputRef = useRef<HTMLInputElement>(null);
  const [section, setSection] = useState<DesignSection>(
    projectId || creativeProjectId ? "canvas" : "home",
  );
  const [layout, setLayout] = useState<WorkspaceLayout>(() => {
    if (embeddedProject) return "canvas";
    if (typeof window === "undefined") return "chat-left";
    const saved = window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
    return saved === "split" ||
      saved === "chat-left" ||
      saved === "chat" ||
      saved === "canvas"
      ? saved
      : "chat-left";
  });
  const [layoutOpen, setLayoutOpen] = useState(false);
  const handleCreativeProjectChange = useCallback(
    (nextProjectId: string | null) => {
      const next = new URLSearchParams(searchParams);
      next.delete("project");
      next.delete("name");
      next.delete("workspace_path");
      if (nextProjectId) {
        next.set("creative_project", nextProjectId);
      } else {
        next.delete("creative_project");
      }
      setSection("canvas");
      const query = next.toString();
      navigate(`/workspace/design${query ? `?${query}` : ""}`);
    },
    [navigate, searchParams],
  );
  useEffect(() => {
    if (embeddedProject) return;
    if (
      !searchParams.has("workspace_path") &&
      !searchParams.has("project") &&
      !searchParams.has("name")
    )
      return;
    const next = new URLSearchParams(searchParams);
    next.delete("workspace_path");
    next.delete("project");
    next.delete("name");
    const query = next.toString();
    navigate(`/workspace/design${query ? `?${query}` : ""}`, {
      replace: true,
    });
  }, [embeddedProject, navigate, searchParams]);
  useEffect(() => {
    if (creativeProjectId && !currentCreativeProject) {
      handleCreativeProjectChange(null);
    }
  }, [creativeProjectId, currentCreativeProject, handleCreativeProjectChange]);
  const [document, setDocumentState] = useState<DesignCanvasDocument>(() => {
    const raw =
      typeof window === "undefined"
        ? null
        : window.localStorage.getItem(storageKey);
    const initial = parseDesignCanvas(raw);
    return canvasScopeName && !raw
      ? { ...initial, title: `${canvasScopeName} · 创作画布` }
      : initial;
  });
  const undoHistoryRef = useRef<DesignCanvasDocument[]>([]);
  const redoHistoryRef = useRef<DesignCanvasDocument[]>([]);
  const dragHistorySnapshotRef = useRef<DesignCanvasDocument | null>(null);
  const [, setHistoryVersion] = useState(0);
  const setDocument = useCallback(
    (action: SetStateAction<DesignCanvasDocument>) => {
      setDocumentState((current) => {
        const next = typeof action === "function" ? action(current) : action;
        if (
          next === current ||
          JSON.stringify(next) === JSON.stringify(current)
        )
          return current;
        undoHistoryRef.current = [
          ...undoHistoryRef.current.slice(-79),
          structuredClone(current),
        ];
        redoHistoryRef.current = [];
        return next;
      });
      setHistoryVersion((value) => value + 1);
    },
    [],
  );
  const activeStorageKeyRef = useRef(storageKey);
  const documentRef = useRef(document);
  documentRef.current = document;
  const undoCanvas = useCallback(() => {
    const previous = undoHistoryRef.current.pop();
    if (!previous) return;
    redoHistoryRef.current.push(structuredClone(documentRef.current));
    setDocumentState(previous);
    setHistoryVersion((value) => value + 1);
  }, []);
  const redoCanvas = useCallback(() => {
    const next = redoHistoryRef.current.pop();
    if (!next) return;
    undoHistoryRef.current.push(structuredClone(documentRef.current));
    setDocumentState(next);
    setHistoryVersion((value) => value + 1);
  }, []);
  const beginCanvasTransaction = useCallback(() => {
    dragHistorySnapshotRef.current = structuredClone(documentRef.current);
  }, []);
  const endCanvasTransaction = useCallback(() => {
    const before = dragHistorySnapshotRef.current;
    dragHistorySnapshotRef.current = null;
    if (
      !before ||
      JSON.stringify(before) === JSON.stringify(documentRef.current)
    )
      return;
    undoHistoryRef.current = [...undoHistoryRef.current.slice(-79), before];
    redoHistoryRef.current = [];
    setHistoryVersion((value) => value + 1);
  }, []);
  const serverRevisionRef = useRef(0);
  const serverReadyRef = useRef(false);
  const activeServerProjectRef = useRef<string | null>(projectId);
  const lastSyncedDocumentRef = useRef("");
  const serverSaveChainRef = useRef(Promise.resolve());
  const canvasChannelRef = useRef<BroadcastChannel | null>(null);
  const presenceClientIdRef = useRef(
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `canvas-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
  );
  const presencePointerRef = useRef<{ x: number; y: number } | null>(null);
  const [presenceMembers, setPresenceMembers] = useState<
    CanvasPresenceMember[]
  >([]);
  const [canvasSyncState, setCanvasSyncState] = useState<CanvasSyncState>(
    projectId ? "loading" : "local",
  );
  const [pendingCanvasConflict, setPendingCanvasConflict] =
    useState<PendingCanvasConflict | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectionRect, setSelectionRect] = useState<{
    x: number;
    y: number;
    width: number;
    height: number;
  } | null>(null);
  const [connectionSourceId, setConnectionSourceId] = useState<string | null>(
    null,
  );
  const [connectionPointer, setConnectionPointer] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [canvasClipboard, setCanvasClipboard] =
    useState<DesignCanvasClipboard | null>(null);
  const canvasPasteCountRef = useRef(0);
  const [nodeContextMenu, setNodeContextMenu] = useState<{
    x: number;
    y: number;
    target: "node" | "pane";
    flowX: number;
    flowY: number;
  } | null>(null);
  const [pendingLargeDelete, setPendingLargeDelete] = useState<{
    plan: DesignCanvasDeletionPlan;
    snapshot: string;
  } | null>(null);
  const [renameNode, setRenameNode] = useState<{
    id: string;
    value: string;
  } | null>(null);
  const [tagNode, setTagNode] = useState<{
    id: string;
    value: string;
  } | null>(null);
  const [assetAction, setAssetAction] = useState<"library" | "project" | null>(
    null,
  );
  const [toolMode, setToolMode] = useState<ToolMode>("select");
  const [toolModeOpen, setToolModeOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpDialog, setHelpDialog] = useState<"tutorial" | "shortcuts" | null>(
    null,
  );
  const [feedbackDialog, setFeedbackDialog] = useState<
    "feedback" | "wish" | null
  >(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [spacePanning, setSpacePanning] = useState(false);
  const [zoom, setZoom] = useState(0.83);
  const [pan, setPan] = useState({ x: 80, y: 80 });
  const [canvasView, setCanvasView] = useState<{
    pattern: CanvasBackgroundPattern;
    tone: CanvasBackgroundTone;
    showEdges: boolean;
    showMinimap: boolean;
  }>(() => {
    const fallback = {
      pattern: "dots" as CanvasBackgroundPattern,
      tone: "default" as CanvasBackgroundTone,
      showEdges: true,
      showMinimap: false,
    };
    if (typeof window === "undefined") return fallback;
    try {
      const saved = JSON.parse(
        window.localStorage.getItem(CANVAS_VIEW_STORAGE_KEY) || "null",
      ) as Partial<typeof fallback> | null;
      return {
        pattern:
          saved?.pattern === "grid" || saved?.pattern === "none"
            ? saved.pattern
            : "dots",
        tone: CANVAS_BACKGROUND_TONES.some((tone) => tone.id === saved?.tone)
          ? (saved?.tone as CanvasBackgroundTone)
          : "default",
        showEdges: saved?.showEdges !== false,
        showMinimap: saved?.showMinimap === true,
      };
    } catch {
      return fallback;
    }
  });
  const [canvasSettingsOpen, setCanvasSettingsOpen] = useState(false);
  const [tidyOpen, setTidyOpen] = useState(false);
  const [tidyGroup, setTidyGroup] = useState<"category" | "layout" | null>(
    null,
  );
  const [tidySnapshot, setTidySnapshot] = useState<DesignCanvasDocument | null>(
    null,
  );
  const [addOpen, setAddOpen] = useState(false);
  const [stickerOpen, setStickerOpen] = useState(false);
  const [stickerMode, setStickerMode] = useState<"follow" | "free">("follow");
  const [clearStickersConfirmOpen, setClearStickersConfirmOpen] =
    useState(false);
  const [addNodePosition, setAddNodePosition] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [assetsOpen, setAssetsOpen] = useState(false);
  const [embeddedSurface, setEmbeddedSurface] = useState<EmbeddedSurface>(null);
  const [comfyNative, setComfyNative] = useState(false);
  const [embeddedChatUrl, setEmbeddedChatUrl] = useState<string | null>(null);
  const { skills, isLoading: skillsLoading } = useSkills();
  useEffect(() => {
    window.localStorage.setItem(
      CANVAS_VIEW_STORAGE_KEY,
      JSON.stringify(canvasView),
    );
  }, [canvasView]);
  useEffect(() => {
    if (!embeddedProject)
      window.localStorage.setItem(WORKSPACE_LAYOUT_STORAGE_KEY, layout);
  }, [embeddedProject, layout]);
  useEffect(() => {
    if (document.mode !== "workflow") {
      setConnectionSourceId(null);
      setConnectionPointer(null);
    }
  }, [document.mode]);
  useEffect(() => {
    const isEditable = (target: EventTarget | null) => {
      const element = target as HTMLElement | null;
      return Boolean(
        element?.isContentEditable ||
        element?.closest("input, textarea, select, [contenteditable='true']"),
      );
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (isEditable(event.target)) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redoCanvas();
        else undoCanvas();
      } else if (event.key === "Escape") {
        setConnectionSourceId(null);
        setConnectionPointer(null);
        setSelectionRect(null);
      } else if (event.code === "Space") {
        event.preventDefault();
        setSpacePanning(true);
      } else if (event.key.toLowerCase() === "v") {
        setToolMode("select");
      } else if (event.key.toLowerCase() === "h") {
        setToolMode("hand");
      }
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code === "Space") setSpacePanning(false);
    };
    const onBlur = () => setSpacePanning(false);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [redoCanvas, undoCanvas]);
  const reconcileRemoteCanvas = useCallback(
    (payload: CanvasServerPayload) => {
      if (!projectId || !payload.document) return;
      const revision = payload.revision ?? 0;
      if (revision <= serverRevisionRef.current) return;
      const remote = parseDesignCanvas(JSON.stringify(payload.document));
      const remoteSerialized = JSON.stringify(remote);
      const local = documentRef.current;
      const localSerialized = JSON.stringify(local);
      const isDirty = localSerialized !== lastSyncedDocumentRef.current;

      serverRevisionRef.current = revision;
      if (!isDirty) {
        lastSyncedDocumentRef.current = remoteSerialized;
        serverReadyRef.current = true;
        setPendingCanvasConflict(null);
        setDocument(remote);
        setCanvasSyncState("saved");
        return;
      }

      const base = parseDesignCanvas(lastSyncedDocumentRef.current);
      const merged = mergeDesignCanvases(base, local, remote);
      lastSyncedDocumentRef.current = remoteSerialized;
      if (merged.conflicts.length === 0) {
        serverReadyRef.current = true;
        setDocument(merged.document);
        setCanvasSyncState("saving");
        toast.success("已合并其他成员的画布更新");
        return;
      }

      serverReadyRef.current = false;
      setPendingCanvasConflict({
        revision,
        remote,
        merged: merged.document,
        conflicts: merged.conflicts,
      });
      setDocument(merged.document);
      setCanvasSyncState("conflict");
      toast.warning("同一画布内容被多人修改，请确认保留方式");
    },
    [projectId, setDocument],
  );

  const pullRemoteCanvas = useCallback(async () => {
    if (!projectId || pendingCanvasConflict) return;
    const response = await fetch(
      `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/canvas`,
      { headers: authHeaders() },
    );
    if (!response.ok)
      throw new Error(`canvas refresh failed: ${response.status}`);
    reconcileRemoteCanvas((await response.json()) as CanvasServerPayload);
  }, [pendingCanvasConflict, projectId, reconcileRemoteCanvas]);

  useEffect(() => {
    activeServerProjectRef.current = projectId;
    serverReadyRef.current = false;
    serverRevisionRef.current = 0;
    lastSyncedDocumentRef.current = "";
    setPendingCanvasConflict(null);
    if (!projectId) {
      setCanvasSyncState("local");
      return;
    }
    const controller = new AbortController();
    setCanvasSyncState("loading");
    void fetch(
      `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/canvas`,
      { headers: authHeaders(), signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`canvas load failed: ${response.status}`);
        return (await response.json()) as CanvasServerPayload;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        serverRevisionRef.current = payload.revision ?? 0;
        serverReadyRef.current = true;
        if (payload.document) {
          const remote = parseDesignCanvas(JSON.stringify(payload.document));
          lastSyncedDocumentRef.current = JSON.stringify(remote);
          setDocument(remote);
        } else {
          // Trigger the save effect once so an existing local project canvas
          // becomes the first shared server revision.
          setDocument((current) => ({ ...current }));
        }
        setCanvasSyncState("saved");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.warn("Failed to load project canvas", error);
        setCanvasSyncState("error");
      });
    return () => controller.abort();
  }, [projectId, setDocument]);

  useEffect(() => {
    if (!projectId || typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(`echo:design:${projectId}`);
    canvasChannelRef.current = channel;
    channel.onmessage = (event: MessageEvent<CanvasServerPayload>) => {
      reconcileRemoteCanvas(event.data);
    };
    return () => {
      canvasChannelRef.current = null;
      channel.close();
    };
  }, [projectId, reconcileRemoteCanvas]);

  const presenceDisplayName =
    user?.username?.trim() || user?.actor_id?.trim() || "本地成员";
  useEffect(() => {
    if (!projectId) {
      setPresenceMembers([]);
      return;
    }
    let stopped = false;
    let failureReported = false;
    const clientId = presenceClientIdRef.current;
    const endpoint = `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/presence`;
    const heartbeat = async () => {
      if (window.document.visibilityState === "hidden") return;
      const pointer = section === "canvas" ? presencePointerRef.current : null;
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({
            client_id: clientId,
            display_name: presenceDisplayName,
            x: pointer?.x ?? null,
            y: pointer?.y ?? null,
            section,
          }),
        });
        if (!response.ok)
          throw new Error(`presence heartbeat failed: ${response.status}`);
        const payload = (await response.json()) as {
          self_id?: string;
          items?: CanvasPresenceMember[];
        };
        if (stopped) return;
        setPresenceMembers(
          (payload.items ?? []).filter(
            (member) => member.id !== payload.self_id,
          ),
        );
        failureReported = false;
      } catch (error) {
        if (!failureReported) {
          console.warn("Failed to update Design presence", error);
          failureReported = true;
        }
      }
    };
    void heartbeat();
    const timer = window.setInterval(() => void heartbeat(), 750);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      setPresenceMembers([]);
      void fetch(`${endpoint}/${encodeURIComponent(clientId)}`, {
        method: "DELETE",
        headers: authHeaders(),
        keepalive: true,
      }).catch(() => undefined);
    };
  }, [presenceDisplayName, projectId, section]);

  useEffect(() => {
    if (!projectId || pendingCanvasConflict) return;
    const timer = window.setInterval(() => {
      void pullRemoteCanvas().catch((error: unknown) => {
        console.warn("Failed to refresh project canvas", error);
      });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [pendingCanvasConflict, projectId, pullRemoteCanvas]);

  useEffect(() => {
    if (activeStorageKeyRef.current !== storageKey) {
      activeStorageKeyRef.current = storageKey;
      const raw = window.localStorage.getItem(storageKey);
      const next = parseDesignCanvas(raw);
      undoHistoryRef.current = [];
      redoHistoryRef.current = [];
      setHistoryVersion((value) => value + 1);
      setDocumentState(
        canvasScopeName && !raw
          ? { ...next, title: `${canvasScopeName} · 创作画布` }
          : next,
      );
      return;
    }
    window.localStorage.setItem(storageKey, JSON.stringify(document));
  }, [canvasScopeName, document, storageKey]);
  useEffect(() => {
    if (!projectId || !serverReadyRef.current) return;
    const serialized = JSON.stringify(document);
    if (serialized === lastSyncedDocumentRef.current) return;
    const timer = window.setTimeout(() => {
      serverSaveChainRef.current = serverSaveChainRef.current.then(async () => {
        if (
          !serverReadyRef.current ||
          activeServerProjectRef.current !== projectId
        )
          return;
        setCanvasSyncState("saving");
        try {
          const response = await fetch(
            `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/canvas`,
            {
              method: "PUT",
              headers: {
                "Content-Type": "application/json",
                ...authHeaders(),
              },
              body: JSON.stringify({
                expected_revision: serverRevisionRef.current,
                document: JSON.parse(serialized) as Record<string, unknown>,
              }),
            },
          );
          if (response.status === 409) {
            await pullRemoteCanvas();
            return;
          }
          if (!response.ok)
            throw new Error(`canvas save failed: ${response.status}`);
          const payload = (await response.json()) as { revision?: number };
          if (activeServerProjectRef.current !== projectId) return;
          serverRevisionRef.current =
            payload.revision ?? serverRevisionRef.current + 1;
          lastSyncedDocumentRef.current = serialized;
          setCanvasSyncState("saved");
          canvasChannelRef.current?.postMessage({
            revision: serverRevisionRef.current,
            document: JSON.parse(serialized) as Record<string, unknown>,
          } satisfies CanvasServerPayload);
        } catch (error) {
          console.warn("Failed to save project canvas", error);
          setCanvasSyncState("error");
        }
      });
    }, 650);
    return () => window.clearTimeout(timer);
  }, [document, projectId, pullRemoteCanvas]);

  const resolveCanvasConflict = useCallback(
    (choice: "merge" | "remote") => {
      if (!pendingCanvasConflict) return;
      serverRevisionRef.current = pendingCanvasConflict.revision;
      serverReadyRef.current = true;
      setPendingCanvasConflict(null);
      if (choice === "remote") {
        const serialized = JSON.stringify(pendingCanvasConflict.remote);
        lastSyncedDocumentRef.current = serialized;
        setDocument(pendingCanvasConflict.remote);
        setCanvasSyncState("saved");
        toast.success("已载入成员的最新画布");
        return;
      }
      setCanvasSyncState("saving");
      setDocument({ ...pendingCanvasConflict.merged });
    },
    [pendingCanvasConflict, setDocument],
  );
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type === "echo.design.close-surface") {
        setEmbeddedSurface(null);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
  const selectedNode = document.nodes.find((node) => node.id === selectedId);
  const selectNode = useCallback((id: string, additive: boolean) => {
    if (!additive) {
      setSelectedIds([id]);
      setSelectedId(id);
      return;
    }
    setSelectedIds((current) => {
      const next = current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id];
      setSelectedId(next.at(-1) ?? null);
      return next;
    });
  }, []);
  const patchNode = useCallback(
    (id: string, patch: Partial<DesignCanvasNode>, record = true) =>
      (record ? setDocument : setDocumentState)((current) => ({
        ...current,
        nodes: current.nodes.map((node) =>
          node.id === id
            ? {
                ...node,
                ...patch,
                positions:
                  typeof patch.x === "number" || typeof patch.y === "number"
                    ? {
                        ...node.positions,
                        [current.mode]: {
                          x: patch.x ?? node.x,
                          y: patch.y ?? node.y,
                        },
                      }
                    : node.positions,
              }
            : node,
        ),
      })),
    [setDocument],
  );
  const moveCanvasNode = useCallback(
    (id: string, x: number, y: number) =>
      setDocumentState((current) => {
        const source = current.nodes.find((node) => node.id === id);
        if (!source) return current;
        const dx = x - source.x;
        const dy = y - source.y;
        const moving = new Set(
          source.kind === "group"
            ? [source.id, ...(source.childIds ?? [])]
            : selectedIds.includes(id) && selectedIds.length > 1
              ? selectedIds
              : [id],
        );
        if (source.kind !== "sticker") {
          current.nodes.forEach((node) => {
            if (
              node.kind === "sticker" &&
              node.attachedTo &&
              moving.has(node.attachedTo)
            )
              moving.add(node.id);
          });
        }
        return {
          ...current,
          nodes: current.nodes.map((node) => {
            if (!moving.has(node.id)) return node;
            const nextX = node.x + dx;
            const nextY = node.y + dy;
            return {
              ...node,
              x: nextX,
              y: nextY,
              positions: {
                ...node.positions,
                [current.mode]: { x: nextX, y: nextY },
              },
            };
          }),
        };
      }),
    [selectedIds],
  );
  const connectFromTo = useCallback(
    (sourceId: string, targetId: string) => {
      setDocument((current) => connectDesignNodes(current, sourceId, targetId));
      setConnectionSourceId(null);
      setConnectionPointer(null);
    },
    [setDocument],
  );
  const completeConnection = useCallback(
    (targetId: string) => {
      if (!connectionSourceId) return;
      connectFromTo(connectionSourceId, targetId);
    },
    [connectFromTo, connectionSourceId],
  );
  const startConnection = useCallback(
    (sourceId: string, event: ReactPointerEvent<HTMLButtonElement>) => {
      setConnectionSourceId(sourceId);
      const source = document.nodes.find((node) => node.id === sourceId);
      if (source) {
        setConnectionPointer({
          x: source.x + (source.width ?? NODE_WIDTH) + 64,
          y: source.y + (source.height ?? NODE_HEIGHT) / 2,
        });
      }
      const startX = event.clientX;
      const startY = event.clientY;
      let dragged = false;
      const move = (next: PointerEvent) => {
        if (
          Math.abs(next.clientX - startX) > 4 ||
          Math.abs(next.clientY - startY) > 4
        )
          dragged = true;
        const bounds = stageRef.current?.getBoundingClientRect();
        if (!bounds) return;
        setConnectionPointer({
          x: (next.clientX - bounds.left - pan.x) / zoom,
          y: (next.clientY - bounds.top - pan.y) / zoom,
        });
      };
      const end = (next: PointerEvent) => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
        if (!dragged) return;
        const target = globalThis.document
          .elementFromPoint(next.clientX, next.clientY)
          ?.closest<HTMLElement>("[data-connection-target]");
        const targetId = target?.dataset.connectionTarget;
        if (targetId) {
          connectFromTo(sourceId, targetId);
          return;
        }
        const bounds = stageRef.current?.getBoundingClientRect();
        if (
          !bounds ||
          next.clientX < bounds.left ||
          next.clientX > bounds.right ||
          next.clientY < bounds.top ||
          next.clientY > bounds.bottom
        )
          return;
        setAddNodePosition({
          x: (next.clientX - bounds.left - pan.x) / zoom - NODE_WIDTH / 2,
          y: (next.clientY - bounds.top - pan.y) / zoom - NODE_HEIGHT / 2,
        });
        setAddOpen(true);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end, { once: true });
    },
    [connectFromTo, document.nodes, pan.x, pan.y, zoom],
  );
  const copySelection = useCallback(() => {
    if (!selectedIds.length) return;
    const clipboard = copyDesignSelection(document, selectedIds);
    setCanvasClipboard(clipboard);
    canvasPasteCountRef.current = 0;
    setNodeContextMenu(null);
    toast.success(`已复制 ${clipboard.nodes.length} 个节点`);
  }, [document, selectedIds]);
  const pasteSelection = useCallback(
    (clipboard = canvasClipboard, targetCenter?: { x: number; y: number }) => {
      if (!clipboard?.nodes.length) return;
      canvasPasteCountRef.current += 1;
      const result = pasteDesignSelection(
        document,
        clipboard,
        Date.now().toString(36),
        targetCenter ? 0 : 32 * canvasPasteCountRef.current,
        targetCenter,
      );
      setDocument(result.document);
      setSelectedIds(result.nodeIds);
      setSelectedId(result.nodeIds.at(-1) ?? null);
      setNodeContextMenu(null);
    },
    [canvasClipboard, document, setDocument],
  );
  const duplicateSelection = useCallback(() => {
    if (!selectedIds.length) return;
    pasteSelection(copyDesignSelection(document, selectedIds));
  }, [document, pasteSelection, selectedIds]);
  const groupSelection = useCallback(() => {
    const eligible = selectedIds.filter(
      (id) => document.nodes.find((node) => node.id === id)?.kind !== "group",
    );
    if (eligible.length < 2) return;
    const groupId = nextNodeId("group");
    setDocument((current) => groupDesignNodes(current, eligible, groupId));
    setSelectedIds([groupId]);
    setSelectedId(groupId);
  }, [document.nodes, selectedIds, setDocument]);
  const ungroupSelection = useCallback(() => {
    if (!selectedNode || selectedNode.kind !== "group") return;
    const children = selectedNode.childIds ?? [];
    setDocument((current) => ungroupDesignNode(current, selectedNode.id));
    setSelectedIds(children);
    setSelectedId(children.at(-1) ?? null);
  }, [selectedNode, setDocument]);
  const addNode = useCallback(
    (
      kind: DesignNodeKind,
      title: string,
      description: string,
      binding?: DesignCanvasNode["binding"],
      extras?: Pick<DesignCanvasNode, "asset" | "height" | "width">,
    ) => {
      const rect = stageRef.current?.getBoundingClientRect();
      const node: DesignCanvasNode = {
        id: nextNodeId(kind),
        kind,
        title,
        description,
        binding,
        ...extras,
        x:
          addNodePosition?.x ??
          ((rect?.width ?? 850) / 2 - pan.x) / zoom - NODE_WIDTH / 2,
        y:
          addNodePosition?.y ??
          ((rect?.height ?? 620) / 2 - pan.y) / zoom - NODE_HEIGHT / 2,
      };
      node.positions = {
        [document.mode]: { x: node.x, y: node.y },
      };
      setDocument((current) =>
        appendDesignNode(
          current,
          node,
          current.mode === "workflow"
            ? (connectionSourceId ?? selectedId)
            : null,
        ),
      );
      setSelectedId(node.id);
      setSelectedIds([node.id]);
      setConnectionSourceId(null);
      setConnectionPointer(null);
      setAddOpen(false);
      setAddNodePosition(null);
      return node.id;
    },
    [
      addNodePosition,
      connectionSourceId,
      document.mode,
      pan.x,
      pan.y,
      selectedId,
      setDocument,
      zoom,
    ],
  );
  const addSticker = useCallback(
    (emoji: string) => {
      const rect = stageRef.current?.getBoundingClientRect();
      const target =
        stickerMode === "follow" &&
        selectedNode &&
        selectedNode.kind !== "sticker" &&
        selectedNode.kind !== "group"
          ? selectedNode
          : null;
      const x = target
        ? target.x + (target.width ?? NODE_WIDTH) - 24
        : ((rect?.width ?? 850) / 2 - pan.x) / zoom - 28;
      const y = target
        ? target.y - 24
        : ((rect?.height ?? 620) / 2 - pan.y) / zoom - 28;
      const node: DesignCanvasNode = {
        id: nextNodeId("sticker"),
        kind: "sticker",
        title: target ? "跟随目标" : "自由贴纸",
        description: target ? `跟随 ${target.title}` : "固定在画布位置",
        emoji,
        attachedTo: target?.id,
        x,
        y,
        width: 56,
        height: 56,
        positions: { [document.mode]: { x, y } },
      };
      setDocument((current) => appendDesignNode(current, node));
      setSelectedId(node.id);
      setSelectedIds([node.id]);
      setStickerOpen(false);
    },
    [document.mode, pan.x, pan.y, selectedNode, setDocument, stickerMode, zoom],
  );
  const placeOrLocateArtifact = useCallback(
    (artifact: ProjectArtifact) => {
      const existing = document.nodes.find(
        (node) => node.asset?.id === artifact.id,
      );
      if (existing) {
        const rect = stageRef.current?.getBoundingClientRect();
        setSelectedId(existing.id);
        setSelectedIds([existing.id]);
        setPan({
          x:
            (rect?.width ?? 850) / 2 -
            (existing.x + (existing.width ?? NODE_WIDTH) / 2) * zoom,
          y:
            (rect?.height ?? 620) / 2 -
            (existing.y + (existing.height ?? NODE_HEIGHT) / 2) * zoom,
        });
        toast.success("已在画布中定位");
        return;
      }
      const kind = artifactNodeKind(artifact);
      addNode(
        kind,
        artifact.name,
        artifact.summary || artifact.path || artifact.kind || "可复用资产",
        { type: "asset", id: artifact.id },
        {
          height: artifact.url
            ? kind === "audio"
              ? 88
              : kind === "image" || kind === "video"
                ? 240
                : undefined
            : undefined,
          asset: {
            id: artifact.id,
            kind: artifact.kind || kind,
            path: artifact.path,
            url: artifact.url,
            projectId: artifact.category ? undefined : (projectId ?? undefined),
            source: artifact.task_id || artifact.milestone_id,
          },
        },
      );
      toast.success("资产已加入画布");
    },
    [addNode, document.nodes, projectId, zoom],
  );
  const uploadHomeFiles = useCallback(
    async (files: FileList) => {
      if (!projectId) {
        toast.info("请先从输入框下方选择一个项目");
        return;
      }
      const body = new FormData();
      Array.from(files)
        .slice(0, 12)
        .forEach((file) => body.append("files", file));
      try {
        const response = await fetch(
          `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/assets`,
          { method: "POST", headers: authHeaders(), body },
        );
        const payload = (await response.json()) as {
          items?: ProjectArtifact[];
        };
        if (!response.ok || !payload.items?.length)
          throw new Error("project upload failed");
        for (const artifact of payload.items) placeOrLocateArtifact(artifact);
        setSection("canvas");
        toast.success(`已把 ${payload.items.length} 个文件加入画布`);
      } catch {
        toast.error("项目文件上传失败");
      }
    },
    [placeOrLocateArtifact, projectId],
  );
  const applyDeletion = useCallback(
    (nodeIds: string[]) => {
      setDocument((current) => deleteDesignSelection(current, nodeIds));
      setSelectedId(null);
      setSelectedIds([]);
    },
    [setDocument],
  );
  const removeSelected = useCallback(() => {
    if (!selectedIds.length) return;
    const plan = planDesignSelectionDeletion(document, selectedIds);
    if (plan.highBlast) {
      setPendingLargeDelete({ plan, snapshot: JSON.stringify(document) });
      setNodeContextMenu(null);
      return;
    }
    applyDeletion(plan.nodeIds);
  }, [applyDeletion, document, selectedIds]);
  useEffect(() => {
    const onClipboardShortcut = (event: KeyboardEvent) => {
      const element = event.target as HTMLElement | null;
      if (
        element?.isContentEditable ||
        element?.closest("input, textarea, select, [contenteditable='true']")
      )
        return;
      const command = event.metaKey || event.ctrlKey;
      const key = event.key.toLowerCase();
      if (command && key === "c") {
        event.preventDefault();
        copySelection();
      } else if (command && key === "v") {
        event.preventDefault();
        pasteSelection();
      } else if (command && key === "d") {
        event.preventDefault();
        duplicateSelection();
      } else if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        removeSelected();
      }
    };
    window.addEventListener("keydown", onClipboardShortcut);
    return () => window.removeEventListener("keydown", onClipboardShortcut);
  }, [copySelection, duplicateSelection, pasteSelection, removeSelected]);
  useEffect(() => {
    if (!nodeContextMenu) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target?.closest("[data-canvas-context-menu]"))
        setNodeContextMenu(null);
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNodeContextMenu(null);
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", dismissWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", dismissWithEscape);
    };
  }, [nodeContextMenu]);
  useEffect(() => {
    if (!addOpen) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("[data-add-node-menu], [data-add-node-trigger]"))
        return;
      setAddOpen(false);
      if (connectionSourceId) {
        setConnectionSourceId(null);
        setConnectionPointer(null);
      }
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setAddOpen(false);
      setConnectionSourceId(null);
      setConnectionPointer(null);
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", dismissWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", dismissWithEscape);
    };
  }, [addOpen, connectionSourceId]);
  useEffect(() => {
    if (!toolModeOpen) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("[data-tool-mode-menu], [data-tool-mode-trigger]"))
        return;
      setToolModeOpen(false);
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setToolModeOpen(false);
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", dismissWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", dismissWithEscape);
    };
  }, [toolModeOpen]);
  useEffect(() => {
    if (!helpOpen) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.closest("[data-canvas-help-menu], [data-canvas-help-trigger]")
      )
        return;
      setHelpOpen(false);
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setHelpOpen(false);
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", dismissWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", dismissWithEscape);
    };
  }, [helpOpen]);
  const fitCanvas = useCallback(() => {
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect || !document.nodes.length) return;
    const minX = Math.min(...document.nodes.map((node) => node.x));
    const minY = Math.min(...document.nodes.map((node) => node.y));
    const maxX = Math.max(
      ...document.nodes.map((node) => node.x + (node.width ?? NODE_WIDTH)),
    );
    const maxY = Math.max(
      ...document.nodes.map((node) => node.y + (node.height ?? NODE_HEIGHT)),
    );
    const next = Math.min(
      1,
      Math.max(
        MIN_ZOOM,
        Math.min(
          (rect.width - 120) / (maxX - minX),
          (rect.height - 120) / (maxY - minY),
        ),
      ),
    );
    setZoom(next);
    setPan({
      x: (rect.width - (maxX - minX) * next) / 2 - minX * next,
      y: (rect.height - (maxY - minY) * next) / 2 - minY * next,
    });
  }, [document.nodes]);
  const applyTidy = useCallback(
    (mode: DesignCanvasTidyMode) => {
      setTidySnapshot(document);
      setDocument(tidyDesignCanvas(document, mode));
      setTidyOpen(false);
    },
    [document, setDocument],
  );
  const runCanvas = useCallback(
    (extra?: string) => {
      const base = designCanvasRunPrompt(document);
      const prompt = extra?.trim() ? `${extra.trim()}\n\n${base}` : base;
      const agent =
        document.nodes.find((node) => node.binding?.type === "agent")?.binding
          ?.id ?? "general";
      const params = new URLSearchParams({
        prompt,
        agent,
        embedded: "design",
      });
      if (projectId) params.set("project", projectId);
      if (!embeddedProject) {
        params.set("creation_space", personaId);
        if (creativeProjectId)
          params.set("creative_project", creativeProjectId);
      }
      const shellBase = window.location.href.split("#", 1)[0];
      setEmbeddedChatUrl(
        `${shellBase}#/workspace/realtime/new?${params.toString()}`,
      );
      if (layout === "canvas") setLayout("chat-left");
    },
    [
      creativeProjectId,
      document,
      embeddedProject,
      layout,
      personaId,
      projectId,
    ],
  );
  const nodeAssetFile = useCallback(async (node: DesignCanvasNode) => {
    if (!node.asset?.url) throw new Error("当前节点没有可保存的文件");
    const url = node.asset.url.startsWith("/")
      ? `${getBackendBaseURL()}${node.asset.url}`
      : node.asset.url;
    const response = await fetch(url, { headers: authHeaders() });
    if (!response.ok) throw new Error(`读取画布文件失败 (${response.status})`);
    const blob = await response.blob();
    const pathName = node.asset.path?.split("/").at(-1)?.trim();
    const urlName = (() => {
      try {
        return new URL(url, window.location.href).pathname.split("/").at(-1);
      } catch {
        return undefined;
      }
    })();
    const fallbackExtension =
      node.kind === "image"
        ? ".png"
        : node.kind === "video"
          ? ".mp4"
          : node.kind === "audio"
            ? ".mp3"
            : ".bin";
    const rawName = pathName || urlName || `${node.title}${fallbackExtension}`;
    const filename =
      rawName.replace(/[\\/:*?"<>|]/g, "-").slice(0, 180) ||
      `asset${fallbackExtension}`;
    return new File([blob], filename, {
      type: blob.type || "application/octet-stream",
    });
  }, []);
  const copyNodeAssetReference = useCallback(async (node: DesignCanvasNode) => {
    const reference =
      node.asset?.path || node.asset?.url || node.asset?.id || node.id;
    try {
      await navigator.clipboard.writeText(reference);
      toast.success("已复制资产引用");
    } catch {
      toast.error("复制失败，请检查剪贴板权限");
    }
  }, []);
  const downloadNodeAsset = useCallback(
    async (node: DesignCanvasNode) => {
      try {
        const file = await nodeAssetFile(node);
        const url = URL.createObjectURL(file);
        const anchor = globalThis.document.createElement("a");
        anchor.href = url;
        anchor.download = file.name;
        anchor.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast.success("已开始另存文件");
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "另存文件失败");
      }
    },
    [nodeAssetFile],
  );
  const revealNodeAsset = useCallback(async (node: DesignCanvasNode) => {
    if (!node.asset?.path || !window.echo?.desktop) return;
    const result = await window.echo.desktop.openItem(node.asset.path);
    if (!result.ok) toast.error(result.error || "无法在 Finder 中打开");
  }, []);
  const saveNodeToLibrary = useCallback(
    async (node: DesignCanvasNode) => {
      setAssetAction("library");
      try {
        const file = await nodeAssetFile(node);
        const body = new FormData();
        body.append("file", file);
        body.append("name", node.title.trim() || file.name);
        body.append("category", "自定义");
        body.append("description", node.description.trim());
        body.append(
          "tags",
          node.tags?.length ? node.tags.join(",") : "画布,Design",
        );
        body.append("persona_id", personaId);
        const response = await fetch(
          `${getBackendBaseURL()}/api/design/assets`,
          {
            method: "POST",
            headers: authHeaders(),
            body,
          },
        );
        const payload = (await response.json().catch(() => ({}))) as {
          item?: DesignLibraryAsset;
          detail?: string;
        };
        if (!response.ok || !payload.item)
          throw new Error(payload.detail || "资产保存失败");
        toast.success("已存为资产，可在其他项目复用");
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "资产保存失败");
      } finally {
        setAssetAction(null);
      }
    },
    [nodeAssetFile, personaId],
  );
  const saveNodeToProject = useCallback(
    async (node: DesignCanvasNode) => {
      if (!projectId) {
        toast.info("请先为画布选择项目");
        return;
      }
      setAssetAction("project");
      try {
        const file = await nodeAssetFile(node);
        const body = new FormData();
        body.append("files", file);
        const response = await fetch(
          `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/assets`,
          { method: "POST", headers: authHeaders(), body },
        );
        const payload = (await response.json().catch(() => ({}))) as {
          items?: ProjectArtifact[];
          detail?: string;
        };
        const artifact = payload.items?.[0];
        if (!response.ok || !artifact)
          throw new Error(payload.detail || "项目资产保存失败");
        patchNode(node.id, {
          binding: { type: "asset", id: artifact.id },
          asset: {
            id: artifact.id,
            kind: artifact.kind || node.asset?.kind || node.kind,
            path: artifact.path,
            url: artifact.url,
            projectId,
            source: node.asset?.source,
          },
        });
        toast.success("已存到项目资产，并绑定当前节点");
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : "项目资产保存失败",
        );
      } finally {
        setAssetAction(null);
      }
    },
    [nodeAssetFile, patchNode, projectId],
  );
  const addNodeAssetToChat = useCallback(
    (node: DesignCanvasNode) => {
      const reference = [
        `请在本次对话中使用画布资产「${node.title}」。`,
        `资产 ID：${node.asset?.id ?? node.id}`,
        node.asset?.path ? `资产路径：${node.asset.path}` : "",
        node.asset?.url ? `资产地址：${node.asset.url}` : "",
        node.description ? `说明：${node.description}` : "",
        node.tags?.length ? `标签：${node.tags.join("、")}` : "",
      ]
        .filter(Boolean)
        .join("\n");
      runCanvas(reference);
      setNodeContextMenu(null);
      toast.success("已添加到对话");
    },
    [runCanvas],
  );
  const submitDesignFeedback = useCallback(async () => {
    if (!feedbackDialog || !feedbackText.trim()) return;
    setFeedbackSaving(true);
    try {
      const response = await fetch(`${getBackendBaseURL()}/api/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          sentiment: feedbackDialog === "wish" ? "liked" : "disliked",
          agent_id: "echo-design",
          reason:
            feedbackDialog === "wish"
              ? "design_feature_request"
              : "design_feedback",
          content_preview: feedbackText.trim(),
        }),
      });
      if (!response.ok) throw new Error(`反馈保存失败 (${response.status})`);
      toast.success(
        feedbackDialog === "wish" ? "功能愿望已记录" : "反馈已记录",
      );
      setFeedbackDialog(null);
      setFeedbackText("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "反馈保存失败");
    } finally {
      setFeedbackSaving(false);
    }
  }, [feedbackDialog, feedbackText]);
  const handleStagePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    setNodeContextMenu(null);
    const isHand = toolMode === "hand" || spacePanning;
    if (connectionSourceId && event.target === event.currentTarget && !isHand) {
      const bounds = event.currentTarget.getBoundingClientRect();
      setAddNodePosition({
        x: (event.clientX - bounds.left - pan.x) / zoom - NODE_WIDTH / 2,
        y: (event.clientY - bounds.top - pan.y) / zoom - NODE_HEIGHT / 2,
      });
      setAddOpen(true);
      return;
    }
    if (event.target !== event.currentTarget && !isHand) return;
    const startX = event.clientX;
    const startY = event.clientY;
    if (isHand) {
      const origin = pan;
      const move = (next: PointerEvent) =>
        setPan({
          x: origin.x + next.clientX - startX,
          y: origin.y + next.clientY - startY,
        });
      const end = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end, { once: true });
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    const originX = (startX - bounds.left - pan.x) / zoom;
    const originY = (startY - bounds.top - pan.y) / zoom;
    const additive = event.shiftKey || event.metaKey;
    let latest = { x: originX, y: originY, width: 0, height: 0 };
    setSelectionRect(latest);
    const move = (next: PointerEvent) => {
      const cursorX = (next.clientX - bounds.left - pan.x) / zoom;
      const cursorY = (next.clientY - bounds.top - pan.y) / zoom;
      latest = {
        x: Math.min(originX, cursorX),
        y: Math.min(originY, cursorY),
        width: Math.abs(cursorX - originX),
        height: Math.abs(cursorY - originY),
      };
      setSelectionRect(latest);
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      setSelectionRect(null);
      if (latest.width < 3 && latest.height < 3) {
        if (!additive) {
          setSelectedId(null);
          setSelectedIds([]);
        }
        return;
      }
      const matched = document.nodes
        .filter((node) => {
          const width =
            node.width ?? (node.kind === "sticker" ? 56 : NODE_WIDTH);
          const height =
            node.height ?? (node.kind === "sticker" ? 56 : NODE_HEIGHT);
          return (
            node.x < latest.x + latest.width &&
            node.x + width > latest.x &&
            node.y < latest.y + latest.height &&
            node.y + height > latest.y
          );
        })
        .map((node) => node.id);
      setSelectedIds((current) => {
        const next = additive
          ? Array.from(new Set([...current, ...matched]))
          : matched;
        setSelectedId(next.at(-1) ?? null);
        return next;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end, { once: true });
  };
  const handleWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      setPan((current) => ({
        x: current.x - event.deltaX,
        y: current.y - event.deltaY,
      }));
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    const pointerX = event.clientX - bounds.left;
    const pointerY = event.clientY - bounds.top;
    setZoom((currentZoom) => {
      const nextZoom = Math.min(
        MAX_ZOOM,
        Math.max(MIN_ZOOM, currentZoom * (event.deltaY > 0 ? 0.9 : 1.1)),
      );
      const ratio = nextZoom / currentZoom;
      setPan((currentPan) => ({
        x: pointerX - (pointerX - currentPan.x) * ratio,
        y: pointerY - (pointerY - currentPan.y) * ratio,
      }));
      return nextZoom;
    });
  };
  const handleStagePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const bounds = stageRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const pointer = {
      x: (event.clientX - bounds.left - pan.x) / zoom,
      y: (event.clientY - bounds.top - pan.y) / zoom,
    };
    presencePointerRef.current = pointer;
    if (connectionSourceId) setConnectionPointer(pointer);
  };
  const canvasTone =
    CANVAS_BACKGROUND_TONES.find((tone) => tone.id === canvasView.tone) ??
    CANVAS_BACKGROUND_TONES[0]!;
  const minimapBounds = useMemo(() => {
    if (!document.nodes.length) return null;
    const minX = Math.min(...document.nodes.map((node) => node.x));
    const minY = Math.min(...document.nodes.map((node) => node.y));
    const maxX = Math.max(
      ...document.nodes.map((node) => node.x + (node.width ?? NODE_WIDTH)),
    );
    const maxY = Math.max(
      ...document.nodes.map((node) => node.y + (node.height ?? NODE_HEIGHT)),
    );
    return {
      minX,
      minY,
      width: Math.max(1, maxX - minX),
      height: Math.max(1, maxY - minY),
    };
  }, [document.nodes]);

  const canvasSurface = (
    <main
      ref={stageRef}
      data-testid="design-infinite-canvas"
      onPointerDown={handleStagePointerDown}
      onDoubleClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        setAddNodePosition({
          x: (event.clientX - bounds.left - pan.x) / zoom - NODE_WIDTH / 2,
          y: (event.clientY - bounds.top - pan.y) / zoom - NODE_HEIGHT / 2,
        });
        setAddOpen(true);
      }}
      onContextMenu={(event) => {
        if (event.target !== event.currentTarget) return;
        event.preventDefault();
        const bounds = event.currentTarget.getBoundingClientRect();
        setSelectedId(null);
        setSelectedIds([]);
        setNodeContextMenu({
          x: Math.max(
            8,
            Math.min(event.clientX - bounds.left, bounds.width - 248),
          ),
          y: Math.max(
            8,
            Math.min(event.clientY - bounds.top, bounds.height - 230),
          ),
          target: "pane",
          flowX: (event.clientX - bounds.left - pan.x) / zoom,
          flowY: (event.clientY - bounds.top - pan.y) / zoom,
        });
      }}
      onPointerMove={handleStagePointerMove}
      onPointerLeave={() => {
        presencePointerRef.current = null;
      }}
      onWheel={handleWheel}
      className={cn(
        "relative min-w-0 flex-1 touch-none overflow-hidden transition-colors dark:bg-[#0a0a0a]",
        (toolMode === "hand" || spacePanning) &&
          "cursor-grab active:cursor-grabbing",
      )}
      style={{ backgroundColor: canvasTone.color }}
    >
      <input
        ref={canvasFileInputRef}
        type="file"
        multiple
        hidden
        accept="image/*,video/*,audio/*,.pdf,.txt,.md,.csv,.xls,.xlsx,.ppt,.pptx,.doc,.docx"
        onChange={(event) => {
          if (event.target.files?.length)
            void uploadHomeFiles(event.target.files);
          event.target.value = "";
        }}
      />
      {canvasView.pattern !== "none" ? (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-55"
          style={{
            backgroundImage:
              canvasView.pattern === "dots"
                ? "radial-gradient(circle, color-mix(in oklch, var(--foreground) 18%, transparent) 1px, transparent 1px)"
                : "linear-gradient(color-mix(in oklch, var(--foreground) 10%, transparent) 1px, transparent 1px), linear-gradient(90deg, color-mix(in oklch, var(--foreground) 10%, transparent) 1px, transparent 1px)",
            backgroundSize: `${24 * zoom}px ${24 * zoom}px`,
            backgroundPosition: `${pan.x}px ${pan.y}px`,
          }}
        />
      ) : null}
      <div
        className="pointer-events-none absolute left-0 top-0 h-[3000px] w-[4200px] origin-top-left"
        style={{
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
        }}
      >
        {canvasView.showEdges ? (
          <EdgeLayer
            document={document}
            connectionSourceId={connectionSourceId}
            pointer={connectionPointer}
          />
        ) : null}
        {[...document.nodes]
          .sort((left, right) =>
            left.kind === "group" && right.kind !== "group"
              ? -1
              : right.kind === "group" && left.kind !== "group"
                ? 1
                : 0,
          )
          .map((node) => (
            <CanvasNode
              key={node.id}
              node={node}
              selected={selectedIds.includes(node.id)}
              zoom={zoom}
              mode={spacePanning ? "hand" : toolMode}
              onSelect={(additive) => selectNode(node.id, additive)}
              onMove={(x, y) => moveCanvasNode(node.id, x, y)}
              onMoveStart={beginCanvasTransaction}
              onMoveEnd={endCanvasTransaction}
              onContextMenu={(event) => {
                event.preventDefault();
                event.stopPropagation();
                if (!selectedIds.includes(node.id)) selectNode(node.id, false);
                const bounds = stageRef.current?.getBoundingClientRect();
                setNodeContextMenu({
                  x: Math.max(
                    8,
                    Math.min(
                      event.clientX - (bounds?.left ?? 0),
                      (bounds?.width ?? 900) - 188,
                    ),
                  ),
                  y: Math.max(
                    8,
                    Math.min(
                      event.clientY - (bounds?.top ?? 0),
                      (bounds?.height ?? 700) - 230,
                    ),
                  ),
                  target: "node",
                  flowX: (event.clientX - (bounds?.left ?? 0) - pan.x) / zoom,
                  flowY: (event.clientY - (bounds?.top ?? 0) - pan.y) / zoom,
                });
              }}
              onDownload={() => void downloadNodeAsset(node)}
              onMediaDimensions={(mediaWidth, mediaHeight) => {
                const next = fitDesignMediaNodeDimensions(
                  mediaWidth,
                  mediaHeight,
                );
                if (node.width === next.width && node.height === next.height)
                  return;
                patchNode(node.id, next, false);
              }}
              showPorts={document.mode === "workflow"}
              connecting={connectionSourceId === node.id}
              onStartConnection={(event) => startConnection(node.id, event)}
              onCompleteConnection={() => completeConnection(node.id)}
            />
          ))}
        {selectionRect ? (
          <div
            aria-hidden
            className="absolute border border-violet-500/70 bg-violet-500/10"
            style={{
              transform: `translate(${selectionRect.x}px, ${selectionRect.y}px)`,
              width: selectionRect.width,
              height: selectionRect.height,
            }}
          />
        ) : null}
      </div>
      {!document.nodes.length ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <div className="rounded-[14px] border border-black/[0.06] bg-white/75 px-5 py-3 text-center shadow-[0_8px_28px_-20px_rgba(0,0,0,.35)] backdrop-blur dark:border-white/10 dark:bg-black/45">
            <div className="text-[11px] font-medium">
              双击画布，自由生成节点
            </div>
            <div className="mt-1 text-[9px] text-muted-foreground">
              拖动框选 · 滚动缩放 · 按住 Space 拖动画布
            </div>
          </div>
        </div>
      ) : null}
      {presenceMembers
        .filter(
          (member) =>
            member.section === "canvas" &&
            typeof member.x === "number" &&
            typeof member.y === "number",
        )
        .map((member) => (
          <div
            key={member.id}
            aria-hidden
            className="pointer-events-none absolute z-10 transition-[left,top] duration-200 ease-out"
            style={{
              left: pan.x + (member.x ?? 0) * zoom,
              top: pan.y + (member.y ?? 0) * zoom,
            }}
          >
            <svg
              width="18"
              height="22"
              viewBox="0 0 18 22"
              className="drop-shadow-sm"
            >
              <path
                d="M2 1.5 16 12l-7.1 1.2L5.4 20Z"
                fill={member.color}
                stroke="white"
                strokeWidth="1.5"
                strokeLinejoin="round"
              />
            </svg>
            <span
              className="absolute left-3 top-4 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[9px] font-medium text-white shadow-sm"
              style={{ backgroundColor: member.color }}
            >
              {member.display_name}
            </span>
          </div>
        ))}
      {nodeContextMenu ? (
        <div
          role="menu"
          aria-label="画布节点菜单"
          data-canvas-context-menu
          onPointerDown={(event) => event.stopPropagation()}
          className={cn(
            "absolute z-50 border border-black/[0.08] bg-white/95 shadow-[0_16px_40px_-18px_rgba(0,0,0,.38)] backdrop-blur dark:border-white/10 dark:bg-[#181818]/95",
            nodeContextMenu.target === "pane"
              ? "flex w-[240px] flex-col gap-0.5 rounded-lg p-1"
              : "w-48 overflow-hidden rounded-lg p-0",
          )}
          style={{ left: nodeContextMenu.x, top: nodeContextMenu.y }}
        >
          {nodeContextMenu.target === "pane" ? (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  canvasFileInputRef.current?.click();
                  setNodeContextMenu(null);
                }}
                className="flex h-9 w-full items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted"
              >
                上传
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setAddNodePosition({
                    x: nodeContextMenu.flowX - NODE_WIDTH / 2,
                    y: nodeContextMenu.flowY - NODE_HEIGHT / 2,
                  });
                  setAddOpen(true);
                  setNodeContextMenu(null);
                }}
                className="flex h-9 w-full items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted"
              >
                添加节点
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setStickerOpen(true);
                  setAddOpen(false);
                  setNodeContextMenu(null);
                }}
                className="flex h-9 w-full items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted"
              >
                添加贴纸
              </button>
              <div className="my-1 h-px bg-border-subtle" />
              <button
                type="button"
                role="menuitem"
                disabled={!undoHistoryRef.current.length}
                onClick={() => {
                  undoCanvas();
                  setNodeContextMenu(null);
                }}
                className="flex h-9 w-full items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted disabled:opacity-35"
              >
                <span>撤销</span>
                <kbd className="text-[10px] text-muted-foreground">⌘Z</kbd>
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={!redoHistoryRef.current.length}
                onClick={() => {
                  redoCanvas();
                  setNodeContextMenu(null);
                }}
                className="flex h-9 w-full items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted disabled:opacity-35"
              >
                <span>重做</span>
                <kbd className="text-[10px] text-muted-foreground">⇧⌘Z</kbd>
              </button>
              <div className="my-1 h-px bg-border-subtle" />
              <button
                type="button"
                role="menuitem"
                disabled={!canvasClipboard?.nodes.length}
                onClick={() =>
                  pasteSelection(canvasClipboard, {
                    x: nodeContextMenu.flowX,
                    y: nodeContextMenu.flowY,
                  })
                }
                className="flex h-9 w-full items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted disabled:opacity-35"
              >
                <span className="flex-1">粘贴</span>
                <kbd className="text-[10px] text-muted-foreground">⌘V</kbd>
              </button>
            </>
          ) : (
            <>
              {selectedIds.length === 1 && selectedNode?.asset?.url ? (
                <>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => addNodeAssetToChat(selectedNode)}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                  >
                    <MessageSquarePlusIcon className="size-3.5" />
                    添加到对话
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setNodeContextMenu(null);
                      void copyNodeAssetReference(selectedNode);
                    }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                  >
                    <CopyIcon className="size-3.5" />
                    复制
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setNodeContextMenu(null);
                      void downloadNodeAsset(selectedNode);
                    }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                  >
                    <DownloadIcon className="size-3.5" />
                    另存为
                  </button>
                </>
              ) : null}
              {selectedIds.length === 1 && selectedNode ? (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setRenameNode({
                      id: selectedNode.id,
                      value: selectedNode.title,
                    });
                    setNodeContextMenu(null);
                  }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                >
                  <PencilIcon className="size-3.5" />
                  重命名
                </button>
              ) : null}
              {selectedIds.length === 1 && selectedNode ? (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setTagNode({
                      id: selectedNode.id,
                      value: (selectedNode.tags ?? []).join("，"),
                    });
                    setNodeContextMenu(null);
                  }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                >
                  <TagIcon className="size-3.5" />
                  标签
                </button>
              ) : null}
              <button
                type="button"
                role="menuitem"
                disabled={!selectedIds.length}
                onClick={duplicateSelection}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted disabled:opacity-35"
              >
                <CopyPlusIcon className="size-3.5" />
                复制节点
              </button>
              {selectedIds.length === 1 && selectedNode?.asset?.url ? (
                <>
                  <div className="mx-1.5 my-1.5 h-px bg-border-subtle" />
                  <button
                    type="button"
                    role="menuitem"
                    disabled={assetAction !== null}
                    onClick={() => {
                      setNodeContextMenu(null);
                      void saveNodeToLibrary(selectedNode);
                    }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted disabled:opacity-50"
                  >
                    {assetAction === "library" ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <LibraryIcon className="size-3.5" />
                    )}
                    存为资产
                  </button>
                  {projectId && selectedNode.asset.projectId !== projectId ? (
                    <button
                      type="button"
                      role="menuitem"
                      disabled={assetAction !== null}
                      onClick={() => {
                        setNodeContextMenu(null);
                        void saveNodeToProject(selectedNode);
                      }}
                      className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted disabled:opacity-50"
                    >
                      {assetAction === "project" ? (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      ) : (
                        <FolderInputIcon className="size-3.5" />
                      )}
                      存到项目资产
                    </button>
                  ) : null}
                  {selectedNode.asset.path?.startsWith("/") &&
                  window.echo?.desktop ? (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setNodeContextMenu(null);
                        void revealNodeAsset(selectedNode);
                      }}
                      className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                    >
                      <FolderOpenIcon className="size-3.5" />在 Finder 中打开
                    </button>
                  ) : null}
                </>
              ) : null}
            </>
          )}
          {nodeContextMenu.target === "node" &&
          (selectedIds.length > 1 || selectedNode?.kind === "group") ? (
            <>
              <div className="mx-1.5 my-1.5 h-px bg-border-subtle" />
              {selectedNode?.kind === "group" ? (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    ungroupSelection();
                    setNodeContextMenu(null);
                  }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                >
                  <UngroupIcon className="size-3.5" />
                  解散分组
                </button>
              ) : (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    groupSelection();
                    setNodeContextMenu(null);
                  }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium hover:bg-muted"
                >
                  <GroupIcon className="size-3.5" />
                  编组
                </button>
              )}
            </>
          ) : null}
          {nodeContextMenu.target === "node" && selectedIds.length ? (
            <>
              <div className="mx-1.5 my-1.5 h-px bg-border-subtle" />
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  removeSelected();
                  setNodeContextMenu(null);
                }}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm font-medium text-destructive hover:bg-destructive/10"
              >
                <Trash2Icon className="size-3.5" />
                <span className="flex-1">删除</span>
                <kbd className="text-[9px] opacity-65">⌫</kbd>
              </button>
            </>
          ) : null}
        </div>
      ) : null}
      {canvasView.showMinimap && minimapBounds ? (
        <button
          type="button"
          onClick={fitCanvas}
          aria-label="小地图，点击适配全部内容"
          className="absolute bottom-16 right-3 z-20 h-28 w-40 overflow-hidden rounded-[12px] border border-black/[0.08] bg-white/88 shadow-[0_8px_24px_-16px_rgba(0,0,0,.4)] backdrop-blur dark:border-white/10 dark:bg-black/70"
        >
          <span className="absolute left-2 top-1.5 text-[8px] font-medium text-muted-foreground">
            小地图
          </span>
          <span className="absolute inset-x-2 bottom-2 top-6 rounded-lg bg-black/[0.035] dark:bg-white/[0.05]">
            {document.nodes.map((node) => (
              <span
                key={node.id}
                className={cn(
                  "absolute min-h-1 min-w-1 rounded-[2px] border border-black/10 bg-foreground/35",
                  node.id === selectedId && "bg-violet-500/80",
                )}
                style={{
                  left: `${((node.x - minimapBounds.minX) / minimapBounds.width) * 100}%`,
                  top: `${((node.y - minimapBounds.minY) / minimapBounds.height) * 100}%`,
                  width: `${Math.max(4, ((node.width ?? NODE_WIDTH) / minimapBounds.width) * 100)}%`,
                  height: `${Math.max(4, ((node.height ?? NODE_HEIGHT) / minimapBounds.height) * 100)}%`,
                }}
              />
            ))}
          </span>
        </button>
      ) : null}
      <div
        aria-label="画布缩放与视图工具"
        className="absolute left-1/2 top-3 z-20 flex h-9 -translate-x-1/2 items-center gap-0.5 rounded-[12px] border border-[#0000000f] bg-white/90 px-1 shadow-[0_2px_5px_rgba(0,0,0,.08)] backdrop-blur dark:border-[#4a4a4a] dark:bg-[#1a1a1a]/90"
      >
        <button
          data-add-node-trigger
          onClick={() => {
            setTidyOpen((value) => !value);
            setTidyGroup(null);
            setCanvasSettingsOpen(false);
          }}
          className="grid size-7 place-items-center rounded-lg hover:bg-muted"
          title="整理"
        >
          <Grid2X2Icon className="size-3.5" />
        </button>
        <button
          onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - 0.1))}
          className="grid size-7 place-items-center rounded-lg hover:bg-muted"
          title="缩小"
        >
          <MinusIcon className="size-3.5" />
        </button>
        <button
          onClick={fitCanvas}
          className="min-w-10 px-1 text-[10px] text-muted-foreground"
        >
          {Math.round(zoom * 100)}%
        </button>
        <button
          onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + 0.1))}
          className="grid size-7 place-items-center rounded-lg hover:bg-muted"
          title="放大"
        >
          <PlusIcon className="size-3.5" />
        </button>
        <button
          onClick={() => setCanvasSettingsOpen((value) => !value)}
          className={cn(
            "grid size-7 place-items-center rounded-lg hover:bg-muted",
            canvasSettingsOpen && "bg-muted",
          )}
          title="画布设置"
        >
          <Settings2Icon className="size-3.5" />
        </button>
        <button
          onClick={() =>
            setCanvasView((current) => ({
              ...current,
              showEdges: !current.showEdges,
            }))
          }
          className={cn(
            "grid size-7 place-items-center rounded-lg hover:bg-muted",
            !canvasView.showEdges && "bg-muted",
          )}
          title={canvasView.showEdges ? "隐藏连线" : "显示连线"}
        >
          <WorkflowIcon className="size-3.5" />
        </button>
        <button
          onClick={() =>
            setCanvasView((current) => ({
              ...current,
              showMinimap: !current.showMinimap,
            }))
          }
          className={cn(
            "grid size-7 place-items-center rounded-lg hover:bg-muted",
            canvasView.showMinimap && "bg-muted",
          )}
          title={canvasView.showMinimap ? "关闭小地图" : "小地图"}
        >
          <Grid2X2Icon className="size-3.5" />
        </button>
      </div>
      {tidyOpen ? (
        <div className="absolute left-1/2 top-14 z-40 w-36 -translate-x-[142px] rounded-[12px] border border-black/[0.08] bg-white/95 p-1.5 shadow-[0_16px_40px_-20px_rgba(0,0,0,.38)] backdrop-blur dark:border-white/10 dark:bg-[#181818]/95">
          {(
            [
              ["category", "分类整理"],
              ["layout", "布局整理"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onMouseEnter={() => setTidyGroup(id)}
              onClick={() => setTidyGroup(id)}
              className={cn(
                "flex h-8 w-full items-center rounded-lg px-2 text-left text-[10px] hover:bg-muted",
                tidyGroup === id && "bg-muted",
              )}
            >
              <span className="flex-1 font-medium">{label}</span>
              <ArrowRightIcon className="size-3 text-muted-foreground" />
            </button>
          ))}
          {tidyGroup ? (
            <div className="absolute right-[calc(100%+6px)] top-0 w-52 rounded-[12px] border border-black/[0.08] bg-white/95 p-1.5 shadow-[0_16px_40px_-20px_rgba(0,0,0,.38)] backdrop-blur dark:border-white/10 dark:bg-[#181818]/95">
              {(tidyGroup === "category"
                ? ([
                    ["connections", "按连线", "按上下游关系分层"],
                    ["media", "按媒体类型", "文本、图片、视频分组"],
                  ] as const)
                : ([
                    ["grid", "网格", "紧凑排列全部节点"],
                    ["horizontal", "水平", "从左到右排列"],
                    ["vertical", "垂直", "从上到下排列"],
                  ] as const)
              ).map(([id, label, description]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => applyTidy(id)}
                  className="flex w-full items-center rounded-lg px-2 py-2 text-left hover:bg-muted"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-[10px] font-medium">
                      {label}
                    </span>
                    <span className="block text-[9px] text-muted-foreground">
                      {description}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {tidySnapshot ? (
        <div className="absolute bottom-16 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-[12px] border border-black/[0.08] bg-white/95 px-3 py-2 text-[10px] shadow-[0_12px_30px_-18px_rgba(0,0,0,.45)] backdrop-blur dark:border-white/10 dark:bg-[#181818]/95">
          <span className="font-medium">已整理画布</span>
          <button
            type="button"
            onClick={() => setTidySnapshot(null)}
            className="rounded-md bg-foreground px-2 py-1 text-background"
          >
            保留
          </button>
          <button
            type="button"
            onClick={() => {
              setDocument(tidySnapshot);
              setTidySnapshot(null);
            }}
            className="rounded-md px-2 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            恢复
          </button>
        </div>
      ) : null}
      {canvasSettingsOpen ? (
        <div className="absolute left-1/2 top-14 z-40 w-64 translate-x-[-12px] rounded-[14px] border border-black/[0.08] bg-white/95 p-3 shadow-[0_16px_40px_-20px_rgba(0,0,0,.38)] backdrop-blur dark:border-white/10 dark:bg-[#181818]/95">
          <div className="text-[10px] font-semibold">画布设置</div>
          <div className="mt-3 text-[9px] text-muted-foreground">背景样式</div>
          <div className="mt-1.5 grid grid-cols-3 gap-1 rounded-lg bg-muted/60 p-1">
            {(
              [
                ["dots", "点阵"],
                ["grid", "网格"],
                ["none", "纯色"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                onClick={() =>
                  setCanvasView((current) => ({ ...current, pattern: id }))
                }
                className={cn(
                  "h-7 rounded-md text-[9px]",
                  canvasView.pattern === id
                    ? "bg-background font-medium shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="mt-3 text-[9px] text-muted-foreground">背景颜色</div>
          <div className="mt-2 grid grid-cols-9 gap-1.5">
            {CANVAS_BACKGROUND_TONES.map((tone) => (
              <button
                key={tone.id}
                onClick={() =>
                  setCanvasView((current) => ({
                    ...current,
                    tone: tone.id,
                  }))
                }
                title={tone.label}
                aria-label={`背景颜色：${tone.label}`}
                className={cn(
                  "size-5 rounded-full border border-black/10",
                  canvasView.tone === tone.id &&
                    "ring-2 ring-foreground ring-offset-1",
                )}
                style={{ backgroundColor: tone.color }}
              />
            ))}
          </div>
          <div className="mt-3 border-t border-border-subtle pt-2 text-[9px] text-muted-foreground">
            画布偏好会自动保存在本机
          </div>
        </div>
      ) : null}
      {connectionSourceId ? (
        <div className="absolute bottom-[68px] left-1/2 z-30 -translate-x-1/2 rounded-full border border-violet-200 bg-white/92 px-3 py-1.5 text-[9px] font-medium text-violet-700 shadow-sm backdrop-blur dark:border-violet-400/30 dark:bg-[#1a1a1a]/92 dark:text-violet-300">
          拖到目标端口完成连接 · 落到空白处新建并连接 · Esc 取消
        </div>
      ) : null}
      <div className="absolute bottom-4 left-1/2 z-30 flex h-11 -translate-x-1/2 items-center gap-1 rounded-[12px] border border-[#0000000f] bg-white/90 px-1.5 shadow-[0_2px_5px_rgba(0,0,0,.08)] backdrop-blur dark:border-[#4a4a4a] dark:bg-[#1a1a1a]/90">
        <button
          onClick={() => {
            setAddNodePosition(null);
            setAddOpen((value) => !value);
            setStickerOpen(false);
          }}
          className="grid size-8 place-items-center rounded-full bg-foreground text-background"
          title="添加节点"
        >
          <PlusIcon className="size-4" />
        </button>
        <div className="relative flex items-center" data-tool-mode-trigger>
          <button
            type="button"
            onClick={() => setToolMode(toolMode)}
            className="grid size-8 place-items-center rounded-l-lg hover:bg-muted"
            title={toolMode === "select" ? "移动 V" : "小手工具 H"}
          >
            {toolMode === "select" ? (
              <MousePointer2Icon className="size-4" />
            ) : (
              <HandIcon className="size-4" />
            )}
          </button>
          <button
            type="button"
            onClick={() => setToolModeOpen((value) => !value)}
            className="grid h-8 w-4 place-items-center rounded-r-lg hover:bg-muted"
            aria-label="移动 / 小手工具"
            aria-haspopup="menu"
            aria-expanded={toolModeOpen}
          >
            <ChevronDownIcon className="size-3" />
          </button>
          {toolModeOpen ? (
            <div
              data-tool-mode-menu
              role="menu"
              aria-label="移动 / 小手工具"
              className="absolute bottom-11 left-0 w-36 rounded-[10px] border border-black/[0.08] bg-background p-1 shadow-[0_8px_32px_rgba(0,0,0,.10)]"
            >
              {(
                [
                  ["select", "移动", "V", MousePointer2Icon],
                  ["hand", "小手工具", "H", HandIcon],
                ] as const
              ).map(([id, label, shortcut, Icon]) => (
                <button
                  key={id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={toolMode === id}
                  onClick={() => {
                    setToolMode(id);
                    setToolModeOpen(false);
                  }}
                  className="flex h-9 w-full items-center gap-2 rounded-lg px-2 text-[11px] font-medium hover:bg-muted"
                >
                  <Icon className="size-3.5" />
                  <span>{label}</span>
                  <kbd className="ml-auto text-[9px] text-muted-foreground">
                    {shortcut}
                  </kbd>
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <button
          onClick={() => setAssetsOpen((value) => !value)}
          className={cn(
            "grid size-8 place-items-center rounded-lg",
            assetsOpen ? "bg-muted" : "hover:bg-muted",
          )}
          title="项目资产"
        >
          <FolderIcon className="size-4" />
        </button>
        <div className="relative" data-canvas-help-trigger>
          <button
            type="button"
            onClick={() => setHelpOpen((value) => !value)}
            className="grid size-8 place-items-center rounded-lg hover:bg-muted"
            aria-label="帮助指南"
            aria-haspopup="menu"
            aria-expanded={helpOpen}
          >
            <CircleHelpIcon className="size-4" />
          </button>
          {helpOpen ? (
            <div
              data-canvas-help-menu
              role="menu"
              aria-label="帮助指南"
              className="absolute bottom-11 right-0 w-40 rounded-[10px] border border-black/[0.08] bg-background p-1 shadow-[0_8px_32px_rgba(0,0,0,.10)]"
            >
              {(
                [
                  ["tutorial", "教程", BookOpenIcon],
                  ["feedback", "反馈", MessageSquareIcon],
                  ["wish", "功能许愿", SparklesIcon],
                  ["shortcuts", "快捷键", WorkflowIcon],
                ] as const
              ).map(([id, label, Icon]) => (
                <button
                  key={id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setHelpOpen(false);
                    if (id === "tutorial" || id === "shortcuts")
                      setHelpDialog(id);
                    else {
                      setFeedbackDialog(id as "feedback" | "wish");
                      setFeedbackText("");
                    }
                  }}
                  className="flex h-9 w-full items-center gap-2 rounded-lg px-2 text-[11px] font-medium hover:bg-muted"
                >
                  <Icon className="size-3.5" />
                  {label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      {stickerOpen ? (
        <div className="absolute bottom-[72px] left-1/2 z-40 w-72 -translate-x-1/2 rounded-[16px] border border-[#e6e6e6] bg-white p-3 shadow-[0_8px_32px_rgba(0,0,0,.08)] dark:border-[#454545] dark:bg-[#1a1a1a]">
          <div className="flex items-center">
            <span className="text-[12px] font-semibold">添加贴纸</span>
            <span className="flex-1" />
            <button
              type="button"
              onClick={() => setClearStickersConfirmOpen(true)}
              disabled={!document.nodes.some((node) => node.kind === "sticker")}
              className="text-[9px] text-muted-foreground hover:text-foreground disabled:opacity-35"
            >
              清空全部贴纸
            </button>
          </div>
          <div className="mt-2 grid grid-cols-8 gap-1">
            {[
              "✨",
              "⭐",
              "❤️",
              "🔥",
              "💡",
              "✅",
              "🎬",
              "🎵",
              "📌",
              "🚀",
              "👏",
              "💬",
              "👀",
              "🎨",
              "⚡",
              "🌈",
            ].map((emoji) => (
              <button
                key={emoji}
                type="button"
                onClick={() => addSticker(emoji)}
                className="grid size-7 place-items-center rounded-lg text-lg hover:bg-muted"
              >
                {emoji}
              </button>
            ))}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-1 rounded-lg bg-muted/65 p-1 text-[9px]">
            <button
              type="button"
              onClick={() => setStickerMode("follow")}
              className={cn(
                "h-7 rounded-md",
                stickerMode === "follow"
                  ? "bg-background font-medium shadow-sm"
                  : "text-muted-foreground",
              )}
            >
              跟随目标
            </button>
            <button
              type="button"
              onClick={() => setStickerMode("free")}
              className={cn(
                "h-7 rounded-md",
                stickerMode === "free"
                  ? "bg-background font-medium shadow-sm"
                  : "text-muted-foreground",
              )}
            >
              自由贴纸
            </button>
          </div>
          <p className="mt-2 text-[9px] leading-4 text-muted-foreground">
            {stickerMode === "follow"
              ? selectedNode &&
                selectedNode.kind !== "sticker" &&
                selectedNode.kind !== "group"
                ? `将跟随「${selectedNode.title}」移动`
                : "先选择一个产物；未选择时会作为自由贴纸添加"
              : "自由贴纸固定在画布位置，不跟随产物"}
          </p>
        </div>
      ) : null}
      <Dialog
        open={clearStickersConfirmOpen}
        onOpenChange={setClearStickersConfirmOpen}
      >
        <DialogContent className="max-w-sm rounded-[16px]">
          <DialogHeader>
            <DialogTitle>清空全部贴纸？</DialogTitle>
            <DialogDescription>
              将移除当前画布中的跟随贴纸和自由贴纸。此操作可以整体撤销一次。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setClearStickersConfirmOpen(false)}
            >
              取消
            </Button>
            <Button
              onClick={() => {
                setDocument((current) => ({
                  ...current,
                  nodes: current.nodes.filter(
                    (node) => node.kind !== "sticker",
                  ),
                }));
                if (selectedNode?.kind === "sticker") {
                  setSelectedId(null);
                  setSelectedIds([]);
                }
                setClearStickersConfirmOpen(false);
                setStickerOpen(false);
              }}
            >
              清空
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(renameNode)}
        onOpenChange={(open) => {
          if (!open) setRenameNode(null);
        }}
      >
        <DialogContent className="max-w-sm rounded-[16px]">
          <DialogHeader>
            <DialogTitle>重命名画布节点</DialogTitle>
            <DialogDescription>
              名称会同步到画布保存记录和其他协作成员。
            </DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={renameNode?.value ?? ""}
            maxLength={120}
            aria-label="节点名称"
            onChange={(event) =>
              setRenameNode((current) =>
                current ? { ...current, value: event.target.value } : current,
              )
            }
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.nativeEvent.isComposing)
                return;
              const value = renameNode?.value.trim();
              if (!renameNode || !value) return;
              patchNode(renameNode.id, { title: value });
              setRenameNode(null);
            }}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameNode(null)}>
              取消
            </Button>
            <Button
              disabled={!renameNode?.value.trim()}
              onClick={() => {
                const value = renameNode?.value.trim();
                if (!renameNode || !value) return;
                patchNode(renameNode.id, { title: value });
                setRenameNode(null);
              }}
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(tagNode)}
        onOpenChange={(open) => {
          if (!open) setTagNode(null);
        }}
      >
        <DialogContent className="max-w-sm rounded-[16px]">
          <DialogHeader>
            <DialogTitle>编辑节点标签</DialogTitle>
            <DialogDescription>
              使用逗号分隔，最多保留 12 个标签；标签会随项目画布同步。
            </DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={tagNode?.value ?? ""}
            maxLength={400}
            aria-label="节点标签"
            placeholder="例如：角色，白港，竖屏"
            onChange={(event) =>
              setTagNode((current) =>
                current ? { ...current, value: event.target.value } : current,
              )
            }
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setTagNode(null)}>
              取消
            </Button>
            <Button
              onClick={() => {
                if (!tagNode) return;
                const tags = Array.from(
                  new Set(
                    tagNode.value
                      .split(/[,，]/)
                      .map((tag) => tag.trim().slice(0, 32))
                      .filter(Boolean),
                  ),
                ).slice(0, 12);
                patchNode(tagNode.id, { tags });
                setTagNode(null);
              }}
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(helpDialog)}
        onOpenChange={(open) => {
          if (!open) setHelpDialog(null);
        }}
      >
        <DialogContent className="max-w-md rounded-[16px]">
          <DialogHeader>
            <DialogTitle>
              {helpDialog === "shortcuts" ? "画布快捷键" : "画布教程"}
            </DialogTitle>
            <DialogDescription>
              {helpDialog === "shortcuts"
                ? "在未编辑文字时可直接使用。"
                : "从素材、能力到执行结果都保留在同一项目画布。"}
            </DialogDescription>
          </DialogHeader>
          {helpDialog === "shortcuts" ? (
            <div className="grid grid-cols-[1fr_auto] gap-x-5 gap-y-2 text-[12px]">
              {[
                ["移动工具", "V"],
                ["小手工具", "H / Space"],
                ["撤销 / 重做", "⌘Z / ⇧⌘Z"],
                ["复制 / 粘贴", "⌘C / ⌘V"],
                ["复制节点", "⌘D"],
                ["删除节点", "Delete"],
                ["取消操作", "Esc"],
              ].map(([label, shortcut]) => (
                <div key={label} className="contents">
                  <span>{label}</span>
                  <kbd className="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                    {shortcut}
                  </kbd>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-3 text-[12px] leading-5 text-muted-foreground">
              <p>1. 从底部“+”添加媒体、导演台、剪辑或 ComfyUI 节点。</p>
              <p>2. 工作流模式下拖动端口建立依赖；落到空白处可新建并连接。</p>
              <p>3. 从资产中心和 Skill 市场加入真实项目素材与执行能力。</p>
              <p>
                4. 在个人工作台发送需求时，会自动附带当前节点、连线和资产身份。
              </p>
            </div>
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(feedbackDialog)}
        onOpenChange={(open) => {
          if (!open && !feedbackSaving) {
            setFeedbackDialog(null);
            setFeedbackText("");
          }
        }}
      >
        <DialogContent className="max-w-md rounded-[16px]">
          <DialogHeader>
            <DialogTitle>
              {feedbackDialog === "wish" ? "功能许愿" : "反馈"}
            </DialogTitle>
            <DialogDescription>
              内容会保存到 Echo 的反馈记录，便于后续产品迭代。
            </DialogDescription>
          </DialogHeader>
          <Textarea
            autoFocus
            value={feedbackText}
            maxLength={400}
            onChange={(event) => setFeedbackText(event.target.value)}
            placeholder={
              feedbackDialog === "wish"
                ? "希望 Design 增加什么能力？"
                : "哪里不好用，或与你预期不一致？"
            }
            className="min-h-28 resize-none"
          />
          <DialogFooter>
            <Button
              variant="outline"
              disabled={feedbackSaving}
              onClick={() => {
                setFeedbackDialog(null);
                setFeedbackText("");
              }}
            >
              取消
            </Button>
            <Button
              disabled={!feedbackText.trim() || feedbackSaving}
              onClick={() => void submitDesignFeedback()}
            >
              {feedbackSaving ? (
                <Loader2Icon className="mr-1.5 size-3.5 animate-spin" />
              ) : null}
              提交
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(pendingLargeDelete)}
        onOpenChange={(open) => {
          if (!open) setPendingLargeDelete(null);
        }}
      >
        <DialogContent className="max-w-sm rounded-[16px]">
          <DialogHeader>
            <DialogTitle>确认删除大量画布内容</DialogTitle>
            <DialogDescription>
              此操作将从画布移除 {pendingLargeDelete?.plan.nodeIds.length ?? 0}{" "}
              个节点和 {pendingLargeDelete?.plan.edgeIds.length ?? 0}{" "}
              条连线。为防止误清空，请确认是否继续。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingLargeDelete(null)}
            >
              取消
            </Button>
            <Button
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                if (!pendingLargeDelete) return;
                if (pendingLargeDelete.snapshot !== JSON.stringify(document)) {
                  toast.info("画布内容已发生变化，本次删除已取消，请重新操作");
                  setPendingLargeDelete(null);
                  return;
                }
                applyDeletion(pendingLargeDelete.plan.nodeIds);
                setPendingLargeDelete(null);
              }}
            >
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {addOpen ? (
        <AddNodePopover
          position={
            addNodePosition
              ? (() => {
                  const bounds = stageRef.current?.getBoundingClientRect();
                  const rawX =
                    pan.x + (addNodePosition.x + NODE_WIDTH / 2) * zoom;
                  const rawY =
                    pan.y + (addNodePosition.y + NODE_HEIGHT / 2) * zoom;
                  return {
                    x: Math.max(
                      12,
                      Math.min(rawX, (bounds?.width ?? 900) - 248),
                    ),
                    y: Math.max(
                      12,
                      Math.min(rawY, (bounds?.height ?? 700) - 500),
                    ),
                  };
                })()
              : null
          }
          onAdd={addNode}
        />
      ) : null}
      {selectedNode ? (
        <div className="absolute bottom-4 right-4 z-30 w-64 rounded-[12px] border border-[#e6e6e6] bg-white p-3 shadow-[0_8px_32px_rgba(0,0,0,.08),0_2px_8px_rgba(0,0,0,.04)] dark:border-[#454545] dark:bg-[#1a1a1a]">
          <div className="flex items-center">
            <span className="text-xs font-semibold">节点设置</span>
            <span className="flex-1" />
            <button
              onClick={() => {
                setSelectedId(null);
                setSelectedIds([]);
              }}
            >
              <XIcon className="size-3.5" />
            </button>
          </div>
          <Input
            value={selectedNode.title}
            onFocus={beginCanvasTransaction}
            onBlur={endCanvasTransaction}
            onChange={(event) =>
              patchNode(selectedNode.id, { title: event.target.value }, false)
            }
            className="mt-3 h-8 text-xs"
          />
          <Textarea
            value={selectedNode.description}
            onFocus={beginCanvasTransaction}
            onBlur={endCanvasTransaction}
            onChange={(event) =>
              patchNode(
                selectedNode.id,
                { description: event.target.value },
                false,
              )
            }
            className="mt-2 min-h-20 resize-none text-xs"
          />
          {document.mode === "workflow" &&
          document.edges.some(
            (edge) =>
              edge.source === selectedNode.id ||
              edge.target === selectedNode.id,
          ) ? (
            <div className="mt-3 border-t border-border-subtle pt-2">
              <div className="text-[9px] text-muted-foreground">节点关系</div>
              <div className="mt-1 space-y-1">
                {document.edges
                  .filter(
                    (edge) =>
                      edge.source === selectedNode.id ||
                      edge.target === selectedNode.id,
                  )
                  .map((edge) => {
                    const outgoing = edge.source === selectedNode.id;
                    const other = document.nodes.find(
                      (node) =>
                        node.id === (outgoing ? edge.target : edge.source),
                    );
                    return (
                      <div
                        key={edge.id}
                        className="flex h-7 items-center gap-1.5 rounded-lg bg-muted/60 px-2 text-[9px]"
                      >
                        <span className="text-muted-foreground">
                          {outgoing ? "输出到" : "来自"}
                        </span>
                        <span className="min-w-0 flex-1 truncate font-medium">
                          {other?.title || "未知节点"}
                        </span>
                        <button
                          type="button"
                          aria-label="解除连接"
                          onClick={() =>
                            setDocument((current) =>
                              disconnectDesignEdge(current, edge.id),
                            )
                          }
                          className="grid size-5 place-items-center rounded-md text-muted-foreground hover:bg-background hover:text-foreground"
                        >
                          <XIcon className="size-3" />
                        </button>
                      </div>
                    );
                  })}
              </div>
            </div>
          ) : null}
          {selectedNode.kind === "group" ? (
            <div className="mt-3 border-t border-border-subtle pt-2">
              <div className="text-[9px] text-muted-foreground">分组颜色</div>
              <div className="mt-2 flex gap-1.5">
                {(Object.keys(GROUP_TONES) as GroupTone[]).map((color) => (
                  <button
                    key={color}
                    type="button"
                    aria-label={`分组颜色：${color}`}
                    onClick={() => patchNode(selectedNode.id, { color })}
                    className={cn(
                      "size-5 rounded-full border border-black/10",
                      GROUP_TONES[color].swatch,
                      selectedNode.color === color &&
                        "ring-2 ring-foreground ring-offset-1",
                    )}
                  />
                ))}
              </div>
            </div>
          ) : null}
          {selectedNode.kind === "director" ? (
            <Button
              className="mt-2 w-full rounded-lg text-xs"
              onClick={() => setEmbeddedSurface("director")}
            >
              打开导演台
            </Button>
          ) : null}
          {selectedNode.kind === "editor" ? (
            <Button
              className="mt-2 w-full rounded-lg text-xs"
              onClick={() => setEmbeddedSurface("editor")}
            >
              打开剪辑工坊
            </Button>
          ) : null}
          {selectedNode.kind === "comfyui" ? (
            <Button
              className="mt-2 w-full rounded-lg text-xs"
              onClick={() => {
                setComfyNative(false);
                setEmbeddedSurface("comfyui");
              }}
            >
              打开 ComfyUI
            </Button>
          ) : null}
          <button
            onClick={removeSelected}
            className="mt-2 flex items-center gap-1.5 text-[10px] text-destructive"
          >
            <Trash2Icon className="size-3" />
            删除节点
          </button>
        </div>
      ) : null}
      {embeddedSurface ? (
        <div className="absolute inset-0 z-50 flex flex-col overflow-hidden bg-background">
          {embeddedSurface === "director" ? (
            <DirectorStage
              sceneId={projectId || selectedNode?.id || "default"}
              onClose={() => setEmbeddedSurface(null)}
            />
          ) : embeddedSurface === "editor" ? (
            <PluginNodeFrame
              title="AI 剪辑工坊"
              src={`${getBackendBaseURL()}/api/plugins/clip-studio/page?project=${encodeURIComponent(projectId || selectedNode?.id || "default")}`}
              projectId={projectId}
              pluginId="clip-studio"
              nodeId={selectedNode?.id || "clip-studio"}
              className="min-h-0 flex-1 border-0 bg-background"
            />
          ) : !comfyNative ? (
            <ComfyWorkflowEditor
              workflowId={
                selectedNode?.binding?.type === "workflow"
                  ? selectedNode.binding.id
                  : "blank"
              }
              onClose={() => setEmbeddedSurface(null)}
              onOpenNative={() => setComfyNative(true)}
            />
          ) : (
            <>
              <div className="flex h-11 shrink-0 items-center border-b border-border-subtle px-3">
                <span className="text-xs font-semibold">ComfyUI 工作流</span>
                <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[8px] text-muted-foreground">
                  本机原生界面
                </span>
                <span className="flex-1" />
                <Button
                  variant="ghost"
                  size="sm"
                  className="mr-1 h-8 text-[10px]"
                  onClick={() => setComfyNative(false)}
                >
                  返回 Echo 编辑器
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  onClick={() => setEmbeddedSurface(null)}
                >
                  <XIcon className="size-4" />
                </Button>
              </div>
              <iframe
                title="ComfyUI 工作流"
                src="http://127.0.0.1:8188"
                className="min-h-0 flex-1 border-0 bg-background"
                sandbox="allow-scripts allow-same-origin allow-downloads allow-forms"
              />
            </>
          )}
        </div>
      ) : null}
    </main>
  );

  return (
    <div className="relative flex h-full min-h-0 w-full flex-col overflow-hidden bg-background">
      <header className="flex h-12 shrink-0 items-center border-b border-border-subtle bg-background px-2.5">
        <div className="ml-1 flex min-w-0 items-center gap-2">
          <span className="grid size-7 place-items-center rounded-lg bg-violet-100 text-violet-600">
            <WandSparklesIcon className="size-3.5" />
          </span>
          {section === "home" ? (
            <span className="text-[13px] font-semibold">Echo Design</span>
          ) : (
            <>
              {embeddedProject ? (
                <span className="flex h-8 max-w-44 items-center gap-1.5 px-2 text-[11px] font-medium">
                  <FolderIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate">{projectName || "当前目录"}</span>
                </span>
              ) : (
                <CreativeProjectSelector
                  personaId={personaId}
                  projects={creativeProjects}
                  currentProjectId={creativeProjectId}
                  onSelect={handleCreativeProjectChange}
                  className="w-44"
                />
              )}
              <span className="text-muted-foreground/50">/</span>
              <input
                value={document.title}
                onFocus={beginCanvasTransaction}
                onBlur={endCanvasTransaction}
                onChange={(event) =>
                  setDocumentState((current) => ({
                    ...current,
                    title: event.target.value,
                  }))
                }
                className="w-40 truncate bg-transparent text-[13px] font-semibold outline-none"
                aria-label="画布名称"
              />
            </>
          )}
          {projectId ? (
            <span
              className={cn(
                "hidden text-[9px] text-muted-foreground xl:inline",
                canvasSyncState === "conflict" && "text-amber-600",
                canvasSyncState === "error" && "text-red-600",
              )}
            >
              {canvasSyncState === "loading"
                ? "正在载入"
                : canvasSyncState === "saving"
                  ? "正在保存"
                  : canvasSyncState === "saved"
                    ? "已同步"
                    : canvasSyncState === "conflict"
                      ? "版本冲突"
                      : canvasSyncState === "error"
                        ? "仅本地保存"
                        : "本地画布"}
            </span>
          ) : null}
          {projectId ? (
            <div
              className="hidden items-center -space-x-1.5 sm:flex"
              title={`${presenceMembers.length + 1} 位成员在线`}
            >
              <span className="grid size-6 place-items-center rounded-full border-2 border-background bg-foreground text-[8px] font-semibold text-background">
                {presenceDisplayName.slice(0, 1).toUpperCase()}
              </span>
              {presenceMembers.slice(0, 3).map((member) => (
                <span
                  key={member.id}
                  className="grid size-6 place-items-center rounded-full border-2 border-background text-[8px] font-semibold text-white"
                  style={{ backgroundColor: member.color }}
                  title={`${member.display_name} · ${member.section === "canvas" ? "画布" : member.section}`}
                >
                  {member.display_name.slice(0, 1).toUpperCase()}
                </span>
              ))}
              {presenceMembers.length > 3 ? (
                <span className="grid size-6 place-items-center rounded-full border-2 border-background bg-muted text-[8px] font-medium text-muted-foreground">
                  +{presenceMembers.length - 3}
                </span>
              ) : null}
            </div>
          ) : null}
          {pendingCanvasConflict ? (
            <span className="hidden items-center gap-1 xl:flex">
              <button
                type="button"
                onClick={() => resolveCanvasConflict("merge")}
                className="rounded-md bg-amber-100 px-2 py-1 text-[9px] font-medium text-amber-800 transition hover:bg-amber-200"
                title={`本地优先合并 ${pendingCanvasConflict.conflicts.length} 处冲突`}
              >
                合并保存
              </button>
              <button
                type="button"
                onClick={() => resolveCanvasConflict("remote")}
                className="rounded-md px-2 py-1 text-[9px] text-muted-foreground transition hover:bg-muted"
              >
                载入新版
              </button>
            </span>
          ) : null}
        </div>
        <nav
          className={cn(
            "ml-5 h-full items-center gap-1 text-[11px]",
            embeddedProject ? "hidden xl:flex" : "flex",
          )}
        >
          {(
            [
              ["home", "开始创作"],
              ["canvas", "创作画布"],
              ["assets", "资产中心"],
              ["skills", "Skill"],
              ["comfyui", "ComfyUI"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setSection(id)}
              className={cn(
                "relative h-full px-2.5 text-muted-foreground",
                section === id &&
                  "font-medium text-foreground after:absolute after:bottom-0 after:left-2 after:right-2 after:h-0.5 after:rounded-full after:bg-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </nav>
        <span className="flex-1" />
        {section === "canvas" ? (
          <>
            <div className="mr-1 flex rounded-lg bg-muted/70 p-0.5 text-[10px]">
              <button
                onClick={() =>
                  setDocument((current) =>
                    switchDesignCanvasMode(current, "freeform"),
                  )
                }
                className={cn(
                  "rounded-md px-2.5 py-1.5",
                  document.mode === "freeform" &&
                    "bg-background font-medium shadow-sm",
                )}
              >
                自由画布
              </button>
              <button
                onClick={() =>
                  setDocument((current) =>
                    switchDesignCanvasMode(current, "workflow"),
                  )
                }
                className={cn(
                  "rounded-md px-2.5 py-1.5",
                  document.mode === "workflow" &&
                    "bg-background font-medium shadow-sm",
                )}
              >
                工作流
              </button>
            </div>
            <div className="relative">
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                onClick={() => setLayoutOpen((value) => !value)}
                aria-label="工作区布局"
              >
                <LayoutPanelLeftIcon className="size-4" />
              </Button>
              {layoutOpen ? (
                <div className="absolute right-0 top-10 z-50 w-52 rounded-xl border border-border-default bg-background p-2 shadow-xl">
                  <div className="px-2 pb-2 pt-1 text-[10px] font-semibold text-muted-foreground">
                    布局模式
                  </div>
                  {(
                    [
                      ["split", "画布 + 个人工作台", PanelRightIcon],
                      ["chat-left", "工作台在左", PanelLeftCloseIcon],
                      ["chat", "仅个人工作台", MessageSquareIcon],
                      ["canvas", "仅画布", Maximize2Icon],
                    ] as const
                  ).map(([id, label, Icon]) => (
                    <button
                      key={id}
                      onClick={() => {
                        setLayout(id);
                        setLayoutOpen(false);
                      }}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-[11px] hover:bg-muted"
                    >
                      <Icon className="size-3.5" />
                      {label}
                      <span className="flex-1" />
                      {layout === id ? (
                        <CheckIcon className="size-3.5" />
                      ) : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </>
        ) : null}
      </header>
      <div className="min-h-0 flex-1">
        {section === "home" ? (
          <DesignHomeView
            onStart={(prompt, enabledModels) => {
              const capabilityNote = enabledModels.length
                ? `\n\n可调用创作能力：${enabledModels.join("、")}`
                : "";
              addNode("brief", "创作需求", `${prompt}${capabilityNote}`);
              setSection("canvas");
              runCanvas(`${prompt}${capabilityNote}`);
            }}
            onOpenSkills={() => setSection("skills")}
            onAddFiles={(files) => void uploadHomeFiles(files)}
            personaId={personaId}
            projects={creativeProjects}
            currentProjectId={creativeProjectId}
            onSelectProject={handleCreativeProjectChange}
          />
        ) : null}
        {section === "assets" ? (
          <AssetsView
            personaId={personaId}
            projectId={projectId}
            onUseArtifact={(artifact) => {
              placeOrLocateArtifact(artifact);
              setSection("canvas");
            }}
          />
        ) : null}
        {section === "skills" ? (
          <SkillsView
            installedSkills={skills}
            loading={skillsLoading}
            onUse={(id) => {
              const skill = CREATIVE_SKILL_COLLECTION.find(
                (item) => item.id === id,
              );
              if (skill) {
                addNode("skill", skill.title, skill.description, {
                  type: "skill",
                  id,
                });
                setSection("canvas");
                toast.success("Skill 已加入画布");
              }
            }}
          />
        ) : null}
        {section === "comfyui" ? (
          <ComfyUIView
            onUse={(id, title) => {
              addNode(
                "comfyui",
                title,
                "连接本机 ComfyUI，运行节点式生成工作流",
                { type: "workflow", id },
              );
              setSection("canvas");
              toast.success("ComfyUI 工作流已加入画布");
            }}
          />
        ) : null}
        {section === "canvas" ? (
          <div className="flex h-full min-h-0">
            {layout === "chat-left" || layout === "chat" ? (
              <ChatPanel
                chatUrl={embeddedChatUrl}
                onRun={runCanvas}
                onNew={() => setEmbeddedChatUrl(null)}
                onClose={() => setLayout("canvas")}
                surface={embeddedSurface}
                side="left"
              />
            ) : null}
            {layout !== "chat" ? canvasSurface : null}
            {layout !== "chat" && assetsOpen ? (
              <CanvasAssetsPanel
                personaId={personaId}
                projectId={projectId}
                document={document}
                onClose={() => setAssetsOpen(false)}
                onPick={placeOrLocateArtifact}
              />
            ) : null}
            {layout === "split" ? (
              <ChatPanel
                chatUrl={embeddedChatUrl}
                onRun={runCanvas}
                onNew={() => setEmbeddedChatUrl(null)}
                onClose={() => setLayout("canvas")}
                surface={embeddedSurface}
              />
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
