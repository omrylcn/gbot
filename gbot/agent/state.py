"""AgentState — LangGraph state definition."""

from __future__ import annotations

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """
    Extends MessagesState (messages: Annotated[list[BaseMessage], add_messages]).

    LangGraph manages messages automatically — append-only via add_messages reducer.
    """

    user_id: str
    session_id: str
    channel: str
    role: str = "guest"
    allowed_tools: set[str] | None = None
    context_layers: set[str] | None = None
    system_prompt: str = ""
    token_count: int = 0
    iteration: int = 0
    skip_context: bool = False
