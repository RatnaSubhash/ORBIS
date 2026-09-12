"""
Configuration-driven LLM Provider Factory for ORBIS.
Resolves and instantiates BaseLLMProvider implementations without silent fallbacks.
"""

from typing import Optional

from core.agent.llm.base import BaseLLMProvider
from core.agent.llm.mock import MockLLMProvider
from core.agent.llm.ollama import OllamaProvider
from core.config import get_settings


def get_llm_provider(provider_type: Optional[str] = None) -> BaseLLMProvider:
    """
    Factory function resolving and returning the configured BaseLLMProvider.
    Explicitly checks configuration without silent fallbacks across different backends.
    """
    resolved_type = (provider_type or get_settings().llm_provider).lower().strip()

    if resolved_type == "ollama":
        return OllamaProvider()
    elif resolved_type == "gemini":
        from core.agent.llm.gemini import GeminiProvider

        return GeminiProvider()
    elif resolved_type == "mock":
        return MockLLMProvider()
    else:
        raise ValueError(
            f"Unsupported LLM provider: '{resolved_type}'. "
            "Supported providers are 'ollama', 'gemini', 'mock'."
        )
