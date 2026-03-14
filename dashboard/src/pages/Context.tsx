import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, Edit3, X, ChevronDown, ChevronRight } from "lucide-react";
import { adminApi } from "@/api/admin";
import type { ContextLayer } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge } from "@/components/shared/Badge";
import { cn, formatNumber } from "@/lib/utils";

const PROFILES = ["main", "planner", "light"];

export default function ContextPage() {
  const [profile, setProfile] = useState("main");
  const [expandedLayer, setExpandedLayer] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [editLayer, setEditLayer] = useState<ContextLayer | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["context-layers", profile],
    queryFn: () => adminApi.getContextLayers(profile),
  });

  const { data: preview, isLoading: previewLoading } = useQuery({
    queryKey: ["context-preview", profile],
    queryFn: () => adminApi.getContextPreview(profile),
    enabled: previewOpen,
  });

  const overrideMut = useMutation({
    mutationFn: ({ layer, content, enabled }: { layer: string; content: string | null; enabled: boolean }) =>
      adminApi.overrideLayer(profile, layer, content, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["context-layers", profile] });
      setEditLayer(null);
    },
  });

  const clearMut = useMutation({
    mutationFn: (layer: string) => adminApi.clearOverride(profile, layer),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["context-layers", profile] }),
  });

  return (
    <div>
      <PageHeader title="Context Inspector" description="Inspect and edit agent context layers">
        <button
          onClick={() => setPreviewOpen(true)}
          className="px-3 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 flex items-center gap-2"
        >
          <Eye className="w-4 h-4" /> Preview
        </button>
      </PageHeader>

      {/* Profile Tabs */}
      <div className="flex gap-1 bg-background border border-border rounded-lg p-1 mb-6 w-fit">
        {PROFILES.map((p) => (
          <button
            key={p}
            onClick={() => { setProfile(p); setExpandedLayer(null); }}
            className={cn(
              "px-4 py-2 rounded-md text-sm font-medium transition-colors",
              profile === p
                ? "bg-card text-foreground shadow-sm"
                : "text-muted hover:text-foreground"
            )}
          >
            {p}
          </button>
        ))}
      </div>

      {isLoading ? (
        <LoadingSpinner />
      ) : data?.layers ? (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          {/* Summary bar */}
          <div className="px-5 py-3 border-b border-border bg-background/50 flex items-center gap-4">
            <span className="text-sm text-muted">
              {data.layers.length} layers
            </span>
            <span className="text-sm text-muted">
              {formatNumber(data.total_chars)} chars
            </span>
            <span className="text-sm text-muted">
              ~{formatNumber(data.total_tokens)} token
            </span>
          </div>

          {/* Layer rows */}
          {data.layers.map((layer) => (
            <div key={layer.name} className="border-b border-border last:border-0">
              <button
                onClick={() => setExpandedLayer(expandedLayer === layer.name ? null : layer.name)}
                className="w-full px-5 py-3 flex items-center gap-4 hover:bg-sidebar-active transition-colors"
              >
                {expandedLayer === layer.name ? (
                  <ChevronDown className="w-4 h-4 text-muted" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-muted" />
                )}
                <div className="flex-1 text-left">
                  <span className="text-sm font-medium text-foreground">{layer.name}</span>
                  {layer.description && (
                    <span className="text-xs text-muted ml-2">— {layer.description}</span>
                  )}
                  {layer.source && (
                    <p className="text-xs text-muted/60 mt-0.5">{layer.source}</p>
                  )}
                </div>
                <span className="text-xs text-muted">{formatNumber(layer.chars)} chars</span>
                <span className="text-xs text-muted">{formatNumber(layer.tokens)} tok</span>
                {!layer.enabled && <Badge variant="destructive">Disabled</Badge>}
                {layer.truncated && <Badge variant="warning">Truncated</Badge>}
              </button>

              {expandedLayer === layer.name && (
                <div className="px-5 pb-4">
                  <div className="flex gap-2 mb-3">
                    <button
                      onClick={() => { setEditLayer(layer); setEditContent(layer.content); setEditEnabled(layer.enabled); }}
                      className="px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-sidebar-active flex items-center gap-1.5"
                    >
                      <Edit3 className="w-3 h-3" /> Override
                    </button>
                    <button
                      onClick={() => clearMut.mutate(layer.name)}
                      className="px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 text-destructive flex items-center gap-1.5"
                    >
                      <X className="w-3 h-3" /> Clear
                    </button>
                  </div>
                  <pre className="text-xs text-foreground bg-background rounded-lg p-4 overflow-x-auto max-h-64 whitespace-pre-wrap">
                    {layer.content || "(empty)"}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl p-5">
          <pre className="text-xs text-foreground whitespace-pre-wrap">
            {JSON.stringify(data, null, 2)}
          </pre>
        </div>
      )}

      {/* Edit Modal */}
      {editLayer && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-card border border-border rounded-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h3 className="font-medium text-foreground">Override: {editLayer.name}</h3>
              <button onClick={() => setEditLayer(null)} className="text-muted hover:text-foreground">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 flex-1 overflow-y-auto space-y-4">
              <label className="flex items-center gap-3">
                <input
                  type="checkbox"
                  checked={editEnabled}
                  onChange={(e) => setEditEnabled(e.target.checked)}
                  className="w-4 h-4 rounded"
                />
                <span className="text-sm text-foreground">Enabled</span>
              </label>
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="w-full h-64 px-3 py-2 bg-background border border-border rounded-lg text-sm font-mono text-foreground resize-y focus:outline-none focus:ring-2 focus:ring-primary/50"
              />
            </div>
            <div className="flex justify-end gap-2 px-5 py-4 border-t border-border">
              <button
                onClick={() => setEditLayer(null)}
                className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-sidebar-active"
              >
                Cancel
              </button>
              <button
                onClick={() => overrideMut.mutate({ layer: editLayer.name, content: editContent, enabled: editEnabled })}
                className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Preview Modal */}
      {previewOpen && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-card border border-border rounded-2xl w-full max-w-4xl max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h3 className="font-medium text-foreground">Context Preview — {profile}</h3>
              <button onClick={() => setPreviewOpen(false)} className="text-muted hover:text-foreground">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5">
              {previewLoading ? (
                <LoadingSpinner />
              ) : (
                <pre className="text-sm text-foreground whitespace-pre-wrap font-mono">
                  {preview?.content}
                </pre>
              )}
            </div>
            <div className="px-5 py-3 border-t border-border text-xs text-muted">
              {preview && `${formatNumber(preview.chars)} chars, ~${formatNumber(preview.tokens)} tokens`}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
