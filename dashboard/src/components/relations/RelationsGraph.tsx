import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ControlsContainer,
  SigmaContainer,
  useLoadGraph,
  useRegisterEvents,
  useSetSettings,
  useSigma,
  ZoomControl,
  FullScreenControl,
} from "@react-sigma/core";
import "@react-sigma/core/lib/style.css";
import { MultiDirectedGraph } from "graphology";
import type { Attributes } from "graphology-types";
import forceAtlas2 from "graphology-layout-forceatlas2";

import { adminApi, type MemoryRelation } from "@/api/admin";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import {
  ALL_ENTITY_TYPES,
  ALL_RELATION_CATEGORIES,
  ENTITY_COLOR,
  RELATION_COLOR,
  deriveEntityTypes,
  getRelationCategory,
  type EntityType,
  type RelationCategory,
} from "./entityType";
import { FilterBar } from "./FilterBar";
import { NodeDetailPanel, type PanelEdge } from "./NodeDetailPanel";

interface RelationsGraphProps {
  userId: string;
}

// ── Sigma settings (style + interaction) ──────────────────────
const SIGMA_SETTINGS = {
  // Default node renderer is "circle" — enough for our case.
  defaultNodeColor: "#64748b",
  defaultEdgeColor: "#475569",
  defaultEdgeType: "arrow",
  labelColor: { color: "#e2e8f0" },
  labelSize: 12,
  labelWeight: "500",
  labelDensity: 1.2,
  labelGridCellSize: 80,
  labelRenderedSizeThreshold: 6,
  // Drag, hover, edge fade nicely.
  enableEdgeEvents: true,
  renderEdgeLabels: false,
  zIndex: true,
};

interface BuildArgs {
  relations: readonly MemoryRelation[];
  entityTypeMap: Map<string, EntityType>;
  enabledTypes: Set<EntityType>;
  enabledCategories: Set<RelationCategory>;
  search: string;
  minDegree: number;
  hoveredNode: string | null;
}

/**
 * Build a graphology Graph for the visible (filtered) subset of
 * relations. Pure function so it can be memoised.
 */
function buildGraph(args: BuildArgs): {
  graph: MultiDirectedGraph;
  totalNodes: number;
  totalEdges: number;
  visibleNodes: number;
  visibleEdges: number;
} {
  const {
    relations,
    entityTypeMap,
    enabledTypes,
    enabledCategories,
    search,
    minDegree,
    hoveredNode,
  } = args;

  // ── Pre-pass: count degree across ALL relations (so the slider
  //   threshold matches the user's mental model regardless of
  //   category filter that runs later). ──────────────────────
  const totalDegree = new Map<string, number>();
  for (const r of relations) {
    const s = r.canonical_source || r.source_entity;
    const t = r.canonical_target || r.target_entity;
    if (s) totalDegree.set(s, (totalDegree.get(s) ?? 0) + 1);
    if (t) totalDegree.set(t, (totalDegree.get(t) ?? 0) + 1);
  }

  const lowerSearch = search.trim().toLowerCase();

  // ── First filter: nodes that survive type + min-degree + search ──
  const candidateIds = new Set<string>();
  for (const [id, deg] of totalDegree) {
    const type = entityTypeMap.get(id) ?? "unknown";
    if (!enabledTypes.has(type)) continue;
    if (deg < minDegree) continue;
    if (lowerSearch && !id.toLowerCase().includes(lowerSearch)) continue;
    candidateIds.add(id);
  }

  // ── Second filter: edges where both endpoints are candidates AND
  //   category is enabled ────────────────────────────────────
  const validRelations: MemoryRelation[] = [];
  for (const r of relations) {
    const s = r.canonical_source || r.source_entity;
    const t = r.canonical_target || r.target_entity;
    if (!candidateIds.has(s) || !candidateIds.has(t)) continue;
    const cat = getRelationCategory(r.relation);
    if (!enabledCategories.has(cat)) continue;
    validRelations.push(r);
  }

  // ── Visible degree (post-filter) — used for radius sizing ──
  const visibleDegree = new Map<string, number>();
  for (const r of validRelations) {
    const s = r.canonical_source || r.source_entity;
    const t = r.canonical_target || r.target_entity;
    visibleDegree.set(s, (visibleDegree.get(s) ?? 0) + 1);
    visibleDegree.set(t, (visibleDegree.get(t) ?? 0) + 1);
  }

  const graph = new MultiDirectedGraph();

  // Hovered node + neighbours: full opacity. Everything else dims.
  const neighbours = new Set<string>();
  if (hoveredNode) {
    neighbours.add(hoveredNode);
    for (const r of validRelations) {
      const s = r.canonical_source || r.source_entity;
      const t = r.canonical_target || r.target_entity;
      if (s === hoveredNode) neighbours.add(t);
      if (t === hoveredNode) neighbours.add(s);
    }
  }

  for (const id of candidateIds) {
    const type = entityTypeMap.get(id) ?? "unknown";
    const deg = visibleDegree.get(id) ?? 0;
    // Sigma uses node `size` directly as radius in pixels (logic units).
    // Range 6 (small) → 28 (hub). Log scale so a 40-rel hub doesn't
    // dwarf a 2-rel node.
    const size =
      deg <= 1
        ? 6
        : deg >= 30
          ? 28
          : 6 + (Math.log(deg) / Math.log(30)) * 22;
    const isFaded = hoveredNode !== null && !neighbours.has(id);
    graph.addNode(id, {
      x: Math.random(), // forceAtlas2 will refine these
      y: Math.random(),
      size,
      label: id,
      color: ENTITY_COLOR[type],
      type: "circle",
      entityType: type,
      relationCount: deg,
      hidden: false,
      forceLabel: deg >= 8 || hoveredNode === id,
      labelColor: isFaded ? "#475569" : "#e2e8f0",
      // Custom colour fade (used by `nodeReducer` below).
      faded: isFaded,
    });
  }

  for (const r of validRelations) {
    const s = r.canonical_source || r.source_entity;
    const t = r.canonical_target || r.target_entity;
    const cat = getRelationCategory(r.relation);
    const color = RELATION_COLOR[cat];
    const isFaded =
      hoveredNode !== null && !(neighbours.has(s) && neighbours.has(t));
    graph.addEdgeWithKey(r.relation_id, s, t, {
      type: "arrow",
      size: 1.2,
      color,
      label: r.relation,
      relation: r.relation,
      category: cat,
      confidence: r.confidence,
      faded: isFaded,
    });
  }

  // ── Apply forceAtlas2 layout synchronously ──
  if (graph.order > 1) {
    forceAtlas2.assign(graph, {
      iterations: 250,
      settings: {
        gravity: 1.2,
        scalingRatio: 12,
        slowDown: 4,
        adjustSizes: true,
        barnesHutOptimize: graph.order > 80,
        linLogMode: false,
        outboundAttractionDistribution: false,
        edgeWeightInfluence: 0,
        strongGravityMode: false,
      },
    });
  }

  return {
    graph,
    totalNodes: totalDegree.size,
    totalEdges: relations.length,
    visibleNodes: candidateIds.size,
    visibleEdges: validRelations.length,
  };
}

