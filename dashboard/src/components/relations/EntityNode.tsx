import { Handle, Position } from "@xyflow/react";
import { memo } from "react";

import {
  ENTITY_COLOR,
  ENTITY_LABEL,
  type EntityType,
} from "./entityType";

export interface EntityNodeData extends Record<string, unknown> {
  label: string;
  entityType: EntityType;
  relationCount: number;
  selected?: boolean;
  /** Allow nodes to render as faded when filters cull them out of the
   *  primary set but they're still kept for orientation. */
  dimmed?: boolean;
}

/**
 * Custom react-flow node — a small card with a colored type bar, the
 * canonical entity name, a type badge, and a relation-count pill.
 *
 * Fixed width keeps the layout predictable across long Turkish names;
 * truncation + native `title` covers the overflow case. The colored
 * bar at the top is the strongest visual signal so users can tell
 * type at a glance even when zoomed out.
 */
const EntityNodeComponent = ({
  data,
  selected,
}: {
  data: EntityNodeData;
  selected?: boolean;
}) => {
  const color = ENTITY_COLOR[data.entityType];
  const dimmed = data.dimmed ?? false;
  return (
    <div
      className={`group relative w-[160px] rounded-lg border bg-card text-foreground shadow-sm transition-all ${
        selected
          ? "border-primary ring-2 ring-primary/40"
          : "border-border hover:border-foreground/40 hover:shadow-md"
      } ${dimmed ? "opacity-40" : ""}`}
      style={{
        // Subtle tint of the type color in the background so the bar
        // doesn't feel disconnected from the body.
        boxShadow: selected
          ? `0 0 0 2px ${color}33, 0 4px 12px ${color}22`
          : undefined,
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: color, width: 6, height: 6 }}
      />

      {/* Type bar — top accent strip */}
      <div
        className="h-1.5 w-full rounded-t-lg"
        style={{ background: color }}
      />

      <div className="px-3 py-2">
        <div
          className="truncate text-sm font-medium leading-tight"
          title={data.label}
        >
          {data.label}
        </div>
        <div className="mt-1 flex items-center justify-between text-[10px] uppercase tracking-wide text-muted">
          <span>{ENTITY_LABEL[data.entityType]}</span>
          <span
            className="rounded-full px-1.5 py-0.5 font-medium"
            style={{
              background: `${color}1a`,
              color,
            }}
          >
            {data.relationCount}
          </span>
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: color, width: 6, height: 6 }}
      />
    </div>
  );
};

export const EntityNode = memo(EntityNodeComponent);
