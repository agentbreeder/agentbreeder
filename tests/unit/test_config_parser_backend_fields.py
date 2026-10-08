import tempfile
from pathlib import Path

import pytest

from engine.config_parser import (
    ConfigParseError,
    MemoryConfig,
    parse_config,
    validate_config,
)


def _write_yaml(content: str) -> Path:
    """Write YAML content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# Pydantic-model unit tests (existing)
# ---------------------------------------------------------------------------


def test_memory_accepts_backend_and_url():
    m = MemoryConfig(stores=["mem/sessions"], backend="redis", backend_url="redis://h:6379")
    assert m.backend == "redis"
    assert m.backend_url == "redis://h:6379"


def test_memory_backend_fields_optional():
    m = MemoryConfig(stores=["mem/x"])
    assert m.backend is None
    assert m.backend_url is None


# ---------------------------------------------------------------------------
# Schema-validation entry-point tests (Fix 3)
# ---------------------------------------------------------------------------

_MINIMAL_VALID_WITH_BACKEND_FIELDS = """\
name: backend-fields-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
memory:
  stores:
    - mem/sessions
  backend: redis
  backend_url: redis://localhost:6379
"""

_INVALID_MEMORY_BACKEND = """\
name: bad-backend-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
memory:
  stores:
    - mem/sessions
  backend: cassandra
"""


def test_schema_validation_passes_with_backend_fields():
    """Valid config with memory.backend=redis and memory.backend_url passes schema + Pydantic."""
    path = _write_yaml(_MINIMAL_VALID_WITH_BACKEND_FIELDS)
    result = validate_config(path)
    assert result.valid, f"Expected valid config but got errors: {result.errors}"
    assert result.config is not None
    assert result.config.memory is not None
    assert result.config.memory.backend == "redis"
    assert result.config.memory.backend_url == "redis://localhost:6379"


def test_schema_validation_rejects_invalid_memory_backend():
    """Config with memory.backend='cassandra' is rejected by the schema enum (not in [redis, postgresql])."""
    path = _write_yaml(_INVALID_MEMORY_BACKEND)
    with pytest.raises(ConfigParseError):
        parse_config(path)


_REMOVED_KNOWLEDGE_BASES = """\
name: kb-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
knowledge_bases:
  - ref: kb/product-docs
"""


def test_schema_rejects_removed_knowledge_bases_field():
    """knowledge_bases was removed from agent.yaml; the schema must reject it."""
    path = _write_yaml(_REMOVED_KNOWLEDGE_BASES)
    with pytest.raises(ConfigParseError):
        parse_config(path)
