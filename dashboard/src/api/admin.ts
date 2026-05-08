import { api } from "./client";

// Types
export interface SystemStats {
  system: { version: string; model: string; session_token_limit: number; thinking: boolean };
  context: { layers: { layer: string; chars: number; tokens: number }[]; total_chars: number; total_tokens: number };
  tools: { total: number; available: number; groups: Record<string, string[]> };
  sessions: { active: number; total: number; total_tokens: number };
  data: { users: number; messages: number; notes: number; memories: number; recurring_tasks: number; pending_delayed: number };
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

export interface BackgroundTask {
  task_id: string;
  user_id: string;
  execution_type: string;
  processor: string;
  message: string;
  channel: string;
  cron_expr: string | null;
  run_at: string | null;
  enabled: number;
  status: string;
  agent_prompt: string | null;
  created_at: string;
  // Legacy compat
  job_id?: string;
}

// Legacy alias
export type CronJob = BackgroundTask;

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
  task_id: string;
  user_id: string;
  execution_type: string | null;
  processor_type: string | null;
  status: string;
  result: string | null;
  reference_id: string | null;
  plan_json: string | null;
  executed_at: string;
  // Legacy compat
  task_description?: string;
  created_at?: string;
}

export interface MemoryFact {
  fact_id: string;
  user_id: string;
  content: string;
  fact_type: string;
  source: string;
  source_session: string | null;
  source_channel: string | null;
  confidence: number;
  importance: number;
  access_count: number;
  valid_from: string;
  valid_until: string | null;
  superseded_by: string | null;
  keywords: string | null;
  category: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryResponse {
  user_id: string;
  notes: string[];
  preferences: Record<string, string>;
  favorites: { item_id: string; item_title: string; added_at: string }[];
  facts: MemoryFact[];
  fact_stats: { total: number; by_type: Record<string, { count: number; avg_importance: number }> };
  processing_log: { id: number; trigger: string; facts_extracted: number; facts_added: number; duration_ms: number; processed_at: string }[];
}

// Faz 22D types
export interface MemoryRelation {
  relation_id: string;
  user_id: string;
  source_entity: string;
  relation: string;
  target_entity: string;
  canonical_source: string | null;
  canonical_target: string | null;
  confidence: number;
  valid_until: string | null;
  source_fact: string | null;
  created_at: string;
}

export interface MemoryEntityPage {
  page_id: string;
  user_id: string;
  entity_canonical: string;
  entity_surface_forms: string;
  content_md: string;
  source_fact_ids: string;
  source_relation_ids: string;
  fact_count: number;
  relation_count: number;
  version: number;
  stale: number;
  last_compiled_at: string;
  last_accessed_at: string | null;
  access_count: number;
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
  getTasks: () => api.get<BackgroundTask[]>("/admin/tasks"),
  deleteTask: (taskId: string) => api.delete<{ status: string }>(`/admin/tasks/${taskId}`),
  // Legacy aliases
  getCrons: () => api.get<BackgroundTask[]>("/admin/tasks"),
  deleteCron: (taskId: string) => api.delete<{ status: string }>(`/admin/tasks/${taskId}`),
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
  getMemory: (userId: string) => api.get<MemoryResponse>(`/admin/memory/${userId}`),
  // Faz 22D — relations / entities / pages
  getMemoryRelations: (userId: string, entity?: string) =>
    api.get<{ user_id: string; entity: string | null; count: number; relations: MemoryRelation[] }>(
      `/admin/memory/${userId}/relations${entity ? `?entity=${encodeURIComponent(entity)}` : ""}`,
    ),
  getMemoryEntities: (userId: string) =>
    api.get<{ user_id: string; count: number; entities: { canonical: string; relation_count: number }[] }>(
      `/admin/memory/${userId}/entities`,
    ),
  getMemoryEntityPages: (userId: string, onlyFresh = false) =>
    api.get<{ user_id: string; count: number; pages: MemoryEntityPage[] }>(
      `/admin/memory/${userId}/entity-pages${onlyFresh ? "?only_fresh=true" : ""}`,
    ),
  recompileEntityPage: (userId: string, entity: string) =>
    api.post<{ ok: boolean; reason?: string; user_id: string; entity: string; page: MemoryEntityPage | null }>(
      `/admin/memory/${userId}/pages/recompile?entity=${encodeURIComponent(entity)}`,
      {},
    ),
  forgetEntity: (userId: string, entity: string) =>
    api.delete<{ user_id: string; entity: string; archived: { relations: number; facts: number; pages: number } }>(
      `/admin/memory/${userId}/entity/${encodeURIComponent(entity)}`,
    ),
  runMemoryMaintenance: (userId: string) =>
    api.post<{ daily: Record<string, unknown>; weekly: Record<string, unknown> }>(
      `/admin/memory/${userId}/maintenance/run`,
      {},
    ),
  retrievalDebug: (userId: string, query: string, topK = 10) =>
    api.get<{
      user_id: string;
      query: string;
      max_distance_gate: number | null;
      count: number;
      candidates: (MemoryFact & { distance: number; above_gate: boolean })[];
    }>(`/admin/memory/${userId}/retrieval-debug?query=${encodeURIComponent(query)}&top_k=${topK}`),
  getSessions: (userId: string, limit = 50) =>
    api.get<Session[]>(`/sessions/${userId}?limit=${limit}`),
  getSessionHistory: (sessionId: string) =>
    api.get<{ session_id: string; messages: Message[] }>(`/session/${sessionId}/history`)
      .then(res => res.messages),
};
