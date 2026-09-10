"""
Unit tests for ORBIS Phase 4 Semantic Vector Memory.
Tests embedding providers, FAISS indexing, cosine similarity, metadata mappings,
persistence / reload parity, and deterministic text representations.
"""

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import pytest

from core.memory.db import DatabaseManager
from core.memory.vector.embeddings.base import BaseEmbeddingProvider
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.indexer import FaissIndexer
from core.memory.vector.memory import SemanticMemory
from core.memory.vector.models import SearchResult, VectorRecord
from core.types import ContextDNA, Session


def test_mock_embedding_provider_contract() -> None:
    """Verifies that MockEmbeddingProvider satisfies the BaseEmbeddingProvider contract."""
    provider = MockEmbeddingProvider(dimension=384, model_name="test-mock")
    assert isinstance(provider, BaseEmbeddingProvider)
    assert provider.dimension == 384
    assert provider.model_name == "test-mock"

    vec = provider.embed_text("Hello ORBIS")
    assert len(vec) == 384
    assert all(isinstance(x, float) for x in vec)

    # Verify unit normalization: norm == 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(norm, rel=1e-4) == 1.0


def test_mock_embedding_determinism_and_differentiation() -> None:
    """Verifies that identical strings yield identical vectors, and distinct strings yield distinct vectors."""
    provider = MockEmbeddingProvider(dimension=128)

    vec1 = provider.embed_text("python asyncio event loop")
    vec2 = provider.embed_text("python asyncio event loop")
    vec3 = provider.embed_text("powershell git commit")

    # Identical inputs produce identical vectors
    assert vec1 == vec2

    # Distinct inputs produce different vectors
    assert vec1 != vec3

    # Batch embedding consistency
    batch = provider.embed_batch(["text a", "text b"])
    assert len(batch) == 2
    assert batch[0] == provider.embed_text("text a")
    assert batch[1] == provider.embed_text("text b")


def test_faiss_indexer_add_and_search() -> None:
    """Verifies adding vectors to FAISS and retrieving them with top-k cosine similarity."""
    indexer = FaissIndexer(dimension=64)
    provider = MockEmbeddingProvider(dimension=64)

    v1 = provider.embed_text("target text alpha")
    v2 = provider.embed_text("distractor beta")
    v3 = provider.embed_text("distractor gamma")

    indexer.add_vectors([v1, v2, v3], ["id_1", "id_2", "id_3"])
    assert indexer.total_vectors == 3

    # Search with exact query matching target text alpha
    q_vec = provider.embed_text("target text alpha")
    results = indexer.search(q_vec, top_k=2)

    assert len(results) == 2
    top_id, top_score = results[0]
    assert top_id == "id_1"
    # Exact match cosine similarity is ~ 1.0
    assert pytest.approx(top_score, rel=1e-3) == 1.0


def test_faiss_indexer_boundary_conditions() -> None:
    """Verifies edge cases: empty index, invalid k, and dimension mismatches."""
    indexer = FaissIndexer(dimension=64)

    # Search on empty index returns []
    assert indexer.search([0.0] * 64, top_k=5) == []

    # Search with k <= 0 returns []
    indexer.add_vectors([[0.1] * 64], ["id_single"])
    assert indexer.search([0.1] * 64, top_k=0) == []
    assert indexer.search([0.1] * 64, top_k=-1) == []

    # Query vector dimension mismatch raises ValueError
    with pytest.raises(ValueError, match="does not match index dimension"):
        indexer.search([0.1] * 32, top_k=2)

    # Adding vector with dimension mismatch raises ValueError
    with pytest.raises(ValueError, match="has dimension 10, expected 64"):
        indexer.add_vectors([[0.1] * 10], ["id_bad"])


def test_faiss_indexer_persistence_and_reload() -> None:
    """Verifies saving FAISS index to disk and reloading with metadata parity."""
    indexer = FaissIndexer(dimension=64)
    provider = MockEmbeddingProvider(dimension=64)

    v1 = provider.embed_text("record one")
    v2 = provider.embed_text("record two")
    indexer.add_vectors([v1, v2], ["rec_1", "rec_2"])

    with tempfile.TemporaryDirectory() as tmp_dir:
        index_file = str(Path(tmp_dir) / "test.faiss")
        indexer.save(index_file)
        assert Path(index_file).exists()

        # Reload into new indexer
        reloaded = FaissIndexer(dimension=64)
        reloaded.load(index_file, id_mapping=indexer.id_mapping)
        assert reloaded.total_vectors == 2

        # Search on reloaded index produces identical results
        q = provider.embed_text("record one")
        res = reloaded.search(q, top_k=1)
        assert res[0][0] == "rec_1"


