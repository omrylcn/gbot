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
    """LLM provider credentials. ``openrouter`` is the primary provider;
    other API keys are kept here for forward compatibility with future
    provider integrations.
    """

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


class LLMRerankConfig(BaseModel):
    """Faz 22G Aşama 5 — Engram-style read-time deep scoring.

    When ``enabled``, the ContextBuilder asks an LLM (typically the
    cheap ``memory.model``) to re-rank the broad candidate pool against
    the current query before truncating to ``top_k_final``. Multiplier:
    one extra LLM call per turn — fail-safe falls back to the
    multiplicative formula on any error.

    Off by default; opt in once gbot-eval's retrieval_quality suite
    shows a clear win on your fixture set.
    """

    enabled: bool = False
    model: str | None = None         # None → uses MemoryConfig.model
    candidates_pool: int = 30        # bumped from top_k_candidates when on
    max_output_tokens: int = 200
    temperature: float = 0.0


class MemoryRetrievalConfig(BaseModel):
    """Faz 22D — semantic retrieval gating for memory_facts."""

    # Cosine distance ceiling (0.0 = identical, 2.0 = opposite). Tuned for
    # gemini-embedding-001 — ~0.45 keeps relevant matches, drops topical noise
    # (e.g. "altın fiyatı" no longer pulls a silver bracelet fact).
    max_distance: float | None = 0.45
    top_k_candidates: int = 20  # broad pool fed to rerank
    top_k_final: int = 10       # what enters context after rerank
    # Faz 22G Aşama 5 — opt-in LLM rerank.
    llm_rerank: LLMRerankConfig = Field(default_factory=LLMRerankConfig)


class PromptCacheConfig(BaseModel):
    """Faz 22E — Anthropic / Gemini prompt caching.

    The static system layers (identity, role, skills, agent_memory) are
    repeatable across turns — good cache candidates. We mark them with
    ``cache_control: {"type": "ephemeral"}`` and OpenRouter forwards
    that to Anthropic / Gemini in the right content-block shape. The
    dynamic layer (user_context with retrieved memory facts) changes
    every turn — must stay outside the cache breakpoint.
    """

    enabled: bool = True

    # Provider prefixes that support ``cache_control``. Anything else
    # is a silent no-op.
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


class MemoryMaintenanceConfig(BaseModel):
    """Faz 22E Step 2 — automatic decay + stale-page recompile + orphan cleanup.

    Manuel admin endpoint var (POST /admin/memory/{user}/maintenance/run);
    bu config otomatik schedule'ı kontrol eder. Disabled bırakılırsa
    decay hiç çalışmaz, fact'ler birikir.
    """

    enabled: bool = True
    daily_cron: str = "0 4 * * *"      # her gün 04:00 — decay + stale recompile + orphan cleanup
    weekly_cron: str = "30 4 * * 0"    # Pazar 04:30 — relations dedup catch-up


class ObsidianSyncConfig(BaseModel):
    """Faz 22G Aşama 4 — entity pages → Obsidian vault markdown.

    Off by default. When enabled, a recurring task per user dumps every
    fresh ``memory_entity_pages.content_md`` row into a markdown file
    under ``vault_path/gbot/<user_id>/<entity>.md`` with a frontmatter
    block. Stale pages are skipped so Obsidian never sees half-baked
    output.
    """

    enabled: bool = False
    vault_path: str = "~/Obsidian/Memories"
    sync_cron: str = "0 * * * *"        # her saat
    include_archived: bool = False
    include_stale: bool = False


class PageScoringConfig(BaseModel):
    """Faz 22J-B — weights for the multi-signal entity-page scorer.

    Each signal returns a value roughly in [0, 1]; weighted sum
    determines rank. Pinned pages bypass the score entirely.
    """

    alpha_direct: float = 2.0          # surface-form mention in query
    beta_semantic: float = 1.5         # cosine(query_emb, page_emb)
    gamma_backlink: float = 1.0        # shared fact_ids w/ retrieved facts
    delta_graph: float = 0.5           # 1-hop neighbour in entities_in_play
    epsilon_recency: float = 0.3       # exp-decayed by half-life
    recency_half_life_days: int = 14


class MemoryEntityPagesConfig(BaseModel):
    """Faz 22D — LLM-compiled entity pages (Karpathy LLM-Wiki pattern).

    Default-off — entity pages add an extra LLM call per touched entity
    (debounced) and a separate context layer. Enable when the relations
    graph is rich enough to justify it (typically after a few weeks of
    accumulated facts).

    Faz 22J — living wiki extensions:
    - ``pinned`` keeps anchor entities (e.g. owner) in context every turn
    - ``incremental_enabled`` activates Karpathy-style "update old page
      with delta facts" instead of full rewrite
    - ``size_buckets`` map entity weight → max_output_tokens bucket so
      hubs get bigger pages naturally
    - ``dynamic_top_k`` limits non-pinned pages selected by the scorer
    - ``scoring`` weights drive the multi-signal page selector
    """

    enabled: bool = False
    model: str = "openrouter/openai/gpt-4o-mini"
    debounce_seconds: int = 60
    min_facts_for_page: int = 3
    min_relations_for_page: int = 2
    max_input_tokens: int = 1000
    max_output_tokens: int = 200       # legacy hard cap (also fallback for small bucket)
    max_pages_in_context: int = 3      # legacy; superseded by len(pinned) + dynamic_top_k

    # ── Faz 22J — Living wiki ─────────────────────────────────
    pinned: list[str] = Field(default_factory=lambda: ["owner"])
    incremental_enabled: bool = True
    incremental_max_delta: int = 5     # if delta > N → fall back to full compile
    lint_enabled: bool = True
    max_ripple_per_extraction: int = 12
    # Adaptive size: (weight upper bound) → (max_output_tokens, bucket name)
    # Highest matching bucket wins. Pinned entities always use the top bucket.
    size_buckets: dict[str, dict] = Field(
        default_factory=lambda: {
            "small":  {"weight_max": 2.0,   "max_tokens": 200},
            "medium": {"weight_max": 8.0,   "max_tokens": 600},
            "large":  {"weight_max": 20.0,  "max_tokens": 1500},
            "owner":  {"weight_max": 9999.0, "max_tokens": 3000},
        }
    )

    # ── Faz 22J-B — Dynamic retrieval ────────────────────────
    dynamic_top_k: int = 4             # non-pinned pages selected by scorer
    wiki_index_enabled: bool = True    # render `[[entity]] · stats` index block
    wiki_index_group_by_type: bool = True
    scoring: PageScoringConfig = Field(default_factory=PageScoringConfig)


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
    maintenance: MemoryMaintenanceConfig = Field(default_factory=MemoryMaintenanceConfig)
    obsidian_sync: ObsidianSyncConfig = Field(default_factory=ObsidianSyncConfig)
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
