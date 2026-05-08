"""Graph nodes — load_context, reason, execute_tools, respond."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, SystemMessage
from loguru import logger

from gbot.agent.state import AgentState
from gbot.core.config.schema import Config
from gbot.core.providers import litellm as llm_provider
from gbot.agent.context import ContextBuilder
from gbot.memory.store import MemoryStore


def make_nodes(config: Config, db: MemoryStore, tools: list | None = None, embedder=None):
    """
    Create node functions closed over config, db, and tools.

    Returns dict of {node_name: callable} for graph registration.
    """
    ctx_builder = ContextBuilder(config, db, embedder=embedder)
    tool_defs = _build_tool_definitions(tools) if tools else None
    tool_map = {t.name: t for t in tools} if tools else {}

    async def load_context(state: AgentState) -> dict[str, Any]:
        """Build system prompt from SQLite + workspace.

        When skip_context is True, only the identity layer is loaded
        (no user context, memory, skills, etc.). Used by background tasks.
        Context layers can be restricted via RBAC (state["context_layers"]).
        """
        if state.get("skip_context"):
            prompt = ctx_builder._get_identity()
            logger.debug(f"Lightweight context (identity only) for user {state['user_id']}")
        else:
            layers = state.get("context_layers")
            # Get last user message for semantic retrieval
            last_msg = None
            from langchain_core.messages import HumanMessage
            for msg in reversed(state.get("messages", [])):
                if isinstance(msg, HumanMessage) and msg.content:
                    last_msg = msg.content
                    break
            prompt = ctx_builder.build(
                state["user_id"], context_layers=layers, last_message=last_msg
            )
            logger.debug(
                f"Context built for user {state['user_id']} "
                f"(role={state.get('role', '?')}, layers={len(layers) if layers else 'all'})"
            )
        return {"system_prompt": prompt}

    async def reason(state: AgentState) -> dict[str, Any]:
        """Call LLM with messages + tools (filtered by role)."""
        # Build the system message — Faz 22E adds prompt-caching support
        # for Anthropic/Gemini via LiteLLM's auto-inject (PR #15345).
        system_msg = _build_system_message(state["system_prompt"], config)

        messages: list[dict[str, Any]] = [system_msg]
        for msg in state["messages"]:
            messages.append(_langchain_to_dict(msg))

        # RBAC: filter tool definitions by allowed_tools
        allowed = state.get("allowed_tools")
        if allowed is not None and tool_defs:
            filtered_defs = [
                d for d in tool_defs if d["function"]["name"] in allowed
            ]
        else:
            filtered_defs = tool_defs

        ai_message = await llm_provider.achat(
            messages=messages,
            model=config.assistant.model,
            tools=filtered_defs or None,
            temperature=config.assistant.temperature,
            api_base=config.get_api_base(),
            thinking=config.assistant.thinking,
        )

        # Log tool calls for debugging
        if ai_message.tool_calls:
            names = [tc["name"] for tc in ai_message.tool_calls]
            logger.debug(f"LLM tool calls: {names}")
        else:
            snippet = (ai_message.content or "")[:80]
            logger.debug(f"LLM response (no tools): {snippet!r}")

        # Faz 22E — log cache hit/miss telemetry if available
        _log_cache_telemetry(ai_message)

        return {
            "messages": [ai_message],
            "iteration": state["iteration"] + 1,
        }

    async def execute_tools(state: AgentState) -> dict[str, Any]:
        """Execute tool calls from the last AI message (with RBAC guard)."""
        from langchain_core.messages import ToolMessage

        last_msg = state["messages"][-1]
        results = []
        allowed = state.get("allowed_tools")

        for call in last_msg.tool_calls:
            # RBAC guard: reject unauthorized tool calls
            if allowed is not None and call["name"] not in allowed:
                result = (
                    f"Permission denied: '{call['name']}' is not available "
                    f"for role '{state.get('role', 'unknown')}'."
                )
                logger.warning(
                    f"RBAC denied: user={state['user_id']}, "
                    f"role={state.get('role')}, tool={call['name']}"
                )
            elif (tool := tool_map.get(call["name"])) is None:
                result = f"Tool '{call['name']}' not found"
                logger.warning(f"Tool not found: {call['name']}")
            else:
                try:
                    args = call["args"].copy()
                    # Clean up malformed LLM args (e.g. {"raw": "..."})
                    args.pop("raw", None)
                    # Inject state context into tools that accept these params
                    tool_fields = set(tool.args_schema.model_fields) if tool.args_schema else set()
                    if "channel" in tool_fields and not args.get("channel"):
                        args["channel"] = state["channel"]
                        logger.debug(
                            f"Channel inject: tool={call['name']}, "
                            f"→ {state['channel']!r}"
                        )
                    # Pre-validate: detect empty args for tools with required params
                    auto_keys = {"channel"}
                    user_args = {k: v for k, v in args.items() if k not in auto_keys}
                    required = set()
                    if tool.args_schema:
                        for k, f in tool.args_schema.model_fields.items():
                            if k not in auto_keys and f.is_required():
                                required.add(k)
                    if required and not user_args:
                        param_hints = ", ".join(sorted(required))
                        example_args = ", ".join(
                            f'{k}="..."' for k in sorted(required)
                        )
                        result = (
                            f"Error: {call['name']} requires: {param_hints}. "
                            f"Example: {call['name']}({example_args})"
                        )
                        logger.warning(f"Empty args: {call['name']} needs {required}")
                    else:
                        logger.debug(f"Executing tool: {call['name']}({args})")
                        result = await tool.ainvoke(args)
                        logger.debug(f"Tool result: {call['name']} → {str(result)[:100]}")
                except Exception as e:
                    result = f"Tool error: {e}"
                    logger.error(f"Tool error: {call['name']} → {e}")

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


def _build_system_message(system_prompt: str, config: Config) -> dict[str, Any]:
    """Build the system message, optionally with prompt-cache breakpoint.

    Faz 22E — When the active model supports prompt caching (Anthropic
    family, Gemini 2.5+, accessed via OpenRouter or directly), we mark the
    system prompt as cacheable using Anthropic's content-block format:

        {"role": "system", "content": [
            {"type": "text", "text": "...",
             "cache_control": {"type": "ephemeral"}}
        ]}

    LiteLLM PR #15345 transforms this into the right format per provider
    transparently. For unsupported providers (or when the prompt is too
    short to benefit), we fall back to the plain string format.

    Sticking the breakpoint at the END of the system prompt means
    everything before it is cached — which works for us because the
    dynamic part (retrieved memory facts) is currently inside the same
    system block. v1.22.0 plans to split static/dynamic into two system
    blocks for better cache hit rates.
    """
    cache_cfg = getattr(config.assistant, "prompt_cache", None)
    if cache_cfg is None or not cache_cfg.enabled:
        return {"role": "system", "content": system_prompt}

    model = config.assistant.model or ""
    if not _model_supports_caching(model, cache_cfg):
        return {"role": "system", "content": system_prompt}

    if len(system_prompt) < cache_cfg.min_chars_to_cache:
        return {"role": "system", "content": system_prompt}

    return {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }


def _model_supports_caching(model: str, cache_cfg: Any) -> bool:
    """Check whether ``model`` is on the cache-supported allowlist."""
    if model in cache_cfg.excluded_models:
        return False
    return any(model.startswith(prefix) for prefix in cache_cfg.supported_prefixes)


def _log_cache_telemetry(ai_message: AIMessage) -> None:
    """Log cache_creation_tokens / cache_read_tokens when present.

    Anthropic returns these in usage; LiteLLM normalises the shape.
    Surfacing them on every turn lets us measure the actual savings
    once Step 3 (benchmark suite) is in place.
    """
    try:
        usage = (ai_message.response_metadata or {}).get("usage", {}) or {}
        creation = usage.get("cache_creation_input_tokens") or usage.get(
            "cache_creation_tokens"
        )
        read = usage.get("cache_read_input_tokens") or usage.get("cache_read_tokens")
        if creation or read:
            logger.info(
                f"prompt cache: creation={creation or 0}, read={read or 0}"
            )
    except Exception:  # pragma: no cover — defensive, telemetry-only
        pass


def _langchain_to_dict(msg: Any) -> dict[str, Any]:
    """Convert LangChain message to dict for litellm."""
    from langchain_core.messages import HumanMessage, ToolMessage

    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": msg.content}
    elif isinstance(msg, AIMessage):
        d: dict[str, Any] = {"role": "assistant", "content": msg.content}
        # Preserve reasoning_content for thinking models
        reasoning = msg.additional_kwargs.get("reasoning_content")
        if reasoning:
            d["reasoning_content"] = reasoning
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
    from langchain_core.utils.function_calling import convert_to_openai_function

    return [
        {"type": "function", "function": convert_to_openai_function(t)}
        for t in tools
    ]
