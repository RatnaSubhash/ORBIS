"""
Sentence Transformers Embedding Provider for ORBIS Phase 4.
Wraps local HuggingFace / Sentence Transformers models for dense semantic vector generation.
"""

import logging
from typing import List, Optional

from core.config import get_settings
from core.memory.vector.embeddings.base import BaseEmbeddingProvider

logger = logging.getLogger("orbis.memory.vector.embeddings.st")


class SentenceTransformerEmbeddingProvider(BaseEmbeddingProvider):
    """
    Production embedding provider wrapping SentenceTransformers.
    Generates unit-normalized dense vectors suitable for cosine similarity via FAISS.
    """

    def __init__(self, model_name: Optional[str] = None) -> None:
        if model_name is not None:
            self._model_name = model_name
        else:
            self._model_name = get_settings().embedding_model_name
        self._model = None
        self._dimension: Optional[int] = None

    def _load_model(self):
        """Lazy loads the SentenceTransformer model on first invocation."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading SentenceTransformer model: %s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            self._dimension = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._load_model()
        return self._dimension

    def embed_text(self, text: str) -> List[float]:
        """Embeds a single string into a dense unit-normalized vector."""
        model = self._load_model()
        vector = model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch embeds strings into dense unit-normalized vectors."""
        if not texts:
            return []
        model = self._load_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()
