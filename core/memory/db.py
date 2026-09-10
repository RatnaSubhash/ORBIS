"""
Local SQLite Database Manager for ORBIS.
Enforces Write-Ahead Logging (WAL) mode, foreign key constraints, clean connection
lifecycle management, and simple deterministic migrations.
Extended for Phase 2 Workspace Event Persistence.
"""

from datetime import datetime
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from core.types import ContextDNA, Session, WorkspaceEvent, WorkspaceEventType


class DatabaseManager:
    """Manages SQLite database connections, settings, and migrations."""

    MIGRATIONS: List[Dict[str, Any]] = [
        {
            "version": 1,
            "name": "phase1_initial_schema",
            "sql": """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    action_name TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    result_json TEXT,
                    verified INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_audit_task_id ON audit_logs(task_id);
            """,
        },
        {
            "version": 2,
            "name": "phase2_workspace_events",
            "sql": """
                CREATE TABLE IF NOT EXISTS workspace_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    event_type TEXT NOT NULL,
                    process_name TEXT,
                    process_id INTEGER,
                    window_title TEXT,
                    resource_path TEXT,
                    metadata_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_workspace_events_timestamp ON workspace_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_workspace_events_process ON workspace_events(process_name);
            """,
        },
        {
            "version": 3,
            "name": "phase3_sessions_and_context_dna",
            "sql": """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    duration_seconds REAL NOT NULL,
                    event_count INTEGER NOT NULL,
                    applications_json TEXT NOT NULL,
                    resources_json TEXT NOT NULL,
                    event_ids_json TEXT NOT NULL,
                    metadata_json TEXT,
                    confidence REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON sessions(start_time);

                CREATE TABLE IF NOT EXISTS context_dna (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dna_id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    version TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_dna_session_id ON context_dna(session_id);
            """,
        },
        {
            "version": 4,
            "name": "phase4_graph_and_vectors",
            "sql": """
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT UNIQUE NOT NULL,
                    node_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(node_type);

                CREATE TABLE IF NOT EXISTS graph_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
                    target_id TEXT NOT NULL REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
                    edge_type TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_id, target_id, edge_type)
                );

                CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id);

                CREATE TABLE IF NOT EXISTS vector_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vector_id TEXT UNIQUE NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    text_representation TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_vector_records_entity ON vector_records(entity_type, entity_id);
            """,
        },
    ]

    def __init__(self, db_path: str = "orbis.db") -> None:
        self.db_path = db_path
        self._is_memory = db_path == ":memory:"
        if not self._is_memory:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Persistent connection for in-memory DB so it doesn't vanish between context calls
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self._is_memory:
            self._memory_conn = self._create_connection()

    def _create_connection(self) -> sqlite3.Connection:
        """Creates and configures a new SQLite connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Yields a managed SQLite connection.
        Automatically commits transactions upon normal exit, or rolls back on exception.
        """
        if self._is_memory and self._memory_conn is not None:
            conn = self._memory_conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        else:
            conn = self._create_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def initialize(self) -> None:
        """Runs migrations and initializes database tables."""
        with self.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            cursor = conn.execute("SELECT version FROM schema_migrations;")
            applied_versions = {row["version"] for row in cursor.fetchall()}

            for migration in self.MIGRATIONS:
                ver = migration["version"]
                if ver not in applied_versions:
                    conn.executescript(migration["sql"])
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (?, ?);",
                        (ver, migration["name"]),
                    )

    def is_wal_mode(self) -> bool:
        """Returns True if database is in WAL journal mode."""
        with self.get_connection() as conn:
            cursor = conn.execute("PRAGMA journal_mode;")
            row = cursor.fetchone()
            return row[0].upper() == "WAL" if row else False

    def log_action(
        self,
        task_id: str,
        action_name: str,
        risk_level: str,
        parameters: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        verified: bool = False,
    ) -> int:
        """Records an action execution into the audit_logs table."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_logs (
                    task_id, action_name, risk_level, parameters_json, result_json, verified
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    task_id,
                    action_name,
                    risk_level,
                    json.dumps(parameters),
                    json.dumps(result) if result else None,
                    1 if verified else 0,
                ),
            )
            return cursor.lastrowid

    def get_audit_logs(self, task_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves audit logs, optionally filtered by task_id."""
        with self.get_connection() as conn:
            if task_id:
                cursor = conn.execute(
                    "SELECT * FROM audit_logs WHERE task_id = ? ORDER BY id ASC;",
                    (task_id,),
                )
            else:
                cursor = conn.execute("SELECT * FROM audit_logs ORDER BY id ASC;")
            return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # Phase 2 Workspace Event Methods
    # --------------------------------------------------------------------------

    def record_workspace_event(self, event: WorkspaceEvent) -> int:
        """Stores a structured workspace observation event in SQLite."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO workspace_events (
                    event_id, timestamp, event_type, process_name, process_id,
                    window_title, resource_path, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.event_type.value,
                    event.process_name,
                    event.process_id,
                    event.window_title,
                    event.resource_path,
                    json.dumps(event.metadata),
                ),
            )
            return cursor.lastrowid

    def get_workspace_events(
        self,
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[WorkspaceEvent]:
        """
        Retrieves workspace events in chronological order, optionally
        constrained to a time range and record limit.
        """
        query = "SELECT * FROM workspace_events WHERE 1=1"
        params: List[Any] = []

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time.isoformat())
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time.isoformat())

        query += " ORDER BY id ASC LIMIT ?;"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            events: List[WorkspaceEvent] = []
            for row in cursor.fetchall():
                events.append(
                    WorkspaceEvent(
                        event_id=row["event_id"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                        event_type=WorkspaceEventType(row["event_type"]),
                        process_name=row["process_name"],
                        process_id=row["process_id"],
                        window_title=row["window_title"],
                        resource_path=row["resource_path"],
                        metadata=json.loads(row["metadata_json"] or "{}"),
                    )
                )
            return events

    # --------------------------------------------------------------------------
    # Phase 3 Session & Context DNA Persistence Methods
    # --------------------------------------------------------------------------

    def record_session(self, session: Session) -> int:
        """Stores a segmented workspace session in SQLite."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions (
                    session_id, start_time, end_time, duration_seconds, event_count,
                    applications_json, resources_json, event_ids_json, metadata_json, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    session.session_id,
                    session.start_time.isoformat(),
                    session.end_time.isoformat(),
                    session.duration_seconds,
                    session.event_count,
                    json.dumps(session.applications),
                    json.dumps(session.resources),
                    json.dumps(session.event_ids),
                    json.dumps(session.session_metadata),
                    session.confidence,
                ),
            )
            return cursor.lastrowid

    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieves a single session by its unique session_id."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?;", (session_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return Session(
                session_id=row["session_id"],
                start_time=datetime.fromisoformat(row["start_time"]),
                end_time=datetime.fromisoformat(row["end_time"]),
                duration_seconds=row["duration_seconds"],
                event_count=row["event_count"],
                applications=json.loads(row["applications_json"] or "[]"),
                resources=json.loads(row["resources_json"] or "[]"),
                event_ids=json.loads(row["event_ids_json"] or "[]"),
                session_metadata=json.loads(row["metadata_json"] or "{}"),
                confidence=row["confidence"],
            )

    def get_sessions(
        self,
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Session]:
        """Retrieves sessions chronologically, with optional time range filtering."""
        query = "SELECT * FROM sessions WHERE 1=1"
        params: List[Any] = []

        if start_time:
            query += " AND start_time >= ?"
            params.append(start_time.isoformat())
        if end_time:
            query += " AND end_time <= ?"
            params.append(end_time.isoformat())

        query += " ORDER BY start_time ASC LIMIT ?;"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            sessions: List[Session] = []
            for row in cursor.fetchall():
                sessions.append(
                    Session(
                        session_id=row["session_id"],
                        start_time=datetime.fromisoformat(row["start_time"]),
                        end_time=datetime.fromisoformat(row["end_time"]),
                        duration_seconds=row["duration_seconds"],
                        event_count=row["event_count"],
                        applications=json.loads(row["applications_json"] or "[]"),
                        resources=json.loads(row["resources_json"] or "[]"),
                        event_ids=json.loads(row["event_ids_json"] or "[]"),
                        session_metadata=json.loads(row["metadata_json"] or "{}"),
                        confidence=row["confidence"],
                    )
                )
            return sessions

    def record_context_dna(self, dna: ContextDNA) -> int:
        """Stores a serialized ContextDNA record in SQLite."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO context_dna (dna_id, session_id, version, data_json)
                VALUES (?, ?, ?, ?);
                """,
                (
                    dna.dna_id,
                    dna.session_id,
                    dna.context_dna_version,
                    dna.model_dump_json(),
                ),
            )
            return cursor.lastrowid

    def get_context_dna(self, identifier: str) -> Optional[ContextDNA]:
        """Retrieves ContextDNA by dna_id or session_id."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT data_json FROM context_dna WHERE dna_id = ? OR session_id = ? ORDER BY id DESC LIMIT 1;",
                (identifier, identifier),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return ContextDNA.model_validate_json(row["data_json"])

    def get_session_events(self, session_id: str) -> List[WorkspaceEvent]:
        """
        Traceability helper: retrieves all raw source WorkspaceEvents
        associated with a session by reading its recorded event_ids.
        """
        session = self.get_session(session_id)
        if not session or not session.event_ids:
            return []

        placeholders = ",".join("?" for _ in session.event_ids)
        query = f"SELECT * FROM workspace_events WHERE event_id IN ({placeholders}) ORDER BY timestamp ASC, event_id ASC;"

        with self.get_connection() as conn:
            cursor = conn.execute(query, tuple(session.event_ids))
            events: List[WorkspaceEvent] = []
            for row in cursor.fetchall():
                events.append(
                    WorkspaceEvent(
                        event_id=row["event_id"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                        event_type=WorkspaceEventType(row["event_type"]),
                        process_name=row["process_name"],
                        process_id=row["process_id"],
                        window_title=row["window_title"],
                        resource_path=row["resource_path"],
                        metadata=json.loads(row["metadata_json"] or "{}"),
                    )
                )
            return events

    # --------------------------------------------------------------------------
    # Phase 4 Knowledge Graph & Vector Record Persistence Methods
    # --------------------------------------------------------------------------

    def record_graph_node(
        self, node_id: str, node_type: str, label: str, properties: Dict[str, Any]
    ) -> int:
        """Stores or updates a graph node in SQLite."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO graph_nodes (node_id, node_type, label, properties_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    node_type = excluded.node_type,
                    label = excluded.label,
                    properties_json = excluded.properties_json;
                """,
                (node_id, node_type, label, json.dumps(properties)),
            )
            return cursor.lastrowid

    def get_graph_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single graph node by node_id."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM graph_nodes WHERE node_id = ?;", (node_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "node_id": row["node_id"],
                "node_type": row["node_type"],
                "label": row["label"],
                "properties": json.loads(row["properties_json"] or "{}"),
            }

    def get_all_graph_nodes(self) -> List[Dict[str, Any]]:
        """Retrieves all persisted graph nodes."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM graph_nodes ORDER BY id ASC;")
            nodes: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                nodes.append(
                    {
                        "node_id": row["node_id"],
                        "node_type": row["node_type"],
                        "label": row["label"],
                        "properties": json.loads(row["properties_json"] or "{}"),
                    }
                )
            return nodes

    def record_graph_edge(
        self, source_id: str, target_id: str, edge_type: str, properties: Dict[str, Any]
    ) -> int:
        """Stores or updates a directed graph edge in SQLite."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO graph_edges (source_id, target_id, edge_type, properties_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, edge_type) DO UPDATE SET
                    properties_json = excluded.properties_json;
                """,
                (source_id, target_id, edge_type, json.dumps(properties)),
            )
            return cursor.lastrowid

    def get_graph_edges(
        self,
        source_id: Optional[str] = None,
        target_id: Optional[str] = None,
        edge_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves graph edges with optional filtering."""
        query = "SELECT * FROM graph_edges WHERE 1=1"
        params: List[Any] = []
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        if target_id:
            query += " AND target_id = ?"
            params.append(target_id)
        if edge_type:
            query += " AND edge_type = ?"
            params.append(edge_type)
        query += " ORDER BY id ASC;"

        with self.get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            edges: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                edges.append(
                    {
                        "source_id": row["source_id"],
                        "target_id": row["target_id"],
                        "edge_type": row["edge_type"],
                        "properties": json.loads(row["properties_json"] or "{}"),
                    }
                )
            return edges

    def get_all_graph_edges(self) -> List[Dict[str, Any]]:
        """Retrieves all persisted graph edges."""
        return self.get_graph_edges()

    def record_vector_record(
        self,
        vector_id: str,
        entity_type: str,
        entity_id: str,
        embedding_model: str,
        text_representation: str,
        metadata: Dict[str, Any],
    ) -> int:
        """Stores or updates a vector metadata record in SQLite."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO vector_records (
                    vector_id, entity_type, entity_id, embedding_model,
                    text_representation, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(vector_id) DO UPDATE SET
                    entity_type = excluded.entity_type,
                    entity_id = excluded.entity_id,
                    embedding_model = excluded.embedding_model,
                    text_representation = excluded.text_representation,
                    metadata_json = excluded.metadata_json;
                """,
                (
                    vector_id,
                    entity_type,
                    entity_id,
                    embedding_model,
                    text_representation,
                    json.dumps(metadata),
                ),
            )
            return cursor.lastrowid

    def get_vector_record(self, vector_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single vector record by vector_id."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM vector_records WHERE vector_id = ?;", (vector_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "vector_id": row["vector_id"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "embedding_model": row["embedding_model"],
                "text_representation": row["text_representation"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }

    def get_all_vector_records(self) -> List[Dict[str, Any]]:
        """Retrieves all persisted vector records in insertion order."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM vector_records ORDER BY id ASC;")
            records: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                records.append(
                    {
                        "vector_id": row["vector_id"],
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "embedding_model": row["embedding_model"],
                        "text_representation": row["text_representation"],
                        "metadata": json.loads(row["metadata_json"] or "{}"),
                    }
                )
            return records

    def get_vector_records_by_entity(
        self, entity_type: str, entity_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieves vector records associated with a specific entity."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM vector_records WHERE entity_type = ? AND entity_id = ? ORDER BY id ASC;",
                (entity_type, entity_id),
            )
            records: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                records.append(
                    {
                        "vector_id": row["vector_id"],
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "embedding_model": row["embedding_model"],
                        "text_representation": row["text_representation"],
                        "metadata": json.loads(row["metadata_json"] or "{}"),
                    }
                )
            return records

    def close(self) -> None:
        """Closes memory connection if open."""
        if self._memory_conn:
            self._memory_conn.close()
            self._memory_conn = None
