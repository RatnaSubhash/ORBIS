"""
Base Embedding Provider abstraction for ORBIS Phase 4.
"""

from abc import ABC, abstractmethod
from typing import List


class BaseEmbeddingProvider(ABC):
    """Abstract base class for all vector embedding providers."""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """Embeds a single string into a dense vector."""
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embeds a list of strings into dense vectors."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Returns the dimensionality of the embedding vector."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Returns the model name or identifier."""
        pass
