import { useQuery } from "@tanstack/react-query";
import {
  Users, MessageSquare, Activity, Clock,
  Cpu, Zap, Database, Bot,
} from "lucide-react";
import { adminApi } from "@/api/admin";
import { StatCard } from "@/components/shared/StatCard";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";
import { formatNumber, formatDate } from "@/lib/utils";

export default function DashboardPage() {
  const { data: stats, isLoading } = useQuery({
    queryKey: ["stats"],
    queryFn: adminApi.getStats,
    refetchInterval: 30000,
  });

  const { data: logs } = useQuery({
    queryKey: ["logs"],
    queryFn: () => adminApi.getLogs(10),
  });

  if (isLoading) return <LoadingSpinner />;
  if (!stats) return null;

  return (
    <div>
      <PageHeader title="Overview" description="System status and statistics" />

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard title="Users" value={formatNumber(stats.data.users)} icon={Users} />
        <StatCard title="Active Sessions" value={formatNumber(stats.sessions.active)} icon={Activity} />
        <StatCard title="Total Messages" value={formatNumber(stats.data.messages)} icon={MessageSquare} />
        <StatCard title="Active Tasks" value={formatNumber(stats.data.recurring_tasks)} icon={Clock} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        {/* System Info */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-medium text-muted mb-4 flex items-center gap-2">
            <Cpu className="w-4 h-4" /> System Info
          </h3>
          <div className="space-y-3">
            <InfoRow label="Version" value={stats.system.version} />
            <InfoRow label="Model" value={stats.system.model} />
            <InfoRow label="Token Limit" value={formatNumber(stats.system.session_token_limit)} />
            <InfoRow label="Thinking" value={stats.system.thinking ? "Active" : "Disabled"} />
          </div>
        </div>

        {/* Tools & Data */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-medium text-muted mb-4 flex items-center gap-2">
            <Database className="w-4 h-4" /> Data Summary
          </h3>
          <div className="space-y-3">
            <InfoRow label="Tools" value={`${stats.tools.available} / ${stats.tools.total}`} />
            <InfoRow label="Total Sessions" value={formatNumber(stats.sessions.total)} />
            <InfoRow label="Total Tokens" value={formatNumber(stats.sessions.total_tokens)} />
            <InfoRow label="Notes" value={formatNumber(stats.data.notes)} />
            <InfoRow label="Pending Tasks" value={formatNumber(stats.data.pending_delayed)} />
          </div>
        </div>
      </div>

      {/* Context Summary */}
      <div className="bg-card border border-border rounded-xl p-5 mb-6">
        <h3 className="text-sm font-medium text-muted mb-4 flex items-center gap-2">
          <Zap className="w-4 h-4" /> Context Layers
        </h3>
        <div className="flex flex-wrap gap-2">
          {stats.context.layers.map((l) => (
            <div key={l.layer} className="flex items-center gap-1.5 bg-background rounded-lg px-3 py-1.5 text-sm">
              <span className="text-foreground font-medium">{l.layer}</span>
              <Badge variant="default">{l.tokens} tok</Badge>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted mt-3">
          Total: {formatNumber(stats.context.total_chars)} chars, ~{formatNumber(stats.context.total_tokens)} tokens
        </p>
      </div>

      {/* Recent Delegation Logs */}
      {logs && logs.length > 0 && (
        <div className="bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-medium text-muted mb-4 flex items-center gap-2">
            <Bot className="w-4 h-4" /> Recent Delegation Logs
          </h3>
          <div className="space-y-2">
            {logs.map((log) => (
              <div key={log.id} className="flex items-start gap-3 py-2 border-b border-border last:border-0">
                <Badge variant={log.processor_type === "runner" ? "primary" : "warning"}>
                  {log.processor_type || log.status}
                </Badge>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground truncate">{log.result || log.task_description || ""}</p>
                  <p className="text-xs text-muted">{formatDate(log.executed_at || log.created_at || "")}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm font-medium text-card-foreground">{value}</span>
    </div>
  );
}
