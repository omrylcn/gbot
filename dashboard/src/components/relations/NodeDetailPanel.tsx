import { X } from "lucide-react";
import type { Edge } from "@xyflow/react";

import type { MemoryRelation } from "@/api/admin";
import {
  ENTITY_COLOR,
  ENTITY_LABEL,
  RELATION_COLOR,
  RELATION_LABEL,
  getRelationCategory,
  type EntityType,
  type RelationCategory,
} from "./entityType";

interface NodeDetailPanelProps {
  relations: readonly MemoryRelation[];
  entityTypeMap: Map<string, EntityType>;
  edges: readonly Edge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  onClose: () => void;
}

/** Floating right-side panel inside the graph container. Renders details
 *  for the selected node OR edge — whichever was last clicked. */
export function NodeDetailPanel({
  relations,
  entityTypeMap,
  edges,
  selectedNodeId,
  selectedEdgeId,
  onClose,
}: NodeDetailPanelProps) {
  if (!selectedNodeId && !selectedEdgeId) return null;

  return (
    <div className="absolute right-3 top-3 bottom-3 w-72 overflow-hidden rounded-lg border border-border bg-card/95 backdrop-blur shadow-lg">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-xs font-medium text-foreground">
          {selectedNodeId ? "Entity" : "İlişki"}
        </span>
        <button
          onClick={onClose}
          className="text-muted hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="overflow-y-auto p-3" style={{ maxHeight: "calc(100% - 36px)" }}>
        {selectedNodeId ? (
          <NodeDetail
            entity={selectedNodeId}
            relations={relations}
            entityTypeMap={entityTypeMap}
          />
        ) : selectedEdgeId ? (
          <EdgeDetail edge={edges.find((e) => e.id === selectedEdgeId)} />
        ) : null}
      </div>
    </div>
  );
}

function NodeDetail({
  entity,
  relations,
  entityTypeMap,
}: {
  entity: string;
  relations: readonly MemoryRelation[];
  entityTypeMap: Map<string, EntityType>;
}) {
  const type = entityTypeMap.get(entity) ?? "unknown";
  const color = ENTITY_COLOR[type];

  // Collect all relations touching this entity, with the "other" side
  // and direction marker.
  const touching = relations
    .filter((r) => {
      const src = r.canonical_source || r.source_entity;
      const tgt = r.canonical_target || r.target_entity;
      return src === entity || tgt === entity;
    })
    .map((r) => {
      const src = r.canonical_source || r.source_entity;
      const isSource = src === entity;
      const other = isSource
        ? r.canonical_target || r.target_entity
        : src;
      return {
        ...r,
        other,
        isSource,
        category: getRelationCategory(r.relation),
      };
    });

  const top = touching.slice(0, 12);

  return (
    <div className="space-y-3">
      <div>
        <div className="text-base font-semibold text-foreground">{entity}</div>
        <div className="mt-1 flex items-center gap-2 text-[10px]">
          <span
            className="inline-flex items-center gap-1 rounded-full px-2 py-0.5"
            style={{ background: `${color}1a`, color }}
          >
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: color }}
            />
            {ENTITY_LABEL[type]}
          </span>
          <span className="text-muted">
            {touching.length} ilişki
          </span>
        </div>
      </div>

      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wide text-muted">
          İlişkiler
        </div>
        <ul className="space-y-1.5">
          {top.map((t) => {
            const cat = t.category;
            const catColor = RELATION_COLOR[cat];
            return (
              <li
                key={t.relation_id}
                className="flex items-start gap-2 text-xs"
              >
                <span
                  className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full"
                  style={{ background: catColor }}
                />
                <div className="flex-1 min-w-0">
                  <span className="text-muted">
                    {t.isSource ? "→" : "←"}
                  </span>{" "}
                  <span style={{ color: catColor }}>{t.relation}</span>{" "}
                  <span className="font-medium text-foreground">
                    {t.other}
                  </span>
                </div>
              </li>
            );
          })}
          {touching.length > 12 && (
            <li className="text-[11px] italic text-muted">
              … {touching.length - 12} daha
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}

function EdgeDetail({ edge }: { edge: Edge | undefined }) {
  if (!edge) return <div className="text-xs text-muted">Edge bulunamadı.</div>;
  const data = (edge.data ?? {}) as {
    category?: RelationCategory;
    relation?: string;
    confidence?: number;
  };
  const cat: RelationCategory = data.category ?? "other";
  const color = RELATION_COLOR[cat];
  return (
    <div className="space-y-3">
      <div className="text-sm">
        <span className="font-medium text-foreground">{edge.source}</span>{" "}
        <span style={{ color }}>→ {data.relation ?? edge.label}</span>{" "}
        <span className="font-medium text-foreground">{edge.target}</span>
      </div>
      <div className="text-xs space-y-1">
        <div>
          <span className="text-muted">Kategori:</span>{" "}
          <span style={{ color }}>{RELATION_LABEL[cat]}</span>
        </div>
        {data.confidence !== undefined && (
          <div>
            <span className="text-muted">Güven:</span>{" "}
            <span className="text-foreground">
              {(data.confidence * 100).toFixed(0)}%
            </span>
          </div>
        )}
        <div>
          <span className="text-muted">Edge ID:</span>{" "}
          <span className="font-mono text-foreground">{edge.id}</span>
        </div>
      </div>
    </div>
  );
}
