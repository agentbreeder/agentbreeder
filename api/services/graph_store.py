"""GraphStore — In-memory knowledge graph store singleton.

Provides:
- Node and edge upsert with deduplication
- BFS multi-hop neighbor traversal
- Paginated listing with optional filters
- Subgraph deletion by index
"""

from __future__ import annotations

import copy
import logging
from collections import deque

from api.services.rag_service import GraphEdge, GraphNode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalization helper
# ---------------------------------------------------------------------------


def _normalize_entity_name(name: str) -> str:
    """Lowercase, strip, collapse internal whitespace."""
    return " ".join(name.strip().lower().split())


# ---------------------------------------------------------------------------
# GraphStore
# ---------------------------------------------------------------------------


class GraphStore:
    """In-memory store for knowledge graph nodes and edges.

    Keyed by index_id so multiple RAG graph indexes can coexist.
    """

    def __init__(self) -> None:
        # _nodes: dict[index_id, dict[node_id, GraphNode]]
        self._nodes: dict[str, dict[str, GraphNode]] = {}
        # _edges: dict[index_id, dict[edge_id, GraphEdge]]
        self._edges: dict[str, dict[str, GraphEdge]] = {}
        # _entity_name_index: dict[index_id, dict[normalized_name, node_id]]
        self._entity_name_index: dict[str, dict[str, str]] = {}
        # _triple_index: dict[index_id, dict[triple_key, edge_id]] — O(1) dedup
        self._triple_index: dict[str, dict[tuple[str, str, str], str]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_index(self, index_id: str) -> None:
        """Ensure dicts exist for this index_id."""
        if index_id not in self._nodes:
            self._nodes[index_id] = {}
        if index_id not in self._edges:
            self._edges[index_id] = {}
        if index_id not in self._entity_name_index:
            self._entity_name_index[index_id] = {}
        if index_id not in self._triple_index:
            self._triple_index[index_id] = {}

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def upsert_node(self, index_id: str, node: GraphNode) -> GraphNode:
        """Insert or merge node.

        If a node with the same normalized entity name already exists for
        this index, merge chunk_ids (union) and update description if the
        incoming description is non-empty. The existing node keeps its
        original id. Returns the (possibly merged) node.
        """
        self._ensure_index(index_id)

        norm_name = _normalize_entity_name(node.entity)
        name_idx = self._entity_name_index[index_id]

        if norm_name in name_idx:
            # Merge into existing node
            existing_id = name_idx[norm_name]
            existing = self._nodes[index_id][existing_id]
            # Union chunk_ids
            merged_chunks = list(set(existing.chunk_ids) | set(node.chunk_ids))
            existing.chunk_ids = merged_chunks
            # Update description if incoming is non-empty
            if node.description:
                existing.description = node.description
            # Update entity_type if incoming is non-empty
            if node.entity_type:
                existing.entity_type = node.entity_type
            # Update embedding if provided
            if node.embedding is not None:
                existing.embedding = node.embedding
            logger.debug(
                "GraphStore: merged node '%s' into existing id=%s (index=%s)",
                node.entity,
                existing_id,
                index_id,
            )
            return existing

        # New node
        self._nodes[index_id][node.id] = node
        name_idx[norm_name] = node.id
        logger.debug(
            "GraphStore: inserted node id=%s entity='%s' (index=%s)",
            node.id,
            node.entity,
            index_id,
        )
        return node

    def get_node(self, index_id: str, node_id: str) -> GraphNode | None:
        """Return node by id, or None if not found."""
        node = self._nodes.get(index_id, {}).get(node_id)
        return copy.deepcopy(node) if node else None

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def upsert_edge(self, index_id: str, edge: GraphEdge) -> GraphEdge:
        """Insert or merge edge.

        If a (subject_id, predicate, object_id) triple already exists,
        merge chunk_ids (union) and update weight (average of existing and
        incoming). Returns the (possibly merged) edge.
        """
        self._ensure_index(index_id)

        edges = self._edges[index_id]
        triple_key = (edge.subject_id, edge.predicate, edge.object_id)

        if triple_key in self._triple_index.get(index_id, {}):
            existing_id = self._triple_index[index_id][triple_key]
            existing = self._edges[index_id][existing_id]
            # Merge chunk_ids and average weight
            existing.chunk_ids = list(set(existing.chunk_ids) | set(edge.chunk_ids))
            existing.weight = (existing.weight + edge.weight) / 2.0
            logger.debug(
                "GraphStore: merged edge triple %s (index=%s)",
                triple_key,
                index_id,
            )
            return existing

        # New edge
        self._triple_index.setdefault(index_id, {})[triple_key] = edge.id
        edges[edge.id] = edge
        logger.debug(
            "GraphStore: inserted edge id=%s triple=%s (index=%s)",
            edge.id,
            triple_key,
            index_id,
        )
        return edge

    def get_edge(self, index_id: str, edge_id: str) -> GraphEdge | None:
        """Return edge by id, or None if not found."""
        edge = self._edges.get(index_id, {}).get(edge_id)
        return copy.deepcopy(edge) if edge else None

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def get_neighbors(
        self, index_id: str, node_ids: list[str], hops: int
    ) -> list[tuple[GraphNode, int]]:
        """BFS from seed node_ids up to `hops` depth.

        Returns all unique neighbor nodes (excluding seed nodes themselves) as
        (GraphNode, depth) tuples where depth is the BFS hop depth at which the
        node was first reached.
        Returns empty list if hops=0 or node_ids is empty.
        """
        if hops <= 0 or not node_ids:
            return []

        nodes = self._nodes.get(index_id, {})
        edges = self._edges.get(index_id, {})

        if not nodes:
            return []

        # Build adjacency: node_id -> set of adjacent node_ids
        adjacency: dict[str, set[str]] = {nid: set() for nid in nodes}
        for edge in edges.values():
            if edge.subject_id in adjacency:
                adjacency[edge.subject_id].add(edge.object_id)
            if edge.object_id in adjacency:
                adjacency[edge.object_id].add(edge.subject_id)

        visited: set[str] = set(node_ids)
        # BFS queue: (node_id, depth)
        queue: deque[tuple[str, int]] = deque()
        for nid in node_ids:
            if nid in nodes:
                queue.append((nid, 0))

        result: list[tuple[str, int]] = []

        while queue:
            current_id, depth = queue.popleft()
            if depth >= hops:
                continue
            for neighbor_id in adjacency.get(current_id, set()):
                if neighbor_id not in visited and neighbor_id in nodes:
                    visited.add(neighbor_id)
                    result.append((neighbor_id, depth + 1))
                    queue.append((neighbor_id, depth + 1))

        return [(copy.deepcopy(nodes[nid]), depth) for nid, depth in result]

    # ------------------------------------------------------------------
    # Subgraph deletion
    # ------------------------------------------------------------------

    def delete_subgraph(self, index_id: str) -> bool:
        """Delete all nodes, edges, and name index entries for this index_id.

        Returns True if any data existed, False if index_id was unknown.
        """
        existed = (
            index_id in self._nodes
            or index_id in self._edges
            or index_id in self._entity_name_index
        )
        self._nodes.pop(index_id, None)
        self._edges.pop(index_id, None)
        self._entity_name_index.pop(index_id, None)
        self._triple_index.pop(index_id, None)
        logger.debug("GraphStore: deleted subgraph for index=%s (existed=%s)", index_id, existed)
        return existed

    # ------------------------------------------------------------------
    # Counts
    # ------------------------------------------------------------------

    def node_count(self, index_id: str) -> int:
        """Return number of nodes for this index, or 0 if unknown."""
        return len(self._nodes.get(index_id, {}))

    def edge_count(self, index_id: str) -> int:
        """Return number of edges for this index, or 0 if unknown."""
        return len(self._edges.get(index_id, {}))

    # ------------------------------------------------------------------
    # Paginated listing
    # ------------------------------------------------------------------

    def list_nodes(
        self,
        index_id: str,
        page: int = 1,
        per_page: int = 50,
        entity_type: str | None = None,
    ) -> tuple[list[GraphNode], int]:
        """Return paginated nodes for index.

        Filter by entity_type if provided. Returns (nodes_page, total_count).
        """
        # Order is insertion order (CPython 3.7+); not stable across upserts
        all_nodes = list(self._nodes.get(index_id, {}).values())
        if entity_type is not None:
            all_nodes = [n for n in all_nodes if n.entity_type == entity_type]
        total = len(all_nodes)
        start = (page - 1) * per_page
        end = start + per_page
        return all_nodes[start:end], total

    def list_edges(
        self,
        index_id: str,
        page: int = 1,
        per_page: int = 50,
        predicate: str | None = None,
    ) -> tuple[list[GraphEdge], int]:
        """Return paginated edges for index.

        Filter by predicate if provided. Returns (edges_page, total_count).
        """
        # Order is insertion order (CPython 3.7+); not stable across upserts
        all_edges = list(self._edges.get(index_id, {}).values())
        if predicate is not None:
            all_edges = [e for e in all_edges if e.predicate == predicate]
        total = len(all_edges)
        start = (page - 1) * per_page
        end = start + per_page
        return all_edges[start:end], total

    def get_all_nodes(self, index_id: str) -> list[GraphNode]:
        """Return all nodes for index_id (no pagination). Use for internal search operations."""
        return list(self._nodes.get(index_id, {}).values())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_graph_store: GraphStore | None = None


def get_graph_store() -> GraphStore:
    """Return the global GraphStore singleton."""
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore()
    return _graph_store
