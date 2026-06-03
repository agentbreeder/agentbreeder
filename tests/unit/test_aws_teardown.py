"""AWS data-backend teardown — bugs surfaced by real-cloud validation (2026-05-31).

Covers four teardown defects the mocked provision tests couldn't catch:
  1. _delete_rds must wait for the instance to be DELETED before dependents.
  2. _delete_security_group must retry the transient DependencyViolation
     (ENI release lag) instead of orphaning the SG.
  3. _delete_rds must remove the RDS subnet group + Secrets Manager secret.
  4. destroy_data_backend must NOT leave a final snapshot (ephemeral backend).

boto3 is patched at the ``_client`` boundary so no real AWS creds are needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")
pytest.importorskip("botocore")

from botocore.exceptions import ClientError  # noqa: E402

from engine.provisioners.aws import AWSProvisioner  # noqa: E402
from engine.provisioners.state import InfraState  # noqa: E402

_AB_TAGS = [{"Key": "AgentBreeder", "Value": "true"}]


def _client_factory(builders: dict[str, MagicMock]) -> Any:
    def _fn(service: str, region: str, fields: dict[str, Any]) -> MagicMock:
        return builders.setdefault(service, MagicMock())

    return _fn


def _state() -> InfraState:
    return InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="test",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "rds": {
                "db_instance_identifier": "ab-data",
                "subnet_group": "ab-data-subnets",
                "secret_arn": "arn:aws:secretsmanager:us-east-1:1:secret:ab/db-x",
            },
            "security_groups": {"db_sg_id": "sg-db"},
        },
    )


def _clients() -> dict[str, MagicMock]:
    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{"DBInstanceArn": "arn:rds", "DBInstanceStatus": "available"}]
    }
    rds.list_tags_for_resource.return_value = {"TagList": _AB_TAGS}
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": [{"Tags": _AB_TAGS}]}
    sm = MagicMock()
    return {"rds": rds, "ec2": ec2, "secretsmanager": sm}


def _dep_violation() -> ClientError:
    return ClientError(
        {"Error": {"Code": "DependencyViolation", "Message": "has a dependent object"}},
        "DeleteSecurityGroup",
    )


async def test_destroy_data_backend_skips_final_snapshot() -> None:
    clients = _clients()
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(clients)):
        await AWSProvisioner().destroy_data_backend(_state())
    kwargs = clients["rds"].delete_db_instance.call_args.kwargs
    assert kwargs.get("SkipFinalSnapshot") is True
    assert "FinalDBSnapshotIdentifier" not in kwargs


async def test_delete_rds_waits_then_removes_subnet_group_and_secret() -> None:
    clients = _clients()
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(clients)):
        await AWSProvisioner().destroy_data_backend(_state())
    clients["rds"].get_waiter.assert_any_call("db_instance_deleted")
    clients["rds"].delete_db_subnet_group.assert_called_once_with(
        DBSubnetGroupName="ab-data-subnets"
    )
    sm_kwargs = clients["secretsmanager"].delete_secret.call_args.kwargs
    assert sm_kwargs.get("ForceDeleteWithoutRecovery") is True


async def test_delete_security_group_retries_on_dependency_violation() -> None:
    clients = _clients()
    clients["ec2"].delete_security_group.side_effect = [_dep_violation(), None]
    with (
        patch("engine.provisioners.aws._client", side_effect=_client_factory(clients)),
        patch("engine.provisioners.aws.asyncio.sleep", return_value=None),
    ):
        await AWSProvisioner().destroy_data_backend(_state())
    assert clients["ec2"].delete_security_group.call_count == 2
