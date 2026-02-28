"""LightAgent — lightweight, isolated agent for background tasks."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from loguru import logger

from graphbot.agent.nodes import should_continue, _langchain_to_dict, _build_tool_definitions
from graphbot.agent.state import AgentState
from graphbot.core.config.schema import Config
from graphbot.core.providers import litellm as llm_provider
from graphbot.core.providers.litellm import setup_provider


class LightAgent:
    """Lightweight agent for background tasks (cron jobs, subagents).

    Unlike the full GraphRunner, LightAgent:
      - Skips load_context (prompt provided externally)
      - Uses a restricted tool set
      - Can override model (e.g. cheaper model for monitoring)
      - Has no session/history management

    Parameters
    ----------
    config : Config
        Application config.
    prompt : str
        System prompt for this agent.
    tools : list, optional
        Restricted tool list. Empty = no tools.
    model : str, optional
        Model override. Defaults to config.assistant.model.
    """

    def __init__(
        self,
        config: Config,
        prompt: str,
        tools: list | None = None,
        model: str | None = None,
    ):
        self.config = config
        self.prompt = prompt
        self.tools = tools or []
        self.model = model or config.assistant.model
        setup_provider(config)
        self._graph = self._compile()

    async def run(self, message: str) -> tuple[str, int]:
        """Run a single task and return (response, token_count).

        The response text is the final assistant message.
        Use `run_with_meta` to also get tool call metadata.
        """
        response, tokens, _ = await self.run_with_meta(message)
        return response, tokens

    async def run_with_meta(self, message: str) -> tuple[str, int, set[str]]:
        """Run a task and return (response, token_count, called_tools).

        Parameters
        ----------
        message : str
            The task description.

        Returns
        -------
        tuple[str, int, set[str]]
            (response_text, token_count, set_of_tool_names_called)
        """
        state = await self._graph.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "system_prompt": self.prompt,
                "user_id": "system",
                "session_id": "",
                "channel": "background",
                "iteration": 0,
                "token_count": 0,
            },
            config={"recursion_limit": 50},
        )
        response = self._extract(state)
        tokens = state.get("token_count", 0)
        called = self._extract_called_tools(state)
        logger.debug(f"LightAgent done: {len(response)} chars, {tokens} tokens")
        return response, tokens, called

    @staticmethod
    def _extract_called_tools(state: dict) -> set[str]:
        """Extract set of tool names called during execution."""
        names: set[str] = set()
        for msg in state.get("messages", []):
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    names.add(tc["name"])
        return names

    def _compile(self) -> StateGraph:
        """Build minimal graph: reason -> execute_tools -> respond."""
        tool_defs = _build_tool_definitions(self.tools) if self.tools else None
        tool_map = {t.name: t for t in self.tools} if self.tools else {}
        model = self.model
        config = self.config

        max_tool_iterations = 10

        async def reason(state: AgentState) -> dict[str, Any]:
            """Call LLM with system prompt + messages."""
            messages = [{"role": "system", "content": state["system_prompt"]}]
            for msg in state["messages"]:
                messages.append(_langchain_to_dict(msg))

            # Force final response when nearing iteration limit
            use_tools = tool_defs
            if state["iteration"] >= max_tool_iterations:
                use_tools = None
                messages.append({
                    "role": "user",
                    "content": "Summarize your findings now. Do not make any more tool calls.",
                })

            ai_message = await llm_provider.achat(
                messages=messages,
                model=model,
                tools=use_tools,
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
                        result = await tool.ainvoke(call["args"])
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

        graph = StateGraph(AgentState)
        graph.add_node("reason", reason)
        graph.add_node("execute_tools", execute_tools)
        graph.add_node("respond", respond)

        graph.add_edge(START, "reason")
        graph.add_conditional_edges("reason", should_continue)
        graph.add_edge("execute_tools", "reason")
        graph.add_edge("respond", END)
        return graph.compile()

    @staticmethod
    def _extract(state: dict) -> str:
        """Get final assistant text from state."""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                return msg.content
        return ""
