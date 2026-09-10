"""
LLM Abstraction Subsystem for ORBIS.
"""

from core.agent.llm.base import BaseLLMProvider
from core.agent.llm.mock import MockLLMProvider
from core.agent.llm.ollama import OllamaProvider

__all__ = ["BaseLLMProvider", "OllamaProvider", "MockLLMProvider"]
