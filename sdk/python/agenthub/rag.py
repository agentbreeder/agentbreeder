"""RAG index client for the AgentBreeder SDK.

Provides a thin wrapper over the ``/api/v1/rag/*`` HTTP API for ingesting
files into a registered RAG index and running semantic search against it.

Usage::

    from agenthub import RagIndex

    index = RagIndex("agentbreeder-knowledge", token=token)
    job = index.ingest(["./docs/intro.md", "./docs/quickstart.pdf"])
    hits = index.search("how do I deploy an agent?", top_k=5)
    for h in hits:
        print(h["score"], h["source"], h["text"][:120])
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

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

    def ingest(self, files: list[str | Path]) -> IngestResult:
        """Upload and ingest one or more files.

        Accepted formats: PDF, TXT, MD, CSV, JSON. Posts multipart/form-data
        to ``/api/v1/rag/indexes/{id}/ingest``.
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
