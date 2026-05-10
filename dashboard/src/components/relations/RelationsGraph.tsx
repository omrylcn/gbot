import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type EdgeMouseHandler,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { adminApi, type MemoryRelation } from "@/api/admin";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { EntityNode, type EntityNodeData } from "./EntityNode";
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
import { layoutGraph } from "./layout";
import { FilterBar } from "./FilterBar";
import { NodeDetailPanel } from "./NodeDetailPanel";

const NODE_TYPES = { entity: EntityNode };

interface RelationsGraphInnerProps {
  userId: string;
}

function RelationsGraphInner({ userId }: RelationsGraphInnerProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["memory-relations", userId, "graph"],
    queryFn: () => adminApi.getMemoryRelations(userId),
  });

  const allRelations = useMemo<MemoryRelation[]>(
    () => data?.relations ?? [],
    [data?.relations],
  );

  // Derive entity types once per relations refresh.
  const entityTypeMap = useMemo(
    () => deriveEntityTypes(allRelations),
    [allRelations],
  );

  // ── Filter state ──────────────────────────────────────────
  const [enabledTypes, setEnabledTypes] = useState<Set<EntityType>>(
    () => new Set(ALL_ENTITY_TYPES),
  );
  const [enabledCategories, setEnabledCategories] = useState<
    Set<RelationCategory>
  >(() => new Set(ALL_RELATION_CATEGORIES));
  const [search, setSearch] = useState("");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);

  const isFiltered =
    enabledTypes.size !== ALL_ENTITY_TYPES.length ||
    enabledCategories.size !== ALL_RELATION_CATEGORIES.length ||
    search.trim() !== "";

  const resetFilters = () => {
    setEnabledTypes(new Set(ALL_ENTITY_TYPES));
    setEnabledCategories(new Set(ALL_RELATION_CATEGORIES));
    setSearch("");
  };

  // ── Build candidate nodes + edges ────────────────────────
  const { allNodes, allEdges } = useMemo(() => {
    const nodeMap = new Map<string, { count: number }>();
    for (const r of allRelations) {
      const src = r.canonical_source || r.source_entity;
      const tgt = r.canonical_target || r.target_entity;
      if (src) nodeMap.set(src, { count: (nodeMap.get(src)?.count ?? 0) + 1 });
      if (tgt) nodeMap.set(tgt, { count: (nodeMap.get(tgt)?.count ?? 0) + 1 });
    }

    const nodes: Node[] = Array.from(nodeMap.entries()).map(
      ([id, { count }]) => ({
        id,
        type: "entity",
        position: { x: 0, y: 0 },
        data: {
          label: id,
          entityType: entityTypeMap.get(id) ?? "unknown",
          relationCount: count,
        } as EntityNodeData,
      }),
    );

    const edges: Edge[] = allRelations.map((r) => {
      const src = r.canonical_source || r.source_entity;
      const tgt = r.canonical_target || r.target_entity;
      const category = getRelationCategory(r.relation);
      const color = RELATION_COLOR[category];
      return {
        id: r.relation_id,
        source: src,
        target: tgt,
        label: r.relation,
        type: "smoothstep",
        animated: false,
        style: {
          stroke: color,
          strokeWidth: 1 + (r.confidence ?? 0.5) * 2,
          opacity: 0.7,
        },
        labelStyle: {
          fontSize: 10,
          fill: color,
          fontWeight: 500,
        },
        labelBgStyle: {
          fill: "var(--card)",
          fillOpacity: 0.9,
        },
        labelBgPadding: [3, 1] as [number, number],
        labelBgBorderRadius: 4,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color,
          width: 14,
          height: 14,
        },
        data: { category, relation: r.relation, confidence: r.confidence },
      } as Edge;
    });

    return { allNodes: nodes, allEdges: edges };
  }, [allRelations, entityTypeMap]);

  // ── Apply filters ────────────────────────────────────────
  const { filteredNodes, filteredEdges } = useMemo(() => {
    const lowerSearch = search.trim().toLowerCase();
    const survivingIds = new Set<string>();
    const nodeFiltered = allNodes.filter((n) => {
      const d = n.data as EntityNodeData;
      if (!enabledTypes.has(d.entityType)) return false;
      if (
        lowerSearch &&
        !d.label.toLowerCase().includes(lowerSearch)
      ) {
        return false;
      }
      survivingIds.add(n.id);
      return true;
    });

    const edgeFiltered = allEdges.filter((e) => {
      if (!survivingIds.has(e.source) || !survivingIds.has(e.target)) {
        return false;
      }
      const cat = (e.data?.category ?? "other") as RelationCategory;
      return enabledCategories.has(cat);
    });

    // Drop any node that has no surviving edges and is not isolate-default
    // — actually we keep them so isolated entities still appear, but cull
    // ones that lost all their edges due to category filter.
    const kept = new Set<string>();
    for (const e of edgeFiltered) {
      kept.add(e.source);
      kept.add(e.target);
    }
    // If category filter removed every edge, keep all surviving nodes
    // anyway so the user sees what's still there.
    const keepAllNodes = edgeFiltered.length === 0;
    const finalNodes = keepAllNodes
      ? nodeFiltered
      : nodeFiltered.filter(
          (n) =>
            kept.has(n.id) ||
            (n.data as EntityNodeData).relationCount === 0,
        );

    return { filteredNodes: finalNodes, filteredEdges: edgeFiltered };
  }, [allNodes, allEdges, enabledTypes, enabledCategories, search]);

  // ── Layout ───────────────────────────────────────────────
  const layoutKey = useMemo(
    () =>
      filteredNodes
        .map((n) => n.id)
        .sort()
        .join("|") +
      "::" +
      filteredEdges
        .map((e) => e.id)
        .sort()
        .join("|"),
    [filteredNodes, filteredEdges],
  );

  const positionedNodes = useMemo(
    () => layoutGraph(filteredNodes, filteredEdges),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layoutKey],
  );

  // Add `selected` / `dimmed` flags to node data for styling.
  const renderedNodes = useMemo(() => {
    return positionedNodes.map((n) => ({
      ...n,
      data: {
        ...(n.data as EntityNodeData),
        selected: n.id === selectedNode,
      },
    }));
  }, [positionedNodes, selectedNode]);

  const onNodeClick: NodeMouseHandler = (_, node) => {
    setSelectedNode(node.id);
    setSelectedEdge(null);
  };
  const onEdgeClick: EdgeMouseHandler = (_, edge) => {
    setSelectedEdge(edge.id);
    setSelectedNode(null);
  };
  const onPaneClick = () => {
    setSelectedNode(null);
    setSelectedEdge(null);
  };

  if (isLoading) return <LoadingSpinner />;

  if (allNodes.length === 0) {
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
        nodeCount={filteredNodes.length}
        totalNodes={allNodes.length}
        edgeCount={filteredEdges.length}
        totalEdges={allEdges.length}
        isFiltered={isFiltered}
        onReset={resetFilters}
      />

      <div className="relative h-[600px] overflow-hidden rounded-xl border border-border bg-card">
        <ReactFlow
          nodes={renderedNodes}
          edges={filteredEdges}
          nodeTypes={NODE_TYPES}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          onPaneClick={onPaneClick}
          fitView
          fitViewOptions={{ padding: 0.2, maxZoom: 1.2 }}
          minZoom={0.2}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          nodesDraggable
          panOnDrag
          zoomOnScroll
        >
          <Background gap={24} size={1} color="#334155" />
          <Controls
            showInteractive={false}
            className="!rounded-lg !border-border !bg-card"
          />
          <MiniMap
            nodeColor={(n) =>
              ENTITY_COLOR[
                ((n.data as EntityNodeData)?.entityType ?? "unknown") as EntityType
              ]
            }
            nodeStrokeWidth={2}
            maskColor="rgba(15, 23, 42, 0.6)"
            className="!rounded-lg !border-border !bg-card"
          />
        </ReactFlow>

        <NodeDetailPanel
          relations={allRelations}
          entityTypeMap={entityTypeMap}
          selectedNodeId={selectedNode}
          selectedEdgeId={selectedEdge}
          edges={allEdges}
          onClose={() => {
            setSelectedNode(null);
            setSelectedEdge(null);
          }}
        />
      </div>
    </div>
  );
}

export function RelationsGraph(props: RelationsGraphInnerProps) {
  return (
    <ReactFlowProvider>
      <RelationsGraphInner {...props} />
    </ReactFlowProvider>
  );
}

export default RelationsGraph;
