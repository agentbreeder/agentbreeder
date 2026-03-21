"""AWS Secrets Manager backend.

Requires: pip install boto3
Optional env vars:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
    or any standard boto3 credential chain (IAM role, ~/.aws/credentials, etc.)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from engine.secrets.base import SecretEntry, SecretsBackend

if TYPE_CHECKING:
    pass  # avoid importing boto3 at module level

logger = logging.getLogger(__name__)

_BOTO_IMPORT_ERROR = (
    "AWS Secrets Manager backend requires boto3. Install it with: pip install boto3"
)


def _client(region: str):  # type: ignore[return]
    """Create a boto3 Secrets Manager client. Raises ImportError if boto3 not installed."""
    try:
        import boto3  # type: ignore[import-untyped]

        return boto3.client("secretsmanager", region_name=region)
    except ImportError as exc:
        raise ImportError(_BOTO_IMPORT_ERROR) from exc


class AWSSecretsManagerBackend(SecretsBackend):
    """Secrets stored in AWS Secrets Manager.

    Secret names are stored as plain strings. If the secret value is a JSON
    object with a single key ``value``, that value is returned. Otherwise the
    raw string value is returned. This matches the common pattern used by
    Secrets Manager secrets created via the console.
    """

    def __init__(self, region: str = "us-east-1", prefix: str = "agentbreeder/") -> None:
        self._region = region
        # e.g. prefix="agentbreeder/" → "OPENAI_API_KEY" stored as "agentbreeder/OPENAI_API_KEY"
        self._prefix = prefix

    @property
    def backend_name(self) -> str:
        return "aws"

    def _full_name(self, name: str) -> str:
        return f"{self._prefix}{name}" if self._prefix else name

    async def get(self, name: str) -> str | None:
        client = _client(self._region)
        try:
            resp = client.get_secret_value(SecretId=self._full_name(name))
            raw = resp.get("SecretString", "")
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "value" in parsed:
                    return parsed["value"]
            except (json.JSONDecodeError, TypeError):
                pass
            return raw
        except client.exceptions.ResourceNotFoundException:
            return None
        except Exception as exc:
            logger.error("Failed to get secret '%s' from AWS: %s", name, exc)
            raise

    async def set(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        client = _client(self._region)
        full_name = self._full_name(name)
        secret_string = json.dumps({"value": value})
        aws_tags = [{"Key": k, "Value": v} for k, v in (tags or {}).items()]

        try:
            client.put_secret_value(SecretId=full_name, SecretString=secret_string)
            logger.info("Updated secret '%s' in AWS Secrets Manager", full_name)
        except client.exceptions.ResourceNotFoundException:
            kwargs: dict = {
                "Name": full_name,
                "SecretString": secret_string,
                "Description": f"Managed by AgentBreeder (key: {name})",
            }
            if aws_tags:
                kwargs["Tags"] = aws_tags
            client.create_secret(**kwargs)
            logger.info("Created secret '%s' in AWS Secrets Manager", full_name)

    async def delete(self, name: str) -> None:
        client = _client(self._region)
        full_name = self._full_name(name)
        try:
            client.delete_secret(
                SecretId=full_name,
                ForceDeleteWithoutRecovery=False,  # use 7-day recovery window
            )
            logger.info("Scheduled deletion of secret '%s'", full_name)
        except client.exceptions.ResourceNotFoundException as exc:
            raise KeyError(f"Secret '{name}' not found in AWS Secrets Manager") from exc

    async def list(self) -> list[SecretEntry]:
        client = _client(self._region)
        entries: list[SecretEntry] = []
        paginator = client.get_paginator("list_secrets")

        filter_args = []
        if self._prefix:
            filter_args = [{"Key": "name", "Values": [self._prefix]}]

        for page in paginator.paginate(Filters=filter_args):
            for secret in page.get("SecretList", []):
                raw_name = secret["Name"]
                # Strip prefix to get the logical name
                logical = raw_name.removeprefix(self._prefix) if self._prefix else raw_name
                created = secret.get("CreatedDate")
                updated = secret.get("LastChangedDate")
                entries.append(
                    SecretEntry(
                        name=logical,
                        masked_value="••••(aws)",
                        backend="aws",
                        created_at=_to_utc(created),
                        updated_at=_to_utc(updated),
                        tags={t["Key"]: t["Value"] for t in secret.get("Tags", [])},
                    )
                )
        return entries


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