/** Loads the graph + applies fade reducer based on hovered node. */
function GraphLoader({
  graph,
}: {
  graph: MultiDirectedGraph;
}) {
  const loadGraph = useLoadGraph();
  const setSettings = useSetSettings();

  useEffect(() => {
    loadGraph(graph);
  }, [graph, loadGraph]);

  useEffect(() => {
    setSettings({
      nodeReducer: (_node, data) => {
        if (data.faded) {
          return {
            ...data,
            color: `${data.color as string}33`,
            label: undefined,
          };
        }
        return data;
      },
      edgeReducer: (_edge, data) => {
        if (data.faded) {
          return { ...data, hidden: true };
        }
        return { ...data, color: `${data.color as string}88` };
      },
    });
  }, [setSettings]);

  return null;
}

/** Wires hover, click, pane-click, etc. */
function GraphEvents({
  setHoveredNode,
  setSelectedNode,
  setSelectedEdge,
}: {
  setHoveredNode: (id: string | null) => void;
  setSelectedNode: (id: string | null) => void;
  setSelectedEdge: (id: string | null) => void;
}) {
  const sigma = useSigma();
  const registerEvents = useRegisterEvents();

  useEffect(() => {
    registerEvents({
      enterNode: (event) => {
        setHoveredNode(event.node);
        sigma.getContainer().style.cursor = "pointer";
      },
      leaveNode: () => {
        setHoveredNode(null);
        sigma.getContainer().style.cursor = "default";
      },
      clickNode: (event) => {
        setSelectedNode(event.node);
        setSelectedEdge(null);
      },
      clickEdge: (event) => {
        setSelectedEdge(event.edge);
        setSelectedNode(null);
      },
      clickStage: () => {
        setSelectedNode(null);
        setSelectedEdge(null);
      },
    });
  }, [registerEvents, sigma, setHoveredNode, setSelectedNode, setSelectedEdge]);

  return null;
}

