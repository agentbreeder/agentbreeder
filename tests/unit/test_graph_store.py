"""Unit tests for api.services.graph_store."""

from __future__ import annotations

import pytest

from api.services.graph_store import GraphStore
from api.services.rag_service import GraphEdge, GraphNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IDX = "test-index"


def make_node(
    node_id: str,
    entity: str,
    entity_type: str = "PERSON",
    description: str = "",
    chunk_ids: list[str] | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        entity=entity,
        entity_type=entity_type,
        description=description,
        chunk_ids=chunk_ids or [],
    )


def make_edge(
    edge_id: str,
    subject_id: str,
    predicate: str,
    object_id: str,
    chunk_ids: list[str] | None = None,
    weight: float = 1.0,
) -> GraphEdge:
    return GraphEdge(
        id=edge_id,
        subject_id=subject_id,
        predicate=predicate,
        object_id=object_id,
        chunk_ids=chunk_ids or [],
        weight=weight,
    )


# ---------------------------------------------------------------------------
# Node tests
# ---------------------------------------------------------------------------


def test_upsert_node_basic():
    gs = GraphStore()
    node = make_node("n1", "Alice", chunk_ids=["c1"])
    result = gs.upsert_node(IDX, node)
    assert result.id == "n1"
    fetched = gs.get_node(IDX, "n1")
    assert fetched is not None
    assert fetched.entity == "Alice"
    assert fetched.chunk_ids == ["c1"]


def test_upsert_node_dedup_by_name():
    gs = GraphStore()
    n1 = make_node("n1", "Alice", chunk_ids=["c1"])
    n2 = make_node("n2", "alice", chunk_ids=["c2"])  # same normalized name
    gs.upsert_node(IDX, n1)
    merged = gs.upsert_node(IDX, n2)
    # Should keep original id
    assert merged.id == "n1"
    # chunk_ids should be union
    assert set(merged.chunk_ids) == {"c1", "c2"}
    # n2 id should not exist as a separate node
    assert gs.get_node(IDX, "n2") is None
    assert gs.node_count(IDX) == 1


def test_upsert_node_dedup_updates_entity_type():
    gs = GraphStore()
    n1 = make_node("n1", "Alice", entity_type="PERSON", chunk_ids=["c1"])
    n2 = make_node("n2", "Alice", entity_type="EMPLOYEE", chunk_ids=["c2"])
    gs.upsert_node(IDX, n1)
    merged = gs.upsert_node(IDX, n2)
    assert merged.entity_type == "EMPLOYEE"


# ---------------------------------------------------------------------------
# Edge tests
# ---------------------------------------------------------------------------


def test_upsert_edge_basic():
    gs = GraphStore()
    edge = make_edge("e1", "n1", "KNOWS", "n2", chunk_ids=["c1"])
    result = gs.upsert_edge(IDX, edge)
    assert result.id == "e1"
    fetched = gs.get_edge(IDX, "e1")
    assert fetched is not None
    assert fetched.predicate == "KNOWS"


def test_upsert_edge_dedup_by_triple():
    gs = GraphStore()
    e1 = make_edge("e1", "n1", "KNOWS", "n2", chunk_ids=["c1"], weight=1.0)
    e2 = make_edge("e2", "n1", "KNOWS", "n2", chunk_ids=["c2"], weight=0.5)
    gs.upsert_edge(IDX, e1)
    merged = gs.upsert_edge(IDX, e2)
    # Should keep original id
    assert merged.id == "e1"
    # chunk_ids should be union
    assert set(merged.chunk_ids) == {"c1", "c2"}
    # weight should be averaged
    assert merged.weight == pytest.approx(0.75)
    # Only one edge should exist
    assert gs.edge_count(IDX) == 1


def test_upsert_edge_ghost_id_resolved():
    """After a merge, neither old nor new ID returns None from get_edge for the canonical edge."""
    gs = GraphStore()
    e1 = make_edge("e1", "n1", "KNOWS", "n2", chunk_ids=["c1"])
    e2 = make_edge("e2", "n1", "KNOWS", "n2", chunk_ids=["c2"])
    r1 = gs.upsert_edge(IDX, e1)
    r2 = gs.upsert_edge(IDX, e2)
    # Both upsert calls returned the canonical edge (id=e1)
    assert r1.id == "e1"
    assert r2.id == "e1"
    # The canonical edge is accessible by its id
    canonical = gs.get_edge(IDX, "e1")
    assert canonical is not None
    # The ghost id (e2) was never stored — get_edge returns None for it
    ghost = gs.get_edge(IDX, "e2")
    assert ghost is None


# ---------------------------------------------------------------------------
# get_neighbors tests
# ---------------------------------------------------------------------------


def _populate_linear(gs: GraphStore) -> None:
    """Build: A -[r]-> B -[r]-> C"""
    for nid, name in [("A", "NodeA"), ("B", "NodeB"), ("C", "NodeC")]:
        gs.upsert_node(IDX, make_node(nid, name))
    gs.upsert_edge(IDX, make_edge("e1", "A", "rel", "B"))
    gs.upsert_edge(IDX, make_edge("e2", "B", "rel", "C"))


def test_get_neighbors_zero_hops():
    gs = GraphStore()
    _populate_linear(gs)
    result = gs.get_neighbors(IDX, ["A"], hops=0)
    assert result == []


def test_get_neighbors_one_hop():
    gs = GraphStore()
    _populate_linear(gs)
    result = gs.get_neighbors(IDX, ["A"], hops=1)
    ids = {n.id for n, _depth in result}
    assert ids == {"B"}


def test_get_neighbors_two_hops():
    gs = GraphStore()
    _populate_linear(gs)
    result = gs.get_neighbors(IDX, ["A"], hops=2)
    ids = {n.id for n, _depth in result}
    assert ids == {"B", "C"}


