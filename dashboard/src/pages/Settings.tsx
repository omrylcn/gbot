import { useQuery } from "@tanstack/react-query";
import { Settings as SettingsIcon, Bot, FileText } from "lucide-react";
import { adminApi } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";
import { useState } from "react";
import { cn } from "@/lib/utils";

export default function SettingsPage() {
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null);

  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ["config"],
    queryFn: adminApi.getConfig,
  });

  const { data: profiles, isLoading: profilesLoading } = useQuery({
    queryKey: ["profiles"],
    queryFn: adminApi.getProfiles,
  });

  const { data: profileDetail } = useQuery({
    queryKey: ["profile-detail", selectedProfile],
    queryFn: () => adminApi.getProfileDetail(selectedProfile!),
    enabled: !!selectedProfile,
  });

  if (configLoading) return <LoadingSpinner />;

  return (
    <div>
      <PageHeader title="Settings" description="System configuration and agent profiles" />

      {/* Config */}
      <div className="bg-card border border-border rounded-xl p-5 mb-6">
        <h3 className="text-sm font-medium text-muted mb-4 flex items-center gap-2">
          <SettingsIcon className="w-4 h-4" /> Configuration
        </h3>
        {config && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <ConfigItem label="Model" value={config.model} />
            <ConfigItem label="Temperature" value={String(config.temperature)} />
            <ConfigItem label="Token Limit" value={String(config.session_token_limit)} />
            <ConfigItem label="Max Iterations" value={String(config.max_iterations)} />
            <ConfigItem label="Auth" value={config.auth_enabled ? "Active" : "Disabled"} />
            <ConfigItem label="Cron" value={config.cron_enabled ? "Active" : "Disabled"} />
            <ConfigItem label="Heartbeat" value={config.heartbeat_enabled ? "Active" : "Disabled"} />
            <ConfigItem label="Database" value={config.db_path} />
          </div>
        )}
      </div>

      {/* Profiles */}
      <div className="bg-card border border-border rounded-xl p-5">
        <h3 className="text-sm font-medium text-muted mb-4 flex items-center gap-2">
          <Bot className="w-4 h-4" /> Agent Profiles
        </h3>
        {profilesLoading ? (
          <LoadingSpinner />
        ) : (
          <div className="space-y-2">
            {profiles?.map((p) => (
              <div key={p.name} className="border border-border rounded-lg overflow-hidden">
                <button
                  onClick={() => setSelectedProfile(selectedProfile === p.name ? null : p.name)}
                  className={cn(
                    "w-full px-4 py-3 flex items-center gap-3 hover:bg-sidebar-active transition-colors",
                    selectedProfile === p.name && "bg-sidebar-active"
                  )}
                >
                  <FileText className="w-4 h-4 text-primary" />
                  <span className="text-sm font-medium text-foreground flex-1 text-left">{p.name}</span>
                  <Badge variant={p.has_agent_md ? "success" : "default"}>
                    {p.has_agent_md ? "AGENT.md" : "None"}
                  </Badge>
                  {p.skills.length > 0 && (
                    <Badge variant="primary">{p.skills[0] === "*" ? "All Skills" : `${p.skills.length} skills`}</Badge>
                  )}
                </button>

                {selectedProfile === p.name && profileDetail && (
                  <div className="px-4 pb-4 border-t border-border">
                    <div className="mt-3 space-y-2 text-xs">
                      <p><span className="text-muted">Agent MD:</span> <code className="text-foreground">{profileDetail.agent_md}</code></p>
                      {profileDetail.template_vars.length > 0 && (
                        <p>
                          <span className="text-muted">Template Vars:</span>{" "}
                          {profileDetail.template_vars.map((v) => (
                            <Badge key={v} variant="default" className="mr-1">{v}</Badge>
                          ))}
                        </p>
                      )}
                    </div>
                    {profileDetail.agent_md_content && (
                      <details className="mt-3">
                        <summary className="text-xs text-muted cursor-pointer hover:text-foreground">
                          AGENT.md Content
                        </summary>
                        <pre className="mt-2 text-xs text-foreground bg-background rounded-lg p-4 overflow-x-auto max-h-64 whitespace-pre-wrap">
                          {profileDetail.agent_md_content}
                        </pre>
                      </details>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ConfigItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-2 px-3 bg-background rounded-lg">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm font-medium text-card-foreground">{value}</span>
    </div>
  );
}
