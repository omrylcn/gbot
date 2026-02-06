"""Configuration module."""

from graphbot.core.config.loader import load_config
from graphbot.core.config.schema import Config

__all__ = ["Config", "load_config"]
