import { useEffect, useMemo, useRef, useState } from "react";
// @ts-expect-error — react-cytoscapejs ships no .d.ts
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape from "cytoscape";
import type { Core, ElementDefinition } from "cytoscape";

// cytoscape's exported `Stylesheet*` types don't match the
// `{selector, style: {...}}` runtime shape we use; use a loose
// local type instead.
type StyleSheet = { selector: string; style: Record<string, unknown> };
// @ts-expect-error — cytoscape-cose-bilkent ships no .d.ts
import coseBilkent from "cytoscape-cose-bilkent";
import { useQuery } from "@tanstack/react-query";

import { adminApi } from "../api/admin";
import type { MemoryRelation } from "../api/admin";

cytoscape.use(coseBilkent);

interface Props {
  userId: string;
}

interface SidePanelData {
  type: "node" | "edge";
  label: string;
  detail: string;
}

const STYLE: StyleSheet[] = [
  {
    selector: "node",
    style: {
      "background-color": "hsl(220, 13%, 24%)",
      "label": "data(label)",
      "color": "hsl(220, 9%, 92%)",
      "font-size": 11,
      "text-valign": "center",
      "text-halign": "center",
      "width": "mapData(degree, 1, 20, 30, 70)",
      "height": "mapData(degree, 1, 20, 30, 70)",
      "border-width": 2,
      "border-color": "hsl(220, 13%, 38%)",
    },
  },
  {
    selector: "node:selected",
    style: {
      "background-color": "hsl(217, 91%, 60%)",
      "border-color": "hsl(217, 91%, 70%)",
      "border-width": 3,
    },
  },
  {
    selector: "edge",
    style: {
      "width": "mapData(weight, 0.3, 1.0, 1, 4)",
      "line-color": "hsl(220, 13%, 38%)",
      "target-arrow-color": "hsl(220, 13%, 38%)",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "label": "data(label)",
      "font-size": 9,
      "color": "hsl(220, 9%, 60%)",
      "text-rotation": "autorotate" as unknown as number,
      "text-margin-y": -8,
    },
  },
  {
    selector: "edge:selected",
    style: {
      "line-color": "hsl(217, 91%, 60%)",
      "target-arrow-color": "hsl(217, 91%, 60%)",
      "color": "hsl(217, 91%, 70%)",
    },
  },
];

// Convert /relations API → cytoscape elements (nodes + edges + per-node degree).
function toElements(rels: MemoryRelation[]): ElementDefinition[] {
  const nodes = new Map<string, { id: string; label: string; degree: number }>();
  const edges: ElementDefinition[] = [];

  const ensure = (id: string) => {
    if (!nodes.has(id)) {
      nodes.set(id, { id, label: id, degree: 0 });
    }
    nodes.get(id)!.degree += 1;
  };

  for (const r of rels) {
    const src = r.canonical_source || r.source_entity;
    const tgt = r.canonical_target || r.target_entity;
    if (!src || !tgt) continue;
    ensure(src);
    ensure(tgt);
    edges.push({
      data: {
        id: r.relation_id,
        source: src,
        target: tgt,
        label: r.relation,
        weight: r.confidence ?? 0.5,
      },
    });
  }

  const nodeElements: ElementDefinition[] = Array.from(nodes.values()).map((n) => ({
    data: { id: n.id, label: n.label, degree: n.degree },
  }));
  return [...nodeElements, ...edges];
}

export function RelationsGraph({ userId }: Props) {
  const cyRef = useRef<Core | null>(null);
  const [side, setSide] = useState<SidePanelData | null>(null);
  const [filter, setFilter] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["memory-relations-graph", userId],
    queryFn: () => adminApi.getMemoryRelations(userId),
  });

  const allElements = useMemo(
    () => (data?.relations ? toElements(data.relations) : []),
    [data]
  );

  const elements = useMemo(() => {
    if (!filter.trim()) return allElements;
    const q = filter.toLowerCase();
    const matchedNodes = new Set(
      allElements
        .filter((el) => el.data.id && !el.data.source && el.data.label?.toLowerCase().includes(q))
        .map((el) => el.data.id as string)
    );
    if (matchedNodes.size === 0) return [];
    const visibleEdges = allElements.filter(
      (el) =>
        el.data.source &&
        (matchedNodes.has(el.data.source as string) || matchedNodes.has(el.data.target as string))
    );
    visibleEdges.forEach((e) => {
      matchedNodes.add(e.data.source as string);
      matchedNodes.add(e.data.target as string);
    });
    return allElements.filter(
      (el) =>
        (el.data.id && !el.data.source && matchedNodes.has(el.data.id as string)) ||
        (el.data.source && visibleEdges.includes(el))
    );
  }, [allElements, filter]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.removeListener("tap");
    cy.on("tap", "node", (evt) => {
      const node = evt.target;
      setSide({
        type: "node",
        label: node.data("label"),
        detail: `${node.degree(false)} relation(s)`,
      });
    });
    cy.on("tap", "edge", (evt) => {
      const e = evt.target;
      setSide({
        type: "edge",
        label: e.data("label"),
        detail: `${e.data("source")} → ${e.data("target")}\nconfidence: ${(
          (e.data("weight") ?? 0.5) * 100
        ).toFixed(0)}%`,
      });
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) setSide(null);
    });
  }, [elements]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by entity name…"
          className="flex-1 px-3 py-1.5 text-sm bg-background border border-border rounded-lg
                     text-foreground placeholder:text-muted focus:outline-none
                     focus:ring-1 focus:ring-primary"
        />
        <span className="text-xs text-muted">
          {elements.filter((e) => e.data.id && !e.data.source).length} nodes /{" "}
          {elements.filter((e) => e.data.source).length} edges
        </span>
      </div>

      <div className="flex gap-3">
        <div
          className="flex-1 bg-card border border-border rounded-xl overflow-hidden"
          style={{ height: 520 }}
        >
          {isLoading ? (
            <div className="h-full flex items-center justify-center text-sm text-muted">
              Loading…
            </div>
          ) : elements.length === 0 ? (
            <div className="h-full flex items-center justify-center text-sm text-muted italic">
              No relations to graph
            </div>
          ) : (
            <CytoscapeComponent
              elements={elements as ElementDefinition[]}
              style={{ width: "100%", height: "100%" }}
              stylesheet={STYLE}
              cy={(cy: Core) => {
                cyRef.current = cy;
              }}
              layout={{
                name: "cose-bilkent",
                animate: false,
                idealEdgeLength: 100,
                nodeRepulsion: 4500,
                edgeElasticity: 0.45,
              } as cytoscape.LayoutOptions}
            />
          )}
        </div>

        <div className="w-64 bg-card border border-border rounded-xl p-4 text-sm">
          {side ? (
            <>
              <div className="text-xs text-muted uppercase mb-1">{side.type}</div>
              <div className="text-base font-medium text-foreground mb-2 break-words">
                {side.label}
              </div>
              <pre className="text-xs text-muted whitespace-pre-wrap break-words">
                {side.detail}
              </pre>
            </>
          ) : (
            <div className="text-xs text-muted italic">
              Click a node or edge to inspect.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
