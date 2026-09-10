"""
Knowledge Graph Builder for ORBIS Phase 4.
Constructs factual graph nodes and relationships from Session, ContextDNA,
and WorkspaceEvent records, with persistence to/from SQLite.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from core.types import ContextDNA, Session, WorkspaceEvent


class GraphBuilder:
    """
    Deterministic builder that transforms factual workspace evidence
    into strongly typed Knowledge Graph nodes and edges.
    """

    @staticmethod
    def normalize_app_id(process_name: str) -> str:
        """Generates a stable normalized node ID for an application."""
        clean = process_name.strip().lower() if process_name else "unknown"
        return f"app:{clean}"

    @staticmethod
    def normalize_resource_id(resource_path: str) -> str:
        """Generates a stable normalized node ID for a resource."""
        clean = resource_path.strip().replace("\\", "/").lower() if resource_path else "unknown"
        return f"resource:{clean}"

    @staticmethod
    def normalize_session_id(session_id: str) -> str:
        return f"session:{session_id}" if not session_id.startswith("session:") else session_id

    @staticmethod
    def normalize_event_id(event_id: str) -> str:
        return f"event:{event_id}" if not event_id.startswith("event:") else event_id

    @staticmethod
    def normalize_dna_id(dna_id: str) -> str:
        return f"dna:{dna_id}" if not dna_id.startswith("dna:") else dna_id

    def build_from_session(
        self,
        session: Session,
        dna: Optional[ContextDNA] = None,
        events: Optional[List[WorkspaceEvent]] = None,
        graph: Optional[KnowledgeGraph] = None,
    ) -> KnowledgeGraph:
        """
        Populates a KnowledgeGraph with entities and relationships
        derived from a Session, optional ContextDNA, and source WorkspaceEvents.
        """
        kg = graph if graph is not None else KnowledgeGraph()

        # 1. Session Node
        session_node_id = self.normalize_session_id(session.session_id)
        kg.add_node(
            GraphNode(
                node_id=session_node_id,
                node_type=NodeType.SESSION,
                label=f"Session {session.session_id[:8]}",
                properties={
                    "session_id": session.session_id,
                    "start_time": session.start_time.isoformat(),
                    "end_time": session.end_time.isoformat(),
                    "duration_seconds": session.duration_seconds,
                    "event_count": session.event_count,
                    "confidence": session.confidence,
                },
            )
        )

        # 2. Application Nodes & USES Edges
        for app in session.applications:
            if not app:
                continue
            app_node_id = self.normalize_app_id(app)
            kg.add_node(
                GraphNode(
                    node_id=app_node_id,
                    node_type=NodeType.APPLICATION,
                    label=app,
                    properties={"process_name": app},
                )
            )
            kg.add_edge(
                GraphEdge(
                    source_id=session_node_id,
                    target_id=app_node_id,
                    edge_type=EdgeType.USES,
                    properties={},
                )
            )

        # 3. Resource Nodes & OBSERVES Edges
        for res in session.resources:
            if not res:
                continue
            res_node_id = self.normalize_resource_id(res)
            extension = Path(res).suffix.lower() if res else ""
            kg.add_node(
                GraphNode(
                    node_id=res_node_id,
                    node_type=NodeType.RESOURCE,
                    label=res,
                    properties={"resource_path": res, "extension": extension},
                )
            )
            kg.add_edge(
                GraphEdge(
                    source_id=session_node_id,
                    target_id=res_node_id,
                    edge_type=EdgeType.OBSERVES,
                    properties={},
                )
            )

        # 4. Context DNA Node & HAS_CONTEXT Edge
        if dna:
            dna_node_id = self.normalize_dna_id(dna.dna_id)
            kg.add_node(
                GraphNode(
                    node_id=dna_node_id,
                    node_type=NodeType.CONTEXT_DNA,
                    label=f"Context DNA v{dna.context_dna_version}",
                    properties={
                        "dna_id": dna.dna_id,
                        "session_id": dna.session_id,
                        "version": dna.context_dna_version,
                        "created_at": dna.created_at.isoformat(),
                    },
                )
            )
            kg.add_edge(
                GraphEdge(
                    source_id=session_node_id,
                    target_id=dna_node_id,
                    edge_type=EdgeType.HAS_CONTEXT,
                    properties={"version": dna.context_dna_version},
                )
            )

        # 5. Workspace Event Nodes & Interconnected Edges
        if events:
            for event in events:
                # Filter events to those in this session if session has event_ids
                if session.event_ids and event.event_id not in session.event_ids:
                    continue

                event_node_id = self.normalize_event_id(event.event_id)
                kg.add_node(
                    GraphNode(
                        node_id=event_node_id,
                        node_type=NodeType.WORKSPACE_EVENT,
                        label=f"{event.event_type.value} ({event.process_name or 'system'})",
                        properties={
                            "event_id": event.event_id,
                            "event_type": event.event_type.value,
                            "timestamp": event.timestamp.isoformat(),
                            "process_name": event.process_name,
                            "window_title": event.window_title,
                            "resource_path": event.resource_path,
                        },
                    )
                )

                # SESSION --contains--> WORKSPACE_EVENT
                kg.add_edge(
                    GraphEdge(
                        source_id=session_node_id,
                        target_id=event_node_id,
                        edge_type=EdgeType.CONTAINS,
                        properties={},
                    )
                )

                # WORKSPACE_EVENT --occurred_in--> SESSION
                kg.add_edge(
                    GraphEdge(
                        source_id=event_node_id,
                        target_id=session_node_id,
                        edge_type=EdgeType.OCCURRED_IN,
                        properties={},
                    )
                )

                # WORKSPACE_EVENT --occurred_in--> APPLICATION
                if event.process_name:
                    app_node_id = self.normalize_app_id(event.process_name)
                    kg.add_edge(
                        GraphEdge(
                            source_id=event_node_id,
                            target_id=app_node_id,
                            edge_type=EdgeType.OCCURRED_IN,
                            properties={},
                        )
                    )

                # WORKSPACE_EVENT --references--> RESOURCE
                if event.resource_path:
                    res_node_id = self.normalize_resource_id(event.resource_path)
                    kg.add_edge(
                        GraphEdge(
                            source_id=event_node_id,
                            target_id=res_node_id,
                            edge_type=EdgeType.REFERENCES,
                            properties={},
                        )
                    )

        return kg

    def persist_graph(self, db: DatabaseManager, graph: KnowledgeGraph) -> None:
        """Saves all nodes and edges from KnowledgeGraph into SQLite."""
        for node in graph.get_all_nodes():
            db.record_graph_node(
                node_id=node.node_id,
                node_type=node.node_type.value,
                label=node.label,
                properties=node.properties,
            )

        for edge in graph.get_all_edges():
            db.record_graph_edge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                edge_type=edge.edge_type.value,
                properties=edge.properties,
            )

    def load_graph(self, db: DatabaseManager) -> KnowledgeGraph:
        """Reconstructs a KnowledgeGraph from SQLite graph tables."""
        kg = KnowledgeGraph()
        nodes = db.get_all_graph_nodes()
        for nd in nodes:
            kg.add_node(
                GraphNode(
                    node_id=nd["node_id"],
                    node_type=NodeType(nd["node_type"]),
                    label=nd["label"],
                    properties=nd["properties"],
                )
            )

        edges = db.get_all_graph_edges()
        for ed in edges:
            kg.add_edge(
                GraphEdge(
                    source_id=ed["source_id"],
                    target_id=ed["target_id"],
                    edge_type=EdgeType(ed["edge_type"]),
                    properties=ed["properties"],
                )
            )
        return kg
