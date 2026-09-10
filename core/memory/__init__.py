"""
Memory Subsystem for ORBIS.
Phase 1: Local SQLite database manager with WAL mode and schema migrations.
"""

from core.memory.db import DatabaseManager

__all__ = ["DatabaseManager"]
