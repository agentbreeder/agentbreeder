"""End-to-end CLI: verified tokens, pending approval exit code, approve/reject, audit output."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from conftest import enable_cedar, principal_entry, v2_manifest_data, write_private
from test_deployer import ISSUER, KEY, jwk, token

from agentbreeder import cli
from agentbreeder import deployer as deployer_module

pytest.importorskip("cedarpy")


@pytest.fixture
def setup(identity_env, tmp_path, monkeypatch):
    enable_cedar(identity_env)
    write_private(identity_env.directory / "jwks.json", json.dumps({"keys": [jwk(KEY)]}))
    identity_env.config["deployer"] = {
        "issuers": [
            {
                "issuer": ISSUER,
                "audience": "agentbreeder",
                "jwks_file": "jwks.json",
                "kind": "human",
            }
        ]
    }
    identity_env.principals["agent-1"] = principal_entry(
        attributes={"acme/data_classification": "confidential", "acme/risk_tier": "high"}
    )
    identity_env.write()
    fake = Mock(context="local-test")
    running = {"Id": "c", "State": {"Running": True}}
    fake.inspect.side_effect = [None, running, running]
    fake.endpoint.return_value = "http://127.0.0.1:1"
    fake.healthy.return_value = True
    monkeypatch.setattr(cli, "Docker", lambda: fake)
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(json.dumps(v2_manifest_data()))
    return manifest, ["--state-dir", str(tmp_path / "state")], fake


def as_user(monkeypatch, subject):
    monkeypatch.setenv(deployer_module.TOKEN_ENV, token({"sub": subject}))


def test_cli_maker_checker_flow(setup, monkeypatch, capsys):
    manifest, state, fake = setup
    as_user(monkeypatch, "dana")
    assert cli.main(["deploy", str(manifest), "--local", *state]) == cli.EXIT_PENDING
    out = capsys.readouterr().out
    approval_id = out.split()[1]
    assert "agentbreeder approve" in out
    fake.authorize.assert_not_called()
    assert cli.main(["approve", approval_id, *state]) == 2  # dana cannot self-approve
    assert "[SELF_APPROVAL]" in capsys.readouterr().err
    as_user(monkeypatch, "carol")
    assert cli.main(["approve", approval_id, *state, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "approved"
    as_user(monkeypatch, "dana")
    assert cli.main(["deploy", str(manifest), "--local", *state, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ready" and result["gate"]["actor_verified"] is True
    assert result["gate"]["checks_unavailable"] == []
    assert cli.main(["status", result["id"], *state]) == 0
    assert "Identity: compliant" in capsys.readouterr().out
    assert (
        cli.main(["audit", "list", *state, "--attempt", result["gate"]["attempt_id"], "--json"])
        == 0
    )
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["action"] == "attempt_started"
    assert any(r["action"] == "approval_consumed" for r in rows)
    assert cli.main(["audit", "list", *state]) == 0
    assert "approval_granted" in capsys.readouterr().out


def test_cli_reject(setup, monkeypatch, capsys):
    manifest, state, _ = setup
    as_user(monkeypatch, "dana")
    assert cli.main(["deploy", str(manifest), "--local", *state]) == cli.EXIT_PENDING
    approval_id = capsys.readouterr().out.split()[1]
    as_user(monkeypatch, "carol")
    assert cli.main(["reject", approval_id, *state]) == 0
    assert "rejected" in capsys.readouterr().out


def test_cli_audit_verify_detects_tampering(setup, monkeypatch, capsys):
    manifest, state, _ = setup
    as_user(monkeypatch, "dana")
    cli.main(["deploy", str(manifest), "--local", *state])
    capsys.readouterr()
    import sqlite3

    db = sqlite3.connect(state[1] + "/deployments.sqlite3")
    db.execute("UPDATE audit SET decision='allow' WHERE seq=2")
    db.commit()
    db.close()
    assert cli.main(["audit", "verify", *state]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out and "Limitation" in out


def test_cli_login_without_device_issuer(setup, capsys):
    assert cli.main(["login"]) == 2
    assert "[DEPLOYER_UNVERIFIED]" in capsys.readouterr().err


def test_cli_bad_operator_config(identity_env, tmp_path, capsys):
    identity_env.config = {"enforcement": "off"}
    identity_env.write()
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(json.dumps(v2_manifest_data()))
    assert cli.main(["deploy", str(manifest), "--local", "--state-dir", str(tmp_path / "s")]) == 2
    assert "Invalid operator config" in capsys.readouterr().err
