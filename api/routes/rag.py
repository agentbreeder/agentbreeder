"""RAG Builder API routes — vector index management, ingestion, and search."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from api.models.schemas import ApiMeta, ApiResponse
from api.services.rag_service import get_rag_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


@router.post("/indexes", status_code=201)
async def create_index(
    body: dict[str, Any],
) -> ApiResponse[dict]:
    """Create a new vector index."""
    store = get_rag_store()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
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
    return ApiResponse(data=idx.to_dict())


@router.get("/indexes")
async def list_indexes(
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
async def get_index(index_id: str) -> ApiResponse[dict]:
    """Get a vector index by ID."""
    store = get_rag_store()
    idx = store.get_index(index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")
    return ApiResponse(data=idx.to_dict())


@router.delete("/indexes/{index_id}")
async def delete_index(index_id: str) -> ApiResponse[dict]:
    """Delete a vector index."""
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
    files: list[UploadFile] = File(...),
) -> ApiResponse[dict]:
    """Upload and ingest files into a vector index.

    Accepted formats: PDF, TXT, MD, CSV, JSON.
    Files are chunked, embedded, and indexed in the background.
    """
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
async def search(body: dict[str, Any]) -> ApiResponse[dict]:
    """Search across a vector index using hybrid vector + text search.

    Request body:
    - index_id: str (required)
    - query: str (required)
    - top_k: int (default 10)
    - vector_weight: float (default 0.7)
    - text_weight: float (default 0.3)
    """
    store = get_rag_store()

    index_id = body.get("index_id")
    query = body.get("query")
    if not index_id or not query:
        raise HTTPException(status_code=400, detail="index_id and query are required")

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
