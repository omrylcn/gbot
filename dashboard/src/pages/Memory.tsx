import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { adminApi, type MemoryFact } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";
import { formatDate } from "@/lib/utils";

const FACT_TYPES = ["all", "semantic", "episodic", "preference", "procedural"] as const;
type FactFilter = (typeof FACT_TYPES)[number];

export default function MemoryPage() {
  const [userId, setUserId] = useState("owner");
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

  const filteredFacts = memory?.facts?.filter(
    (f: MemoryFact) => filter === "all" || f.fact_type === filter
  ) ?? [];

  const validFacts = filteredFacts.filter((f: MemoryFact) => !f.valid_until);
  const invalidFacts = filteredFacts.filter((f: MemoryFact) => f.valid_until);

  return (
    <div>
      <PageHeader title="Memory" description="User memory: explicit notes + learned facts" />

      {/* User selector */}
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
          {/* Left: Explicit data */}
          <div className="space-y-4">
            {/* Notes */}
            <div className="bg-card border border-border rounded-xl p-4">
              <h3 className="text-sm font-medium text-foreground mb-3">User Notes ({memory.notes?.length ?? 0})</h3>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {memory.notes?.map((note, i) => (
                  <p key={i} className="text-xs text-muted">• {note}</p>
                ))}
                {!memory.notes?.length && <p className="text-xs text-muted italic">No notes</p>}
              </div>
            </div>

            {/* Preferences */}
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

            {/* Favorites */}
            <div className="bg-card border border-border rounded-xl p-4">
              <h3 className="text-sm font-medium text-foreground mb-3">Favorites ({memory.favorites?.length ?? 0})</h3>
              <div className="space-y-1.5">
                {memory.favorites?.map((f, i) => (
                  <p key={i} className="text-xs text-muted">• {f.item_title}</p>
                ))}
                {!memory.favorites?.length && <p className="text-xs text-muted italic">No favorites</p>}
              </div>
            </div>

            {/* Processing Log */}
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

          {/* Right: Memory Facts (2 columns wide) */}
          <div className="lg:col-span-2">
            {/* Filter tabs */}
            <div className="flex gap-2 mb-4">
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
                  {type === "all" ? "All" : type}
                  {type !== "all" && memory.fact_stats?.by_type[type] && (
                    <span className="ml-1 opacity-70">({memory.fact_stats.by_type[type].count})</span>
                  )}
                </button>
              ))}
            </div>

            {/* Facts table */}
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border bg-background/50">
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase">Fact</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-24">Type</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-20">Conf.</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-24">Category</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase w-28">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {validFacts.map((f: MemoryFact) => (
                    <tr key={f.fact_id} className="border-b border-border last:border-0 hover:bg-sidebar-active transition-colors">
                      <td className="px-4 py-2.5 text-sm text-foreground">{f.content}</td>
                      <td className="px-4 py-2.5"><Badge>{f.fact_type}</Badge></td>
                      <td className="px-4 py-2.5 text-xs text-muted">{(f.confidence * 100).toFixed(0)}%</td>
                      <td className="px-4 py-2.5 text-xs text-muted">{f.category || "—"}</td>
                      <td className="px-4 py-2.5 text-xs text-muted">{formatDate(f.created_at)}</td>
                    </tr>
                  ))}
                  {!validFacts.length && (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-sm text-muted italic">
                        No facts yet
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Invalidated facts */}
            {invalidFacts.length > 0 && (
              <div className="mt-4">
                <h4 className="text-xs font-medium text-muted mb-2">Superseded ({invalidFacts.length})</h4>
                <div className="bg-card border border-border rounded-xl overflow-hidden opacity-60">
                  <table className="w-full">
                    <tbody>
                      {invalidFacts.map((f: MemoryFact) => (
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
        </div>
      )}
    </div>
  );
}