def test_faiss_indexer_parity_mismatch_fails_safely() -> None:
    """Verifies that an inconsistent index count vs metadata count raises an explicit error."""
    indexer = FaissIndexer(dimension=64)
    provider = MockEmbeddingProvider(dimension=64)
    indexer.add_vectors([provider.embed_text("doc1"), provider.embed_text("doc2")], ["id1", "id2"])

    with tempfile.TemporaryDirectory() as tmp_dir:
        index_file = str(Path(tmp_dir) / "test.faiss")
        indexer.save(index_file)

        reloaded = FaissIndexer(dimension=64)
        # Pass incomplete id_mapping (1 item vs 2 in FAISS index)
        with pytest.raises(ValueError, match="Parity mismatch: FAISS index has 2 vectors, but metadata mapping has 1"):
            reloaded.load(index_file, id_mapping={0: "id1"})


def test_deterministic_text_representations() -> None:
    """Verifies that format_session_text and format_context_dna_text produce factual, explainable strings."""
    t0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
    session = Session(
        session_id="sess_text_test",
        start_time=t0,
        end_time=t0 + timedelta(minutes=10),
        duration_seconds=600.0,
        event_count=4,
        applications=["Code.exe", "chrome.exe"],
        resources=["c:\\test.py"],
        confidence=1.0,
    )

    s_text = SemanticMemory.format_session_text(session)
    assert "sess_text_test" in s_text
    assert "Code.exe" in s_text
    assert "c:\\test.py" in s_text
    assert "600.0s" in s_text

    dna = ContextDNA(
        dna_id="dna_text_test",
        session_id="sess_text_test",
        context_dna_version="1.0",
        temporal_context={"event_count": 4},
        application_context={"active_applications": ["Code.exe"], "primary_application": "Code.exe"},
        resource_context={"observed_resource_paths": ["c:\\test.py"], "resource_extensions": [".py"]},
        activity_context={"event_types": {"APPLICATION_ACTIVATED": 4}},
        privacy_context={},
        quality_indicators={"data_quality": "high"},
    )

    dna_text = SemanticMemory.format_context_dna_text(dna)
    assert "Context DNA v1.0" in dna_text
    assert "sess_text_test" in dna_text
    assert "Code.exe" in dna_text
    assert ".py" in dna_text


def test_semantic_memory_session_and_dna_indexing() -> None:
    """Verifies high-level SemanticMemory indexing, SQLite storage, and top-k search."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    provider = MockEmbeddingProvider(dimension=64)
    mem = SemanticMemory(embedding_provider=provider, db=db)

    t0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
    session = Session(
        session_id="sess_idx_1",
        start_time=t0,
        end_time=t0 + timedelta(minutes=5),
        duration_seconds=300.0,
        event_count=2,
        applications=["Code.exe"],
    )

    dna = ContextDNA(
        dna_id="dna_idx_1",
        session_id="sess_idx_1",
        context_dna_version="1.0",
        temporal_context={"event_count": 2},
        application_context={"active_applications": ["chrome.exe"], "primary_application": "chrome.exe"},
        resource_context={},
        activity_context={},
        privacy_context={},
        quality_indicators={},
    )

    # Index both entities
    rec_s = mem.index_session(session)
    rec_d = mem.index_context_dna(dna)

    assert rec_s.entity_type == "SESSION"
    assert rec_s.entity_id == "sess_idx_1"
    assert rec_d.entity_type == "CONTEXT_DNA"
    assert rec_d.entity_id == "dna_idx_1"

    # Verify SQLite persisted vector_records
    records_in_db = db.get_all_vector_records()
    assert len(records_in_db) == 2

    # Query matching session text
    results = mem.search(rec_s.text_representation, top_k=1)
    assert len(results) == 1
    assert results[0].entity_id == "sess_idx_1"
    assert results[0].entity_type == "SESSION"
    assert pytest.approx(results[0].similarity, rel=1e-3) == 1.0


def test_semantic_memory_save_and_reload() -> None:
    """Verifies end-to-end saving of FAISS vector index and re-populating metadata from SQLite."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    provider = MockEmbeddingProvider(dimension=64)

    with tempfile.TemporaryDirectory() as tmp_dir:
        idx_path = str(Path(tmp_dir) / "vectors.faiss")
        mem1 = SemanticMemory(embedding_provider=provider, db=db, index_path=idx_path)

        t0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
        session = Session(
            session_id="sess_persist",
            start_time=t0,
            end_time=t0 + timedelta(minutes=1),
            duration_seconds=60.0,
            event_count=1,
            applications=["notepad.exe"],
        )
        mem1.index_session(session)
        mem1.save()

        # Create fresh SemanticMemory instance connected to same SQLite DB
        mem2 = SemanticMemory(embedding_provider=provider, db=db, index_path=idx_path)
        mem2.load()

        assert mem2.indexer.total_vectors == 1
        query_text = SemanticMemory.format_session_text(session)
        results = mem2.search(query_text, top_k=1)
        assert len(results) == 1
        assert results[0].entity_id == "sess_persist"
