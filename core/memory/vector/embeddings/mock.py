"""
Mock Embedding Provider for ORBIS Phase 4.
Produces deterministic, normalized pseudo-vectors from text hashes
for 100% offline, reproducible testing and infrastructure verification.
"""

import hashlib
import math
from typing import List

from core.memory.vector.embeddings.base import BaseEmbeddingProvider


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """
    Deterministic mock embedding provider.
    Uses cryptographic hashing to generate reproducible, unit-normalized vectors.
    """

    def __init__(self, dimension: int = 384, model_name: str = "mock-embedding-384") -> None:
        self._dimension = dimension
        self._model_name = model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_text(self, text: str) -> List[float]:
        """Generates a deterministic L2-normalized vector from text."""
        # Use SHA-256 with counter suffixes to generate pseudo-random float values
        values: List[float] = []
        clean_text = text.strip() if text else "empty"

        counter = 0
        while len(values) < self._dimension:
            seed = f"{clean_text}:{counter}".encode("utf-8")
            digest = hashlib.sha256(seed).digest()
            # Unpack bytes into floats between -1.0 and 1.0
            for i in range(0, len(digest) - 1, 2):
                if len(values) >= self._dimension:
                    break
                val = int.from_bytes(digest[i:i+2], byteorder="big", signed=True)
                values.append(val / 32768.0)
            counter += 1

        # L2-normalize vector to unit length
        norm = math.sqrt(sum(x * x for x in values))
        if norm > 0:
            return [x / norm for x in values]
        return [0.0] * self._dimension

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch embedding generation."""
        return [self.embed_text(t) for t in texts]
