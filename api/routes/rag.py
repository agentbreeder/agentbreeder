"""RAG Builder API routes — vector index management, ingestion, and search."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.services.graph_store import get_graph_store
from api.services.rag_service import get_rag_store
from registry.rag import BACKEND_IN_MEMORY, BACKEND_NEO4J, BACKEND_PGVECTOR, get_rag_backend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


# ---------------------------------------------------------------------------
# ACL enforcement helper
# ---------------------------------------------------------------------------


async def _enforce_acl(
    db: AsyncSession,
    user_email: str,
    resource_id: uuid.UUID,
    action: str,
) -> None:
    """Check ACL for a knowledge base index. Raises 403 if explicitly denied.

    Passes silently if: no ACL rows exist, DB unavailable, or permission granted.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from api.models.database import ResourcePermission  # noqa: PLC0415
        from api.services.rbac_service import check_permission  # noqa: PLC0415

        allowed, reason = await check_permission(
            db,
            user_email=user_email,
            resource_type="knowledge_base",
            resource_id=resource_id,
            action=action,
        )
        result = await db.execute(
            select(ResourcePermission)
            .where(
                ResourcePermission.resource_type == "knowledge_base",
                ResourcePermission.resource_id == resource_id,
            )
            .limit(1)
        )
        has_acl = result.scalar_one_or_none() is not None
        if has_acl and not allowed:
            from fastapi import HTTPException  # noqa: PLC0415

            raise HTTPException(status_code=403, detail=f"Access denied: {reason}")
    except HTTPException:
        raise
    except Exception:
        pass  # Fail open if DB unavailable


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


