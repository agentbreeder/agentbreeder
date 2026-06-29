"""Tests for agenthub.rag MCP tool wrappers (#279)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_SDK = Path(__file__).resolve().parents[2] / "sdk" / "python"
if str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

from agenthub.rag import (  # noqa: E402
    CypherResponse,
    DeleteResponse,
    ListIndexesResponse,
    NeighborhoodResponse,
    RagMcpClient,
    RagMcpToolError,
    SearchResponse,
    StatsResponse,
    UpsertResponse,
    _parse_tool_payload,
    close_default_client,
    cypher,
    delete,
    list_indexes,
    neighborhood,
    query,
    search,
    stats,
    upsert,
)


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> MagicMock:
    result = MagicMock()
    result.isError = is_error
    result.structuredContent = payload if not is_error else {"message": payload.get("message", "fail")}
    result.content = []
    return result


def _assert_tool_called(
    mock_session: AsyncMock, name: str, arguments: dict[str, Any]
) -> None:
    mock_session.call_tool.assert_awaited_once_with(name, arguments=arguments)


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.initialize = AsyncMock()
    return session


@pytest.fixture
async def client(mock_session: AsyncMock) -> RagMcpClient:
    await close_default_client()
    return RagMcpClient(session=mock_session)


@pytest.fixture(autouse=True)
async def _reset_default_client() -> None:
    await close_default_client()
    yield
    await close_default_client()


class TestResolveUrl:
    def test_explicit_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTBREEDER_RAG_MCP_URL", "http://rag.example/mcp")
        c = RagMcpClient()
        assert c.url == "http://rag.example/mcp"

    def test_from_mcp_servers_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENTBREEDER_RAG_MCP_URL", raising=False)
        monkeypatch.setenv(
            "AGENTBREEDER_MCP_SERVERS",
            json.dumps({"rag": {"url": "http://localhost:3100/", "transport": "streamable_http"}}),
        )
        c = RagMcpClient()
        assert c.url == "http://localhost:3100"


class TestParseToolPayload:
    def test_structured_content(self) -> None:
        payload = _parse_tool_payload(_tool_result({"results": [], "trace_id": "t1"}))
        assert payload["trace_id"] == "t1"

    def test_error_raises_tool_error(self) -> None:
        with pytest.raises(RagMcpToolError, match="denied"):
            _parse_tool_payload(_tool_result({"message": "access denied"}, is_error=True))


class TestSearch:
    async def test_search_invokes_tool_with_defaults(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        mock_session.call_tool.return_value = _tool_result(
            {
                "results": [
                    {
                        "chunk_id": "doc-1#0",
                        "text": "refund steps",
                        "score": 0.9,
                        "rank": 1,
                        "source_doc_id": "doc-1",
                        "source_path": "manual://faq.md",
                        "source_kind": "manual",
                        "metadata": {"page": 1},
                    }
                ],
                "trace_id": "rag-search-abc",
            }
        )
        resp = await client.search("kb/support-docs", "How do I refund?")
        _assert_tool_called(
            mock_session,
            "rag.search",
            {
                "index": "kb/support-docs",
                "query": "How do I refund?",
                "k": 5,
                "filters": None,
                "strategy": "hybrid",
                "rerank": False,
            },
        )
        assert isinstance(resp, SearchResponse)
        assert resp.trace_id == "rag-search-abc"
        assert len(resp.results) == 1
        assert resp.results[0].source_path == "manual://faq.md"
        assert resp.results[0].score == 0.9


class TestQuery:
    async def test_query_forwards_k_default(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        mock_session.call_tool.return_value = _tool_result({"results": [], "trace_id": "q1"})
        await client.query("kb/docs", "embedding query")
        _assert_tool_called(
            mock_session,
            "rag.query",
            {"index": "kb/docs", "query": "embedding query", "k": 5, "filters": None},
        )


class TestNeighborhood:
    async def test_neighborhood_default_hops(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        mock_session.call_tool.return_value = _tool_result(
            {"nodes": [{"id": "n1", "labels": ["Doc"]}], "edges": [], "trace_id": "n1"}
        )
        resp = await client.neighborhood("kb/graph", "entity-42")
        _assert_tool_called(
            mock_session,
            "rag.neighborhood",
            {"index": "kb/graph", "entity_id": "entity-42", "hops": 2},
        )
        assert isinstance(resp, NeighborhoodResponse)
        assert resp.nodes[0].id == "n1"


class TestCypher:
    async def test_cypher_forwards_params(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        mock_session.call_tool.return_value = _tool_result(
            {"rows": [{"name": "Alice"}], "trace_id": "c1"}
        )
        resp = await client.cypher(
            "kb/graph", "MATCH (n) WHERE n.id = $id RETURN n", params={"id": "x"}
        )
        _assert_tool_called(
            mock_session,
            "rag.cypher",
            {
                "index": "kb/graph",
                "query": "MATCH (n) WHERE n.id = $id RETURN n",
                "params": {"id": "x"},
            },
        )
        assert isinstance(resp, CypherResponse)
        assert resp.rows[0]["name"] == "Alice"


class TestUpsert:
    async def test_upsert_documents(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        docs = [
            {
                "id": "manual-faq-001",
                "content": "FAQ body",
                "source_path": "manual://faq.md",
                "metadata": {"visibility": "public"},
            }
        ]
        mock_session.call_tool.return_value = _tool_result(
            {"inserted": 1, "updated": 0, "skipped": 0, "trace_id": "u1"}
        )
        resp = await client.upsert("kb/support-docs", docs)
        _assert_tool_called(
            mock_session, "rag.upsert", {"index": "kb/support-docs", "documents": docs}
        )
        assert isinstance(resp, UpsertResponse)
        assert resp.inserted == 1


class TestDelete:
    async def test_delete_doc_ids(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        mock_session.call_tool.return_value = _tool_result(
            {"deleted": 2, "not_found": 1, "trace_id": "d1"}
        )
        resp = await client.delete("kb/support-docs", ["a", "b", "c"])
        _assert_tool_called(
            mock_session,
            "rag.delete",
            {"index": "kb/support-docs", "doc_ids": ["a", "b", "c"]},
        )
        assert isinstance(resp, DeleteResponse)
        assert resp.deleted == 2
        assert resp.not_found == 1


class TestListIndexes:
    async def test_list_indexes_with_filter(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        mock_session.call_tool.return_value = _tool_result(
            {
                "indexes": [
                    {
                        "name": "kb/support-docs",
                        "version": "1.4.0",
                        "env": "prod",
                        "total_documents": 12400,
                        "last_indexed_at": "2026-04-30T03:00:00Z",
                        "tags": ["customer-facing"],
                    }
                ]
            }
        )
        resp = await client.list_indexes(filter={"team": "support"})
        _assert_tool_called(
            mock_session, "rag.list_indexes", {"filter": {"team": "support"}}
        )
        assert isinstance(resp, ListIndexesResponse)
        assert resp.indexes[0].name == "kb/support-docs"
        assert resp.indexes[0].total_documents == 12400


class TestStats:
    async def test_stats(self, client: RagMcpClient, mock_session: AsyncMock) -> None:
        mock_session.call_tool.return_value = _tool_result(
            {
                "name": "kb/support-docs",
                "version": "1.4.0",
                "env": "prod",
                "total_documents": 12400,
                "index_size_bytes": 999,
                "last_indexed_at": "2026-04-30T03:00:00Z",
                "sources": ["gdrive://folder/abc"],
                "trace_id": "s1",
            }
        )
        resp = await client.stats("kb/support-docs")
        _assert_tool_called(mock_session, "rag.stats", {"index": "kb/support-docs"})
        assert isinstance(resp, StatsResponse)
        assert resp.index_size_bytes == 999
        assert resp.sources == ["gdrive://folder/abc"]


class TestModuleFunctions:
    async def test_module_search_uses_shared_client(self, mock_session: AsyncMock) -> None:
        shared = RagMcpClient(session=mock_session)
        mock_session.call_tool.return_value = _tool_result({"results": [], "trace_id": "m1"})

        import agenthub.rag as ragmod

        ragmod._default_client = shared
        await search("kb/x", "q")
        assert mock_session.call_tool.await_count == 1

    async def test_client_reused_across_calls(self, mock_session: AsyncMock) -> None:
        shared = RagMcpClient(session=mock_session)
        mock_session.call_tool.return_value = _tool_result({"results": [], "trace_id": "r"})

        import agenthub.rag as ragmod

        ragmod._default_client = shared
        await search("kb/a", "one")
        await query("kb/a", "two")
        await stats("kb/a")
        assert mock_session.call_tool.await_count == 3
        assert ragmod.get_default_client() is shared


class TestErrors:
    async def test_tool_error_propagates(
        self, client: RagMcpClient, mock_session: AsyncMock
    ) -> None:
        mock_session.call_tool.return_value = _tool_result(
            {"message": "403 forbidden"}, is_error=True
        )
        with pytest.raises(RagMcpToolError, match="403"):
            await client.search("kb/x", "q")

    async def test_module_level_delete(self, mock_session: AsyncMock) -> None:
        shared = RagMcpClient(session=mock_session)
        mock_session.call_tool.return_value = _tool_result(
            {"deleted": 1, "not_found": 0, "trace_id": "d"}
        )
        import agenthub.rag as ragmod

        ragmod._default_client = shared
        resp = await delete("kb/x", ["doc-1"])
        assert resp.deleted == 1

    async def test_module_level_upsert_and_list(self, mock_session: AsyncMock) -> None:
        shared = RagMcpClient(session=mock_session)
        import agenthub.rag as ragmod

        ragmod._default_client = shared
        mock_session.call_tool.return_value = _tool_result(
            {"inserted": 0, "updated": 0, "skipped": 0, "trace_id": "u"}
        )
        await upsert("kb/x", [])
        mock_session.call_tool.return_value = _tool_result({"indexes": []})
        await list_indexes()
        assert mock_session.call_tool.await_count == 2

    async def test_module_level_neighborhood_and_cypher(
        self, mock_session: AsyncMock
    ) -> None:
        shared = RagMcpClient(session=mock_session)
        import agenthub.rag as ragmod

        ragmod._default_client = shared
        mock_session.call_tool.return_value = _tool_result(
            {"nodes": [], "edges": [], "trace_id": "n"}
        )
        await neighborhood("kb/x", "e1")
        mock_session.call_tool.return_value = _tool_result({"rows": [], "trace_id": "c"})
        await cypher("kb/x", "RETURN 1")
        assert mock_session.call_tool.await_count == 2
