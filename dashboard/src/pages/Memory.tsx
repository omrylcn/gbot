import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EyeOff, RotateCcw, Play, FileDown } from "lucide-react";

import { adminApi, type MemoryFact } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";
import { RelationsGraph } from "@/components/RelationsGraph";
import { formatDate } from "@/lib/utils";

const FACT_TYPES = [
  "all", "semantic", "episodic", "preference", "procedural", "style",
] as const;
type FactFilter = (typeof FACT_TYPES)[number];

const TABS = ["facts", "relations", "pages", "debug", "ops"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABEL: Record<Tab, string> = {
  facts: "Facts",
  relations: "Relations",
  pages: "Entity Pages",
  debug: "Retrieval Debug",
  ops: "Ops",
};

export default function MemoryPage() {
  const [userId, setUserId] = useState("owner");
  const [tab, setTab] = useState<Tab>("facts");
  const [filter, setFilter] = useState<FactFilter>("all");

  const { data: users } = useQuery({
    queryKey: ["users"],
    queryFn: adminApi.getUsers,
  });

  const { data: memory, isLoading } = useQuery({
    queryKey: ["memory", userId],
    queryFn: () => adminApi.getMemory(userId),
    enabled: !!userId,
  });

  return (
    <div>
      <PageHeader title="Memory" description="User memory: notes, facts, relations, entity pages" />

      <div className="flex items-center gap-4 mb-6">
        <select
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          className="px-3 py-2 text-sm bg-background border border-border rounded-lg text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          {users?.map((u) => (
            <option key={u.user_id} value={u.user_id}>@{u.user_id}</option>
          ))}
        </select>
        {memory?.fact_stats && (
          <div className="flex gap-3 text-xs text-muted">
            <span>{memory.fact_stats.total} facts</span>
            {Object.entries(memory.fact_stats.by_type).map(([type, stats]) => (
              <span key={type}>{type}: {stats.count}</span>
            ))}
          </div>
        )}
      </div>

      {isLoading && <LoadingSpinner />}

      {memory && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left: explicit data + processing log (same on every tab) */}
          <ExplicitPanel memory={memory} />

          {/* Right: tab content */}
          <div className="lg:col-span-2">
            <div className="flex gap-2 mb-4 border-b border-border">
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`px-3 py-2 text-sm border-b-2 transition-colors ${
                    tab === t
                      ? "border-primary text-foreground font-medium"
                      : "border-transparent text-muted hover:text-foreground"
                  }`}
                >
                  {TAB_LABEL[t]}
                </button>
              ))}
            </div>

            {tab === "facts" && (
              <FactsTab userId={userId} memory={memory} filter={filter} setFilter={setFilter} />
            )}
            {tab === "relations" && <RelationsTab userId={userId} />}
            {tab === "pages" && <EntityPagesTab userId={userId} />}
            {tab === "debug" && <RetrievalDebugTab userId={userId} />}
            {tab === "ops" && <OpsTab userId={userId} />}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Left panel ─────────────────────────────────────────────────

