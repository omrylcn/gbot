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
    system_prompt: str = ""
    token_count: int = 0
    iteration: int = 0
