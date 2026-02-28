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
    path: str = "data/graphbot.db"


# ════════════════════════════════════════════════════════════
# ROOT CONFIG (BaseSettings — env + .env support)
# ════════════════════════════════════════════════════════════


class Config(BaseSettings):
    """
    Root configuration.

    Priority: env vars > .env > YAML (init kwargs) > defaults

    Env override examples:
        GRAPHBOT_ASSISTANT__MODEL=openai/gpt-4o
        GRAPHBOT_DATABASE__PATH=data/prod.db
        GRAPHBOT_PROVIDERS__ANTHROPIC__API_KEY=sk-...
    """

    model_config = SettingsConfigDict(
        env_prefix="GRAPHBOT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
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
