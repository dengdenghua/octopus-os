import {
  SearchIcon,
  RefreshCwIcon,
  Loader2Icon,
  NetworkIcon,
  PaletteIcon,
  SlidersHorizontalIcon,
  TagsIcon,
  EyeIcon,
  ZapIcon,
  FilterIcon,
  InfoIcon,
  type LucideIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { authHeaders } from "@/core/auth/api";
import { useI18n } from "@/core/i18n/hooks";
import { globalControlPlaneUrl } from "@/core/observability/api";
import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";

interface ApiNode {
  id: string;
  label: string;
  full_name?: string;
  entity_type?: string;
}

interface ApiEdge {
  id: string;
  triple_id?: string;
  source: string;
  target: string;
  label: string;
  confidence: number;
  status?: string;
  source_ref?: string;
  ts?: string | null;
}

interface ApiGraphEntity {
  id: string;
  name: string;
  full_name?: string;
  entity_type?: string;
  roles?: string[];
  degree?: number;
  in_degree?: number;
  out_degree?: number;
  confidence_avg?: number;
  sources?: string[];
  first_seen?: string | null;
  last_seen?: string | null;
}

interface ApiGraphRelationship {
  id: string;
  triple_id?: string;
  source_name: string;
  target_name: string;
  source_label?: string;
  target_label?: string;
  relationship_type: string;
  confidence: number;
  status?: string;
  source_ref?: string;
  ts?: string | null;
  valid_from?: string | null;
  valid_until?: string | null;
}

interface ApiGraphResponse {
  entities?: ApiGraphEntity[];
  relationships?: ApiGraphRelationship[];
  stats?: {
    persistent?: boolean;
    kg_size?: number;
    total_entities?: number;
    total_relationships?: number;
  };
}

interface GraphNodeRecord {
  id: string;
  label: string;
  fullName: string;
  entityType: string;
  roles: string[];
  backendDegree: number;
  inDegree: number;
  outDegree: number;
  confidenceAvg: number | null;
  sources: string[];
  firstSeen: string | null;
  lastSeen: string | null;
  color: string;
}

interface GraphEdgeRecord {
  id: string;
  tripleId: string;
  source: string;
  target: string;
  label: string;
  confidence: number;
  status: string;
  sourceRef: string;
  timestamp: string | null;
}

interface PositionedNode extends GraphNodeRecord {
  degree: number;
  matched: boolean;
  position: THREE.Vector3;
}

interface PositionedEdge extends GraphEdgeRecord {
  sourceNode: PositionedNode;
  targetNode: PositionedNode;
  active: boolean;
}

interface RenderedGraph {
  nodes: PositionedNode[];
  edges: PositionedEdge[];
}

interface GraphDisplaySettings {
  showLabels: boolean;
  showLinks: boolean;
  showStars: boolean;
  autoRotate: boolean;
  nodeScale: number;
  linkScale: number;
  linkDistance: number;
  spread: number;
}

interface GraphFilters {
  search: string;
  minConfidence: number;
  disabledTypes: Set<string>;
}

type DisplayToggleKey = "showLabels" | "showLinks" | "showStars" | "autoRotate";

const LINK_COLOR = "#9aa8ba";
const ACTIVE_LINK_COLOR = "#7dd3fc";
const SPACE_BG = "#05070d";

const ENTITY_COLORS: Record<string, string> = {
  center: "#f472b6",
  subject: "#22d3ee",
  object: "#a3e635",
  neighbor: "#f59e0b",
  other: "#94a3b8",
};

const TYPE_PALETTE = [
  "#22d3ee",
  "#a3e635",
  "#f472b6",
  "#f59e0b",
  "#818cf8",
  "#34d399",
  "#fb7185",
  "#c084fc",
  "#67e8f9",
  "#fda4af",
];

const DEFAULT_DISPLAY_SETTINGS: GraphDisplaySettings = {
  showLabels: true,
  showLinks: true,
  showStars: true,
  autoRotate: true,
  nodeScale: 1.04,
  linkScale: 1,
  linkDistance: 168,
  spread: 1.18,
};

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function hashString(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function seededUnit(value: string): number {
  return hashString(value) / 0xffffffff;
}

function colorForEntityType(entityType?: string): string {
  const key = entityType?.trim() || "other";
  return (
    ENTITY_COLORS[key] ??
    TYPE_PALETTE[hashString(key) % TYPE_PALETTE.length] ??
    "#94a3b8"
  );
}

function nodeVisualSize(
  degree: number,
  settings: GraphDisplaySettings,
): number {
  return clamp((4.5 + Math.sqrt(degree + 1) * 2.8) * settings.nodeScale, 5, 28);
}

function formatConfidence(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  return `${Math.round(clamp(value, 0, 1) * 100)}%`;
}

function shortText(value: string, max = 42): string {
  if (value.length <= max) return value;
  return `${value.slice(0, Math.max(0, max - 3)).trimEnd()}...`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function nodeDegree(edges: GraphEdgeRecord[]): Map<string, number> {
  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  return degree;
}

function neighborIds(edges: PositionedEdge[], nodeId: string): Set<string> {
  const ids = new Set<string>();
  for (const edge of edges) {
    if (edge.source === nodeId) ids.add(edge.target);
    if (edge.target === nodeId) ids.add(edge.source);
  }
  return ids;
}

function initialSpherePosition(
  id: string,
  index: number,
  count: number,
  settings: GraphDisplaySettings,
): THREE.Vector3 {
  const theta = seededUnit(`${id}:theta:${index}`) * Math.PI * 2;
  const phi = Math.acos(2 * seededUnit(`${id}:phi`) - 1);
  const radius =
    (120 + seededUnit(`${id}:radius`) * 360) *
    settings.spread *
    clamp(Math.sqrt(count) / 8, 0.75, 1.65);
  return new THREE.Vector3(
    Math.sin(phi) * Math.cos(theta) * radius,
    Math.cos(phi) * radius * 0.72,
    Math.sin(phi) * Math.sin(theta) * radius,
  );
}

function visibleIdsForFilters(
  nodes: GraphNodeRecord[],
  edges: GraphEdgeRecord[],
  filters: GraphFilters,
): { visibleIds: Set<string>; matches: Set<string> } {
  const q = filters.search.trim().toLowerCase();
  const allowed = nodes.filter(
    (node) => !filters.disabledTypes.has(node.entityType),
  );
  const allowedIds = new Set(allowed.map((node) => node.id));

  if (!q) {
    return { visibleIds: allowedIds, matches: new Set() };
  }

  const matches = new Set(
    allowed
      .filter((node) => node.label.toLowerCase().includes(q))
      .map((node) => node.id),
  );
  const visibleIds = new Set(matches);
  for (const edge of edges) {
    if (edge.confidence < filters.minConfidence) continue;
    if (!allowedIds.has(edge.source) || !allowedIds.has(edge.target)) continue;
    if (matches.has(edge.source)) visibleIds.add(edge.target);
    if (matches.has(edge.target)) visibleIds.add(edge.source);
  }

  return { visibleIds, matches };
}

function forceLayout3D(
  nodes: GraphNodeRecord[],
  edges: GraphEdgeRecord[],
  settings: GraphDisplaySettings,
  currentPositions: Map<string, THREE.Vector3>,
): Map<string, THREE.Vector3> {
  const positions = nodes.map((node, index) => ({
    id: node.id,
    position:
      currentPositions.get(node.id)?.clone() ??
      initialGalaxyPosition(node.id, index, nodes.length, settings),
    velocity: new THREE.Vector3(),
  }));
  const byId = new Map(positions.map((item, index) => [item.id, index]));
  const links = edges
    .map((edge) => ({
      source: byId.get(edge.source),
      target: byId.get(edge.target),
      confidence: edge.confidence,
    }))
    .filter(
      (link) => link.source !== undefined && link.target !== undefined,
    ) as {
    source: number;
    target: number;
    confidence: number;
  }[];

  const iterations = clamp(100 + nodes.length * 1.6, 140, 260);
  const repulsion = 16500 * settings.spread;
  const springStrength = 0.0028;
  const centerStrength = 0.0024;
  const damping = 0.82;

  for (let iteration = 0; iteration < iterations; iteration++) {
    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        const a = positions[i]!;
        const b = positions[j]!;
        const delta = a.position.clone().sub(b.position);
        let distSq = delta.lengthSq();
        if (distSq < 1) {
          delta.set(
            seededUnit(`${a.id}:${b.id}:x`) - 0.5,
            seededUnit(`${a.id}:${b.id}:y`) - 0.5,
            seededUnit(`${a.id}:${b.id}:z`) - 0.5,
          );
          distSq = Math.max(delta.lengthSq(), 1);
        }
        const force = repulsion / distSq;
        delta.normalize().multiplyScalar(force);
        a.velocity.add(delta);
        b.velocity.sub(delta);
      }
    }

    for (const link of links) {
      const a = positions[link.source]!;
      const b = positions[link.target]!;
      const delta = b.position.clone().sub(a.position);
      const dist = Math.max(delta.length(), 1);
      const targetLength =
        settings.linkDistance *
        clamp(1.15 - link.confidence * 0.24, 0.74, 1.15);
      const force = (dist - targetLength) * springStrength;
      delta.normalize().multiplyScalar(force);
      a.velocity.add(delta);
      b.velocity.sub(delta);
    }

    for (const item of positions) {
      item.velocity.add(item.position.clone().multiplyScalar(-centerStrength));
      item.velocity.multiplyScalar(damping);
      item.position.add(item.velocity);
      item.position.clampLength(20, 760 * settings.spread);
    }
  }

  return new Map(positions.map((item) => [item.id, item.position]));
}

function initialGalaxyPosition(
  id: string,
  index: number,
  total: number,
  settings: GraphDisplaySettings,
): THREE.Vector3 {
  const arms = 4;
  const arm = index % arms;
  const lane = Math.floor(index / arms);
  const t = (lane + 1) / Math.max(Math.ceil(total / arms), 1);
  const radius = 70 + Math.pow(t, 0.72) * 560 * settings.spread;
  const angle =
    arm * ((Math.PI * 2) / arms) +
    radius * 0.008 +
    seededUnit(`${id}:angle`) * 0.24;
  const jitter = (seededUnit(`${id}:jitter`) - 0.5) * 34;
  return new THREE.Vector3(
    Math.cos(angle) * radius + jitter,
    (seededUnit(`${id}:y`) - 0.5) * (80 + radius * 0.12),
    Math.sin(angle) * radius + jitter,
  );
}

function materializeGraph(
  baseNodes: GraphNodeRecord[],
  baseEdges: GraphEdgeRecord[],
  settings: GraphDisplaySettings,
  filters: GraphFilters,
  currentPositions: Map<string, THREE.Vector3>,
): RenderedGraph {
  const { visibleIds, matches } = visibleIdsForFilters(
    baseNodes,
    baseEdges,
    filters,
  );
  const filteredEdges = baseEdges.filter(
    (edge) =>
      visibleIds.has(edge.source) &&
      visibleIds.has(edge.target) &&
      edge.confidence >= filters.minConfidence,
  );
  const filteredNodes = baseNodes.filter((node) => visibleIds.has(node.id));
  const degree = nodeDegree(filteredEdges);
  const nextPositions = forceLayout3D(
    filteredNodes,
    filteredEdges,
    settings,
    currentPositions,
  );
  const positionedNodes = filteredNodes.map((node) => ({
    ...node,
    degree: degree.get(node.id) ?? 0,
    matched: matches.has(node.id),
    position: nextPositions.get(node.id) ?? new THREE.Vector3(),
  }));
  const nodeById = new Map(positionedNodes.map((node) => [node.id, node]));
  const edges: PositionedEdge[] = [];
  for (const edge of filteredEdges) {
    const sourceNode = nodeById.get(edge.source);
    const targetNode = nodeById.get(edge.target);
    if (!sourceNode || !targetNode) continue;
    edges.push({
      ...edge,
      sourceNode,
      targetNode,
      active: false,
    });
  }

  return { nodes: positionedNodes, edges };
}

function makeGlowTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const gradient = ctx.createRadialGradient(64, 64, 4, 64, 64, 62);
    gradient.addColorStop(0, "rgba(255,255,255,0.95)");
    gradient.addColorStop(0.28, "rgba(255,255,255,0.38)");
    gradient.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 128, 128);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function makeLabelTexture(text: string): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font =
      "500 28px Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
    const metrics = ctx.measureText(text);
    const width = clamp(metrics.width + 42, 96, 500);
    const x = (canvas.width - width) / 2;
    ctx.fillStyle = "rgba(5, 8, 16, 0.74)";
    ctx.strokeStyle = "rgba(148, 163, 184, 0.26)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(x, 38, width, 48, 14);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "rgba(241, 245, 249, 0.92)";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 256, 62, 444);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function makeStarField(count: number): THREE.Points {
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const color = new THREE.Color();
  for (let i = 0; i < count; i++) {
    const radius = 900 + seededUnit(`star:${i}:r`) * 1400;
    const theta = seededUnit(`star:${i}:theta`) * Math.PI * 2;
    const phi = Math.acos(2 * seededUnit(`star:${i}:phi`) - 1);
    positions[i * 3] = Math.sin(phi) * Math.cos(theta) * radius;
    positions[i * 3 + 1] = Math.cos(phi) * radius * 0.8;
    positions[i * 3 + 2] = Math.sin(phi) * Math.sin(theta) * radius;
    color.setHSL(0.55 + seededUnit(`star:${i}:h`) * 0.14, 0.5, 0.62);
    colors[i * 3] = color.r;
    colors[i * 3 + 1] = color.g;
    colors[i * 3 + 2] = color.b;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const material = new THREE.PointsMaterial({
    size: 2.2,
    vertexColors: true,
    transparent: true,
    opacity: 0.74,
    depthWrite: false,
  });
  return new THREE.Points(geometry, material);
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    mesh.geometry?.dispose?.();
    const material = mesh.material as
      | THREE.Material
      | THREE.Material[]
      | undefined;
    const materials = Array.isArray(material)
      ? material
      : material
        ? [material]
        : [];
    for (const item of materials) {
      const maybeMap = item as THREE.Material & { map?: THREE.Texture };
      maybeMap.map?.dispose?.();
      item.dispose();
    }
  });
}

