/**
 * Dagre layout wrapper for the relations graph.
 *
 * react-flow doesn't ship a layout algorithm — node positions arrive
 * as `{x, y}` pairs and we compute them ourselves. dagre gives a clean
 * left-to-right layered flow that scales to ~100 nodes with sub-50ms
 * render. Force-directed alternatives (d3-force, elkjs) either jiggle
 * forever or weigh too much for the gain.
 *
 * Isolated nodes (degree 0) get pushed into a 3-column grid in the
 * bottom-left corner so they don't waste graph width and don't drift
 * off-screen on zoom-fit.
 */

import dagre from "dagre";
import type { Edge, Node } from "@xyflow/react";

const NODE_WIDTH = 160;
const NODE_HEIGHT = 64;

export interface LayoutOptions {
  /** "LR" left-right (default — friendlier for long Turkish names),
   *  "TB" top-bottom (compact when names are short). */
  direction?: "LR" | "TB";
  /** Horizontal spacing between layers. Defaults to 80px. */
  ranksep?: number;
  /** Vertical spacing between siblings within a layer. Defaults to 30px. */
  nodesep?: number;
}

/**
 * Compute positions for `nodes` based on `edges`. Returns a NEW array
 * of nodes with `position` filled in; edges pass through untouched.
 *
 * Pure function — call from useMemo so positions don't recompute on
 * every render. Memoize key on the joined node ID list and edge list.
 */
export function layoutGraph(
  nodes: Node[],
  edges: Edge[],
  opts: LayoutOptions = {},
): Node[] {
  if (nodes.length === 0) return nodes;

  const direction = opts.direction ?? "LR";
  const ranksep = opts.ranksep ?? 80;
  const nodesep = opts.nodesep ?? 30;

  // Build a degree map so we can detach isolated nodes from the dagre
  // pass — they pollute the layout and dagre tries to spread them
  // across the layers, which looks worse than just gridding them.
  const degree = new Map<string, number>();
  for (const e of edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }

  const connectedIds = new Set(
    nodes.map((n) => n.id).filter((id) => (degree.get(id) ?? 0) > 0),
  );
  const connected = nodes.filter((n) => connectedIds.has(n.id));
  const isolated = nodes.filter((n) => !connectedIds.has(n.id));

  // ── Connected subgraph: dagre ────────────────────────────────
  const positioned: Node[] = [];

  if (connected.length > 0) {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: direction, ranksep, nodesep });
    g.setDefaultEdgeLabel(() => ({}));

    for (const n of connected) {
      g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }
    for (const e of edges) {
      // dagre handles multigraphs poorly when node ids appear in only
      // the edge map — keep the guard to avoid crashes from stale edges.
      if (connectedIds.has(e.source) && connectedIds.has(e.target)) {
        g.setEdge(e.source, e.target);
      }
    }

    dagre.layout(g);

    for (const n of connected) {
      const layoutNode = g.node(n.id);
      // dagre returns center-points; react-flow expects top-left.
      positioned.push({
        ...n,
        position: {
          x: layoutNode.x - NODE_WIDTH / 2,
          y: layoutNode.y - NODE_HEIGHT / 2,
        },
        // Make sure react-flow re-applies positions when we re-layout
        // (it caches by node id).
      });
    }
  }

  // ── Isolated subgraph: 3-column grid below the main flow ────
  if (isolated.length > 0) {
    // Find the bottom-left of the connected layout to anchor the grid.
    let minX = 0;
    let maxY = 0;
    for (const p of positioned) {
      if (p.position.x < minX || positioned.length === 1) minX = p.position.x;
      if (p.position.y > maxY) maxY = p.position.y;
    }
    const startX = minX;
    const startY = maxY + 100;
    const cols = 3;
    isolated.forEach((n, i) => {
      positioned.push({
        ...n,
        position: {
          x: startX + (i % cols) * (NODE_WIDTH + 20),
          y: startY + Math.floor(i / cols) * (NODE_HEIGHT + 20),
        },
      });
    });
  }

  return positioned;
}
