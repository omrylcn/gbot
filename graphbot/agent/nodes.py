"""Graph nodes — load_context, reason, execute_tools, respond."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, SystemMessage
from loguru import logger

from graphbot.agent.state import AgentState
from graphbot.core.config.schema import Config
from graphbot.core.providers import litellm as llm_provider
from graphbot.agent.context import ContextBuilder
from graphbot.memory.store import MemoryStore


def make_nodes(config: Config, db: MemoryStore, tools: list | None = None):
    """
    Create node functions closed over config, db, and tools.

    Returns dict of {node_name: callable} for graph registration.
    """
    ctx_builder = ContextBuilder(config, db)
    tool_defs = _build_tool_definitions(tools) if tools else None
    tool_map = {t.name: t for t in tools} if tools else {}

    async def load_context(state: AgentState) -> dict[str, Any]:
        """Build system prompt from SQLite + workspace."""
        prompt = ctx_builder.build(state["user_id"])
        logger.debug(f"Context built for user {state['user_id']}")
        return {"system_prompt": prompt}

    async def reason(state: AgentState) -> dict[str, Any]:
        """Call LLM with messages + tools."""
        # Build messages for litellm (dict format)
        messages = [{"role": "system", "content": state["system_prompt"]}]
        for msg in state["messages"]:
            messages.append(_langchain_to_dict(msg))

        ai_message = await llm_provider.achat(
            messages=messages,
            model=config.assistant.model,
            tools=tool_defs,
            temperature=config.assistant.temperature,
            api_base=config.get_api_base(),
        )

        return {
            "messages": [ai_message],
            "iteration": state["iteration"] + 1,
        }

    async def execute_tools(state: AgentState) -> dict[str, Any]:
        """Execute tool calls from the last AI message."""
        from langchain_core.messages import ToolMessage

        last_msg = state["messages"][-1]
        results = []

        for call in last_msg.tool_calls:
            tool = tool_map.get(call["name"])
            if tool is None:
                result = f"Tool '{call['name']}' not found"
            else:
                try:
                    args = call["args"].copy()
                    # Inject state context into tools that accept these params
                    tool_fields = set(tool.args_schema.model_fields) if tool.args_schema else set()
                    if "channel" in tool_fields:
                        original = args.get("channel")
                        args["channel"] = state["channel"]
                        logger.debug(
                            f"Channel inject: tool={call['name']}, "
                            f"{original!r} → {state['channel']!r}"
                        )
                    result = await tool.ainvoke(args)
                except Exception as e:
                    result = f"Tool error: {e}"

            results.append(
                ToolMessage(content=str(result), tool_call_id=call["id"])
            )

        return {"messages": results}

    async def respond(state: AgentState) -> dict[str, Any]:
        """Calculate token count from last response."""
        last_msg = state["messages"][-1]
        usage = getattr(last_msg, "response_metadata", {}).get("usage", {})
        total = usage.get("total_tokens", 0)
        return {"token_count": state["token_count"] + total}

    return {
        "load_context": load_context,
        "reason": reason,
        "execute_tools": execute_tools,
        "respond": respond,
    }


def should_continue(state: AgentState) -> str:
    """Conditional edge: after reason, go to tools or respond."""
    last_msg = state["messages"][-1]

    # Max iteration guard
    if state["iteration"] >= 20:
        logger.warning("Max iterations reached, forcing respond")
        return "respond"

    # Has tool calls?
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "execute_tools"

    return "respond"


def _langchain_to_dict(msg: Any) -> dict[str, Any]:
    """Convert LangChain message to dict for litellm."""
    from langchain_core.messages import HumanMessage, ToolMessage

    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": msg.content}
    elif isinstance(msg, AIMessage):
        d: dict[str, Any] = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": str(tc["args"])},
                }
                for tc in msg.tool_calls
            ]
        return d
    elif isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "content": msg.content,
        }
    elif isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content}
    else:
        return {"role": "user", "content": str(msg.content)}


def _build_tool_definitions(tools: list) -> list[dict[str, Any]]:
    """Convert LangChain tools to OpenAI function format."""
    defs = []
    for tool in tools:
        schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
        defs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": schema,
                },
            }
        )
    return defs