export function RelationsGraph({ userId }: RelationsGraphProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["memory-relations", userId, "graph"],
    queryFn: () => adminApi.getMemoryRelations(userId),
  });

  const allRelations = useMemo<MemoryRelation[]>(
    () => data?.relations ?? [],
    [data?.relations],
  );

  const entityTypeMap = useMemo(
    () => deriveEntityTypes(allRelations),
    [allRelations],
  );

  const [enabledTypes, setEnabledTypes] = useState<Set<EntityType>>(
    () => new Set(ALL_ENTITY_TYPES),
  );
  const [enabledCategories, setEnabledCategories] = useState<
    Set<RelationCategory>
  >(() => new Set(ALL_RELATION_CATEGORIES));
  const [search, setSearch] = useState("");
  const [minDegree, setMinDegree] = useState(2);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);

  const isFiltered =
    enabledTypes.size !== ALL_ENTITY_TYPES.length ||
    enabledCategories.size !== ALL_RELATION_CATEGORIES.length ||
    search.trim() !== "" ||
    minDegree !== 2;

  const resetFilters = () => {
    setEnabledTypes(new Set(ALL_ENTITY_TYPES));
    setEnabledCategories(new Set(ALL_RELATION_CATEGORIES));
    setSearch("");
    setMinDegree(2);
  };

  // Re-derive the layout-ready graph when any input changes. Hovered
  // node deliberately NOT in deps (we use sigma's nodeReducer for
  // hover dim instead of rebuilding the whole layout).
  const built = useMemo(
    () =>
      buildGraph({
        relations: allRelations,
        entityTypeMap,
        enabledTypes,
        enabledCategories,
        search,
        minDegree,
        hoveredNode: null,
      }),
    [
      allRelations,
      entityTypeMap,
      enabledTypes,
      enabledCategories,
      search,
      minDegree,
    ],
  );

  // For the side panel: reuse the original allRelations for context
  // (some relations may be filtered out of the graph but the user
  // might still want to see them in the detail listing).
  const allEdgesForPanel = useMemo<PanelEdge[]>(
    () =>
      allRelations.map((r) => ({
        id: r.relation_id,
        source: r.canonical_source || r.source_entity,
        target: r.canonical_target || r.target_entity,
        label: r.relation,
        data: {
          relation: r.relation,
          confidence: r.confidence,
          category: getRelationCategory(r.relation),
        },
      })),
    [allRelations],
  );

  // Reapply hover state via sigma's nodeReducer hook — we update the
  // graph attributes in-place to flip `faded` flags. This avoids a
  // full forceAtlas2 re-layout on every hover.
  const containerRef = useRef<HTMLDivElement>(null);

  if (isLoading) return <LoadingSpinner />;

  if (built.totalNodes === 0) {
    return (
      <div className="flex h-[480px] items-center justify-center rounded-xl border border-border bg-card text-sm text-muted">
        Bu kullanıcı için henüz relation yok.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <FilterBar
        enabledTypes={enabledTypes}
        setEnabledTypes={setEnabledTypes}
        enabledCategories={enabledCategories}
        setEnabledCategories={setEnabledCategories}
        search={search}
        setSearch={setSearch}
        minDegree={minDegree}
        setMinDegree={setMinDegree}
        nodeCount={built.visibleNodes}
        totalNodes={built.totalNodes}
        edgeCount={built.visibleEdges}
        totalEdges={built.totalEdges}
        isFiltered={isFiltered}
        onReset={resetFilters}
      />

      <div
        ref={containerRef}
        className="relative h-[78vh] min-h-[560px] overflow-hidden rounded-xl border border-border bg-card"
      >
        <SigmaContainer
          style={{ height: "100%", width: "100%", background: "transparent" }}
          settings={SIGMA_SETTINGS}
          graph={MultiDirectedGraph}
        >
          <GraphLoader graph={built.graph} />
          <GraphEvents
            setHoveredNode={setHoveredNode}
            setSelectedNode={setSelectedNode}
            setSelectedEdge={setSelectedEdge}
          />
          <HoverFader hoveredNode={hoveredNode} />
          <ControlsContainer position={"bottom-right"}>
            <ZoomControl />
            <FullScreenControl />
          </ControlsContainer>
        </SigmaContainer>

        <NodeDetailPanel
          relations={allRelations}
          entityTypeMap={entityTypeMap}
          edges={allEdgesForPanel}
          selectedNodeId={selectedNode}
          selectedEdgeId={selectedEdge}
          onClose={() => {
            setSelectedNode(null);
            setSelectedEdge(null);
          }}
        />
      </div>
    </div>
  );
}

/**
 * Watches `hoveredNode` and updates each node/edge's `faded` attribute
 * in-place so the nodeReducer/edgeReducer in GraphLoader picks it up.
 * Avoids a full graph rebuild on hover.
 */
function HoverFader({ hoveredNode }: { hoveredNode: string | null }) {
  const sigma = useSigma();
  useEffect(() => {
    const graph = sigma.getGraph();
    if (!hoveredNode) {
      graph.forEachNode((_id: string, attrs: Attributes) => {
        attrs.faded = false;
      });
      graph.forEachEdge((_id: string, attrs: Attributes) => {
        attrs.faded = false;
      });
      sigma.refresh();
      return;
    }
    const neighbourSet = new Set<string>([hoveredNode]);
    graph.forEachEdge(
      (_id: string, _attrs: Attributes, source: string, target: string) => {
        if (source === hoveredNode) neighbourSet.add(target);
        if (target === hoveredNode) neighbourSet.add(source);
      },
    );
    graph.forEachNode((id: string, attrs: Attributes) => {
      attrs.faded = !neighbourSet.has(id);
    });
    graph.forEachEdge(
      (_id: string, attrs: Attributes, source: string, target: string) => {
        attrs.faded = !(neighbourSet.has(source) && neighbourSet.has(target));
      },
    );
    sigma.refresh();
  }, [sigma, hoveredNode]);
  return null;
}

export default RelationsGraph;
