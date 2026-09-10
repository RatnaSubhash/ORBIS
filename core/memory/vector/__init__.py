"""
ORBIS Semantic Vector Memory Package.
Provides vector records, FAISS indexer, and semantic memory coordinator.
"""

from core.memory.vector.embeddings.base import BaseEmbeddingProvider
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.embeddings.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from core.memory.vector.indexer import FaissIndexer
from core.memory.vector.memory import SemanticMemory
from core.memory.vector.models import SearchResult, VectorRecord

__all__ = [
    "SemanticMemory",
    "VectorRecord",
    "SearchResult",
    "FaissIndexer",
    "BaseEmbeddingProvider",
    "MockEmbeddingProvider",
    "SentenceTransformerEmbeddingProvider",
]
