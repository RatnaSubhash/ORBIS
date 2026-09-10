"""
Unit tests for DatabaseManager (SQLite with WAL mode and schema migrations).
"""

import tempfile
from pathlib import Path
import pytest

from core.memory.db import DatabaseManager


def test_sqlite_in_memory_initialization() -> None:
    """Verifies that an in-memory database initializes tables and migrations correctly."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    with db.get_connection() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        )
        tables = [row["name"] for row in cursor.fetchall()]
        assert "schema_migrations" in tables
        assert "audit_logs" in tables

        # Verify migrations record
        cursor = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version ASC;")
        migrations = cursor.fetchall()
        assert len(migrations) >= 1
        assert migrations[0]["version"] == 1
        assert migrations[0]["name"] == "phase1_initial_schema"


def test_sqlite_wal_mode_on_disk() -> None:
    """Verifies that WAL mode is activated on file-backed databases."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_wal.db")
        db = DatabaseManager(db_path=db_path)
        db.initialize()

        assert db.is_wal_mode() is True

        with db.get_connection() as conn:
            cursor = conn.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
            assert mode.upper() == "WAL"


def test_sqlite_audit_logging() -> None:
    """Verifies that action execution audit records are stored and queryable."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    log_id = db.log_action(
        task_id="task_123",
        action_name="test_tool",
        risk_level="LOW",
        parameters={"foo": "bar"},
        result={"output": "success"},
        verified=True,
    )
    assert log_id > 0

    logs = db.get_audit_logs(task_id="task_123")
    assert len(logs) == 1
    assert logs[0]["task_id"] == "task_123"
    assert logs[0]["action_name"] == "test_tool"
    assert logs[0]["risk_level"] == "LOW"
    assert logs[0]["verified"] == 1
    assert "foo" in logs[0]["parameters_json"]


def test_sqlite_transaction_rollback() -> None:
    """Verifies that errors inside get_connection rollback the transaction."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    with pytest.raises(ValueError):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (999, 'bad_migration');"
            )
            raise ValueError("Intentional trigger for rollback")

    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM schema_migrations WHERE version = 999;")
        assert cursor.fetchone() is None
