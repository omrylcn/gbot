"""GraphRunner — orchestrator between SQLite and LangGraph."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from loguru import logger

from graphbot.agent.graph import create_graph
from graphbot.agent.permissions import get_allowed_tools, get_context_layers, get_max_sessions
from graphbot.agent.tools import make_tools
from graphbot.core.config.schema import Config
from graphbot.core.providers.litellm import setup_provider
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

    def __init__(self, config: Config, db: MemoryStore, tools: list | None = None):
        self.config = config
        self.db = db
        self.tools = tools if tools is not None else make_tools(config, db)
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
        allowed_tools = get_allowed_tools(role)
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
        elif not self.db.get_session(session_id):
            self.db.create_session(user_id, channel, session_id=session_id)

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

    async def _rotate_session(self, user_id: str, session_id: str) -> None:
        """Close session with summary, open new one."""
        logger.info(f"Token limit reached for session {session_id}, rotating")
        # TODO: LLM summarize call (Faz 2 basic — just close for now)
        self.db.end_session(
            session_id, summary="Session closed due to token limit.", close_reason="token_limit"
        )
