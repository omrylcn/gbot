"""GraphBot configuration schema — YAML + Pydantic + env override."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ════════════════════════════════════════════════════════════
# SUB-CONFIGS (nested BaseModel)
# ════════════════════════════════════════════════════════════


class ProviderConfig(BaseModel):
    """Single LLM provider."""

    api_key: str = ""
    api_base: str | None = None


class ProvidersConfig(BaseModel):
    """LLM providers (LiteLLM multi-provider)."""

    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)


class OwnerConfig(BaseModel):
    """Owner (default user) configuration."""

    username: str
    name: str = ""
    password: str = ""


class PersonaConfig(BaseModel):
    """Agent persona definition (tone, language, constraints)."""

    name: str = "GraphBot"
    tone: str = ""
    language: str = ""
    constraints: list[str] = Field(default_factory=list)


class RolesConfig(BaseModel):
    """Agent role definitions."""

    default: str = ""
    available: dict[str, str] = Field(default_factory=dict)


class ContextPrioritiesConfig(BaseModel):
    """Token budget per context layer (approximate — 1 token ~ 4 chars)."""

    identity: int = 500
    agent_memory: int = 500
    user_context: int = 1500
    session_summary: int = 500
    skills: int = 1000


class AssistantConfig(BaseModel):
    """Main assistant (assistant.*)."""

    name: str = "GraphBot"
    owner: OwnerConfig | None = None
    workspace: str = "./workspace"
    model: str = "anthropic/claude-sonnet-4-5-20250929"
    temperature: float = 0.7
    thinking: bool = False
    session_token_limit: int = 30_000
    max_iterations: int = 20
    tools: list[str] = Field(default_factory=lambda: ["*"])
    system_prompt: str | None = None
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    roles: RolesConfig = Field(default_factory=RolesConfig)
    context_priorities: ContextPrioritiesConfig = Field(
        default_factory=ContextPrioritiesConfig
    )
    prompt_template: str | None = None
    prompt_cache: "PromptCacheConfig" = Field(
        default_factory=lambda: PromptCacheConfig()
    )


# Channels
class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    allow_from: list[str] = Field(default_factory=list)


class DiscordChannelConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class WhatsAppChannelConfig(BaseModel):
    enabled: bool = False
    waha_url: str = "http://localhost:3000"
    session: str = "default"
    api_key: str = ""
    allow_from: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)
    allowed_dms: dict[str, str] = Field(default_factory=dict)
    respond_to_dm: bool = False
    monitor_dm: bool = False


class FeishuChannelConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allow_from: list[str] = Field(default_factory=list)


class ChannelsConfig(BaseModel):
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    whatsapp: WhatsAppChannelConfig = Field(default_factory=WhatsAppChannelConfig)
    feishu: FeishuChannelConfig = Field(default_factory=FeishuChannelConfig)


# RAG (Faz 9)
class RagConfig(BaseModel):
    embedding_model: str = "intfloat/multilingual-e5-small"
    data_source: str = "./data/items.json"
    index_path: str = "./data/faiss_index"
    text_template: str = "{title}. {description}."
    id_field: str = "id"


# Tools
class ShellToolConfig(BaseModel):
    timeout: int = 60
    restrict_to_workspace: bool = False


class WebToolConfig(BaseModel):
    search_api_key: str = ""
    max_results: int = 5
    fetch_shortcuts: dict[str, str] = Field(default_factory=dict)


class ToolsConfig(BaseModel):
    shell: ShellToolConfig = Field(default_factory=ShellToolConfig)
    web: WebToolConfig = Field(default_factory=WebToolConfig)


# Background
class CronConfig(BaseModel):
    enabled: bool = True


class MemoryEmbeddingConfig(BaseModel):
    """Embedding model for memory semantic search."""

    provider: str = "openrouter"  # openrouter | local
    model: str = "google/gemini-embedding-001"
    dimension: int = 3072


class MemoryUpdateConfig(BaseModel):
    """Conflict resolution strategy for memory updates."""

    strategy: str = "llm"  # llm (default) | cascading | threshold_only
    model: str = "openrouter/openai/gpt-4o-mini"
    noop_threshold: float = 0.90
    add_threshold: float = 0.65


class MemoryRetrievalConfig(BaseModel):
    """Faz 22D — semantic retrieval gating for memory_facts."""

    # Cosine distance ceiling (0.0 = identical, 2.0 = opposite). Tuned for
    # gemini-embedding-001 — ~0.45 keeps relevant matches, drops topical noise
    # (e.g. "altın fiyatı" no longer pulls a silver bracelet fact).
    max_distance: float | None = 0.45
    top_k_candidates: int = 20  # broad pool fed to rerank
    top_k_final: int = 10       # what enters context after rerank


class PromptCacheConfig(BaseModel):
    """Faz 22E — Anthropic / Gemini prompt caching via LiteLLM.

    LiteLLM PR #15345 transforms message-level ``cache_control`` into the
    provider-specific content-block format automatically. We just need to
    mark which part of the system prompt is cacheable.

    The static layers (identity, role, skills, agent_memory) are usually
    repeatable across turns — good cache candidates. The dynamic layer
    (user_context with retrieved memory facts) changes every turn — must
    stay outside the cache breakpoint.
    """

    enabled: bool = True

    # Provider prefixes that support cache_control. LiteLLM auto-injects
    # the right format for these. Anything else is a silent no-op.
    supported_prefixes: list[str] = [
        "anthropic/", "claude",
        "openrouter/anthropic/", "openrouter/claude",
        "google/gemini-2.5-", "openrouter/google/gemini-2.5-",
        "gemini-2.5-",
    ]

    # Models that should NOT cache even if they match a prefix above.
    # Useful for preview models that haven't enabled caching yet.
    excluded_models: list[str] = []

    # Minimum system prompt size (chars) before we bother caching.
    # Anthropic requires ≥1024 cached tokens (~4K chars) for cache to
    # actually save money; below that the write cost outweighs reads.
    min_chars_to_cache: int = 4000


class MemoryRelationsConfig(BaseModel):
    """Faz 22D — backlinks injection in ContextBuilder."""

    enabled: bool = True
    max_entities_per_turn: int = 3
    max_relations_per_entity: int = 8


class MemoryEntityPagesConfig(BaseModel):
    """Faz 22D — LLM-compiled entity pages (Karpathy LLM-Wiki pattern).

    Default-off — entity pages add an extra LLM call per touched entity
    (debounced) and a separate context layer. Enable when the relations
    graph is rich enough to justify it (typically after a few weeks of
    accumulated facts).
    """

    enabled: bool = False
    model: str = "openrouter/openai/gpt-4o-mini"
    debounce_seconds: int = 60
    min_facts_for_page: int = 3
    min_relations_for_page: int = 2
    max_input_tokens: int = 1000
    max_output_tokens: int = 200
    max_pages_in_context: int = 3


class MemoryConfig(BaseModel):
    """Memory extraction configuration."""

    enabled: bool = True
    model: str = ""  # empty → falls back to assistant.model
    extraction_every_n: int = 5
    max_facts_per_user: int = 500
    embedding: MemoryEmbeddingConfig = Field(default_factory=MemoryEmbeddingConfig)
    update: MemoryUpdateConfig = Field(default_factory=MemoryUpdateConfig)
    retrieval: MemoryRetrievalConfig = Field(default_factory=MemoryRetrievalConfig)
    relations: MemoryRelationsConfig = Field(default_factory=MemoryRelationsConfig)
    entity_pages: MemoryEntityPagesConfig = Field(default_factory=MemoryEntityPagesConfig)


class HeartbeatConfig(BaseModel):
    enabled: bool = False
    interval_s: int = 1800


class DelegationConfig(BaseModel):
    """Config for the delegation planner LLM."""

    model: str = ""  # empty → falls back to assistant.model
    temperature: float = 0.3
    examples: list[str] = Field(default_factory=list)


class BackgroundConfig(BaseModel):
    cron: CronConfig = Field(default_factory=CronConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    delegation: DelegationConfig = Field(default_factory=DelegationConfig)


# Auth
class RateLimitConfig(BaseModel):
    enabled: bool = True
    requests_per_minute: int = 60
    burst: int = 10


class AuthConfig(BaseModel):
    """Auth & API security. Empty jwt_secret_key = auth disabled (backward compatible)."""

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24 hours
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


# Database
class DatabaseConfig(BaseModel):
    path: str = "data/gbot.db"


# ════════════════════════════════════════════════════════════
# ROOT CONFIG (BaseSettings — env + .env support)
# ════════════════════════════════════════════════════════════


class Config(BaseSettings):
    """
    Root configuration.

    Priority: env vars > .env > YAML (init kwargs) > defaults

    Env override examples:
        GBOT_ASSISTANT__MODEL=openai/gpt-4o
        GBOT_DATABASE__PATH=data/prod.db
        GBOT_PROVIDERS__ANTHROPIC__API_KEY=sk-...
    """

    model_config = SettingsConfigDict(
        env_prefix="GBOT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    rag: RagConfig | None = None
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    background: BackgroundConfig = Field(default_factory=BackgroundConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    # ── Computed properties ─────────────────────────────────

    @property
    def auth_enabled(self) -> bool:
        """True when JWT secret is set (auth active)."""
        return bool(self.auth.jwt_secret_key)

    @property
    def owner_user_id(self) -> str | None:
        """Return the owner's username if configured, else None."""
        return self.assistant.owner.username if self.assistant.owner else None

    @property
    def workspace_path(self) -> Path:
        return Path(self.assistant.workspace).expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return Path(self.database.path)

    # ── Provider helpers (nanobot pattern) ──────────────────

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for model name. Falls back to first available."""
        model_name = (model or self.assistant.model).lower()

        keyword_map: dict[str, ProviderConfig] = {
            "anthropic": self.providers.anthropic,
            "claude": self.providers.anthropic,
            "openai": self.providers.openai,
            "gpt": self.providers.openai,
            "openrouter": self.providers.openrouter,
            "deepseek": self.providers.deepseek,
            "groq": self.providers.groq,
            "gemini": self.providers.gemini,
        }
        for keyword, provider in keyword_map.items():
            if keyword in model_name and provider.api_key:
                return provider.api_key

        # Fallback: first key found
        for name in ProvidersConfig.model_fields:
            p = getattr(self.providers, name)
            if isinstance(p, ProviderConfig) and p.api_key:
                return p.api_key
        return None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for model name."""
        model_name = (model or self.assistant.model).lower()
        if "openrouter" in model_name:
            return self.providers.openrouter.api_base or "https://openrouter.ai/api/v1"
        for name in ProvidersConfig.model_fields:
            p = getattr(self.providers, name)
            if isinstance(p, ProviderConfig) and name in model_name and p.api_base:
                return p.api_base
        return None