function RangeControl({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format = (next) => next.toFixed(2),
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  format?: (value: number) => string;
}) {
  return (
    <label className="grid gap-1.5">
      <span className="flex items-center justify-between text-xs text-foreground/80">
        <span>{label}</span>
        <span className="font-mono text-xs text-muted-foreground/70">
          {format(value)}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-full cursor-pointer accent-sky-300"
      />
    </label>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2 border-t border-white/10 pt-3 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-caps text-muted-foreground">
        <Icon className="size-3.5" />
        {title}
      </div>
      {children}
    </section>
  );
}

function KnowledgeGraph3DContent() {
  const { t } = useI18n();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const graphGroupRef = useRef<THREE.Group | null>(null);
  const starFieldRef = useRef<THREE.Points | null>(null);
  const nodeMeshesRef = useRef<Map<string, THREE.Object3D>>(new Map());
  const raycasterRef = useRef(new THREE.Raycaster());
  const pointerRef = useRef(new THREE.Vector2());
  const positionsRef = useRef<Map<string, THREE.Vector3>>(new Map());
  const hoverIdRef = useRef<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const displaySettingsRef = useRef(DEFAULT_DISPLAY_SETTINGS);
  const [baseNodes, setBaseNodes] = useState<GraphNodeRecord[]>([]);
  const [baseEdges, setBaseEdges] = useState<GraphEdgeRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [webglError, setWebglError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [minConfidence, setMinConfidence] = useState(0);
  const [disabledTypes, setDisabledTypes] = useState<Set<string>>(new Set());
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(true);
  const [graphTheme, setGraphTheme] = useState<"nebula" | "aurora">("nebula");
  const [displaySettings, setDisplaySettings] = useState(
    DEFAULT_DISPLAY_SETTINGS,
  );
  const expandedRef = useRef<Set<string>>(new Set());
  const glowTextureRef = useRef<THREE.CanvasTexture | null>(null);

  useEffect(() => {
    displaySettingsRef.current = displaySettings;
  }, [displaySettings]);

  useEffect(() => {
    hoverIdRef.current = hoverId;
  }, [hoverId]);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  const controls = t.knowledgePanel.controls;
  const displayToggles = useMemo(
    () =>
      [
        ["showLabels", controls.labels],
        ["showLinks", controls.links],
        ["showStars", controls.stars],
        ["autoRotate", controls.autoRotate],
      ] satisfies Array<[DisplayToggleKey, string]>,
    [controls],
  );

  const filters = useMemo(
    () => ({ search, minConfidence, disabledTypes }),
    [search, minConfidence, disabledTypes],
  );

  const renderedGraph = useMemo(() => {
    const graph = materializeGraph(
      baseNodes,
      baseEdges,
      displaySettings,
      filters,
      positionsRef.current,
    );
    positionsRef.current = new Map(
      graph.nodes.map((node) => [node.id, node.position.clone()]),
    );
    return graph;
  }, [baseNodes, baseEdges, displaySettings, filters]);

  const entityGroups = useMemo(() => {
    const groups = new Map<string, { count: number; color: string }>();
    for (const node of baseNodes) {
      const current = groups.get(node.entityType) ?? {
        count: 0,
        color: node.color,
      };
      current.count += 1;
      groups.set(node.entityType, current);
    }
    return [...groups.entries()].sort((a, b) => b[1].count - a[1].count);
  }, [baseNodes]);

  const baseNodeById = useMemo(
    () => new Map(baseNodes.map((node) => [node.id, node])),
    [baseNodes],
  );

  const focusNodeId = hoverId ?? selectedId;
  const focusNode = focusNodeId
    ? (baseNodeById.get(focusNodeId) ?? null)
    : null;
  const focusEdges = useMemo(() => {
    if (!focusNodeId) return [];
    return baseEdges
      .filter(
        (edge) => edge.source === focusNodeId || edge.target === focusNodeId,
      )
      .sort((a, b) => {
        if (b.confidence !== a.confidence) return b.confidence - a.confidence;
        return (b.timestamp || "").localeCompare(a.timestamp || "");
      })
      .slice(0, 6);
  }, [baseEdges, focusNodeId]);

  const entityTypeLabel = useCallback(
    (entityType: string) => {
      const known = entityType as keyof typeof t.knowledgePanel.entityTypes;
      return t.knowledgePanel.entityTypes[known] ?? entityType;
    },
    [t],
  );

  const loadGraph = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(
        globalControlPlaneUrl("/api/knowledge/graph?limit=260"),
        { headers: authHeaders() },
      );
      if (!response.ok) return;
      const data: ApiGraphResponse = await response.json();
      const entities = (data.entities ?? []).slice(0, 220);
      const relationships = data.relationships ?? [];

      positionsRef.current = new Map();
      expandedRef.current.clear();
      setBaseNodes(
        entities.map((entity) => ({
          id: entity.id,
          label: entity.name,
          fullName: entity.full_name || entity.id,
          entityType: entity.entity_type || "other",
          roles: entity.roles ?? [entity.entity_type || "other"],
          backendDegree: entity.degree ?? 0,
          inDegree: entity.in_degree ?? 0,
          outDegree: entity.out_degree ?? 0,
          confidenceAvg: entity.confidence_avg ?? null,
          sources: entity.sources ?? [],
          firstSeen: entity.first_seen ?? null,
          lastSeen: entity.last_seen ?? null,
          color: colorForEntityType(entity.entity_type),
        })),
      );
      setBaseEdges(
        relationships.map((relationship) => ({
          id: relationship.id,
          tripleId: relationship.triple_id || relationship.id,
          source: relationship.source_name,
          target: relationship.target_name,
          label: relationship.relationship_type,
          confidence: relationship.confidence ?? 0.55,
          status: relationship.status || "active",
          sourceRef: relationship.source_ref || "",
          timestamp: relationship.ts ?? null,
        })),
      );
    } catch (error) {
      swallow(error);
    } finally {
      setLoading(false);
    }
  }, []);

  const expandNode = useCallback(async (nodeId: string) => {
    setSelectedId(nodeId);
    if (expandedRef.current.has(nodeId)) return;
    expandedRef.current.add(nodeId);
    try {
      const response = await fetch(
        globalControlPlaneUrl(
          `/api/knowledge/neighbors?entity=${encodeURIComponent(nodeId)}&hops=1&limit=48`,
        ),
        { headers: authHeaders() },
      );
      if (!response.ok) return;
      const data: { nodes: ApiNode[]; edges: ApiEdge[] } =
        await response.json();
      const anchor =
        positionsRef.current.get(nodeId)?.clone() ?? new THREE.Vector3();
      setBaseNodes((previous) => {
        const existing = new Set(previous.map((node) => node.id));
        const additions = data.nodes
          .filter((node) => !existing.has(node.id))
          .map((node, index) => {
            const position = anchor
              .clone()
              .add(
                initialSpherePosition(
                  `${nodeId}:${node.id}`,
                  index,
                  data.nodes.length,
                  displaySettingsRef.current,
                ).multiplyScalar(0.28),
              );
            positionsRef.current.set(node.id, position);
            return {
              id: node.id,
              label: node.label,
              fullName: node.full_name || node.id,
              entityType: node.entity_type || "neighbor",
              roles: [node.entity_type || "neighbor"],
              backendDegree: 0,
              inDegree: 0,
              outDegree: 0,
              confidenceAvg: null,
              sources: [],
              firstSeen: null,
              lastSeen: null,
              color: colorForEntityType(node.entity_type || "neighbor"),
            };
          });
        return [...previous, ...additions];
      });
      setBaseEdges((previous) => {
        const existing = new Set(previous.map((edge) => edge.id));
        const additions = data.edges
          .filter((edge) => !existing.has(edge.id))
          .map((edge) => ({
            id: edge.id,
            tripleId: edge.triple_id || edge.id,
            source: edge.source,
            target: edge.target,
            label: edge.label,
            confidence: edge.confidence ?? 0.55,
            status: edge.status || "active",
            sourceRef: edge.source_ref || "",
            timestamp: edge.ts ?? null,
          }));
        return [...previous, ...additions];
      });
    } catch (error) {
      swallow(error);
    }
  }, []);

  useEffect(() => {
    void loadGraph();
  }, [loadGraph]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: "high-performance",
      });
    } catch (error) {
      setWebglError(
        error instanceof Error ? error.message : "WebGL unavailable",
      );
      return;
    }

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(new THREE.Color(SPACE_BG), 0.00042);
    const camera = new THREE.PerspectiveCamera(48, 1, 1, 5000);
    camera.position.set(0, 160, 880);
    renderer.setClearColor(graphTheme === "aurora" ? "#071a1d" : SPACE_BG, 1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    // Cap supersampling so large Retina canvases do not multiply GPU work.
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.domElement.dataset.knowledgeGraph3d = "true";
    renderer.domElement.className =
      "h-full w-full cursor-grab active:cursor-grabbing";
    container.appendChild(renderer.domElement);

    const controls3d = new OrbitControls(camera, renderer.domElement);
    controls3d.enableDamping = true;
    controls3d.dampingFactor = 0.065;
    controls3d.rotateSpeed = 0.42;
    controls3d.zoomSpeed = 0.72;
    controls3d.panSpeed = 0.62;
    controls3d.minDistance = 180;
    controls3d.maxDistance = 1900;
    controls3d.enablePan = true;
    controls3d.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    controls3d.touches = {
      ONE: THREE.TOUCH.ROTATE,
      TWO: THREE.TOUCH.DOLLY_PAN,
    };

    const graphGroup = new THREE.Group();
    scene.add(graphGroup);
    const ambient = new THREE.AmbientLight(0xb8d8ff, 0.95);
    const coreLight = new THREE.PointLight(0x7dd3fc, 550, 2400, 1.45);
    coreLight.position.set(0, 120, 420);
    const warmLight = new THREE.PointLight(0xf59e0b, 170, 1500, 1.35);
    warmLight.position.set(-340, -160, -360);
    scene.add(ambient, coreLight, warmLight);

    const starField = makeStarField(1200);
    scene.add(starField);
    glowTextureRef.current = makeGlowTexture();

    sceneRef.current = scene;
    cameraRef.current = camera;
    rendererRef.current = renderer;
    controlsRef.current = controls3d;
    graphGroupRef.current = graphGroup;
    starFieldRef.current = starField;

    const resize = () => {
      const rect = container.getBoundingClientRect();
      const width = Math.max(320, Math.floor(rect.width));
      const height = Math.max(360, Math.floor(rect.height));
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    const setHover = (next: string | null) => {
      if (hoverIdRef.current === next) return;
      hoverIdRef.current = next;
      setHoverId(next);
    };

    let hoverFrame = 0;
    let pendingPointer: { x: number; y: number } | null = null;
    const resolveHover = () => {
      hoverFrame = 0;
      if (!pendingPointer) return;
      const nextPointer = pendingPointer;
      pendingPointer = null;
      pointerRef.current.x = nextPointer.x;
      pointerRef.current.y = nextPointer.y;
      raycasterRef.current.setFromCamera(pointerRef.current, camera);
      const hits = raycasterRef.current.intersectObjects(
        [...nodeMeshesRef.current.values()],
        true,
      );
      const hit = hits.find((item) => item.object.userData.nodeId);
      setHover((hit?.object.userData.nodeId as string | undefined) ?? null);
    };
    const handlePointerMove = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pendingPointer = {
        x: ((event.clientX - rect.left) / rect.width) * 2 - 1,
        y: -((event.clientY - rect.top) / rect.height) * 2 + 1,
      };
      if (!hoverFrame) hoverFrame = window.requestAnimationFrame(resolveHover);
    };

    const handlePointerLeave = () => setHover(null);
    const handleClick = () => {
      const nodeId = hoverIdRef.current;
      if (nodeId) void expandNode(nodeId);
    };

    renderer.domElement.addEventListener("pointermove", handlePointerMove);
    renderer.domElement.addEventListener("pointerleave", handlePointerLeave);
    renderer.domElement.addEventListener("click", handleClick);

    let animationFrame = 0;
    const animate = () => {
      animationFrame = window.requestAnimationFrame(animate);
      if (document.hidden) return;
      if (displaySettingsRef.current.autoRotate) {
        graphGroup.rotation.y += 0.0017;
        graphGroup.rotation.x = Math.sin(performance.now() / 9000) * 0.035;
      }
      starField.rotation.y += 0.00022;
      controls3d.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      window.cancelAnimationFrame(animationFrame);
      if (hoverFrame) window.cancelAnimationFrame(hoverFrame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointermove", handlePointerMove);
      renderer.domElement.removeEventListener(
        "pointerleave",
        handlePointerLeave,
      );
      renderer.domElement.removeEventListener("click", handleClick);
      controls3d.dispose();
      disposeObject(graphGroup);
      disposeObject(starField);
      glowTextureRef.current?.dispose();
      glowTextureRef.current = null;
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = null;
      rendererRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
      graphGroupRef.current = null;
      starFieldRef.current = null;
      nodeMeshesRef.current = new Map();
    };
  }, [expandNode, graphTheme]);

  useEffect(() => {
    const group = graphGroupRef.current;
    if (!group) return;

    disposeObject(group);
    group.clear();
    glowTextureRef.current = null;
    nodeMeshesRef.current = new Map();
    const activeNodeId = hoverId ?? selectedId;
    const activeNeighbors = activeNodeId
      ? neighborIds(renderedGraph.edges, activeNodeId)
      : new Set<string>();
    if (activeNodeId) activeNeighbors.add(activeNodeId);

    if (displaySettings.showLinks && renderedGraph.edges.length) {
      const regularPositions: number[] = [];
      const activePositions: number[] = [];
      for (const edge of renderedGraph.edges) {
        const target =
          activeNodeId &&
          (edge.source === activeNodeId || edge.target === activeNodeId)
            ? activePositions
            : regularPositions;
        target.push(
          edge.sourceNode.position.x,
          edge.sourceNode.position.y,
          edge.sourceNode.position.z,
          edge.targetNode.position.x,
          edge.targetNode.position.y,
          edge.targetNode.position.z,
        );
      }
      if (regularPositions.length) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute(
          "position",
          new THREE.Float32BufferAttribute(regularPositions, 3),
        );
        group.add(
          new THREE.LineSegments(
            geometry,
            new THREE.LineBasicMaterial({
              color: LINK_COLOR,
              transparent: true,
              opacity: activeNodeId ? 0.12 : 0.42 * displaySettings.linkScale,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          ),
        );
      }
      if (activePositions.length) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute(
          "position",
          new THREE.Float32BufferAttribute(activePositions, 3),
        );
        group.add(
          new THREE.LineSegments(
            geometry,
            new THREE.LineBasicMaterial({
              color: ACTIVE_LINK_COLOR,
              transparent: true,
              opacity: 0.96,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          ),
        );
      }
    }

    const glowTexture = glowTextureRef.current ?? makeGlowTexture();
    glowTextureRef.current = glowTexture;
    const sphereDetail = renderedGraph.nodes.length > 160 ? 14 : 22;
    for (const node of renderedGraph.nodes) {
      const active = activeNeighbors.has(node.id);
      const isSelected = selectedId === node.id;
      const isHovered = hoverId === node.id;
      const size =
        nodeVisualSize(node.degree, displaySettings) *
        (isSelected || isHovered ? 1.28 : 1);
      const color = new THREE.Color(
        activeNodeId && !active ? "#475569" : node.color,
      );
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(
          size,
          sphereDetail,
          Math.max(10, sphereDetail - 4),
        ),
        new THREE.MeshStandardMaterial({
          color,
          emissive: color,
          emissiveIntensity: activeNodeId ? (active ? 1.35 : 0.24) : 0.9,
          roughness: 0.42,
          metalness: 0.08,
          transparent: true,
          opacity: activeNodeId ? (active ? 1 : 0.38) : 0.96,
        }),
      );
      mesh.position.copy(node.position);
      mesh.userData.nodeId = node.id;

      const glow = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: glowTexture,
          color,
          transparent: true,
          opacity: activeNodeId ? (active ? 0.6 : 0.12) : 0.36,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        }),
      );
      glow.scale.setScalar(size * (active ? 8.2 : 5.2));
      glow.position.copy(node.position);

      group.add(glow, mesh);
      nodeMeshesRef.current.set(node.id, mesh);

      const showLabel =
        displaySettings.showLabels &&
        (isSelected ||
          isHovered ||
          node.matched ||
          node.degree >= 3 ||
          size > 16);
      if (showLabel) {
        const labelTexture = makeLabelTexture(node.label);
        const label = new THREE.Sprite(
          new THREE.SpriteMaterial({
            map: labelTexture,
            transparent: true,
            opacity: activeNodeId ? (active ? 0.96 : 0.28) : 0.82,
            depthWrite: false,
          }),
        );
        label.position.copy(node.position);
        label.position.y += size + 24;
        label.scale.set(150, 38, 1);
        group.add(label);
      }
    }

    const starField = starFieldRef.current;
    if (starField) starField.visible = displaySettings.showStars;
  }, [renderedGraph, displaySettings, hoverId, selectedId]);

  const updateDisplaySettings = useCallback(
    (patch: Partial<GraphDisplaySettings>) => {
      setDisplaySettings((previous) => ({ ...previous, ...patch }));
    },
    [],
  );

  const toggleType = useCallback((entityType: string) => {
    setDisabledTypes((previous) => {
      const next = new Set(previous);
      if (next.has(entityType)) next.delete(entityType);
      else next.add(entityType);
      return next;
    });
  }, []);

  const resetCamera = useCallback(() => {
    const camera = cameraRef.current;
    const controls3d = controlsRef.current;
    const group = graphGroupRef.current;
    if (!camera || !controls3d || !group) return;
    group.rotation.set(0, 0, 0);
    camera.position.set(0, 160, 880);
    controls3d.target.set(0, 0, 0);
    controls3d.update();
  }, []);

  return (
    <div className="relative h-[var(--panel-height-2xl)] min-h-[var(--panel-height-xl)] overflow-hidden bg-[#05070d] shadow-[inset_0_0_120px_rgba(14,165,233,0.12)]">
      <div
        ref={containerRef}
        className="absolute inset-0"
        data-knowledge-graph3d-container="true"
        data-knowledge-graph-3d-container
      />
      <div
        className={cn(
          "pointer-events-none absolute inset-0",
          graphTheme === "aurora"
            ? "bg-[radial-gradient(circle_at_18%_20%,rgba(45,212,191,0.24),transparent_32%),radial-gradient(circle_at_78%_72%,rgba(59,130,246,0.2),transparent_36%),linear-gradient(180deg,rgba(6,78,59,0.18),rgba(2,44,54,0.5))]"
            : "bg-[radial-gradient(circle_at_24%_18%,rgba(34,211,238,0.16),transparent_32%),radial-gradient(circle_at_74%_68%,rgba(163,230,53,0.12),transparent_34%),linear-gradient(180deg,rgba(2,6,23,0.16),rgba(2,6,23,0.52))]",
        )}
      />

      {!loading && !webglError && renderedGraph.nodes.length === 0 && (
        <div className="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center px-6 text-center">
          <div className="max-w-xs rounded-lg border border-white/10 bg-black/25 px-5 py-4 backdrop-blur-sm">
            <NetworkIcon className="mx-auto size-6 text-white/45" />
            <div className="mt-2 text-sm font-medium text-white/80">
              暂无图谱数据
            </div>
            <div className="mt-1 text-xs leading-5 text-white/50">
              先扫描本地文档，生成实体与关系后会显示在这里。
            </div>
          </div>
        </div>
      )}

      {(loading || webglError) && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-[#05070d]/80">
          {loading ? (
            <Loader2Icon className="size-6 animate-spin text-foreground/80" />
          ) : (
            <div className="max-w-sm rounded-lg border border-destructive/30 bg-destructive/20 px-4 py-3 text-sm text-destructive">
              {webglError}
            </div>
          )}
        </div>
      )}

      <div className="absolute left-3 right-3 top-3 z-10 flex flex-wrap items-center gap-2 sm:left-4 sm:right-auto sm:top-4 sm:max-w-[calc(100%-324px)]">
        <div className="relative min-w-0 flex-1 sm:min-w-[260px]">
          <SearchIcon className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/70" />
          <Input
            className="h-9 border-border-default bg-background/90 pl-9 text-xs text-foreground placeholder:text-muted-foreground/70 focus-visible:ring-primary/30"
            placeholder={t.knowledgePanel.searchPlaceholder}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <Button
          variant="outline"
          size="icon"
          className="size-9 border-border-default bg-background/90 text-foreground/90 hover:bg-muted hover:text-foreground"
          onClick={() => void loadGraph()}
          aria-label={t.knowledgeGraph.refresh}
        >
          <RefreshCwIcon className="size-4" />
        </Button>
        <div className="rounded-md border border-border-default bg-background/90 px-2.5 py-1.5 text-xs text-muted-foreground">
          {t.knowledgePanel.nodeAndEdgeStats(
            renderedGraph.nodes.length,
            renderedGraph.edges.length,
          )}
          {selectedId && ` · ${selectedId.slice(0, 22)}`}
          {hoverId && ` · ${hoverId.slice(0, 18)}`}
        </div>
        <Button
          variant="outline"
          size="icon"
          className="size-9 border-border-default bg-background/90 text-foreground/90 hover:bg-muted hover:text-foreground"
          onClick={() => setShowSettings((visible) => !visible)}
          aria-label="图谱设置"
          aria-pressed={showSettings}
        >
          <SlidersHorizontalIcon className="size-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="size-9 border-border-default bg-background/90 text-foreground/90 hover:bg-muted hover:text-foreground"
          onClick={() =>
            setGraphTheme((theme) => (theme === "nebula" ? "aurora" : "nebula"))
          }
          aria-label="切换图谱主题"
        >
          <PaletteIcon className="size-4" />
        </Button>
      </div>

      {showSettings && (
        <aside className="absolute bottom-3 left-3 right-3 z-10 max-h-[42vh] space-y-3 overflow-y-auto rounded-lg border border-border-default bg-background/80 p-3 text-foreground shadow-2xl backdrop-blur-md sm:bottom-auto sm:left-auto sm:right-4 sm:top-4 sm:max-h-[calc(100%-2rem)] sm:w-[250px]">
          {focusNode && (
            <Section icon={InfoIcon} title={controls.focus}>
              <div className="space-y-2 rounded-md border border-white/10 bg-background/40 p-2">
                <div className="min-w-0">
                  <div className="truncate text-xs font-semibold text-foreground">
                    {focusNode.label}
                  </div>
                  {focusNode.fullName !== focusNode.label && (
                    <div className="mt-0.5 break-words text-xs leading-4 text-muted-foreground/70">
                      {shortText(focusNode.fullName, 96)}
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-1.5 text-center">
                  <div className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-1">
                    <div className="text-xs text-muted-foreground/70">
                      {controls.degree}
                    </div>
                    <div className="font-mono text-xs text-foreground/90">
                      {focusNode.backendDegree || focusEdges.length}
                    </div>
                  </div>
                  <div className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-1">
                    <div className="text-xs text-muted-foreground/70">
                      {controls.confidence}
                    </div>
                    <div className="font-mono text-xs text-foreground/90">
                      {formatConfidence(focusNode.confidenceAvg)}
                    </div>
                  </div>
                  <div className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-1">
                    <div className="text-xs text-muted-foreground/70">
                      {controls.updated}
                    </div>
                    <div className="font-mono text-xs text-foreground/90">
                      {formatDateTime(focusNode.lastSeen)}
                    </div>
                  </div>
                </div>
                {focusNode.sources.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {focusNode.sources.slice(0, 3).map((source) => (
                      <span
                        key={source}
                        className="max-w-full truncate rounded bg-info/10 px-1.5 py-0.5 text-xs text-info"
                      >
                        {shortText(source, 28)}
                      </span>
                    ))}
                  </div>
                )}
                {focusEdges.length > 0 && (
                  <div className="space-y-1 border-t border-white/10 pt-2">
                    <div className="text-xs font-semibold uppercase tracking-caps text-muted-foreground/70">
                      {controls.evidence}
                    </div>
                    {focusEdges.map((edge) => {
                      const outbound = edge.source === focusNode.id;
                      const peer = outbound ? edge.target : edge.source;
                      return (
                        <div
                          key={edge.id}
                          className="rounded border border-white/10 bg-white/[0.025] px-2 py-1.5"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs text-foreground/80">
                              {outbound ? "->" : "<-"}{" "}
                              {shortText(edge.label, 24)}
                            </span>
                            <span className="font-mono text-xs text-muted-foreground/70">
                              {formatConfidence(edge.confidence)}
                            </span>
                          </div>
                          <div className="mt-0.5 truncate text-xs text-muted-foreground/70">
                            {shortText(peer, 48)}
                          </div>
                          {(edge.sourceRef || edge.status !== "active") && (
                            <div className="mt-1 flex gap-1 text-xs text-muted-foreground/50">
                              {edge.status !== "active" && (
                                <span>{edge.status}</span>
                              )}
                              {edge.sourceRef && (
                                <span>{shortText(edge.sourceRef, 30)}</span>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </Section>
          )}

          <Section icon={FilterIcon} title={controls.filters}>
            <RangeControl
              label={controls.confidence}
              min={0}
              max={1}
              step={0.05}
              value={minConfidence}
              format={(value) => `${Math.round(value * 100)}%`}
              onChange={setMinConfidence}
            />
          </Section>

          <Section icon={TagsIcon} title={controls.groups}>
            <div className="grid gap-1.5">
              {entityGroups.slice(0, 8).map(([entityType, group]) => {
                const disabled = disabledTypes.has(entityType);
                return (
                  <button
                    key={entityType}
                    type="button"
                    onClick={() => toggleType(entityType)}
                    className={cn(
                      "flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                      disabled
                        ? "text-muted-foreground/50 hover:bg-background/80"
                        : "text-foreground/80 hover:bg-muted/70",
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        className="size-2.5 shrink-0 rounded-full"
                        style={{
                          background: disabled
                            ? "var(--muted-foreground)"
                            : group.color,
                        }}
                      />
                      <span className="truncate">
                        {entityTypeLabel(entityType)}
                      </span>
                    </span>
                    <span className="font-mono text-xs text-muted-foreground/70">
                      {group.count}
                    </span>
                  </button>
                );
              })}
            </div>
          </Section>

          <Section icon={EyeIcon} title={controls.display}>
            <div className="grid gap-2">
              {displayToggles.map(([key, label]) => (
                <label
                  key={key}
                  className="flex items-center justify-between gap-3 text-xs text-foreground/80"
                >
                  <span>{label}</span>
                  <Switch
                    checked={displaySettings[key]}
                    onCheckedChange={(checked) =>
                      updateDisplaySettings({ [key]: checked })
                    }
                  />
                </label>
              ))}
              <RangeControl
                label={controls.nodeSize}
                min={0.65}
                max={1.7}
                step={0.05}
                value={displaySettings.nodeScale}
                onChange={(value) =>
                  updateDisplaySettings({ nodeScale: value })
                }
              />
              <RangeControl
                label={controls.linkWidth}
                min={0.55}
                max={1.8}
                step={0.05}
                value={displaySettings.linkScale}
                onChange={(value) =>
                  updateDisplaySettings({ linkScale: value })
                }
              />
            </div>
          </Section>

          <Section icon={ZapIcon} title={controls.forces}>
            <div className="grid gap-2">
              <RangeControl
                label={controls.linkDistance}
                min={90}
                max={260}
                step={5}
                value={displaySettings.linkDistance}
                format={(value) => `${Math.round(value)}`}
                onChange={(value) =>
                  updateDisplaySettings({ linkDistance: value })
                }
              />
              <RangeControl
                label={controls.spread}
                min={0.72}
                max={1.9}
                step={0.04}
                value={displaySettings.spread}
                onChange={(value) => updateDisplaySettings({ spread: value })}
              />
            </div>
          </Section>

          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 w-full border-border-default bg-transparent text-xs text-foreground/80 hover:bg-muted hover:text-foreground"
            onClick={resetCamera}
          >
            <SlidersHorizontalIcon className="mr-1.5 size-3.5" />
            {controls.fitGraph}
          </Button>
        </aside>
      )}
    </div>
  );
}

export function KnowledgeGraphView() {
  return <KnowledgeGraph3DContent />;
}
