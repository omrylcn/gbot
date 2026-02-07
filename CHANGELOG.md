# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-02-07

### Added

- **Core:** LangGraph-based stateless agent with 4-node graph (load_context → reason ⇄ execute_tools → respond)
- **Core:** GraphRunner orchestrator — SQLite ↔ LangGraph bridge, request-scoped
- **Core:** Config system — YAML + pydantic-settings + .env overlay
- **Core:** LiteLLM multi-provider support (OpenAI, Anthropic, DeepSeek, Groq, Gemini, OpenRouter)
- **Memory:** SQLite store with 10 tables (users, sessions, messages, agent_memory, user_notes, activity_logs, favorites, preferences, user_channels, cron_jobs)
- **Memory:** ContextBuilder with 6 layers (identity, agent_memory, user_ctx, prev_summary, skills, skills_index)
- **Memory:** Token-based sessions (30k limit, LLM summary on transition)
- **API:** FastAPI service with chat, sessions, health, user context endpoints
- **API:** Token-based authentication (register/login)
- **API:** WebSocket support for real-time chat
- **Channels:** Telegram, Discord, WhatsApp, Feishu multi-channel support
- **Tools:** 9 tool groups — memory, notes, favorites, activity, filesystem, shell, web search, cron, sub-agent
- **Skills:** YAML-based skill system with requirements checking and dynamic index
- **Background:** Cron scheduler for reminders/alarms with proactive messaging
- **Background:** Heartbeat service and sub-agent worker
- **RAG:** Optional FAISS-based retrieval with multilingual embeddings
- **CLI:** Typer-based CLI with interactive chat, config check, version commands