def test_get_neighbors_excludes_seeds():
    gs = GraphStore()
    _populate_linear(gs)
    result = gs.get_neighbors(IDX, ["A"], hops=2)
    ids = {n.id for n, _depth in result}
    assert "A" not in ids


def test_get_neighbors_no_cycles():
    """Cyclic graph A -> B -> A must not loop forever."""
    gs = GraphStore()
    gs.upsert_node(IDX, make_node("A", "NodeA"))
    gs.upsert_node(IDX, make_node("B", "NodeB"))
    gs.upsert_edge(IDX, make_edge("e1", "A", "rel", "B"))
    gs.upsert_edge(IDX, make_edge("e2", "B", "rel", "A"))
    # Should terminate and return B (A is seed)
    result = gs.get_neighbors(IDX, ["A"], hops=5)
    ids = {n.id for n, _depth in result}
    assert ids == {"B"}


# ---------------------------------------------------------------------------
# delete_subgraph tests
# ---------------------------------------------------------------------------


def test_delete_subgraph():
    gs = GraphStore()
    gs.upsert_node(IDX, make_node("n1", "Alice"))
    gs.upsert_edge(IDX, make_edge("e1", "n1", "rel", "n2"))
    assert gs.delete_subgraph(IDX) is True
    assert gs.node_count(IDX) == 0
    assert gs.edge_count(IDX) == 0
    # Second delete returns False — nothing was there
    assert gs.delete_subgraph(IDX) is False


def test_delete_subgraph_clears_triple_index():
    """After delete, re-inserting the same triple creates a new canonical edge."""
    gs = GraphStore()
    gs.upsert_node(IDX, make_node("n1", "Alice"))
    gs.upsert_edge(IDX, make_edge("e1", "n1", "rel", "n2"))
    gs.delete_subgraph(IDX)
    # Now insert with a new edge id — should not merge into e1
    result = gs.upsert_edge(IDX, make_edge("e2", "n1", "rel", "n2"))
    assert result.id == "e2"
    assert gs.get_edge(IDX, "e2") is not None


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_node_count_edge_count():
    gs = GraphStore()
    assert gs.node_count(IDX) == 0
    assert gs.edge_count(IDX) == 0
    gs.upsert_node(IDX, make_node("n1", "Alice"))
    gs.upsert_node(IDX, make_node("n2", "Bob"))
    gs.upsert_edge(IDX, make_edge("e1", "n1", "rel", "n2"))
    assert gs.node_count(IDX) == 2
    assert gs.edge_count(IDX) == 1


# ---------------------------------------------------------------------------
# list_nodes / list_edges pagination and filtering
# ---------------------------------------------------------------------------


def test_list_nodes_pagination():
    gs = GraphStore()
    for i in range(5):
        gs.upsert_node(IDX, make_node(f"n{i}", f"Entity{i}"))
    page1, total = gs.list_nodes(IDX, page=1, per_page=3)
    assert total == 5
    assert len(page1) == 3
    page2, total2 = gs.list_nodes(IDX, page=2, per_page=3)
    assert total2 == 5
    assert len(page2) == 2


def test_list_nodes_type_filter():
    gs = GraphStore()
    gs.upsert_node(IDX, make_node("n1", "Alice", entity_type="PERSON"))
    gs.upsert_node(IDX, make_node("n2", "AcmeCorp", entity_type="ORG"))
    gs.upsert_node(IDX, make_node("n3", "Bob", entity_type="PERSON"))
    persons, total = gs.list_nodes(IDX, entity_type="PERSON")
    assert total == 2
    assert all(n.entity_type == "PERSON" for n in persons)


def test_list_edges_predicate_filter():
    gs = GraphStore()
    gs.upsert_edge(IDX, make_edge("e1", "n1", "KNOWS", "n2"))
    gs.upsert_edge(IDX, make_edge("e2", "n1", "WORKS_AT", "n3"))
    gs.upsert_edge(IDX, make_edge("e3", "n2", "KNOWS", "n4"))
    knows_edges, total = gs.list_edges(IDX, predicate="KNOWS")
    assert total == 2
    assert all(e.predicate == "KNOWS" for e in knows_edges)


# ---------------------------------------------------------------------------
# Mutable leak guard (Critical #2)
# ---------------------------------------------------------------------------


def test_get_node_returns_copy():
    """Mutating a returned node must not affect the stored node."""
    gs = GraphStore()
    gs.upsert_node(IDX, make_node("n1", "Alice", chunk_ids=["c1"]))
    fetched = gs.get_node(IDX, "n1")
    assert fetched is not None
    fetched.chunk_ids.append("LEAKED")
    stored = gs.get_node(IDX, "n1")
    assert "LEAKED" not in stored.chunk_ids


def test_get_edge_returns_copy():
    """Mutating a returned edge must not affect the stored edge."""
    gs = GraphStore()
    gs.upsert_edge(IDX, make_edge("e1", "n1", "rel", "n2", chunk_ids=["c1"]))
    fetched = gs.get_edge(IDX, "e1")
    assert fetched is not None
    fetched.chunk_ids.append("LEAKED")
    stored = gs.get_edge(IDX, "e1")
    assert "LEAKED" not in stored.chunk_ids


def test_get_neighbors_returns_copies():
    """Mutating returned neighbor nodes must not affect the stored nodes."""
    gs = GraphStore()
    _populate_linear(gs)
    neighbors = gs.get_neighbors(IDX, ["A"], hops=1)
    assert neighbors
    node, _depth = neighbors[0]
    node.chunk_ids.append("LEAKED")
    stored = gs.get_node(IDX, node.id)
    assert stored is not None
    assert "LEAKED" not in stored.chunk_ids
