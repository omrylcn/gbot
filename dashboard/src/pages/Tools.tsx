import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Wrench, ChevronDown, ChevronRight, Package } from "lucide-react";
import { adminApi } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";

export default function ToolsPage() {
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["tools"],
    queryFn: adminApi.getTools,
  });

  if (isLoading) return <LoadingSpinner />;
  if (!data) return null;

  const groups = data.groups;
  const toolsByName = new Map(data.tools.map((t) => [t.name, t]));

  return (
    <div>
      <PageHeader title="Tools" description="Tool registry and groups">
        <div className="flex items-center gap-2">
          <Badge variant="primary">{data.available} active</Badge>
          <Badge variant="default">{data.total} total</Badge>
        </div>
      </PageHeader>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {Object.entries(groups).map(([group, toolNames]) => (
          <div key={group} className="border-b border-border last:border-0">
            <button
              onClick={() => setExpandedGroup(expandedGroup === group ? null : group)}
              className="w-full px-5 py-3 flex items-center gap-3 hover:bg-sidebar-active transition-colors"
            >
              {expandedGroup === group ? (
                <ChevronDown className="w-4 h-4 text-muted" />
              ) : (
                <ChevronRight className="w-4 h-4 text-muted" />
              )}
              <Package className="w-4 h-4 text-primary" />
              <span className="text-sm font-medium text-foreground flex-1 text-left">{group}</span>
              <Badge variant="default">{toolNames.length}</Badge>
            </button>

            {expandedGroup === group && (
              <div className="px-5 pb-4">
                <div className="space-y-2">
                  {toolNames.map((name) => {
                    const tool = toolsByName.get(name);
                    return (
                      <div key={name} className="flex items-start gap-3 py-2 px-3 bg-background rounded-lg">
                        <Wrench className="w-4 h-4 text-muted mt-0.5" />
                        <div>
                          <span className="text-sm font-medium text-foreground">{name}</span>
                          {tool?.description && (
                            <p className="text-xs text-muted mt-0.5">{tool.description}</p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
