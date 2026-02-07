"""LangGraph StateGraph — compile agent graph."""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from graphbot.agent.state import AgentState
from graphbot.agent.nodes import make_nodes, should_continue
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore


def create_graph(
    config: Config,
    db: MemoryStore,
    tools: list | None = None,
) -> StateGraph:
    """
    Build and compile the agent graph.

    Graph flow:
        START → load_context → reason ⇄ execute_tools → respond → END
    """
    nodes = make_nodes(config, db, tools)

    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("load_context", nodes["load_context"])
    graph.add_node("reason", nodes["reason"])
    graph.add_node("execute_tools", nodes["execute_tools"])
    graph.add_node("respond", nodes["respond"])

    # Edges
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "reason")
    graph.add_conditional_edges("reason", should_continue)
    graph.add_edge("execute_tools", "reason")
    graph.add_edge("respond", END)
    return graph.compile()
