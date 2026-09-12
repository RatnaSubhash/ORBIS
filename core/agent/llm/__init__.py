"""
LLM Abstraction Subsystem for ORBIS.
"""

from core.agent.llm.base import BaseLLMProvider
from core.agent.llm.factory import get_llm_provider
from core.agent.llm.gemini import GeminiProvider
from core.agent.llm.mock import MockLLMProvider
from core.agent.llm.ollama import OllamaProvider

__all__ = [
    "BaseLLMProvider",
    "OllamaProvider",
    "GeminiProvider",
    "MockLLMProvider",
    "get_llm_provider",
]