@router.post("/indexes", status_code=201)
async def create_index(
    body: dict[str, Any],
    _user: User = Depends(require_role("deployer")),
) -> ApiResponse[dict]:
    """Create a new vector index.

    Supports ``backend`` field values: ``in_memory`` (default), ``pgvector``,
    or ``neo4j``.  For ``neo4j``, pass a ``config`` object with ``uri``,
    ``username``, ``password``, and optionally ``database``.
    """
    store = get_rag_store()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    backend = body.get("backend", BACKEND_IN_MEMORY)
    backend_config: dict[str, Any] = body.get("config") or {}

    # Validate the backend key early so callers get a clear 400 instead of a
    # cryptic KeyError inside the factory.
    valid_backends = {BACKEND_IN_MEMORY, BACKEND_PGVECTOR, BACKEND_NEO4J}
    if backend not in valid_backends:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported backend '{backend}'. Must be one of: {sorted(valid_backends)}",
        )

    try:
        idx = store.create_index(
            name=name,
            description=body.get("description", ""),
            embedding_model=body.get("embedding_model", "openai/text-embedding-3-small"),
            chunk_strategy=body.get("chunk_strategy", "fixed_size"),
            chunk_size=body.get("chunk_size", 512),
            chunk_overlap=body.get("chunk_overlap", 64),
            source=body.get("source", "manual"),
            index_type=body.get("index_type", "vector"),
            entity_model=body.get("entity_model", "claude-haiku-4-5-20251001"),
            max_hops=body.get("max_hops", 2),
            relationship_types=body.get("relationship_types", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Resolve the backend instance (validates + imports lazily)
    try:
        get_rag_backend(backend, config=backend_config, index_id=idx.id)
    except (ImportError, ValueError) as exc:
        # Roll back the index we just created so state stays consistent
        store.delete_index(idx.id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = idx.to_dict()
    result["backend"] = backend
    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# Backend discovery
# ---------------------------------------------------------------------------


@router.get("/backends")
async def list_backends(_user: User = Depends(get_current_user)) -> ApiResponse[list[str]]:
    """List all registered RAG storage backends."""
    from registry.rag import list_backends as _list_backends  # noqa: PLC0415

    return ApiResponse(data=_list_backends())


@router.get("/indexes")
async def list_indexes(
    _user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> ApiResponse[list[dict]]:
    """List all vector indexes."""
    store = get_rag_store()
    indexes, total = store.list_indexes(page=page, per_page=per_page)
    return ApiResponse(
        data=[idx.to_dict() for idx in indexes],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/indexes/{index_id}")
async def get_index(
    index_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Get a vector index by ID."""
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(index_id), "read")
    except HTTPException:
        raise
    except Exception:
        pass
    store = get_rag_store()
    idx = store.get_index(index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")
    return ApiResponse(data=idx.to_dict())


@router.delete("/indexes/{index_id}")
async def delete_index(
    index_id: str,
    _user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Delete a vector index."""
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(index_id), "delete")
    except HTTPException:
        raise
    except Exception:
        pass
    store = get_rag_store()
    deleted = store.delete_index(index_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Index not found")
    return ApiResponse(data={"deleted": True})


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


@router.post("/indexes/{index_id}/ingest")
async def ingest_files(
    index_id: str,
    _user: User = Depends(require_role("deployer")),
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Upload and ingest files into a vector index.

    Accepted formats: PDF, TXT, MD, CSV, JSON.
    Files are chunked, embedded, and indexed in the background.
    """
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(index_id), "write")
    except HTTPException:
        raise
    except Exception:
        pass
    store = get_rag_store()
    idx = store.get_index(index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")

    # Validate file types
    allowed_extensions = {".pdf", ".txt", ".md", ".csv", ".json"}
    file_data: list[tuple[str, bytes]] = []
    for f in files:
        filename = f.filename or "unnamed.txt"
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {ext}. "
                f"Allowed: {', '.join(sorted(allowed_extensions))}",
            )
        content = await f.read()
        file_data.append((filename, content))

    # Run ingestion (in-process for now; background task for production)
    job = await store.ingest_files(index_id, file_data)
    return ApiResponse(data=job.to_dict())


@router.get("/indexes/{index_id}/ingest/{job_id}")
async def get_ingest_job(
    index_id: str,
    job_id: str,
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Get ingestion job progress."""
    store = get_rag_store()
    job = store.get_ingest_job(job_id)
    if not job or job.index_id != index_id:
        raise HTTPException(status_code=404, detail="Ingest job not found")
    return ApiResponse(data=job.to_dict())


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.post("/search")
async def search(
    body: dict[str, Any],
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Search across a vector index using hybrid vector + text search, or graph/hybrid search.

    Request body:
    - index_id: str (required)
    - query: str (required)
    - top_k: int (default 10)
    - vector_weight: float (default 0.7)
    - text_weight: float (default 0.3)
    - hops: int | None (default None) — for graph/hybrid indexes, number of BFS hops
    - seed_entity_limit: int (default 5) — for graph/hybrid indexes, max seed entities
    """
    store = get_rag_store()

    index_id = body.get("index_id")
    query = body.get("query")
    if not index_id or not query:
        raise HTTPException(status_code=400, detail="index_id and query are required")

    try:
        await _enforce_acl(db, _user.email, uuid.UUID(index_id), "read")
    except HTTPException:
        raise
    except Exception:
        pass

    idx = store.get_index(index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")

    top_k = body.get("top_k", 10)
    vector_weight = body.get("vector_weight", 0.7)
    text_weight = body.get("text_weight", 0.3)

    hits = await store.search(
        index_id=index_id,
        query=query,
        top_k=top_k,
        vector_weight=vector_weight,
        text_weight=text_weight,
        hops=body.get("hops", None),
        seed_entity_limit=body.get("seed_entity_limit", 5),
    )

    return ApiResponse(
        data={
            "index_id": index_id,
            "query": query,
            "top_k": top_k,
            "results": [h.to_dict() for h in hits],
            "total": len(hits),
        }
    )


# ---------------------------------------------------------------------------
# Graph metadata endpoints
# ---------------------------------------------------------------------------


@router.get("/indexes/{index_id}/graph")
async def get_index_graph_metadata(
    index_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Get graph metadata for a graph/hybrid index.

    Returns node count, edge count, entity type breakdown, and top entities.
    Returns 404 if index not found. Returns 400 if index is not a graph/hybrid type.
    """
    store = get_rag_store()
    idx = store.get_index(index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")
    if idx.index_type.value not in ("graph", "hybrid"):
        raise HTTPException(status_code=400, detail="Index is not a graph or hybrid type")

    graph_store = get_graph_store()

    # Entity type breakdown
    all_nodes = graph_store.get_all_nodes(index_id)
    entity_types: dict[str, int] = {}
    for node in all_nodes:
        entity_types[node.entity_type] = entity_types.get(node.entity_type, 0) + 1

    # Top 10 entities by chunk_ids count (most referenced)
    top_entities = sorted(all_nodes, key=lambda n: len(n.chunk_ids), reverse=True)[:10]

    return ApiResponse(
        data={
            "index_id": index_id,
            "index_type": idx.index_type.value,
            "node_count": len(all_nodes),
            "edge_count": graph_store.edge_count(index_id),
            "entity_types": [{"type": t, "count": c} for t, c in sorted(entity_types.items())],
            "top_entities": [
                {"entity": n.entity, "type": n.entity_type, "chunk_count": len(n.chunk_ids)}
                for n in top_entities
            ],
        }
    )


@router.get("/indexes/{index_id}/entities")
async def list_index_entities(
    index_id: str,
    _user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    entity_type: str | None = Query(None),
) -> ApiResponse[list[dict]]:
    """List extracted entities for a graph/hybrid index (paginated).

    Optional ?entity_type= filter. Returns 404 if index not found.
    Returns 400 if index is not graph/hybrid type.
    """
    store = get_rag_store()
    idx = store.get_index(index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")
    if idx.index_type.value not in ("graph", "hybrid"):
        raise HTTPException(status_code=400, detail="Index is not a graph or hybrid type")

    graph_store = get_graph_store()
    nodes, total = graph_store.list_nodes(
        index_id, page=page, per_page=per_page, entity_type=entity_type
    )

    return ApiResponse(
        data=[n.to_dict() for n in nodes],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/indexes/{index_id}/relationships")
async def list_index_relationships(
    index_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    predicate: str | None = Query(None),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[dict]]:
    """List extracted relationships for a graph/hybrid index (paginated).

    Optional ?predicate= filter. Returns 404 if index not found.
    Returns 400 if index is not graph/hybrid type.
    """
    store = get_rag_store()
    idx = store.get_index(index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")
    if idx.index_type.value not in ("graph", "hybrid"):
        raise HTTPException(status_code=400, detail="Index is not a graph or hybrid type")

    graph_store = get_graph_store()

    # Resolve subject/object entity names for each edge
    edges, total = graph_store.list_edges(
        index_id, page=page, per_page=per_page, predicate=predicate
    )
    all_nodes = graph_store.get_all_nodes(index_id)
    node_id_to_name = {n.id: n.entity for n in all_nodes}

    edges_out = []
    for e in edges:
        d = e.to_dict()
        d["subject_entity"] = node_id_to_name.get(e.subject_id, e.subject_id)
        d["object_entity"] = node_id_to_name.get(e.object_id, e.object_id)
        edges_out.append(d)

    return ApiResponse(
        data=edges_out,
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )
