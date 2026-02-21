"""GraphRunner — orchestrator between SQLite and LangGraph."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from loguru import logger

from graphbot.agent.graph import create_graph
from graphbot.agent.permissions import get_allowed_tools, get_context_layers, get_max_sessions
from graphbot.agent.tools import ToolRegistry, make_tools
from graphbot.core.config.schema import Config
from graphbot.core.providers.litellm import aextract_facts, asummarize, setup_provider
from graphbot.memory.store import MemoryStore


class GraphRunner:
    """
    Request-scoped orchestrator.

    Flow:
        1. Find/create session (SQLite)
        2. Load history (SQLite → LangChain messages)
        3. graph.ainvoke(state) — stateless, no checkpoint
        4. Save new messages (LangGraph → SQLite)
        5. Check token limit → summarize & rotate if needed
    """

    def __init__(
        self,
        config: Config,
        db: MemoryStore,
        tools: list | ToolRegistry | None = None,
    ):
        self.config = config
        self.db = db
        if isinstance(tools, ToolRegistry):
            self.registry = tools
        elif isinstance(tools, list):
            # Backward compat: wrap plain list in a registry
            self.registry = ToolRegistry()
            if tools:
                self.registry.register_group("custom", tools)
        else:
            self.registry = make_tools(config, db)
        self.tools = self.registry.get_all_tools()
        setup_provider(config)
        self._graph = create_graph(config, db, self.tools)

    async def process(
        self,
        user_id: str,
        channel: str,
        message: str,
        session_id: str | None = None,
        skip_context: bool = False,
    ) -> tuple[str, str]:
        """Process a user message and return (response, session_id).

        Parameters
        ----------
        user_id : str
            User identifier.
        channel : str
            Channel name (api, telegram, etc.).
        message : str
            User message text.
        session_id : str, optional
            Existing session ID. If None, creates a new session.
        skip_context : bool
            If True, load only identity prompt (no user context, memory, etc.).
            Used by background tasks to reduce cost.

        Returns
        -------
        tuple[str, str]
            (assistant_response, session_id).
        """
        # 0. RBAC — resolve user role and permissions
        user = self.db.get_user(user_id)
        role = (user.get("role") or "guest") if user else "guest"
        allowed_tools = get_allowed_tools(role, registry=self.registry)
        context_layers = get_context_layers(role)

        # 1. Session — client provides session_id, or we create one
        #    Guest users: enforce single session limit
        max_sess = get_max_sessions(role)
        if session_id is None:
            if max_sess == 1:
                active = self.db.get_active_session(user_id)
                session_id = (
                    active["session_id"] if active
                    else self.db.create_session(user_id, channel)
                )
            else:
                session_id = self.db.create_session(user_id, channel)
        else:
            existing = self.db.get_session(session_id)
            if not existing:
                self.db.create_session(user_id, channel, session_id=session_id)
            elif existing.get("ended_at") is not None:
                logger.info(
                    f"Session {session_id} is closed, creating new session"
                )
                session_id = self.db.create_session(user_id, channel)

        # 2. Load history → LangChain messages
        history = self._load_history(session_id)

        # 3. Run graph
        state = await self._graph.ainvoke(
            {
                "user_id": user_id,
                "session_id": session_id,
                "channel": channel,
                "role": role,
                "allowed_tools": allowed_tools,
                "context_layers": context_layers,
                "messages": history + [HumanMessage(content=message)],
                "iteration": 0,
                "token_count": 0,
                "skip_context": skip_context,
            }
        )

        # 4. Extract response
        response = self._extract_response(state)

        # 5. Save to SQLite (only NEW messages, skip loaded history + new HumanMessage)
        self.db.add_message(session_id, "user", message)
        new_start = len(history) + 1  # skip history + HumanMessage
        self._save_ai_messages(session_id, state, skip=new_start)

        # 6. Token limit check
        token_count = state.get("token_count", 0)
        self.db.update_session_token_count(session_id, token_count)

        if token_count >= self.config.assistant.session_token_limit:
            await self._rotate_session(user_id, session_id)

        return response, session_id

    def _load_history(self, session_id: str) -> list:
        """SQLite messages → LangChain messages."""
        rows = self.db.get_session_messages(session_id)
        messages = []
        for row in rows:
            role = row["role"]
            content = row["content"] or ""
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                tc = None
                if row.get("tool_calls"):
                    try:
                        tc = json.loads(row["tool_calls"])
                    except json.JSONDecodeError:
                        tc = None
                messages.append(AIMessage(content=content, tool_calls=tc or []))
            elif role == "tool":
                tc_id = row.get("tool_call_id") or ""
                messages.append(ToolMessage(content=content, tool_call_id=tc_id))
        return messages

    def _extract_response(self, state: dict) -> str:
        """Get final assistant text from state."""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                return msg.content
        return ""

    def _save_ai_messages(self, session_id: str, state: dict, skip: int = 0) -> None:
        """Save new AI + tool messages to SQLite."""
        for msg in state["messages"][skip:]:
            if isinstance(msg, AIMessage):
                tc = json.dumps(msg.tool_calls) if msg.tool_calls else None
                self.db.add_message(session_id, "assistant", msg.content, tool_calls=tc)
            elif isinstance(msg, ToolMessage):
                self.db.add_message(
                    session_id, "tool", msg.content, tool_call_id=msg.tool_call_id,
                )

    @staticmethod
    def _prepare_summary_messages(
        db_messages: list[dict],
    ) -> list[dict[str, str]]:
        """Convert DB messages to LiteLLM format for summarization.

        Filters out tool messages and empty content for cleaner summaries.
        """
        result = []
        for msg in db_messages:
            if msg["role"] in ("user", "assistant") and msg.get("content"):
                result.append({"role": msg["role"], "content": msg["content"]})
        return result

    async def _rotate_session(self, user_id: str, session_id: str) -> None:
        """Close session with LLM summary and extract facts to DB."""
        logger.info(f"Token limit reached for session {session_id}, rotating")

        # 1. Prepare messages for summarization
        db_messages = self.db.get_recent_messages(session_id, limit=50)
        llm_messages = self._prepare_summary_messages(db_messages)

        # 2. Hybrid summary (narrative + structured bullets)
        summary = ""
        try:
            if llm_messages:
                summary = await asummarize(llm_messages)
        except Exception as e:
            logger.error(f"Summarization error for session {session_id}: {e}")

        if not summary:
            summary = "Session closed due to token limit (summary unavailable)."

        # 3. Fact extraction → DB tables (best-effort, failure is OK)
        try:
            if llm_messages:
                facts = await aextract_facts(llm_messages)
                self._save_extracted_facts(user_id, facts)
        except Exception as e:
            logger.warning(f"Fact extraction failed for session {session_id}: {e}")

        # 4. Always close the session
        self.db.end_session(
            session_id, summary=summary, close_reason="token_limit"
        )

    def _save_extracted_facts(self, user_id: str, facts: dict) -> None:
        """Save extracted facts to appropriate DB tables."""
        # Preferences → preferences table (JSON merge)
        prefs = facts.get("preferences", [])
        if prefs:
            pref_dict = {
                p["key"]: p["value"]
                for p in prefs
                if isinstance(p, dict) and "key" in p and "value" in p
            }
            if pref_dict:
                self.db.update_preferences(user_id, pref_dict)
                logger.debug(f"Saved {len(pref_dict)} preferences for {user_id}")

        # Notes → user_notes table (source="extraction")
        notes = facts.get("notes", [])
        for note in notes:
            if note and isinstance(note, str):
                self.db.add_note(user_id, note, source="extraction")
                logger.debug(f"Saved extracted note for {user_id}: {note[:50]}")
