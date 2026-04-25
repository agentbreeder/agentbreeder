"""Tests for engine/resolver.py — dependency resolution."""

from __future__ import annotations

from engine.config_parser import AgentConfig, FrameworkType
from engine.resolver import resolve_dependencies


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "gpt-4o"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestResolveDependencies:
    def test_stub_returns_config_unchanged(self) -> None:
        """v0.1 resolver stub should return config unchanged."""
        config = _make_config()
        resolved = resolve_dependencies(config)
        assert resolved.name == config.name
        assert resolved.version == config.version

    def test_with_tool_refs(self) -> None:
        config = _make_config(tools=[{"ref": "tools/zendesk"}, {"ref": "tools/search"}])
        resolved = resolve_dependencies(config)
        assert len(resolved.tools) == 2
        assert resolved.tools[0].ref == "tools/zendesk"

    def test_with_knowledge_base_refs(self) -> None:
        config = _make_config(knowledge_bases=[{"ref": "kb/docs"}])
        resolved = resolve_dependencies(config)
        assert len(resolved.knowledge_bases) == 1

    def test_no_refs_passes(self) -> None:
        config = _make_config()
        resolved = resolve_dependencies(config)
        assert resolved.tools == []
        assert resolved.knowledge_bases == []


# ---------------------------------------------------------------------------
# KB resolution tests
# ---------------------------------------------------------------------------


class TestResolveKnowledgeBases:
    """Tests for _resolve_kb_index_ids and KB env-var injection."""

    def test_kb_index_id_injected_as_env_var_when_store_has_index(self, monkeypatch) -> None:
        """When the RAGStore contains a matching index, KB_INDEX_IDS is set in env_vars."""
        from unittest.mock import MagicMock


        mock_idx = MagicMock()
        mock_idx.name = "product-docs"
        mock_idx.id = "uuid-1234"

        mock_store = MagicMock()
        mock_store.list_indexes.return_value = ([mock_idx], 1)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "engine.resolver.get_rag_store", return_value=mock_store, create=True
        ):
            import api.services.rag_service as _rag_mod

            # Monkey-patch the import inside resolver
            from engine.resolver import _resolve_kb_index_ids as _fn
            original = getattr(_rag_mod, "get_rag_store", None)
            _rag_mod.get_rag_store = lambda: mock_store
            try:
                result = _fn([MagicMock(ref="kb/product-docs")])
            finally:
                if original is not None:
                    _rag_mod.get_rag_store = original

        assert result == ["uuid-1234"]

    def test_kb_slug_fallback_when_store_unavailable(self, monkeypatch) -> None:
        """When the RAGStore has no matching index, the slug is returned as a pass-through."""
        from unittest.mock import MagicMock

        # Simulate store not having the index (empty list)
        import api.services.rag_service as _rag_mod
        from engine.resolver import _resolve_kb_index_ids
        original = _rag_mod.get_rag_store

        mock_store = MagicMock()
        mock_store.list_indexes.return_value = ([], 0)
        _rag_mod.get_rag_store = lambda: mock_store
        try:
            result = _resolve_kb_index_ids([MagicMock(ref="kb/return-policy")])
        finally:
            _rag_mod.get_rag_store = original

        # Falls back to slug
        assert result == ["return-policy"]

    def test_resolve_dependencies_injects_kb_index_ids_env_var(self, monkeypatch) -> None:
        """resolve_dependencies sets KB_INDEX_IDS in deploy.env_vars."""
        from unittest.mock import MagicMock

        import api.services.rag_service as _rag_mod

        mock_idx = MagicMock()
        mock_idx.name = "docs"
        mock_idx.id = "abc-999"
        mock_store = MagicMock()
        mock_store.list_indexes.return_value = ([mock_idx], 1)

        original = _rag_mod.get_rag_store
        _rag_mod.get_rag_store = lambda: mock_store
        try:
            config = _make_config(knowledge_bases=[{"ref": "kb/docs"}])
            resolved = resolve_dependencies(config)
        finally:
            _rag_mod.get_rag_store = original

        assert resolved.deploy.env_vars is not None
        assert "KB_INDEX_IDS" in resolved.deploy.env_vars
        assert resolved.deploy.env_vars["KB_INDEX_IDS"] == "abc-999"

    def test_resolve_dependencies_multiple_kbs(self, monkeypatch) -> None:
        """Multiple KB refs produce a comma-separated KB_INDEX_IDS value."""
        from unittest.mock import MagicMock

        import api.services.rag_service as _rag_mod

        idx_a = MagicMock()
        idx_a.name = "docs"
        idx_a.id = "id-A"

        idx_b = MagicMock()
        idx_b.name = "policy"
        idx_b.id = "id-B"

        mock_store = MagicMock()
        mock_store.list_indexes.return_value = ([idx_a, idx_b], 2)

        original = _rag_mod.get_rag_store
        _rag_mod.get_rag_store = lambda: mock_store
        try:
            config = _make_config(
                knowledge_bases=[{"ref": "kb/docs"}, {"ref": "kb/policy"}]
            )
            resolved = resolve_dependencies(config)
        finally:
            _rag_mod.get_rag_store = original

        kb_ids = resolved.deploy.env_vars["KB_INDEX_IDS"].split(",")
        assert "id-A" in kb_ids
        assert "id-B" in kb_ids
        assert len(kb_ids) == 2

    def test_resolve_dependencies_no_kb_does_not_set_env_var(self) -> None:
        """When there are no knowledge_bases, KB_INDEX_IDS is not injected."""
        config = _make_config()
        resolved = resolve_dependencies(config)
        env_vars = resolved.deploy.env_vars or {}
        assert "KB_INDEX_IDS" not in env_vars
