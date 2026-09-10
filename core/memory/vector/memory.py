"""
Semantic Memory coordinator for ORBIS Phase 4.
Manages deterministic text representations, embedding providers, FAISS indexing,
and SQLite vector metadata persistence.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.config import get_settings
from core.memory.db import DatabaseManager
from core.memory.vector.embeddings.base import BaseEmbeddingProvider
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.indexer import FaissIndexer
from core.memory.vector.models import SearchResult, VectorRecord
from core.types import ContextDNA, Session

logger = logging.getLogger("orbis.memory.vector")


class SemanticMemory:
    """
    Semantic Vector Memory subsystem.
    Indexes factual text representations of Sessions and Context DNA,
    providing fast cosine-similarity search backed by FAISS and SQLite.
    """

    def __init__(
        self,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        db: Optional[DatabaseManager] = None,
        index_path: Optional[str] = None,
    ) -> None:
        self.provider = (
            embedding_provider
            if embedding_provider is not None
            else MockEmbeddingProvider()
        )
        self.db = db
        settings = get_settings()
        self.index_path = index_path or settings.vector_index_path
        self.indexer = FaissIndexer(dimension=self.provider.dimension)
        self._records: Dict[str, VectorRecord] = {}

    @staticmethod
    def format_session_text(session: Session) -> str:
        """Constructs an explainable, deterministic text representation of a Session."""
        apps_str = ", ".join(session.applications) if session.applications else "None"
        resources_str = ", ".join(session.resources) if session.resources else "None"
        return (
            f"Session: {session.session_id}\n"
            f"Applications: {apps_str}\n"
            f"Resources: {resources_str}\n"
            f"Duration: {session.duration_seconds:.1f}s\n"
            f"Event Count: {session.event_count}\n"
            f"Heuristic Score: {session.confidence}"
        )

    @staticmethod
    def format_context_dna_text(dna: ContextDNA) -> str:
        """Constructs an explainable, deterministic text representation of Context DNA."""
        app_ctx = dna.application_context or {}
        res_ctx = dna.resource_context or {}
        act_ctx = dna.activity_context or {}
        qual_ctx = dna.quality_indicators or {}

        apps = ", ".join(app_ctx.get("active_applications", [])) or "None"
        primary_app = app_ctx.get("primary_application") or "None"
        resources = ", ".join(res_ctx.get("observed_resource_paths", [])) or "None"
        extensions = ", ".join(res_ctx.get("resource_extensions", [])) or "None"
        event_types = str(act_ctx.get("event_types", {}))
        quality = qual_ctx.get("data_quality", "normal")

        return (
            f"Context DNA v{dna.context_dna_version}\n"
            f"Session: {dna.session_id}\n"
            f"Primary App: {primary_app}\n"
            f"Applications: {apps}\n"
            f"Observed Resources: {resources}\n"
            f"File Extensions: {extensions}\n"
            f"Activity Event Types: {event_types}\n"
            f"Quality: {quality}"
        )

    def index_session(self, session: Session) -> VectorRecord:
        """Creates a text representation, generates an embedding, and registers a Session."""
        text = self.format_session_text(session)
        record = VectorRecord(
            vector_id=uuid4().hex,
            entity_type="SESSION",
            entity_id=session.session_id,
            embedding_model=self.provider.model_name,
            text_representation=text,
            metadata={
                "session_id": session.session_id,
                "applications": session.applications,
                "resources": session.resources,
                "duration_seconds": session.duration_seconds,
            },
        )
        self._index_record(record)
        return record

    def index_context_dna(self, dna: ContextDNA) -> VectorRecord:
        """Creates a text representation, generates an embedding, and registers Context DNA."""
        text = self.format_context_dna_text(dna)
        record = VectorRecord(
            vector_id=uuid4().hex,
            entity_type="CONTEXT_DNA",
            entity_id=dna.dna_id,
            embedding_model=self.provider.model_name,
            text_representation=text,
            metadata={
                "dna_id": dna.dna_id,
                "session_id": dna.session_id,
                "version": dna.context_dna_version,
                "primary_application": (dna.application_context or {}).get("primary_application"),
            },
        )
        self._index_record(record)
        return record

    def _index_record(self, record: VectorRecord) -> None:
        """Embeds text representation, adds to FAISS index, and persists to SQLite if db is present."""
        vector = self.provider.embed_text(record.text_representation)
        self.indexer.add_vectors([vector], [record.vector_id])
        self._records[record.vector_id] = record

        if self.db:
            self.db.record_vector_record(
                vector_id=record.vector_id,
                entity_type=record.entity_type,
                entity_id=record.entity_id,
                embedding_model=record.embedding_model,
                text_representation=record.text_representation,
                metadata=record.metadata,
            )

    def search(self, query: str, top_k: int = 5) -> List[SearchResult]:
        """
        Performs semantic similarity search for a query string.
        Returns top-k ranked SearchResult instances with cosine similarity scores.
        """
        if top_k <= 0 or self.indexer.total_vectors == 0:
            return []

        query_vector = self.provider.embed_text(query)
        matches = self.indexer.search(query_vector, top_k=top_k)

        results: List[SearchResult] = []
        for vector_id, similarity in matches:
            record = self._records.get(vector_id)
            if not record and self.db:
                # Fallback to SQLite
                db_rec = self.db.get_vector_record(vector_id)
                if db_rec:
                    record = VectorRecord(
                        vector_id=db_rec["vector_id"],
                        entity_type=db_rec["entity_type"],
                        entity_id=db_rec["entity_id"],
                        embedding_model=db_rec["embedding_model"],
                        text_representation=db_rec["text_representation"],
                        metadata=db_rec["metadata"],
                    )
                    self._records[vector_id] = record

            if record:
                results.append(
                    SearchResult(
                        entity_id=record.entity_id,
                        entity_type=record.entity_type,
                        similarity=similarity,
                        source_metadata=record.metadata,
                        text_representation=record.text_representation,
                    )
                )

        return results

    def save(self, index_path: Optional[str] = None) -> None:
        """Saves the FAISS vector index to disk."""
        target_path = index_path or self.index_path
        self.indexer.save(target_path)

    def load(self, index_path: Optional[str] = None) -> None:
        """
        Reloads the FAISS vector index and reconstructs memory from SQLite metadata.
        Fails safely if index and SQLite metadata counts mismatch.
        """
        target_path = index_path or self.index_path
        if not self.db:
            raise ValueError("Cannot reload SemanticMemory without DatabaseManager instance.")

        db_records = self.db.get_all_vector_records()
        id_mapping: Dict[int, str] = {}
        for idx, rec in enumerate(db_records):
            id_mapping[idx] = rec["vector_id"]
            self._records[rec["vector_id"]] = VectorRecord(
                vector_id=rec["vector_id"],
                entity_type=rec["entity_type"],
                entity_id=rec["entity_id"],
                embedding_model=rec["embedding_model"],
                text_representation=rec["text_representation"],
                metadata=rec["metadata"],
            )

        self.indexer.load(target_path, id_mapping)
