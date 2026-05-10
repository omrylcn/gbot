import { Search, X } from "lucide-react";

import {
  ALL_ENTITY_TYPES,
  ALL_RELATION_CATEGORIES,
  ENTITY_COLOR,
  ENTITY_LABEL,
  RELATION_COLOR,
  RELATION_LABEL,
  type EntityType,
  type RelationCategory,
} from "./entityType";

interface FilterBarProps {
  enabledTypes: Set<EntityType>;
  setEnabledTypes: (s: Set<EntityType>) => void;
  enabledCategories: Set<RelationCategory>;
  setEnabledCategories: (s: Set<RelationCategory>) => void;
  search: string;
  setSearch: (s: string) => void;
  minDegree: number;
  setMinDegree: (n: number) => void;
  nodeCount: number;
  totalNodes: number;
  edgeCount: number;
  totalEdges: number;
  isFiltered: boolean;
  onReset: () => void;
}

/**
 * Compact filter row above the graph: entity-type chips + relation-category
 * chips + name search + counter + reset link. Each chip toggles its own
 * presence in the filter set. Default state is "all enabled".
 */
export function FilterBar({
  enabledTypes,
  setEnabledTypes,
  enabledCategories,
  setEnabledCategories,
  search,
  setSearch,
  minDegree,
  setMinDegree,
  nodeCount,
  totalNodes,
  edgeCount,
  totalEdges,
  isFiltered,
  onReset,
}: FilterBarProps) {
  const toggleType = (t: EntityType) => {
    const next = new Set(enabledTypes);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    setEnabledTypes(next);
  };
  const toggleCat = (c: RelationCategory) => {
    const next = new Set(enabledCategories);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    setEnabledCategories(next);
  };

  return (
    <div className="space-y-2 rounded-xl border border-border bg-card p-3">
      {/* Type chips */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[10px] uppercase tracking-wide text-muted">
          Tip
        </span>
        {ALL_ENTITY_TYPES.map((t) => {
          const on = enabledTypes.has(t);
          const color = ENTITY_COLOR[t];
          return (
            <button
              key={t}
              onClick={() => toggleType(t)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${
                on
                  ? "border-transparent text-foreground"
                  : "border-border text-muted hover:text-foreground"
              }`}
              style={{
                background: on ? `${color}1a` : undefined,
                borderColor: on ? `${color}66` : undefined,
              }}
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ background: on ? color : `${color}55` }}
              />
              {ENTITY_LABEL[t]}
            </button>
          );
        })}
      </div>

      {/* Relation category chips */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[10px] uppercase tracking-wide text-muted">
          İlişki
        </span>
        {ALL_RELATION_CATEGORIES.map((c) => {
          const on = enabledCategories.has(c);
          const color = RELATION_COLOR[c];
          return (
            <button
              key={c}
              onClick={() => toggleCat(c)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${
                on
                  ? "border-transparent text-foreground"
                  : "border-border text-muted hover:text-foreground"
              }`}
              style={{
                background: on ? `${color}1a` : undefined,
                borderColor: on ? `${color}66` : undefined,
              }}
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ background: on ? color : `${color}55` }}
              />
              {RELATION_LABEL[c]}
            </button>
          );
        })}
      </div>

      {/* Search + min-degree slider + counter + reset */}
      <div className="flex flex-wrap items-center gap-3 pt-1">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Entity ara..."
            className="w-full rounded-md border border-border bg-background py-1.5 pl-7 pr-7 text-xs text-foreground placeholder:text-muted focus:border-foreground focus:outline-none"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <label className="inline-flex items-center gap-2 text-xs text-muted">
          Min ilişki
          <input
            type="range"
            min={1}
            max={5}
            value={minDegree}
            onChange={(e) => setMinDegree(Number(e.target.value))}
            className="w-20 accent-primary"
          />
          <span className="w-3 text-center font-mono text-foreground">
            {minDegree}
          </span>
        </label>

        <div className="text-xs text-muted">
          {nodeCount} / {totalNodes} node · {edgeCount} / {totalEdges} ilişki
        </div>

        {isFiltered && (
          <button
            onClick={onReset}
            className="text-xs text-primary hover:underline"
          >
            Sıfırla
          </button>
        )}
      </div>
    </div>
  );
}
