import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { User, MessageCircle, Hash } from "lucide-react";
import { adminApi } from "@/api/admin";
import type { Message } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Badge, RoleBadge } from "@/components/shared/Badge";
import { cn, formatDate, truncate } from "@/lib/utils";

export default function ConversationsPage() {
  const [selectedUser, setSelectedUser] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);

  const { data: users, isLoading: usersLoading } = useQuery({
    queryKey: ["users"],
    queryFn: adminApi.getUsers,
  });

  const { data: sessions, isLoading: sessionsLoading } = useQuery({
    queryKey: ["sessions", selectedUser],
    queryFn: () => adminApi.getSessions(selectedUser!, 100),
    enabled: !!selectedUser,
  });

  const { data: messages, isLoading: messagesLoading } = useQuery({
    queryKey: ["messages", selectedSession],
    queryFn: () => adminApi.getSessionHistory(selectedSession!),
    enabled: !!selectedSession,
  });

  return (
    <div className="h-[calc(100vh-8rem)]">
      <PageHeader title="Conversations" description="User sessions and message history" />

      <div className="grid grid-cols-12 gap-4 h-[calc(100%-4rem)]">
        {/* Users Panel */}
        <div className="col-span-3 bg-card border border-border rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-border">
            <h3 className="text-sm font-medium text-foreground flex items-center gap-2">
              <User className="w-4 h-4" /> Users
            </h3>
          </div>
          <div className="flex-1 overflow-y-auto">
            {usersLoading ? (
              <LoadingSpinner />
            ) : (
              users?.map((u) => (
                <button
                  key={u.user_id}
                  onClick={() => {
                    setSelectedUser(u.user_id);
                    setSelectedSession(null);
                  }}
                  className={cn(
                    "w-full px-4 py-3 text-left hover:bg-sidebar-active transition-colors border-b border-border",
                    selectedUser === u.user_id && "bg-sidebar-active"
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-foreground">{u.name || u.user_id}</span>
                    <RoleBadge role={u.role || "guest"} />
                  </div>
                  <p className="text-xs text-muted mt-0.5">@{u.user_id}</p>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Sessions Panel */}
        <div className="col-span-3 bg-card border border-border rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-border">
            <h3 className="text-sm font-medium text-foreground flex items-center gap-2">
              <Hash className="w-4 h-4" /> Sessions
              {sessions && <Badge variant="default">{sessions.length}</Badge>}
            </h3>
          </div>
          <div className="flex-1 overflow-y-auto">
            {!selectedUser ? (
              <p className="text-sm text-muted p-4 text-center">Select a user</p>
            ) : sessionsLoading ? (
              <LoadingSpinner />
            ) : sessions?.length === 0 ? (
              <p className="text-sm text-muted p-4 text-center">No sessions found</p>
            ) : (
              sessions?.map((s) => (
                <button
                  key={s.session_id}
                  onClick={() => setSelectedSession(s.session_id)}
                  className={cn(
                    "w-full px-4 py-3 text-left hover:bg-sidebar-active transition-colors border-b border-border",
                    selectedSession === s.session_id && "bg-sidebar-active"
                  )}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-mono text-muted">{s.session_id.slice(0, 8)}...</span>
                    <Badge variant={s.ended_at ? "default" : "success"}>
                      {s.ended_at ? "Closed" : "Active"}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted">
                    <span>{s.channel || "api"}</span>
                    <span>·</span>
                    <span>{s.token_count} tok</span>
                    <span>·</span>
                    <span>{formatDate(s.started_at)}</span>
                  </div>
                  {s.summary && (
                    <p className="text-xs text-foreground mt-1">{truncate(s.summary, 80)}</p>
                  )}
                </button>
              ))
            )}
          </div>
        </div>

        {/* Messages Panel */}
        <div className="col-span-6 bg-card border border-border rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-border">
            <h3 className="text-sm font-medium text-foreground flex items-center gap-2">
              <MessageCircle className="w-4 h-4" /> Messages
              {messages && <Badge variant="default">{messages.length}</Badge>}
            </h3>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {!selectedSession ? (
              <p className="text-sm text-muted text-center py-8">Select a session</p>
            ) : messagesLoading ? (
              <LoadingSpinner />
            ) : messages?.length === 0 ? (
              <p className="text-sm text-muted text-center py-8">No messages found</p>
            ) : (
              messages?.map((m) => <MessageBubble key={m.id} message={m} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const { role, content, tool_calls, tool_call_id, created_at } = message;

  if (role === "tool") {
    return (
      <div className="msg-tool px-4 py-3 max-w-[90%]">
        <div className="flex items-center gap-2 mb-1">
          <Badge variant="warning">tool</Badge>
          {tool_call_id && <span className="text-xs font-mono text-muted">{tool_call_id}</span>}
        </div>
        <pre className="text-xs text-foreground whitespace-pre-wrap overflow-x-auto">
          {content || "(empty response)"}
        </pre>
      </div>
    );
  }

  const isUser = role === "user" || role === "human";

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] px-4 py-2.5",
          isUser ? "msg-user" : "msg-assistant"
        )}
      >
        <div className="flex items-center gap-2 mb-1">
          <Badge variant={isUser ? "primary" : "default"}>{role}</Badge>
          <span className="text-xs text-muted">{formatDate(created_at)}</span>
        </div>
        <div className="msg-content text-sm text-foreground whitespace-pre-wrap">
          {content || "(empty)"}
          {tool_calls && (
            <details className="mt-2">
              <summary className="text-xs text-muted cursor-pointer hover:text-foreground">
                Tool Calls
              </summary>
              <pre className="text-xs mt-1 p-2 bg-background rounded overflow-x-auto">
                {tool_calls}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