function ExplicitPanel({ memory }: { memory: NonNullable<ReturnType<typeof adminApi.getMemory>> extends Promise<infer T> ? T : never }) {
  return (
    <div className="space-y-4">
      <div className="bg-card border border-border rounded-xl p-4">
        <h3 className="text-sm font-medium text-foreground mb-3">User Notes ({memory.notes?.length ?? 0})</h3>
        <div className="space-y-1.5 max-h-48 overflow-y-auto">
          {memory.notes?.map((note, i) => (
            <p key={i} className="text-xs text-muted">• {note}</p>
          ))}
          {!memory.notes?.length && <p className="text-xs text-muted italic">No notes</p>}
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-4">
        <h3 className="text-sm font-medium text-foreground mb-3">Preferences</h3>
        <div className="space-y-1.5">
          {Object.entries(memory.preferences ?? {}).map(([k, v]) => (
            <div key={k} className="flex justify-between text-xs">
              <span className="text-muted">{k}</span>
              <span className="text-foreground font-medium">{String(v)}</span>
            </div>
          ))}
          {!Object.keys(memory.preferences ?? {}).length && (
            <p className="text-xs text-muted italic">No preferences</p>
          )}
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-4">
        <h3 className="text-sm font-medium text-foreground mb-3">Favorites ({memory.favorites?.length ?? 0})</h3>
        <div className="space-y-1.5">
          {memory.favorites?.map((f, i) => (
            <p key={i} className="text-xs text-muted">• {f.item_title}</p>
          ))}
          {!memory.favorites?.length && <p className="text-xs text-muted italic">No favorites</p>}
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-4">
        <h3 className="text-sm font-medium text-foreground mb-3">Processing Log</h3>
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {memory.processing_log?.map((log) => (
            <div key={log.id} className="text-xs border-b border-border pb-2 last:border-0">
              <div className="flex justify-between">
                <span className="text-muted">{log.trigger}</span>
                <span className="text-muted">{formatDate(log.processed_at)}</span>
              </div>
              <span className="text-foreground">
                +{log.facts_added}/{log.facts_extracted} facts, {log.duration_ms}ms
              </span>
            </div>
          ))}
          {!memory.processing_log?.length && (
            <p className="text-xs text-muted italic">No processing yet</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Facts tab ──────────────────────────────────────────────────

const STATE_FILTERS = ["all", "active", "weak", "inhibited"] as const;
type StateFilter = (typeof STATE_FILTERS)[number];

const STATE_COLORS: Record<string, string> = {
  active: "bg-green-500/15 text-green-500 border-green-500/30",
  weak: "bg-yellow-500/15 text-yellow-500 border-yellow-500/30",
  inhibited: "bg-orange-500/15 text-orange-500 border-orange-500/30",
  archived: "bg-muted/15 text-muted border-border",
};

function FactsTab({
  userId,
  memory,
  filter,
  setFilter,
}: {
  userId: string;
  memory: { facts: MemoryFact[]; fact_stats: { by_type: Record<string, { count: number }> } };
  filter: FactFilter;
  setFilter: (f: FactFilter) => void;
}) {
  const queryClient = useQueryClient();
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [error, setError] = useState<string | null>(null);

  const inhibitMut = useMutation({
    mutationFn: (factId: string) => adminApi.inhibitFact(userId, factId, 7),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memory", userId] }),
    onError: (e: Error) => setError(e.message),
  });

  const restoreMut = useMutation({
    mutationFn: (factId: string) => adminApi.restoreFact(userId, factId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memory", userId] }),
    onError: (e: Error) => setError(e.message),
  });

  const filteredFacts = memory.facts?.filter((f) => {
    if (filter !== "all" && f.fact_type !== filter) return false;
    if (stateFilter !== "all" && f.state !== stateFilter) return false;
    return true;
  }) ?? [];
  const validFacts = filteredFacts.filter((f) => !f.valid_until);
  const invalidFacts = filteredFacts.filter((f) => f.valid_until);

  return (
    <div>
      {error && (
        <div className="mb-3 px-3 py-2 bg-red-500/10 border border-red-500/30 text-red-500 rounded-lg text-xs">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-3">
        {FACT_TYPES.map((type) => (
          <button
            key={type}
            onClick={() => setFilter(type)}
            className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
              filter === type
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-background text-muted border-border hover:text-foreground"
            }`}
          >
            {type === "all" ? "All types" : type}
            {type !== "all" && memory.fact_stats?.by_type[type] && (
              <span className="ml-1 opacity-70">({memory.fact_stats.by_type[type].count})</span>
            )}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        {STATE_FILTERS.map((s) => (
          <button
            key={s}
            onClick={() => setStateFilter(s)}
            className={`px-3 py-1 text-xs rounded-lg border transition-colors ${
              stateFilter === s
                ? "bg-foreground text-background border-foreground"
                : "bg-background text-muted border-border hover:text-foreground"
            }`}
          >
            {s === "all" ? "Any state" : s}
          </button>
        ))}
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-background/50">
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase">Fact</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-20">Type</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-24">State</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-16">Conf.</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-24">Date</th>
              <th className="px-4 py-2.5 text-right text-xs font-medium text-muted uppercase w-24">Actions</th>
            </tr>
          </thead>
          <tbody>
            {validFacts.map((f) => {
              const state = f.state || "active";
              const isInhibited = state === "inhibited";
              return (
                <tr key={f.fact_id} className="border-b border-border last:border-0 hover:bg-sidebar-active transition-colors">
                  <td className="px-4 py-2.5 text-sm text-foreground">
                    {f.content}
                    {f.category && <span className="ml-2 text-xs text-muted">[{f.category}]</span>}
                  </td>
                  <td className="px-4 py-2.5"><Badge>{f.fact_type}</Badge></td>
                  <td className="px-4 py-2.5">
                    <span className={`px-2 py-0.5 text-[10px] uppercase rounded border ${STATE_COLORS[state] || STATE_COLORS.archived}`}>
                      {state}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted">{(f.confidence * 100).toFixed(0)}%</td>
                  <td className="px-4 py-2.5 text-xs text-muted">{formatDate(f.created_at)}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex justify-end gap-1">
                      {isInhibited ? (
                        <button
                          onClick={() => restoreMut.mutate(f.fact_id)}
                          disabled={restoreMut.isPending}
                          className="p-1.5 text-muted hover:text-green-500 hover:bg-sidebar-active rounded transition-colors"
                          title="Restore (INHIBITED → ACTIVE)"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                        </button>
                      ) : (
                        <button
                          onClick={() => {
                            if (confirm(`Inhibit this fact for 7 days?\n\n"${f.content}"`)) {
                              inhibitMut.mutate(f.fact_id);
                            }
                          }}
                          disabled={inhibitMut.isPending}
                          className="p-1.5 text-muted hover:text-orange-500 hover:bg-sidebar-active rounded transition-colors"
                          title="Inhibit for 7 days"
                        >
                          <EyeOff className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {!validFacts.length && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-sm text-muted italic">
                  No facts match these filters
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {invalidFacts.length > 0 && (
        <div className="mt-4">
          <h4 className="text-xs font-medium text-muted mb-2">Superseded / Archived ({invalidFacts.length})</h4>
          <div className="bg-card border border-border rounded-xl overflow-hidden opacity-60">
            <table className="w-full">
              <tbody>
                {invalidFacts.map((f) => (
                  <tr key={f.fact_id} className="border-b border-border last:border-0">
                    <td className="px-4 py-2 text-xs text-muted line-through">{f.content}</td>
                    <td className="px-4 py-2 text-xs text-muted w-28">{formatDate(f.valid_until!)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Relations tab ──────────────────────────────────────────────

function RelationsTab({ userId }: { userId: string }) {
  const [entity, setEntity] = useState<string>("");
  const [view, setView] = useState<"table" | "graph">("table");

  const { data: entities } = useQuery({
    queryKey: ["memory-entities", userId],
    queryFn: () => adminApi.getMemoryEntities(userId),
  });

  const { data: relations } = useQuery({
    queryKey: ["memory-relations", userId, entity],
    queryFn: () => adminApi.getMemoryRelations(userId, entity || undefined),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-2">
        <span className="text-xs text-muted">View:</span>
        {(["table", "graph"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`px-3 py-1 text-xs rounded-lg border ${
              view === v
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-background text-muted border-border hover:text-foreground"
            }`}
          >
            {v === "table" ? "Table" : "Graph"}
          </button>
        ))}
      </div>

      {view === "graph" && <RelationsGraph userId={userId} />}

      {view === "table" && (
      <div className="space-y-4">
      <div className="bg-card border border-border rounded-xl p-4">
        <h3 className="text-sm font-medium text-foreground mb-3">
          Canonical Entities ({entities?.count ?? 0})
        </h3>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setEntity("")}
            className={`px-3 py-1 text-xs rounded-lg border ${
              entity === ""
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-background text-muted border-border hover:text-foreground"
            }`}
          >
            All
          </button>
          {entities?.entities?.slice(0, 30).map((e) => (
            <button
              key={e.canonical}
              onClick={() => setEntity(e.canonical)}
              className={`px-3 py-1 text-xs rounded-lg border ${
                entity === e.canonical
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-background text-muted border-border hover:text-foreground"
              }`}
            >
              {e.canonical} <span className="opacity-60">({e.relation_count})</span>
            </button>
          ))}
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-background/50">
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase">Source</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-28">Relation</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase">Target</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-20">Conf.</th>
            </tr>
          </thead>
          <tbody>
            {relations?.relations?.map((r) => (
              <tr key={r.relation_id} className="border-b border-border last:border-0 hover:bg-sidebar-active transition-colors">
                <td className="px-4 py-2.5 text-sm text-foreground">
                  {r.canonical_source || r.source_entity}
                  {r.canonical_source && r.canonical_source !== r.source_entity && (
                    <span className="ml-2 text-xs text-muted">(raw: {r.source_entity})</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-xs"><Badge>{r.relation}</Badge></td>
                <td className="px-4 py-2.5 text-sm text-foreground">
                  {r.canonical_target || r.target_entity}
                  {r.canonical_target && r.canonical_target !== r.target_entity && (
                    <span className="ml-2 text-xs text-muted">(raw: {r.target_entity})</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-xs text-muted">{(r.confidence * 100).toFixed(0)}%</td>
              </tr>
            ))}
            {!relations?.relations?.length && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-muted italic">
                  No relations
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      </div>
      )}
    </div>
  );
}

// ── Entity Pages tab ───────────────────────────────────────────

function EntityPagesTab({ userId }: { userId: string }) {
  const queryClient = useQueryClient();
  const { data: pages } = useQuery({
    queryKey: ["entity-pages", userId],
    queryFn: () => adminApi.getMemoryEntityPages(userId),
  });

  const recompile = useMutation({
    mutationFn: (entity: string) => adminApi.recompileEntityPage(userId, entity),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["entity-pages", userId] });
    },
  });

  const forget = useMutation({
    mutationFn: (entity: string) => adminApi.forgetEntity(userId, entity),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["entity-pages", userId] });
      queryClient.invalidateQueries({ queryKey: ["memory", userId] });
      queryClient.invalidateQueries({ queryKey: ["memory-entities", userId] });
      queryClient.invalidateQueries({ queryKey: ["memory-relations", userId] });
    },
  });

  return (
    <div className="space-y-4">
      <div className="text-xs text-muted">
        {pages?.count ?? 0} pages. Disabled by default — set
        <code className="px-1 mx-1 bg-background rounded text-foreground">memory.entity_pages.enabled: true</code>
        in config to populate via the agent's extraction pipeline.
      </div>

      {pages?.pages?.map((p) => {
        let factIds: string[] = [];
        try {
          factIds = JSON.parse(p.source_fact_ids || "[]");
        } catch {
          /* ignore */
        }
        return (
          <div key={p.page_id} className="bg-card border border-border rounded-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-foreground">{p.entity_canonical}</h3>
                <Badge>v{p.version}</Badge>
                {p.stale === 1 && (
                  <span className="text-xs px-2 py-0.5 rounded bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
                    stale
                  </span>
                )}
                <span className="text-xs text-muted">
                  {p.fact_count} facts · {p.relation_count} rels · {p.access_count} reads
                </span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => recompile.mutate(p.entity_canonical)}
                  disabled={recompile.isPending}
                  className="px-2 py-1 text-xs rounded border border-border hover:bg-sidebar-active text-foreground disabled:opacity-50"
                >
                  {recompile.isPending && recompile.variables === p.entity_canonical
                    ? "Recompiling…"
                    : "Recompile"}
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Forget "${p.entity_canonical}" entirely? This archives every related fact, relation, and the page.`)) {
                      forget.mutate(p.entity_canonical);
                    }
                  }}
                  className="px-2 py-1 text-xs rounded border border-red-500/50 text-red-600 dark:text-red-400 hover:bg-red-500/10"
                >
                  Forget
                </button>
              </div>
            </div>
            <pre className="text-xs text-foreground whitespace-pre-wrap font-mono bg-background border border-border rounded-lg p-3 overflow-x-auto">
              {p.content_md}
            </pre>
            <div className="mt-2 text-xs text-muted">
              compiled {formatDate(p.last_compiled_at)} · {factIds.length} source facts
            </div>
          </div>
        );
      })}

      {!pages?.pages?.length && (
        <div className="bg-card border border-border rounded-xl p-8 text-center">
          <p className="text-sm text-muted italic">No entity pages yet.</p>
          <p className="text-xs text-muted mt-2">
            Enable in config and let the extraction pipeline run; pages compile on a 60s debounce.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Retrieval Debug tab ────────────────────────────────────────

function RetrievalDebugTab({ userId }: { userId: string }) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");

  const { data, isFetching } = useQuery({
    queryKey: ["retrieval-debug", userId, submitted],
    queryFn: () => adminApi.retrievalDebug(userId, submitted, 15),
    enabled: !!submitted,
  });

  return (
    <div className="space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(query.trim());
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Type a query to embed and inspect distance scores…"
          className="flex-1 px-3 py-2 text-sm bg-background border border-border rounded-lg text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
        />
        <button
          type="submit"
          disabled={!query.trim() || isFetching}
          className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg disabled:opacity-50"
        >
          {isFetching ? "Searching…" : "Search"}
        </button>
      </form>

      {data && (
        <>
          <div className="text-xs text-muted">
            Gate: <code className="bg-background px-1 rounded">max_distance = {data.max_distance_gate ?? "off"}</code>
            · {data.count} candidates returned
          </div>
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border bg-background/50">
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-20">Distance</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase">Fact</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-24">Type</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-16">Acc.</th>
                </tr>
              </thead>
              <tbody>
                {data.candidates.map((c) => (
                  <tr
                    key={c.fact_id}
                    className={`border-b border-border last:border-0 ${
                      c.above_gate ? "opacity-50 bg-red-500/5" : "hover:bg-sidebar-active"
                    } transition-colors`}
                  >
                    <td className="px-4 py-2.5 text-xs font-mono">
                      {c.distance.toFixed(3)}
                      {c.above_gate && <span className="ml-1 text-red-500">↑</span>}
                    </td>
                    <td className="px-4 py-2.5 text-sm text-foreground">{c.content}</td>
                    <td className="px-4 py-2.5"><Badge>{c.fact_type}</Badge></td>
                    <td className="px-4 py-2.5 text-xs text-muted">{c.access_count}</td>
                  </tr>
                ))}
                {!data.candidates.length && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-sm text-muted italic">
                      No candidates within reach
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!submitted && (
        <div className="text-xs text-muted italic">
          Tip: try a query that's been giving the agent trouble — you'll see exactly which facts are within the gate and which got filtered.
        </div>
      )}
    </div>
  );
}

// ── Ops tab ────────────────────────────────────────────────────

function OpsTab({ userId }: { userId: string }) {
  const queryClient = useQueryClient();
  const [maintResult, setMaintResult] = useState<unknown>(null);
  const [syncResult, setSyncResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  const maintMut = useMutation({
    mutationFn: () => adminApi.runMemoryMaintenance(userId),
    onSuccess: (r) => {
      setMaintResult(r);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["memory", userId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const syncMut = useMutation({
    mutationFn: () => adminApi.runObsidianSync(userId),
    onSuccess: (r) => {
      setSyncResult(r);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const { data: tasks } = useQuery({
    queryKey: ["tasks"],
    queryFn: adminApi.getTasks,
  });

  const userTasks = tasks?.filter(
    (t) =>
      t.user_id === userId &&
      (t.processor === "memory_maintenance" || t.processor === "memory_obsidian_sync"),
  ) ?? [];

  return (
    <div className="space-y-5">
      {error && (
        <div className="px-3 py-2 bg-red-500/10 border border-red-500/30 text-red-500 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Maintenance */}
      <section className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-medium text-foreground">Memory Maintenance</h3>
            <p className="text-xs text-muted mt-1">
              Type-aware decay, stale-page recompile, orphan cleanup.
              Cron: daily 04:00, weekly Sunday 04:30.
            </p>
          </div>
          <button
            onClick={() => maintMut.mutate()}
            disabled={maintMut.isPending}
            className="inline-flex items-center gap-2 px-3 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50"
          >
            <Play className="w-4 h-4" />
            {maintMut.isPending ? "Running…" : "Run Now"}
          </button>
        </div>

        {maintResult ? (
          <pre className="text-xs text-foreground bg-background border border-border rounded-lg p-3 overflow-x-auto">
            {JSON.stringify(maintResult, null, 2)}
          </pre>
        ) : (
          <p className="text-xs text-muted italic">Last run output will appear here.</p>
        )}
      </section>

      {/* Obsidian sync */}
      <section className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-medium text-foreground">Obsidian Sync</h3>
            <p className="text-xs text-muted mt-1">
              Exports entity pages to <code className="text-foreground">vault_path/&lt;user&gt;/&lt;entity&gt;.md</code>.
              Cron: hourly. Disabled syncs report enabled=false.
            </p>
          </div>
          <button
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="inline-flex items-center gap-2 px-3 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50"
          >
            <FileDown className="w-4 h-4" />
            {syncMut.isPending ? "Syncing…" : "Sync Now"}
          </button>
        </div>

        {syncResult ? (
          <pre className="text-xs text-foreground bg-background border border-border rounded-lg p-3 overflow-x-auto">
            {JSON.stringify(syncResult, null, 2)}
          </pre>
        ) : (
          <p className="text-xs text-muted italic">Last sync output will appear here.</p>
        )}
      </section>

      {/* Scheduled tasks */}
      <section className="bg-card border border-border rounded-xl p-5">
        <h3 className="text-sm font-medium text-foreground mb-3">Scheduled Memory Tasks</h3>
        {userTasks.length ? (
          <div className="overflow-hidden border border-border rounded-lg">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border bg-background/50">
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted uppercase">Task</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted uppercase">Cron</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted uppercase">Processor</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted uppercase">Status</th>
                </tr>
              </thead>
              <tbody>
                {userTasks.map((t) => (
                  <tr key={t.task_id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 text-xs font-mono text-foreground">{t.task_id}</td>
                    <td className="px-3 py-2 text-xs font-mono text-muted">{t.cron_expr || "—"}</td>
                    <td className="px-3 py-2 text-xs text-muted">{t.processor}</td>
                    <td className="px-3 py-2 text-xs"><Badge>{t.status}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-muted italic">
            No memory tasks scheduled for @{userId}. They register on first server start with the user.
          </p>
        )}
      </section>
    </div>
  );
}
