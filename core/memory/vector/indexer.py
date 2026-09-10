"""
FAISS Vector Indexer wrapper for ORBIS Phase 4.
Manages dense vector indexing, cosine similarity search, disk persistence, and ID mapping.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np


class FaissIndexer:
    """
    Local FAISS dense vector index manager.
    Uses IndexFlatIP on unit-normalized vectors for exact cosine similarity search.
    """

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self._index = None
        self._faiss_id_to_vector_id: Dict[int, str] = {}
        self._vector_id_to_faiss_id: Dict[str, int] = {}
        self._init_index()

    def _init_index(self) -> None:
        import faiss
        self._index = faiss.IndexFlatIP(self.dimension)

    @property
    def total_vectors(self) -> int:
        return self._index.ntotal if self._index is not None else 0

    @property
    def id_mapping(self) -> Dict[int, str]:
        return dict(self._faiss_id_to_vector_id)

    def add_vectors(self, vectors: List[List[float]], vector_ids: List[str]) -> None:
        """
        Adds vectors to the FAISS index and registers their string vector IDs.
        Validates dimensionality and ensures 1:1 mapping parity.
        """
        if not vectors:
            return
        if len(vectors) != len(vector_ids):
            raise ValueError(
                f"Vectors count ({len(vectors)}) does not match vector_ids count ({len(vector_ids)})."
            )

        import faiss

        # Validate dimensionality
        for i, vec in enumerate(vectors):
            if len(vec) != self.dimension:
                raise ValueError(
                    f"Vector at index {i} has dimension {len(vec)}, expected {self.dimension}."
                )

        # Convert to float32 numpy array and normalize for cosine similarity
        arr = np.array(vectors, dtype=np.float32)
        faiss.normalize_L2(arr)

        start_idx = self._index.ntotal
        self._index.add(arr)

        for offset, vid in enumerate(vector_ids):
            faiss_id = start_idx + offset
            self._faiss_id_to_vector_id[faiss_id] = vid
            self._vector_id_to_faiss_id[vid] = faiss_id

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Tuple[str, float]]:
        """
        Executes top-k cosine similarity search.
        Returns a list of (vector_id, cosine_similarity) tuples.
        Similarity ranges from -1.0 to 1.0 (1.0 = identical).
        """
        if top_k <= 0 or self.total_vectors == 0:
            return []

        if len(query_vector) != self.dimension:
            raise ValueError(
                f"Query vector dimension {len(query_vector)} does not match index dimension {self.dimension}."
            )

        import faiss

        q_arr = np.array([query_vector], dtype=np.float32)
        faiss.normalize_L2(q_arr)

        k = min(top_k, self.total_vectors)
        distances, indices = self._index.search(q_arr, k)

        results: List[Tuple[str, float]] = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx in self._faiss_id_to_vector_id:
                vid = self._faiss_id_to_vector_id[idx]
                results.append((vid, float(dist)))

        return results

    def save(self, index_path: str) -> None:
        """Saves the FAISS index binary to disk."""
        import faiss
        p = Path(index_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(p))

    def load(self, index_path: str, id_mapping: Dict[int, str]) -> None:
        """
        Loads a FAISS index from disk and restores the integer-to-string ID mapping.
        Verifies dimensionality and checks count parity between index and mapping.
        """
        p = Path(index_path)
        if not p.exists():
            raise FileNotFoundError(f"FAISS index file not found: {index_path}")

        import faiss
        loaded_index = faiss.read_index(str(p))

        if loaded_index.d != self.dimension:
            raise ValueError(
                f"Loaded index dimension ({loaded_index.d}) does not match expected ({self.dimension})."
            )

        # Count parity check
        if loaded_index.ntotal != len(id_mapping):
            raise ValueError(
                f"Parity mismatch: FAISS index has {loaded_index.ntotal} vectors, "
                f"but metadata mapping has {len(id_mapping)} records. Index may be corrupted."
            )

        self._index = loaded_index
        self._faiss_id_to_vector_id = dict(id_mapping)
        self._vector_id_to_faiss_id = {vid: fid for fid, vid in id_mapping.items()}
