"""Unit tests for the KB_PGVECTOR_DSN builder (P2)."""

from __future__ import annotations

import pytest

from engine.deployers._pgvector_dsn import (
    build_pgvector_dsn,
    pgvector_dsn_from_resources,
    pgvector_host_from_resources,
)


def test_build_dsn_basic():
    dsn = build_pgvector_dsn("db.host", database="kb", user="ab", password="pw", port=5432)
    assert dsn == "postgresql://ab:pw@db.host:5432/kb"


def test_build_dsn_url_encodes_password_special_chars():
    # A generated password with @ : / must not corrupt the URL.
    dsn = build_pgvector_dsn("h", database="d", user="u", password="p@ss:w/rd")
    assert "p%40ss%3Aw%2Frd" in dsn
    assert dsn == "postgresql://u:p%40ss%3Aw%2Frd@h:5432/d"


def test_build_dsn_requires_host():
    with pytest.raises(ValueError, match="host"):
        build_pgvector_dsn("", password="x")


@pytest.mark.parametrize(
    "cloud,resources,expected_host",
    [
        ("aws", {"endpoint": "rds.aws"}, "rds.aws"),
        ("gcp", {"private_ip": "10.0.0.5"}, "10.0.0.5"),
        ("azure", {"fqdn": "pg.azure"}, "pg.azure"),
        ("gcp", {"connection_name": "p:r:i"}, None),  # no private_ip captured
        ("local", {"endpoint": "x"}, None),
    ],
)
def test_host_from_resources(cloud, resources, expected_host):
    assert pgvector_host_from_resources(cloud, resources) == expected_host


def test_dsn_from_resources_aws():
    res = {"endpoint": "rds.aws", "port": 5432, "db_name": "kb", "user": "ab"}
    assert pgvector_dsn_from_resources("aws", res, "pw") == "postgresql://ab:pw@rds.aws:5432/kb"


def test_dsn_from_resources_gcp_uses_private_ip():
    res = {"private_ip": "10.0.0.5", "database": "kb", "user": "ab"}
    assert pgvector_dsn_from_resources("gcp", res, "pw") == "postgresql://ab:pw@10.0.0.5:5432/kb"


def test_dsn_from_resources_azure_uses_admin_user():
    res = {"fqdn": "pg.azure", "database": "kb", "admin_user": "ab"}
    assert pgvector_dsn_from_resources("azure", res, "pw") == "postgresql://ab:pw@pg.azure:5432/kb"


def test_dsn_from_resources_returns_none_without_host():
    # GCP without a captured private_ip → caller falls back to backend_url.
    assert pgvector_dsn_from_resources("gcp", {"connection_name": "p:r:i"}, "pw") is None


def test_dsn_from_resources_unknown_cloud():
    assert pgvector_dsn_from_resources("digitalocean", {"endpoint": "x"}, "pw") is None


# ---------------------------------------------------------------------------
# needs_managed_pgvector / pgvector_secret_ref
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from engine.deployers._pgvector_dsn import (  # noqa: E402
    needs_managed_memory_postgres,
    needs_managed_pgvector,
    pgvector_secret_ref,
)


def _config(kbs, cloud, memory=None):
    return SimpleNamespace(
        knowledge_bases=kbs,
        memory=memory,
        deploy=SimpleNamespace(cloud=SimpleNamespace(value=cloud)),
    )


def test_needs_managed_pgvector_true_for_kb_without_backend_url_on_cloud():
    cfg = _config([SimpleNamespace(ref="kb/docs", backend_url=None)], "aws")
    assert needs_managed_pgvector(cfg) is True


def test_needs_managed_pgvector_false_when_backend_url_pinned():
    cfg = _config([SimpleNamespace(ref="kb/docs", backend_url="postgresql://x")], "gcp")
    assert needs_managed_pgvector(cfg) is False


def test_needs_managed_pgvector_false_without_kbs():
    assert needs_managed_pgvector(_config([], "azure")) is False


def test_needs_managed_pgvector_false_for_local():
    cfg = _config([SimpleNamespace(ref="kb/docs", backend_url=None)], "local")
    assert needs_managed_pgvector(cfg) is False


def test_needs_managed_memory_postgres_true_for_pg_backend_without_url():
    cfg = _config([], "aws", memory=SimpleNamespace(backend="postgresql", backend_url=None))
    assert needs_managed_memory_postgres(cfg) is True


def test_needs_managed_memory_postgres_false_for_redis_backend():
    cfg = _config([], "aws", memory=SimpleNamespace(backend="redis", backend_url=None))
    assert needs_managed_memory_postgres(cfg) is False


def test_needs_managed_memory_postgres_false_when_backend_url_pinned():
    cfg = _config(
        [], "gcp", memory=SimpleNamespace(backend="postgresql", backend_url="postgresql://x")
    )
    assert needs_managed_memory_postgres(cfg) is False


def test_needs_managed_memory_postgres_false_without_memory():
    assert needs_managed_memory_postgres(_config([], "azure", memory=None)) is False


def test_needs_managed_memory_postgres_false_for_local():
    cfg = _config([], "local", memory=SimpleNamespace(backend="postgresql", backend_url=None))
    assert needs_managed_memory_postgres(cfg) is False


def test_pgvector_secret_ref_per_cloud():
    assert pgvector_secret_ref("aws", {"secret_arn": "arn:x"}) == "arn:x"
    assert pgvector_secret_ref("gcp", {"password_secret": "projects/p/secrets/s"}) == (
        "projects/p/secrets/s"
    )
    assert pgvector_secret_ref("azure", {"password_secret_uri": "https://v/secrets/s"}) == (
        "https://v/secrets/s"
    )
    assert pgvector_secret_ref("aws", {}) is None
    assert pgvector_secret_ref("nope", {"secret_arn": "x"}) is None
