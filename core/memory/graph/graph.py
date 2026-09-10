"""
Workspace Knowledge Graph wrapper around NetworkX DiGraph for ORBIS Phase 4.
Provides in-memory graph representation, topological queries, and serialization.
"""

from typing import Any, Dict, List, Optional
import networkx as nx

from core.memory.graph.models import EdgeType, GraphEdge, GraphNode, NodeType


class KnowledgeGraph:
    """
    In-memory Knowledge Graph representation wrapping NetworkX DiGraph.
    Represents explicit, observable workspace relationships.
    """

    def __init__(self) -> None:
        self._graph = nx.DiGraph()

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def add_node(self, node: GraphNode) -> None:
        """Adds or updates a node in the graph."""
        self._graph.add_node(
            node.node_id,
            node_type=node.node_type.value,
            label=node.label,
            properties=node.properties,
        )

    def add_edge(self, edge: GraphEdge) -> None:
        """
        Adds a directed edge between two nodes.
        If the endpoints do not exist yet, creates placeholder nodes.
        """
        if not self._graph.has_node(edge.source_id):
            self._graph.add_node(edge.source_id, node_type="UNKNOWN", label=edge.source_id, properties={})
        if not self._graph.has_node(edge.target_id):
            self._graph.add_node(edge.target_id, node_type="UNKNOWN", label=edge.target_id, properties={})

        self._graph.add_edge(
            edge.source_id,
            edge.target_id,
            edge_type=edge.edge_type.value,
            properties=edge.properties,
        )

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Retrieves a single node by its node_id."""
        if not self._graph.has_node(node_id):
            return None
        attrs = self._graph.nodes[node_id]
        return GraphNode(
            node_id=node_id,
            node_type=NodeType(attrs.get("node_type", NodeType.SESSION.value)),
            label=attrs.get("label", node_id),
            properties=attrs.get("properties", {}),
        )

    def get_neighbors(self, node_id: str, direction: str = "out") -> List[GraphNode]:
        """
        Retrieves neighboring nodes.
        direction: 'out' for successors, 'in' for predecessors, 'both' for both.
        """
        if not self._graph.has_node(node_id):
            return []

        neighbor_ids = set()
        if direction in ("out", "both"):
            neighbor_ids.update(self._graph.successors(node_id))
        if direction in ("in", "both"):
            neighbor_ids.update(self._graph.predecessors(node_id))

        nodes: List[GraphNode] = []
        for nid in sorted(neighbor_ids):
            node = self.get_node(nid)
            if node:
                nodes.append(node)
        return nodes

    def get_session_subgraph(self, session_id: str) -> "KnowledgeGraph":
        """
        Extracts a self-contained subgraph for a given session,
        including the session node and all directly connected entities.
        """
        node_id = f"session:{session_id}" if not session_id.startswith("session:") else session_id
        subgraph = KnowledgeGraph()
        if not self._graph.has_node(node_id):
            return subgraph

        # 1. Add session node
        session_node = self.get_node(node_id)
        if session_node:
            subgraph.add_node(session_node)

        # 2. Add outgoing connected nodes and edges
        for target_id in self._graph.successors(node_id):
            target_node = self.get_node(target_id)
            if target_node:
                subgraph.add_node(target_node)
            edge_attrs = self._graph.edges[node_id, target_id]
            subgraph.add_edge(
                GraphEdge(
                    source_id=node_id,
                    target_id=target_id,
                    edge_type=EdgeType(edge_attrs["edge_type"]),
                    properties=edge_attrs.get("properties", {}),
                )
            )

        # 3. Add incoming connected nodes and edges
        for source_id in self._graph.predecessors(node_id):
            source_node = self.get_node(source_id)
            if source_node:
                subgraph.add_node(source_node)
            edge_attrs = self._graph.edges[source_id, node_id]
            subgraph.add_edge(
                GraphEdge(
                    source_id=source_id,
                    target_id=node_id,
                    edge_type=EdgeType(edge_attrs["edge_type"]),
                    properties=edge_attrs.get("properties", {}),
                )
            )

        return subgraph

    def get_session_resources(self, session_id: str) -> List[str]:
        """Retrieves observed resource paths linked to a session."""
        node_id = f"session:{session_id}" if not session_id.startswith("session:") else session_id
        if not self._graph.has_node(node_id):
            return []

        resources: List[str] = []
        for target_id in self._graph.successors(node_id):
            edge_data = self._graph.edges[node_id, target_id]
            if edge_data.get("edge_type") == EdgeType.OBSERVES.value:
                target_node = self.get_node(target_id)
                if target_node:
                    resources.append(target_node.label)
        return sorted(resources)

    def get_session_applications(self, session_id: str) -> List[str]:
        """Retrieves active application/process names linked to a session."""
        node_id = f"session:{session_id}" if not session_id.startswith("session:") else session_id
        if not self._graph.has_node(node_id):
            return []

        apps: List[str] = []
        for target_id in self._graph.successors(node_id):
            edge_data = self._graph.edges[node_id, target_id]
            if edge_data.get("edge_type") == EdgeType.USES.value:
                target_node = self.get_node(target_id)
                if target_node:
                    apps.append(target_node.label)
        return sorted(apps)

    def get_session_events(self, session_id: str) -> List[str]:
        """Retrieves event IDs contained in a session."""
        node_id = f"session:{session_id}" if not session_id.startswith("session:") else session_id
        if not self._graph.has_node(node_id):
            return []

        event_ids: List[str] = []
        for target_id in self._graph.successors(node_id):
            edge_data = self._graph.edges[node_id, target_id]
            if edge_data.get("edge_type") == EdgeType.CONTAINS.value:
                # Strip 'event:' prefix if present
                clean_id = target_id.replace("event:", "")
                event_ids.append(clean_id)
        return event_ids

    def get_session_context_dna(self, session_id: str) -> Optional[str]:
        """Retrieves Context DNA ID linked to a session."""
        node_id = f"session:{session_id}" if not session_id.startswith("session:") else session_id
        if not self._graph.has_node(node_id):
            return None

        for target_id in self._graph.successors(node_id):
            edge_data = self._graph.edges[node_id, target_id]
            if edge_data.get("edge_type") == EdgeType.HAS_CONTEXT.value:
                return target_id.replace("dna:", "")
        return None

    def get_all_nodes(self) -> List[GraphNode]:
        """Returns all nodes in the graph."""
        nodes: List[GraphNode] = []
        for node_id in sorted(self._graph.nodes):
            n = self.get_node(node_id)
            if n:
                nodes.append(n)
        return nodes

    def get_all_edges(self) -> List[GraphEdge]:
        """Returns all edges in the graph."""
        edges: List[GraphEdge] = []
        for u, v, data in sorted(self._graph.edges(data=True)):
            edges.append(
                GraphEdge(
                    source_id=u,
                    target_id=v,
                    edge_type=EdgeType(data["edge_type"]),
                    properties=data.get("properties", {}),
                )
            )
        return edges

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the graph into a dictionary for transmission or persistence."""
        return {
            "nodes": [n.model_dump() for n in self.get_all_nodes()],
            "edges": [e.model_dump() for e in self.get_all_edges()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeGraph":
        """Reconstructs a KnowledgeGraph from a serialized dictionary."""
        kg = cls()
        for nd in data.get("nodes", []):
            kg.add_node(GraphNode(**nd))
        for ed in data.get("edges", []):
            kg.add_edge(GraphEdge(**ed))
        return kg
