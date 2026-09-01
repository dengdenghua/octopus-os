import { memo, useMemo } from "react";
import { ChevronRightIcon, ChevronDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AgentTile } from "../agent-workbench-utils";

interface AgentNode {
  tile: AgentTile;
  children: AgentNode[];
  depth: number;
}

interface NestedAgentTreeProps {
  agentTiles: AgentTile[];
  selectedAgentId: string | null;
  onSelectAgent: (agentId: string) => void;
  expandedNodes: Set<string>;
  onToggleExpand: (agentId: string) => void;
}

/**
 * Build a hierarchical tree from flat agent tiles using parentToolUseId.
 * Returns root nodes (agents with no parent or parent not in the list).
 */
function buildAgentTree(tiles: AgentTile[]): AgentNode[] {
  // Index all tiles by their ID for quick lookup
  const tileById = new Map<string, AgentTile>();
  for (const tile of tiles) {
    tileById.set(tile.id, tile);
  }

  // Build parent → children map
  const childrenByParent = new Map<string, AgentTile[]>();
  const roots: AgentTile[] = [];

  for (const tile of tiles) {
    if (!tile.parentToolUseId) {
      roots.push(tile);
    } else {
      const siblings = childrenByParent.get(tile.parentToolUseId) ?? [];
      siblings.push(tile);
      childrenByParent.set(tile.parentToolUseId, siblings);
    }
  }

  // Recursively build tree nodes
  function buildNode(tile: AgentTile, depth: number): AgentNode {
    const children = childrenByParent.get(tile.id) ?? [];
    return {
      tile,
      depth,
      children: children
        .sort((a, b) => a.startedAt - b.startedAt)
        .map((child) => buildNode(child, depth + 1)),
    };
  }

  return roots
    .sort((a, b) => a.startedAt - b.startedAt)
    .map((root) => buildNode(root, 0));
}

function AgentNodeRow({
  node,
  selected,
  expanded,
  expandedNodes,
  selectedAgentId,
  onSelect,
  onToggleExpand,
}: {
  node: AgentNode;
  selected: boolean;
  expanded: boolean;
  expandedNodes: Set<string>;
  selectedAgentId: string | null;
  onSelect: (agentId: string) => void;
  onToggleExpand: (agentId: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const indent = node.depth * 16;

  const statusColor = {
    running: "text-blue-500",
    waiting_approval: "text-yellow-500",
    done: "text-green-500",
    pending: "text-gray-400",
    error: "text-red-500",
  }[node.tile.status];

  return (
    <>
      <div
        className={cn(
          "group flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors cursor-pointer",
          selected
            ? "bg-accent/80 text-accent-foreground"
            : "hover:bg-accent/50 text-foreground/90",
        )}
        style={{ paddingLeft: `${8 + indent}px` }}
        onClick={() => onSelect(node.tile.id)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(node.tile.id);
          }
        }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onToggleExpand(node.tile.id);
            }}
            className="flex-shrink-0 p-0.5 hover:bg-accent/70 rounded"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? (
              <ChevronDownIcon className="h-3.5 w-3.5" />
            ) : (
              <ChevronRightIcon className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="w-4 flex-shrink-0" />
        )}

        <span className="text-base flex-shrink-0">{node.tile.avatar ?? "🤖"}</span>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate font-medium">
              {node.tile.codename || node.tile.roleDisplayName || node.tile.role || node.tile.name}
            </span>
            {node.tile.iterationCount !== undefined && (
              <span className="text-xs text-muted-foreground">
                {node.tile.iterationCount} iter
              </span>
            )}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {node.tile.task || node.tile.prompt}
          </div>
        </div>

        <span className={cn("flex-shrink-0 text-xs font-medium", statusColor)}>
          {node.tile.status === "running" && "●"}
          {node.tile.status === "waiting_approval" && "⏸"}
          {node.tile.status === "done" && "✓"}
          {node.tile.status === "error" && "✗"}
          {node.tile.status === "pending" && "○"}
        </span>
      </div>

      {expanded &&
        node.children.map((child) => (
          <AgentNodeRow
            key={child.tile.id}
            node={child}
            selected={child.tile.id === selectedAgentId}
            expanded={expandedNodes.has(child.tile.id)}
            expandedNodes={expandedNodes}
            selectedAgentId={selectedAgentId}
            onSelect={onSelect}
            onToggleExpand={onToggleExpand}
          />
        ))}
    </>
  );
}

function NestedAgentTreeImpl({
  agentTiles,
  selectedAgentId,
  onSelectAgent,
  expandedNodes,
  onToggleExpand,
}: NestedAgentTreeProps) {
  const tree = useMemo(() => buildAgentTree(agentTiles), [agentTiles]);

  if (tree.length === 0) {
    return null;
  }

  function renderNode(node: AgentNode): React.ReactNode {
    const selected = node.tile.id === selectedAgentId;
    const expanded = expandedNodes.has(node.tile.id);

    return (
      <div key={node.tile.id}>
        <AgentNodeRow
          node={node}
          selected={selected}
          expanded={expanded}
          expandedNodes={expandedNodes}
          selectedAgentId={selectedAgentId}
          onSelect={onSelectAgent}
          onToggleExpand={onToggleExpand}
        />
        {expanded && node.children.map(renderNode)}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 py-1">
      {tree.map(renderNode)}
    </div>
  );
}

export const NestedAgentTree = memo(NestedAgentTreeImpl);
