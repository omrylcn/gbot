"""Context package — builder, models, and service for agent context management."""

from gbot.agent.context.builder import ContextBuilder
from gbot.agent.context.models import ContextOverride, LayerResult
from gbot.agent.context.service import ContextService

__all__ = ["ContextBuilder", "ContextOverride", "ContextService", "LayerResult"]
