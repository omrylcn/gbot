import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock, Trash2, ChevronDown, ChevronRight, Play } from "lucide-react";
import { adminApi } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";
import { formatDate, truncate } from "@/lib/utils";

export default function CronsPage() {
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: crons, isLoading } = useQuery({
    queryKey: ["crons"],
    queryFn: adminApi.getCrons,
  });

  const { data: logs } = useQuery({
    queryKey: ["delegation-logs"],
    queryFn: () => adminApi.getLogs(100),
  });

  const deleteMut = useMutation({
    mutationFn: (jobId: string) => adminApi.deleteCron(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["crons"] }),
  });

  if (isLoading) return <LoadingSpinner />;

  return (
    <div>
      <PageHeader title="Scheduled Tasks" description="Cron jobs and execution logs">
        {crons && <Badge variant="primary">{crons.length} tasks</Badge>}
      </PageHeader>

      {crons?.length === 0 ? (
        <div className="bg-card border border-border rounded-xl p-8 text-center">
          <Clock className="w-12 h-12 text-muted mx-auto mb-3" />
          <p className="text-sm text-muted">No scheduled tasks yet</p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          {crons?.map((job) => (
            <div key={job.job_id} className="border-b border-border last:border-0">
              <div className="px-5 py-3 flex items-center gap-3">
                <button
                  onClick={() => setExpandedJob(expandedJob === job.job_id ? null : job.job_id)}
                  className="text-muted hover:text-foreground"
                >
                  {expandedJob === job.job_id ? (
                    <ChevronDown className="w-4 h-4" />
                  ) : (
                    <ChevronRight className="w-4 h-4" />
                  )}
                </button>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <code className="text-xs font-mono text-primary bg-primary/10 px-2 py-0.5 rounded">
                      {job.cron_expr}
                    </code>
                    <Badge variant={job.enabled ? "success" : "default"}>
                      {job.enabled ? "Active" : "Disabled"}
                    </Badge>
                    {job.processor && (
                      <Badge variant="warning">{job.processor}</Badge>
                    )}
                    <span className="text-xs text-muted">@{job.user_id}</span>
                  </div>
                  <p className="text-sm text-foreground">{truncate(job.message, 100)}</p>
                  {job.channel && (
                    <span className="text-xs text-muted">Channel: {job.channel}</span>
                  )}
                </div>

                <button
                  onClick={() => {
                    if (confirm(`Are you sure you want to delete task "${job.job_id}"?`))
                      deleteMut.mutate(job.job_id);
                  }}
                  className="p-2 text-muted hover:text-destructive hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>

              {expandedJob === job.job_id && (
                <div className="px-5 pb-4 pl-12">
                  <div className="bg-background rounded-lg p-4">
                    <h4 className="text-xs font-medium text-muted mb-2">Details</h4>
                    <div className="space-y-1 text-xs">
                      <p><span className="text-muted">Job ID:</span> <span className="font-mono text-foreground">{job.job_id}</span></p>
                      {job.agent_prompt && (
                        <div>
                          <span className="text-muted">Agent Prompt:</span>
                          <pre className="mt-1 text-foreground bg-card p-2 rounded whitespace-pre-wrap">{job.agent_prompt}</pre>
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

      {/* Recent Delegation Logs */}
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
                  <th className="px-4 py-2 text-left text-xs font-medium text-muted">Task</th>
                </tr>
              </thead>
              <tbody>
                {logs.slice(0, 20).map((log) => (
                  <tr key={log.id} className="border-b border-border last:border-0 hover:bg-sidebar-active">
                    <td className="px-4 py-2 text-xs text-muted">{formatDate(log.created_at)}</td>
                    <td className="px-4 py-2 text-xs text-foreground">@{log.user_id}</td>
                    <td className="px-4 py-2">
                      <Badge variant={log.processor_type === "runner" ? "primary" : "warning"}>
                        {log.processor_type}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-xs text-foreground">{truncate(log.task_description, 60)}</td>
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
