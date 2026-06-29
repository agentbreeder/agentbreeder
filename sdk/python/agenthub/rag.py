"""RAG clients for the AgentBreeder SDK.

Two surfaces:

* **``RagIndex``** — synchronous HTTP client for ``/api/v1/rag/*`` (ingest + search).
* **MCP tools** — async wrappers around the RAG MCP server (``rag.search``, etc.)
  that delegate to the local sidecar's MCP endpoint. See ``docs/architecture/rag-tools.md``.

Usage (HTTP index client)::

    from agenthub import RagIndex

    index = RagIndex("agentbreeder-knowledge", token=token)
    job = index.ingest(["./docs/intro.md", "./docs/quickstart.pdf"])
    hits = index.search("how do I deploy an agent?", top_k=5)

Usage (MCP tools — deployed agents)::

    from agenthub.rag import search, upsert

    response = await search("kb/support-docs", "How do I refund?", k=5)
    for hit in response.results:
        print(hit.source_path, hit.text[:100])
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from mcp.client.session import ClientSession

logger = logging.getLogger(__name__)

ALLOWED_EXTS = {".pdf", ".txt", ".md", ".csv", ".json"}
_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}


@dataclass
class IngestResult:
    """Outcome of an ingest call. Mirrors the API ``IngestionJob`` shape."""

    job_id: str
    status: str
    total_files: int
    processed_files: int
    total_chunks: int
    embedded_chunks: int
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IngestResult:
        return cls(
            job_id=d.get("id", ""),
            status=d.get("status", "unknown"),
            total_files=int(d.get("total_files", 0)),
            processed_files=int(d.get("processed_files", 0)),
            total_chunks=int(d.get("total_chunks", 0)),
            embedded_chunks=int(d.get("embedded_chunks", 0)),
            error=d.get("error"),
        )


class RagIndexError(RuntimeError):
    """Raised when the API returns a non-2xx response."""


class RagIndex:
    """Client for a registered RAG index.

    Resolves the index name to a UUID on first use (or accepts a UUID directly)
    and exposes ``ingest`` and ``search`` against ``/api/v1/rag/*``.
    """

    def __init__(
        self,
        name_or_id: str,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.name_or_id = name_or_id
        self.base_url = (
            base_url or os.getenv("AGENTBREEDER_API_URL") or "http://localhost:8000"
        ).rstrip("/")
        self._token = token or os.getenv("AGENTBREEDER_API_TOKEN", "").strip() or None
        if not self._token:
            raise RagIndexError(
                "AGENTBREEDER_API_TOKEN is not set. Pass token=... or export the env var."
            )
        self.timeout = timeout
        self._index_id: str | None = None

    # ------------------------------------------------------------------ helpers

    def _headers(self, *, json_body: bool = True) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token}"}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _resolve_id(self) -> str:
        if self._index_id:
            return self._index_id
        # Direct UUID?
        if len(self.name_or_id) == 36 and self.name_or_id.count("-") == 4:
            self._index_id = self.name_or_id
            return self._index_id
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.base_url}/api/v1/rag/indexes", headers=self._headers())
            if r.status_code >= 400:
                raise RagIndexError(f"GET /api/v1/rag/indexes -> {r.status_code}: {r.text}")
            for item in r.json().get("data", []):
                if item.get("name") == self.name_or_id:
                    self._index_id = item["id"]
                    return self._index_id
        raise RagIndexError(f"RAG index not found: {self.name_or_id}")

    # -------------------------------------------------------------------- API

    @property
    def index_id(self) -> str:
        """Resolved UUID for this index (lazily fetched)."""
        return self._resolve_id()

    def ingest(self, files: list[str | Path], *, replace: bool = False) -> IngestResult:
        """Upload and ingest one or more files.

        Accepted formats: PDF, TXT, MD, CSV, JSON. Posts multipart/form-data
        to ``/api/v1/rag/indexes/{id}/ingest``.

        When ``replace=True``, any existing chunks whose ``source`` matches
        one of the incoming filenames are deleted before ingestion. When
        ``replace=False`` (the default), ingest is idempotent: chunks whose
        SHA-256 content hash already exists in the index are skipped.
        """
        if not files:
            raise RagIndexError("At least one file is required")
        parts: list[tuple[str, tuple[str, bytes, str]]] = []
        for fp in files:
            p = Path(fp)
            if not p.is_file():
                raise RagIndexError(f"File not found: {p}")
            ext = p.suffix.lower()
            if ext not in ALLOWED_EXTS:
                raise RagIndexError(
                    f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTS))}"
                )
            ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
            parts.append(("files", (p.name, p.read_bytes(), ctype)))

        idx = self._resolve_id()
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(
                f"{self.base_url}/api/v1/rag/indexes/{idx}/ingest",
                headers=self._headers(json_body=False),
                files=parts,
                data={"replace": "true"} if replace else None,
            )
            if r.status_code >= 400:
                raise RagIndexError(f"ingest -> {r.status_code}: {r.text}")
            payload = r.json().get("data", {})
            return IngestResult.from_dict(payload)

    def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """Run a hybrid search against this index.

        Returns a normalized list of ``{score, source, text, metadata}`` dicts.
        """
        idx = self._resolve_id()
        body = {"index_id": idx, "query": query, "top_k": int(top_k)}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(
                f"{self.base_url}/api/v1/rag/search",
                headers=self._headers(),
                json=body,
            )
            if r.status_code >= 400:
                raise RagIndexError(f"search -> {r.status_code}: {r.text}")
            data = r.json().get("data", {})
            if isinstance(data, dict):
                results = data.get("results", [])
            else:
                results = data or []
        normalized: list[dict[str, Any]] = []
        for r_ in results:
            normalized.append(
                {
                    "score": float(r_.get("score", r_.get("similarity", 0.0))),
                    "source": r_.get("source") or r_.get("metadata", {}).get("source", ""),
                    "text": r_.get("text") or r_.get("content") or "",
                    "metadata": r_.get("metadata") or {},
                }
            )
        return normalized


# ---------------------------------------------------------------------------
# MCP RAG tools — thin async wrapper over the RAG MCP server (#279)
# ---------------------------------------------------------------------------

MCP_SERVERS_ENV = "AGENTBREEDER_MCP_SERVERS"
RAG_MCP_URL_ENV = "AGENTBREEDER_RAG_MCP_URL"
DEFAULT_RAG_MCP_SERVER = "rag"
DEFAULT_SIDECAR_MCP_URL = "http://127.0.0.1:9090/mcp/rag"


@dataclass
class RagChunk:
    """A single search/query hit returned by ``rag.search`` / ``rag.query``."""

    chunk_id: str
    text: str
    score: float
    rank: int
    source_doc_id: str
    source_path: str
    source_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RagChunk:
        return cls(
            chunk_id=str(d.get("chunk_id", "")),
            text=str(d.get("text", "")),
            score=float(d.get("score", 0.0)),
            rank=int(d.get("rank", 0)),
            source_doc_id=str(d.get("source_doc_id", "")),
            source_path=str(d.get("source_path", "")),
            source_kind=str(d.get("source_kind", "")),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class SearchResponse:
    """Response from ``rag.search`` and ``rag.query``."""

    results: list[RagChunk]
    trace_id: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SearchResponse:
        return cls(
            results=[RagChunk.from_dict(r) for r in d.get("results", [])],
            trace_id=str(d.get("trace_id", "")),
        )


@dataclass
class GraphNode:
    """A node in a ``rag.neighborhood`` subgraph."""

    id: str
    labels: list[str]
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        # TODO(confirm against #270): exact node wire shape not fully specified in §4.
        return cls(
            id=str(d.get("id", d.get("entity_id", ""))),
            labels=list(d.get("labels", [])),
            properties={
                k: v for k, v in d.items() if k not in {"id", "entity_id", "labels"}
            },
        )


@dataclass
class GraphEdge:
    """An edge in a ``rag.neighborhood`` subgraph."""

    source: str
    target: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        # TODO(confirm against #270): exact edge wire shape not fully specified in §4.
        return cls(
            source=str(d.get("source", d.get("from", ""))),
            target=str(d.get("target", d.get("to", ""))),
            type=str(d.get("type", d.get("label", ""))),
            properties={
                k: v
                for k, v in d.items()
                if k not in {"source", "target", "from", "to", "type", "label"}
            },
        )


@dataclass
class NeighborhoodResponse:
    """Response from ``rag.neighborhood``."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    trace_id: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NeighborhoodResponse:
        return cls(
            nodes=[GraphNode.from_dict(n) for n in d.get("nodes", [])],
            edges=[GraphEdge.from_dict(e) for e in d.get("edges", [])],
            trace_id=str(d.get("trace_id", "")),
        )


@dataclass
class CypherResponse:
    """Response from ``rag.cypher``."""

    rows: list[dict[str, Any]]
    trace_id: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CypherResponse:
        return cls(
            rows=[dict(r) for r in d.get("rows", [])],
            trace_id=str(d.get("trace_id", "")),
        )


@dataclass
class UpsertResponse:
    """Response from ``rag.upsert``."""

    inserted: int
    updated: int
    skipped: int
    trace_id: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UpsertResponse:
        return cls(
            inserted=int(d.get("inserted", 0)),
            updated=int(d.get("updated", 0)),
            skipped=int(d.get("skipped", 0)),
            trace_id=str(d.get("trace_id", "")),
        )


@dataclass
class DeleteResponse:
    """Response from ``rag.delete``."""

    deleted: int
    not_found: int
    trace_id: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeleteResponse:
        return cls(
            deleted=int(d.get("deleted", 0)),
            not_found=int(d.get("not_found", 0)),
            trace_id=str(d.get("trace_id", "")),
        )


@dataclass
class RagIndexInfo:
    """Metadata for a single index in ``rag.list_indexes``."""

    name: str
    version: str
    env: str
    total_documents: int
    last_indexed_at: str
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RagIndexInfo:
        return cls(
            name=str(d.get("name", "")),
            version=str(d.get("version", "")),
            env=str(d.get("env", "")),
            total_documents=int(d.get("total_documents", 0)),
            last_indexed_at=str(d.get("last_indexed_at", "")),
            tags=list(d.get("tags", [])),
        )


@dataclass
class ListIndexesResponse:
    """Response from ``rag.list_indexes``."""

    indexes: list[RagIndexInfo]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ListIndexesResponse:
        return cls(indexes=[RagIndexInfo.from_dict(i) for i in d.get("indexes", [])])


@dataclass
class StatsResponse:
    """Response from ``rag.stats``."""

    name: str
    version: str
    env: str
    total_documents: int
    index_size_bytes: int
    last_indexed_at: str
    sources: list[str]
    trace_id: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StatsResponse:
        return cls(
            name=str(d.get("name", "")),
            version=str(d.get("version", "")),
            env=str(d.get("env", "")),
            total_documents=int(d.get("total_documents", 0)),
            index_size_bytes=int(d.get("index_size_bytes", 0)),
            last_indexed_at=str(d.get("last_indexed_at", "")),
            sources=[str(s) for s in d.get("sources", [])],
            trace_id=str(d.get("trace_id", "")),
        )


class RagMcpError(RuntimeError):
    """Base error for RAG MCP wrapper failures."""


class RagMcpTransportError(RagMcpError):
    """Raised when the MCP transport or session fails."""


class RagMcpToolError(RagMcpError):
    """Raised when the MCP server returns ``isError=True`` for a tool call."""


def _resolve_rag_mcp_url(*, server_name: str = DEFAULT_RAG_MCP_SERVER) -> str:
    """Resolve the RAG MCP server URL from env or sidecar default."""
    explicit = os.getenv(RAG_MCP_URL_ENV, "").strip()
    if explicit:
        return explicit.rstrip("/")

    raw = os.getenv(MCP_SERVERS_ENV, "").strip()
    if raw:
        try:
            servers = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise RagMcpTransportError(
                f"Invalid {MCP_SERVERS_ENV}: {exc}"
            ) from exc
        if isinstance(servers, dict):
            for key in (server_name, "rag-mcp", "rag_mcp"):
                cfg = servers.get(key)
                if isinstance(cfg, dict) and cfg.get("url"):
                    return str(cfg["url"]).rstrip("/")

    return DEFAULT_SIDECAR_MCP_URL


def _parse_tool_payload(result: Any) -> dict[str, Any]:
    """Extract a JSON object from an MCP ``CallToolResult``."""
    if result.isError:
        message = _tool_error_message(result)
        raise RagMcpToolError(message)

    if result.structuredContent is not None:
        payload = dict(result.structuredContent)
        if payload:
            return payload

    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            raise RagMcpError(f"Expected JSON object from tool, got {type(parsed).__name__}")

    raise RagMcpError("Empty tool result from RAG MCP server")


def _tool_error_message(result: Any) -> str:
    if result.structuredContent and isinstance(result.structuredContent, dict):
        msg = result.structuredContent.get("message") or result.structuredContent.get("error")
        if msg:
            return str(msg)
    parts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return " ".join(parts) if parts else "RAG MCP tool call failed"


class RagMcpClient:
    """Async MCP client for the 8 RAG tools.

    Opens a single MCP session lazily and reuses it across calls. Use as an
    async context manager or call ``close()`` explicitly when done.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        server_name: str = DEFAULT_RAG_MCP_SERVER,
        timeout: float = 120.0,
        session: ClientSession | None = None,
    ) -> None:
        self._url = url or _resolve_rag_mcp_url(server_name=server_name)
        self._timeout = timeout
        self._injected_session = session
        self._transport_cm: Any = None
        self._session: ClientSession | None = None
        self._session_owned = False

    async def __aenter__(self) -> RagMcpClient:
        await self._ensure_session()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    @property
    def url(self) -> str:
        """Resolved MCP endpoint URL."""
        return self._url

    @property
    def session(self) -> ClientSession | None:
        """The underlying MCP session, if connected."""
        return self._session or self._injected_session

    async def _ensure_session(self) -> ClientSession:
        if self._injected_session is not None:
            return self._injected_session
        if self._session is not None:
            return self._session

        try:
            from mcp.client.session import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise RagMcpTransportError(
                "The 'mcp' package is required for RAG MCP tools. "
                "Install with: pip install 'agentbreeder-sdk[mcp]'"
            ) from exc

        self._transport_cm = streamablehttp_client(self._url, timeout=self._timeout)
        read, write, _get_session_id = await self._transport_cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        self._session_owned = True
        logger.debug("Connected RAG MCP client to %s", self._url)
        return self._session

    async def close(self) -> None:
        """Close the MCP session and transport."""
        if self._session_owned and self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
            self._session_owned = False
        if self._transport_cm is not None:
            await self._transport_cm.__aexit__(None, None, None)
            self._transport_cm = None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke an MCP tool and return the parsed JSON payload."""
        session = await self._ensure_session()
        try:
            result = await session.call_tool(name, arguments=arguments)
        except Exception as exc:
            raise RagMcpTransportError(
                f"MCP transport error calling {name!r}: {exc}"
            ) from exc
        return _parse_tool_payload(result)

    async def search(
        self,
        index: str,
        query: str,
        *,
        k: int = 5,
        filters: dict[str, Any] | None = None,
        strategy: str = "hybrid",
        rerank: bool = False,
    ) -> SearchResponse:
        payload = await self.call_tool(
            "rag.search",
            {
                "index": index,
                "query": query,
                "k": k,
                "filters": filters,
                "strategy": strategy,
                "rerank": rerank,
            },
        )
        return SearchResponse.from_dict(payload)

    async def query(
        self,
        index: str,
        query: str | list[float],
        *,
        k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> SearchResponse:
        payload = await self.call_tool(
            "rag.query",
            {"index": index, "query": query, "k": k, "filters": filters},
        )
        return SearchResponse.from_dict(payload)

    async def neighborhood(
        self,
        index: str,
        entity_id: str,
        *,
        hops: int = 2,
    ) -> NeighborhoodResponse:
        payload = await self.call_tool(
            "rag.neighborhood",
            {"index": index, "entity_id": entity_id, "hops": hops},
        )
        return NeighborhoodResponse.from_dict(payload)

    async def cypher(
        self,
        index: str,
        query: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> CypherResponse:
        payload = await self.call_tool(
            "rag.cypher",
            {"index": index, "query": query, "params": params},
        )
        return CypherResponse.from_dict(payload)

    async def upsert(
        self,
        index: str,
        documents: list[dict[str, Any]],
    ) -> UpsertResponse:
        payload = await self.call_tool(
            "rag.upsert",
            {"index": index, "documents": documents},
        )
        return UpsertResponse.from_dict(payload)

    async def delete(self, index: str, doc_ids: list[str]) -> DeleteResponse:
        payload = await self.call_tool(
            "rag.delete",
            {"index": index, "doc_ids": doc_ids},
        )
        return DeleteResponse.from_dict(payload)

    async def list_indexes(
        self,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 — MCP tool arg name
    ) -> ListIndexesResponse:
        payload = await self.call_tool("rag.list_indexes", {"filter": filter})
        return ListIndexesResponse.from_dict(payload)

    async def stats(self, index: str) -> StatsResponse:
        payload = await self.call_tool("rag.stats", {"index": index})
        return StatsResponse.from_dict(payload)


_default_client: RagMcpClient | None = None


def get_default_client() -> RagMcpClient:
    """Return the module-level shared ``RagMcpClient`` (lazy-created)."""
    global _default_client
    if _default_client is None:
        _default_client = RagMcpClient()
    return _default_client


async def close_default_client() -> None:
    """Close and reset the module-level shared client."""
    global _default_client
    if _default_client is not None:
        await _default_client.close()
        _default_client = None


@asynccontextmanager
async def rag_client(*, url: str | None = None) -> AsyncIterator[RagMcpClient]:
    """Async context manager yielding a dedicated ``RagMcpClient``."""
    client = RagMcpClient(url=url)
    try:
        await client._ensure_session()
        yield client
    finally:
        await client.close()


async def search(
    index: str,
    query: str,
    *,
    k: int = 5,
    filters: dict[str, Any] | None = None,
    strategy: str = "hybrid",
    rerank: bool = False,
) -> SearchResponse:
    """Hybrid semantic search via ``rag.search`` MCP tool."""
    return await get_default_client().search(
        index,
        query,
        k=k,
        filters=filters,
        strategy=strategy,
        rerank=rerank,
    )


async def query(
    index: str,
    query: str | list[float],
    *,
    k: int = 5,
    filters: dict[str, Any] | None = None,
) -> SearchResponse:
    """Raw vector similarity via ``rag.query`` MCP tool."""
    return await get_default_client().query(index, query, k=k, filters=filters)


async def neighborhood(
    index: str,
    entity_id: str,
    *,
    hops: int = 2,
) -> NeighborhoodResponse:
    """Graph traversal via ``rag.neighborhood`` MCP tool."""
    return await get_default_client().neighborhood(index, entity_id, hops=hops)


async def cypher(
    index: str,
    query: str,
    *,
    params: dict[str, Any] | None = None,
) -> CypherResponse:
    """Read-only Cypher via ``rag.cypher`` MCP tool."""
    return await get_default_client().cypher(index, query, params=params)


async def upsert(
    index: str,
    documents: list[dict[str, Any]],
) -> UpsertResponse:
    """Insert or update documents via ``rag.upsert`` MCP tool."""
    return await get_default_client().upsert(index, documents)


async def delete(index: str, doc_ids: list[str]) -> DeleteResponse:
    """Remove documents via ``rag.delete`` MCP tool."""
    return await get_default_client().delete(index, doc_ids)


async def list_indexes(
    *, filter: dict[str, Any] | None = None  # noqa: A002 — MCP tool arg name
) -> ListIndexesResponse:
    """List ACL-visible indexes via ``rag.list_indexes`` MCP tool."""
    return await get_default_client().list_indexes(filter=filter)


async def stats(index: str) -> StatsResponse:
    """Index health via ``rag.stats`` MCP tool."""
    return await get_default_client().stats(index)
