import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock, Trash2, ChevronDown, ChevronRight, Play } from "lucide-react";
import { adminApi } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";
import { formatDate, truncate } from "@/lib/utils";

type Filter = "active" | "all" | "completed";

export default function TasksPage() {
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("active");
  const queryClient = useQueryClient();

  const { data: tasks, isLoading } = useQuery({
    queryKey: ["tasks"],
    queryFn: adminApi.getTasks,
  });

  const { data: logs } = useQuery({
    queryKey: ["execution-logs"],
    queryFn: () => adminApi.getLogs(100),
  });

  const deleteMut = useMutation({
    mutationFn: (taskId: string) => adminApi.deleteTask(taskId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tasks"] }),
  });

  if (isLoading) return <LoadingSpinner />;

  const isActive = (t: { status: string; enabled: number; execution_type: string }) =>
    (t.execution_type in { recurring: 1, monitor: 1 } && t.enabled && t.status === "pending") ||
    (t.execution_type === "delayed" && t.status === "pending") ||
    t.status === "running";

  const filteredTasks = tasks?.filter((t) => {
    if (filter === "active") return isActive(t);
    if (filter === "completed") return !isActive(t);
    return true;
  });

  const activeCount = tasks?.filter(isActive).length || 0;

  const execTypeBadge = (type: string) => {
    const variants: Record<string, "primary" | "success" | "warning" | "default"> = {
      recurring: "primary",
      monitor: "warning",
      delayed: "success",
      immediate: "default",
    };
    return <Badge variant={variants[type] || "default"}>{type}</Badge>;
  };

  const statusBadge = (status: string, enabled: number) => {
    if (status === "paused" || !enabled) return <Badge variant="default">paused</Badge>;
    if (status === "sent" || status === "completed") return <Badge variant="success">{status}</Badge>;
    if (status === "failed") return <Badge variant="warning">failed</Badge>;
    if (status === "running") return <Badge variant="primary">running</Badge>;
    return <Badge variant="success">active</Badge>;
  };

  return (
    <div>
      <PageHeader title="Background Tasks" description="All scheduled, delayed, and background tasks">
        {tasks && <Badge variant="primary">{activeCount} active / {tasks.length} total</Badge>}
      </PageHeader>

      <div className="flex gap-2 mb-4">
        {(["active", "all", "completed"] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
              filter === f
                ? "bg-primary text-white"
                : "bg-card border border-border text-muted hover:text-foreground"
            }`}
          >
            {f === "active" ? `Active (${activeCount})` : f === "all" ? `All (${tasks?.length || 0})` : `Completed (${(tasks?.length || 0) - activeCount})`}
          </button>
        ))}
      </div>

      {filteredTasks?.length === 0 ? (
        <div className="bg-card border border-border rounded-xl p-8 text-center">
          <Clock className="w-12 h-12 text-muted mx-auto mb-3" />
          <p className="text-sm text-muted">No tasks yet</p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          {filteredTasks?.map((task) => (
            <div key={task.task_id} className="border-b border-border last:border-0">
              <div className="px-5 py-3 flex items-center gap-3">
                <button
                  onClick={() => setExpandedTask(expandedTask === task.task_id ? null : task.task_id)}
                  className="text-muted hover:text-foreground"
                >
                  {expandedTask === task.task_id ? (
                    <ChevronDown className="w-4 h-4" />
                  ) : (
                    <ChevronRight className="w-4 h-4" />
                  )}
                </button>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    {execTypeBadge(task.execution_type)}
                    {task.cron_expr && (
                      <code className="text-xs font-mono text-primary bg-primary/10 px-2 py-0.5 rounded">
                        {task.cron_expr}
                      </code>
                    )}
                    {statusBadge(task.status, task.enabled)}
                    {task.processor && (
                      <Badge variant="default">{task.processor}</Badge>
                    )}
                    <span className="text-xs text-muted">@{task.user_id}</span>
                  </div>
                  <p className="text-sm text-foreground">{truncate(task.message, 100)}</p>
                  <div className="flex gap-3 text-xs text-muted mt-1">
                    <span>Channel: {task.channel}</span>
                    {task.run_at && <span>Run at: {formatDate(task.run_at)}</span>}
                    <span>Created: {formatDate(task.created_at)}</span>
                  </div>
                </div>

                <button
                  onClick={() => {
                    if (confirm(`Cancel task "${task.task_id}"?`))
                      deleteMut.mutate(task.task_id);
                  }}
                  className="p-2 text-muted hover:text-destructive hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>

              {expandedTask === task.task_id && (
                <div className="px-5 pb-4 pl-12">
                  <div className="bg-background rounded-lg p-4">
                    <h4 className="text-xs font-medium text-muted mb-2">Details</h4>
                    <div className="space-y-1 text-xs">
                      <p><span className="text-muted">Task ID:</span> <span className="font-mono text-foreground">{task.task_id}</span></p>
                      <p><span className="text-muted">Execution:</span> {task.execution_type} / {task.processor}</p>
                      <p><span className="text-muted">Status:</span> {task.status}</p>
                      {task.agent_prompt && (
                        <div>
                          <span className="text-muted">Agent Prompt:</span>
                          <pre className="mt-1 text-foreground bg-card p-2 rounded whitespace-pre-wrap">{task.agent_prompt}</pre>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Recent Execution Logs */}
      {logs && logs.length > 0 && (
        <div className="mt-6">
          <h3 className="text-lg font-semibold text-foreground mb-3 flex items-center gap-2">
            <Play className="w-5 h-5" /> Recent Execution Logs
          </h3>
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border bg-background/50">
                  <th className="px-4 py-2 text-left text-xs font-medium text-muted">Date</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-muted">User</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-muted">Type</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-muted">Status</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-muted">Result</th>
                </tr>
              </thead>
              <tbody>
                {logs.slice(0, 20).map((log) => (
                  <tr key={log.id} className="border-b border-border last:border-0 hover:bg-sidebar-active">
                    <td className="px-4 py-2 text-xs text-muted">{formatDate(log.executed_at || log.created_at || "")}</td>
                    <td className="px-4 py-2 text-xs text-foreground">@{log.user_id}</td>
                    <td className="px-4 py-2">
                      <Badge variant="default">
                        {log.processor_type || log.execution_type || "—"}
                      </Badge>
                    </td>
                    <td className="px-4 py-2">
                      <Badge variant={log.status === "success" ? "success" : log.status === "error" ? "warning" : "default"}>
                        {log.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-xs text-foreground">{truncate(log.result || "", 60)}</td>
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
