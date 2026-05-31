"""Assemble a ``KB_PGVECTOR_DSN`` from a provisioned managed-Postgres resources dict.

P2 of the cloud-agnostic deployment epic (#523). The infra provisioners
(``engine/provisioners/{aws,gcp,azure}.py``) return a cloud-specific resources
dict describing the Postgres they created. This module turns that — plus the
password resolved from the cloud secret store at deploy time — into a uniform
``postgresql://`` DSN that the agent container reads as ``KB_PGVECTOR_DSN``
(consumed by the runtime in ``engine/runtimes/templates/langgraph_server.py``
and the pgvector backend in ``api/services/pgvector_rag_backend.py``).

Pure string assembly — no cloud SDK calls — so it is fully unit-testable. The
host fields differ per cloud:

* AWS RDS         → ``resources["endpoint"]``
* GCP Cloud SQL   → ``resources["private_ip"]`` (captured by the provisioner)
* Azure Flexible  → ``resources["fqdn"]``
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

DEFAULT_PG_PORT = 5432
DEFAULT_DB_NAME = "agentbreeder"
DEFAULT_DB_USER = "agentbreeder"


def build_pgvector_dsn(
    host: str,
    database: str = DEFAULT_DB_NAME,
    user: str = DEFAULT_DB_USER,
    password: str = "",
    port: int = DEFAULT_PG_PORT,
) -> str:
    """Build a ``postgresql://`` DSN with URL-encoded credentials.

    User and password are percent-encoded so a generated password containing
    ``@``, ``:``, ``/`` etc. cannot corrupt the URL.
    """
    if not host:
        raise ValueError("pgvector DSN requires a host")
    userinfo = f"{quote(user, safe='')}:{quote(password, safe='')}"
    return f"postgresql://{userinfo}@{host}:{port}/{database}"


def pgvector_host_from_resources(cloud: str, resources: dict[str, Any]) -> str | None:
    """Return the connectable host for the provisioned DB, or ``None``."""
    if cloud == "aws":
        return resources.get("endpoint")
    if cloud == "gcp":
        return resources.get("private_ip")
    if cloud == "azure":
        return resources.get("fqdn")
    return None


def pgvector_dsn_from_resources(
    cloud: str,
    resources: dict[str, Any],
    password: str,
) -> str | None:
    """Assemble a ``KB_PGVECTOR_DSN`` from a provisioner resources dict.

    Returns ``None`` when the cloud is unknown or the host has not been
    captured (so callers can fall back to the explicit ``backend_url`` path).
    """
    host = pgvector_host_from_resources(cloud, resources)
    if not host:
        return None

    if cloud == "aws":
        database = resources.get("db_name", DEFAULT_DB_NAME)
        user = resources.get("user", DEFAULT_DB_USER)
        port = int(resources.get("port", DEFAULT_PG_PORT))
    elif cloud == "gcp":
        database = resources.get("database", DEFAULT_DB_NAME)
        user = resources.get("user", DEFAULT_DB_USER)
        port = DEFAULT_PG_PORT
    elif cloud == "azure":
        database = resources.get("database", DEFAULT_DB_NAME)
        user = resources.get("admin_user", DEFAULT_DB_USER)
        port = DEFAULT_PG_PORT
    else:
        return None

    return build_pgvector_dsn(host=host, database=database, user=user, password=password, port=port)
