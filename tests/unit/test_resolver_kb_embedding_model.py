"""Unit tests for KB_EMBEDDING_MODEL resolution (P2).

The deployed runtime must embed queries with the same model used at ingest;
the resolver pins it from the resolved RAG index.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import engine.resolver as resolver
from engine.config_parser import KnowledgeBaseRef


def _store_with(indexes):
    store = MagicMock()
    store.list_indexes.return_value = (indexes, len(indexes))
    return store


def test_resolves_embedding_model_by_slug(monkeypatch):
    idx = SimpleNamespace(
        name="product-docs", id="i1", embedding_model="openai/text-embedding-3-large"
    )
    monkeypatch.setattr("api.services.rag_service.get_rag_store", lambda: _store_with([idx]))
    assert (
        resolver._resolve_kb_embedding_model([KnowledgeBaseRef(ref="kb/product-docs")])
        == "openai/text-embedding-3-large"
    )


def test_returns_none_when_index_unresolvable(monkeypatch):
    monkeypatch.setattr("api.services.rag_service.get_rag_store", lambda: _store_with([]))
    assert resolver._resolve_kb_embedding_model([KnowledgeBaseRef(ref="kb/missing")]) is None


def test_returns_none_for_empty_refs():
    assert resolver._resolve_kb_embedding_model([]) is None


def test_returns_none_when_store_unavailable(monkeypatch):
    def _boom():
        raise RuntimeError("no store")

    monkeypatch.setattr("api.services.rag_service.get_rag_store", _boom)
    assert resolver._resolve_kb_embedding_model([KnowledgeBaseRef(ref="kb/x")]) is None
