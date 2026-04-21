"""TestClient-based tests for the 3 new graph metadata endpoints in api/routes/rag.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rag_index(index_type_value: str = "vector", index_id: str = "idx-test"):
    """Build a lightweight mock RAG index."""
    m = MagicMock()
    m.id = index_id
    m.to_dict.return_value = {
        "id": index_id,
        "name": "test-index",
        "index_type": index_type_value,
    }
    # index_type.value mirrors the StrEnum behaviour used in the route
    m.index_type.value = index_type_value
    return m


def _make_graph_node(entity: str = "Alice", entity_type: str = "PERSON", chunk_ids: list | None = None):
    n = MagicMock()
    n.entity = entity
    n.entity_type = entity_type
    n.chunk_ids = chunk_ids or ["c1"]
    n.id = entity.lower()
    n.to_dict.return_value = {"entity": entity, "entity_type": entity_type}
    return n


def _make_graph_edge(subject_id: str = "alice", object_id: str = "bob", predicate: str = "KNOWS"):
    e = MagicMock()
    e.subject_id = subject_id
    e.object_id = object_id
    e.predicate = predicate
    e.to_dict.return_value = {"subject_id": subject_id, "object_id": object_id, "predicate": predicate}
    return e


# ---------------------------------------------------------------------------
# /graph endpoint
# ---------------------------------------------------------------------------

class TestGetGraphMetadata:
    @patch("api.routes.rag.get_rag_store")
    def test_not_found(self, mock_rag_store):
        store = MagicMock()
        store.get_index.return_value = None
        mock_rag_store.return_value = store

        resp = client.get("/api/v1/rag/indexes/nonexistent/graph")
        assert resp.status_code == 404

    @patch("api.routes.rag.get_rag_store")
    def test_wrong_type_returns_400(self, mock_rag_store):
        store = MagicMock()
        store.get_index.return_value = _make_rag_index("vector", "vec-idx")
        mock_rag_store.return_value = store

        resp = client.get("/api/v1/rag/indexes/vec-idx/graph")
        assert resp.status_code == 400

    @patch("api.routes.rag.get_graph_store")
    @patch("api.routes.rag.get_rag_store")
    def test_graph_index_returns_metadata(self, mock_rag_store, mock_graph_store):
        rag_store = MagicMock()
        rag_store.get_index.return_value = _make_rag_index("graph", "graph-idx")
        mock_rag_store.return_value = rag_store

        gs = MagicMock()
        nodes = [
            _make_graph_node("Alice", "PERSON", ["c1", "c2"]),
            _make_graph_node("Bob", "PERSON", ["c1"]),
            _make_graph_node("Acme", "ORG", ["c3"]),
        ]
        gs.get_all_nodes.return_value = nodes
        gs.edge_count.return_value = 5
        mock_graph_store.return_value = gs

        resp = client.get("/api/v1/rag/indexes/graph-idx/graph")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "node_count" in data
        assert "edge_count" in data
        assert "entity_types" in data
        assert "top_entities" in data
        assert data["node_count"] == 3
        assert data["edge_count"] == 5
        # entity_types sorted by type name
        type_names = [e["type"] for e in data["entity_types"]]
        assert sorted(type_names) == type_names

    @patch("api.routes.rag.get_graph_store")
    @patch("api.routes.rag.get_rag_store")
    def test_hybrid_index_also_works(self, mock_rag_store, mock_graph_store):
        rag_store = MagicMock()
        rag_store.get_index.return_value = _make_rag_index("hybrid", "hybrid-idx")
        mock_rag_store.return_value = rag_store

        gs = MagicMock()
        gs.get_all_nodes.return_value = []
        gs.edge_count.return_value = 0
        mock_graph_store.return_value = gs

        resp = client.get("/api/v1/rag/indexes/hybrid-idx/graph")
        assert resp.status_code == 200
        assert resp.json()["data"]["node_count"] == 0


# ---------------------------------------------------------------------------
# /entities endpoint
# ---------------------------------------------------------------------------

class TestListEntities:
    @patch("api.routes.rag.get_rag_store")
    def test_wrong_type_returns_400(self, mock_rag_store):
        store = MagicMock()
        store.get_index.return_value = _make_rag_index("vector", "vec-idx")
        mock_rag_store.return_value = store

        resp = client.get("/api/v1/rag/indexes/vec-idx/entities")
        assert resp.status_code == 400

    @patch("api.routes.rag.get_rag_store")
    def test_not_found(self, mock_rag_store):
        store = MagicMock()
        store.get_index.return_value = None
        mock_rag_store.return_value = store

        resp = client.get("/api/v1/rag/indexes/missing/entities")
        assert resp.status_code == 404

    @patch("api.routes.rag.get_graph_store")
    @patch("api.routes.rag.get_rag_store")
    def test_graph_index_returns_list(self, mock_rag_store, mock_graph_store):
        rag_store = MagicMock()
        rag_store.get_index.return_value = _make_rag_index("graph", "graph-idx")
        mock_rag_store.return_value = rag_store

        gs = MagicMock()
        gs.list_nodes.return_value = ([_make_graph_node("Alice"), _make_graph_node("Bob")], 2)
        mock_graph_store.return_value = gs

        resp = client.get("/api/v1/rag/indexes/graph-idx/entities")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)
        assert len(resp.json()["data"]) == 2

    @patch("api.routes.rag.get_graph_store")
    @patch("api.routes.rag.get_rag_store")
    def test_entity_type_filter_passed_to_store(self, mock_rag_store, mock_graph_store):
        rag_store = MagicMock()
        rag_store.get_index.return_value = _make_rag_index("graph", "graph-idx")
        mock_rag_store.return_value = rag_store

        gs = MagicMock()
        gs.list_nodes.return_value = ([], 0)
        mock_graph_store.return_value = gs

        resp = client.get("/api/v1/rag/indexes/graph-idx/entities?entity_type=PERSON")
        assert resp.status_code == 200
        gs.list_nodes.assert_called_once()
        call_kwargs = gs.list_nodes.call_args
        assert call_kwargs.kwargs.get("entity_type") == "PERSON" or "PERSON" in str(call_kwargs)


# ---------------------------------------------------------------------------
# /relationships endpoint
# ---------------------------------------------------------------------------

class TestListRelationships:
    @patch("api.routes.rag.get_rag_store")
    def test_wrong_type_returns_400(self, mock_rag_store):
        store = MagicMock()
        store.get_index.return_value = _make_rag_index("vector", "vec-idx")
        mock_rag_store.return_value = store

        resp = client.get("/api/v1/rag/indexes/vec-idx/relationships")
        assert resp.status_code == 400

    @patch("api.routes.rag.get_rag_store")
    def test_not_found(self, mock_rag_store):
        store = MagicMock()
        store.get_index.return_value = None
        mock_rag_store.return_value = store

        resp = client.get("/api/v1/rag/indexes/missing/relationships")
        assert resp.status_code == 404

    @patch("api.routes.rag.get_graph_store")
    @patch("api.routes.rag.get_rag_store")
    def test_graph_index_returns_list(self, mock_rag_store, mock_graph_store):
        rag_store = MagicMock()
        rag_store.get_index.return_value = _make_rag_index("graph", "graph-idx")
        mock_rag_store.return_value = rag_store

        gs = MagicMock()
        nodes = [_make_graph_node("Alice"), _make_graph_node("Bob")]
        gs.get_all_nodes.return_value = nodes
        gs.list_edges.return_value = ([_make_graph_edge("alice", "bob")], 1)
        mock_graph_store.return_value = gs

        resp = client.get("/api/v1/rag/indexes/graph-idx/relationships")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)
        # subject_entity + object_entity are enriched into each edge dict
        edge = resp.json()["data"][0]
        assert "subject_entity" in edge
        assert "object_entity" in edge

    @patch("api.routes.rag.get_graph_store")
    @patch("api.routes.rag.get_rag_store")
    def test_predicate_filter_passed_to_store(self, mock_rag_store, mock_graph_store):
        rag_store = MagicMock()
        rag_store.get_index.return_value = _make_rag_index("graph", "graph-idx")
        mock_rag_store.return_value = rag_store

        gs = MagicMock()
        gs.get_all_nodes.return_value = []
        gs.list_edges.return_value = ([], 0)
        mock_graph_store.return_value = gs

        resp = client.get("/api/v1/rag/indexes/graph-idx/relationships?predicate=KNOWS")
        assert resp.status_code == 200
        gs.list_edges.assert_called_once()
        call_kwargs = gs.list_edges.call_args
        assert call_kwargs.kwargs.get("predicate") == "KNOWS" or "KNOWS" in str(call_kwargs)
