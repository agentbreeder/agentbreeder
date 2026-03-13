"""GCP Secret Manager backend.

Requires: pip install google-cloud-secret-manager
Optional env vars:
    GOOGLE_APPLICATION_CREDENTIALS — path to service account JSON
    GOOGLE_CLOUD_PROJECT           — GCP project ID
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from engine.secrets.base import SecretEntry, SecretsBackend

logger = logging.getLogger(__name__)

_GCP_IMPORT_ERROR = (
    "GCP Secret Manager backend requires google-cloud-secret-manager. "
    "Install it with: pip install google-cloud-secret-manager"
)


def _client():  # type: ignore[return]
    try:
        from google.cloud import secretmanager  # type: ignore[import-untyped]

        return secretmanager.SecretManagerServiceClient()
    except ImportError as exc:
        raise ImportError(_GCP_IMPORT_ERROR) from exc


class GCPSecretManagerBackend(SecretsBackend):
    """Secrets stored in GCP Secret Manager.

    Secret names follow the GCP convention:
        projects/{project}/secrets/{name}/versions/latest

    The logical name (e.g. "OPENAI_API_KEY") is stored as the GCP secret ID,
    optionally prefixed (e.g. "garden-OPENAI_API_KEY").
    """

    def __init__(
        self,
        project_id: str | None = None,
        prefix: str = "garden-",
    ) -> None:
        self._project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not self._project:
            raise ValueError(
                "GCP project ID required. Pass project_id= or set GOOGLE_CLOUD_PROJECT."
            )
        self._prefix = prefix

    @property
    def backend_name(self) -> str:
        return "gcp"

    def _secret_id(self, name: str) -> str:
        """Convert logical name to GCP secret ID (alphanumeric + hyphens/underscores)."""
        gcp_name = f"{self._prefix}{name}" if self._prefix else name
        # GCP secret IDs: letters, digits, hyphens, underscores — max 255 chars
        return gcp_name[:255]

    def _parent(self) -> str:
        return f"projects/{self._project}"

    def _secret_path(self, name: str) -> str:
        return f"{self._parent()}/secrets/{self._secret_id(name)}"

    def _version_path(self, name: str, version: str = "latest") -> str:
        return f"{self._secret_path(name)}/versions/{version}"

    async def get(self, name: str) -> str | None:
        client = _client()
        try:
            response = client.access_secret_version(request={"name": self._version_path(name)})
            return response.payload.data.decode("utf-8")
        except Exception as exc:
            # google.api_core.exceptions.NotFound → return None
            if "NOT_FOUND" in str(exc) or "404" in str(exc):
                return None
            logger.error("Failed to get secret '%s' from GCP: %s", name, exc)
            raise

    async def set(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        client = _client()
        payload = {"data": value.encode("utf-8")}

        try:
            # Try to add a new version to existing secret
            client.add_secret_version(
                request={"parent": self._secret_path(name), "payload": payload}
            )
            logger.info("Added new version for secret '%s' in GCP", name)
        except Exception as exc:
            if "NOT_FOUND" not in str(exc) and "404" not in str(exc):
                raise
            # Secret doesn't exist — create it, then add version
            labels = {k.lower(): v.lower() for k, v in (tags or {}).items()}
            client.create_secret(
                request={
                    "parent": self._parent(),
                    "secret_id": self._secret_id(name),
                    "secret": {
                        "replication": {"automatic": {}},
                        "labels": labels,
                    },
                }
            )
            client.add_secret_version(
                request={"parent": self._secret_path(name), "payload": payload}
            )
            logger.info("Created secret '%s' in GCP Secret Manager", name)

    async def delete(self, name: str) -> None:
        client = _client()
        try:
            client.delete_secret(request={"name": self._secret_path(name)})
            logger.info("Deleted secret '%s' from GCP Secret Manager", name)
        except Exception as exc:
            if "NOT_FOUND" in str(exc) or "404" in str(exc):
                raise KeyError(f"Secret '{name}' not found in GCP Secret Manager") from exc
            raise

    async def list(self) -> list[SecretEntry]:
        client = _client()
        entries: list[SecretEntry] = []
        request = {"parent": self._parent()}
        if self._prefix:
            request["filter"] = f"name:{self._prefix}"  # type: ignore[assignment]

        for secret in client.list_secrets(request=request):
            raw_id = secret.name.split("/")[-1]  # e.g. "garden-OPENAI_API_KEY"
            logical = raw_id.removeprefix(self._prefix) if self._prefix else raw_id
            created = secret.create_time
            entries.append(
                SecretEntry(
                    name=logical,
                    masked_value="••••(gcp)",
                    backend="gcp",
                    created_at=_dt(created),
                    updated_at=None,
                    tags=dict(secret.labels),
                )
            )
        return entries


def _dt(ts: object) -> datetime | None:
    if ts is None:
        return None
    try:
        from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

        if isinstance(ts, Timestamp):
            return datetime.fromtimestamp(ts.seconds, tz=UTC)
    except ImportError:
        pass
    if isinstance(ts, datetime):
        return ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
    return None
