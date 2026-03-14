import { api } from "./client";

// Types
export interface SystemStats {
  system: { version: string; model: string; session_token_limit: number; thinking: boolean };
  context: { layers: { layer: string; chars: number; tokens: number }[]; total_chars: number; total_tokens: number };
  tools: { total: number; available: number; groups: Record<string, string[]> };
  sessions: { active: number; total: number; total_tokens: number };
  data: { users: number; messages: number; notes: number; memories: number; cron_jobs: number; reminders: number };
}

export interface ServerStatus {
  version: string;
  model: string;
  users: number;
  active_sessions: number;
  status: string;
}

export interface User {
  user_id: string;
  name: string;
  role?: string;
  created_at: string;
  channels?: { channel: string; channel_user_id: string }[];
}

export interface CronJob {
  job_id: string;
  user_id: string;
  cron_expr: string;
  message: string;
  channel: string;
  enabled: number;
  agent_prompt: string | null;
  processor: string | null;
}

export interface ToolsResponse {
  tools: { name: string; description: string; group: string }[];
  groups: Record<string, string[]>;
  total: number;
  available: number;
}

export interface ContextLayer {
  name: string;
  description: string;
  source: string;
  content: string;
  chars: number;
  tokens: number;
  budget: number;
  truncated: boolean;
  enabled: boolean;
}

export interface ContextLayersResponse {
  profile: string;
  user_id: string;
  layers: ContextLayer[];
  total_chars: number;
  total_tokens: number;
}

export interface ContextPreviewResponse {
  profile: string;
  content: string;
  chars: number;
  tokens: number;
}

export interface AgentProfile {
  name: string;
  agent_md: string;
  skills: string[];
  template_vars: string[];
  has_agent_md: boolean;
}

export interface ProfileDetail extends AgentProfile {
  agent_md_content: string;
}

export interface DelegationLog {
  id: number;
  user_id: string;
  task_description: string;
  processor_type: string;
  reference_id: string | null;
  plan_json: string | null;
  created_at: string;
}

export interface ServerConfig {
  model: string;
  temperature: number;
  session_token_limit: number;
  max_iterations: number;
  auth_enabled: boolean;
  cron_enabled: boolean;
  heartbeat_enabled: boolean;
  db_path: string;
}

export interface Session {
  session_id: string;
  user_id: string;
  channel: string;
  started_at: string;
  ended_at: string | null;
  summary: string | null;
  token_count: number;
  close_reason: string | null;
}

export interface Message {
  id: number;
  session_id: string;
  role: string;
  content: string;
  tool_calls: string | null;
  tool_call_id: string | null;
  created_at: string;
}

// API calls
export const adminApi = {
  getStats: () => api.get<SystemStats>("/admin/stats"),
  getStatus: () => api.get<ServerStatus>("/admin/status"),
  getConfig: () => api.get<ServerConfig>("/admin/config"),
  getUsers: () => api.get<User[]>("/admin/users"),
  setUserRole: (userId: string, role: string) =>
    api.put<{ user_id: string; role: string }>(`/admin/users/${userId}/role`, { role }),
  getCrons: () => api.get<CronJob[]>("/admin/crons"),
  deleteCron: (jobId: string) => api.delete<{ status: string }>(`/admin/crons/${jobId}`),
  getTools: () => api.get<ToolsResponse>("/admin/tools"),
  getLogs: (limit = 50) => api.get<DelegationLog[]>(`/admin/logs?limit=${limit}`),
  getContextLayers: (profile: string, userId?: string) =>
    api.get<ContextLayersResponse>(`/admin/context/${profile}/layers${userId ? `?user_id=${userId}` : ""}`),
  getContextPreview: (profile: string, userId?: string) =>
    api.get<ContextPreviewResponse>(`/admin/context/${profile}/preview${userId ? `?user_id=${userId}` : ""}`),
  overrideLayer: (profile: string, layer: string, content: string | null, enabled: boolean) =>
    api.put(`/admin/context/${profile}/layers/${layer}`, { content, enabled }),
  clearOverride: (profile: string, layer: string) =>
    api.delete(`/admin/context/${profile}/layers/${layer}`),
  getOverrides: () => api.get<Record<string, Record<string, { content: string | null; enabled: boolean }>>>("/admin/context/overrides"),
  getProfiles: () => api.get<AgentProfile[]>("/admin/profiles"),
  getProfileDetail: (name: string) => api.get<ProfileDetail>(`/admin/profiles/${name}`),
  getSessions: (userId: string, limit = 50) =>
    api.get<Session[]>(`/sessions/${userId}?limit=${limit}`),
  getSessionHistory: (sessionId: string) =>
    api.get<{ session_id: string; messages: Message[] }>(`/session/${sessionId}/history`)
      .then(res => res.messages),
};
