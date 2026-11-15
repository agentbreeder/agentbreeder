"""A bootstrap that writes several secrets (`writes_secrets`, #925: phoenix@1.1.0's read key)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_services_engine import CANARY, FAKEGW, STACK, FakeDocker, up, write_project

from agentbreeder import registry
from agentbreeder.operator_config import CONFIG_ENV
from agentbreeder.services.engine import _bootstrap_values
from agentbreeder.services.schema import ServiceError, ServiceSpec
from agentbreeder.services.state import ROOT_ENV, StackState

KEYS = {"minted": "jwt-one.a.b", "reader": "jwt-two.c.d"}


@pytest.fixture
def env(tmp_path, monkeypatch):  # as test_services_engine's
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", CANARY)
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    return write_project(tmp_path / "project")


def gateway(**bootstrap):
    spec = copy.deepcopy(FAKEGW)
    spec["bootstrap"] = {"command": ["python3", "-c", "print(1)"], **bootstrap}
    spec["outputs"]["reader_key"] = {"type": "secret", "secret": "reader", "operator_only": True}
    return spec


def boots(docker) -> int:
    return [e[1] for e in docker.events if e[0] == "oneshot"].count("gateway.bootstrap")


@pytest.fixture
def stacked(env):
    write_project(env, stack=copy.deepcopy(STACK), gateway=gateway(writes_secrets=list(KEYS)))
    return env


def secret_dir(result):
    return Path(result["state_dir"]).parents[2] / "service-secrets" / "gateway"


# -- schema ---------------------------------------------------------------------------------------


def test_written_lists_every_secret_in_order():
    spec = ServiceSpec.model_validate(gateway(writes_secrets=["minted", "reader"]))
    assert spec.bootstrap.written() == ("minted", "reader")
    assert {"minted", "reader"} <= spec.mounted_secrets()
    single = ServiceSpec.model_validate(gateway(writes_secret="reader"))
    assert single.bootstrap.written() == ("reader",)


@pytest.mark.parametrize(
    "bootstrap, message",
    [
        ({"writes_secret": "minted", "writes_secrets": ["a", "reader"]}, "at most one of"),
        ({"writes_secrets": ["reader", "reader"]}, "must be unique"),
        ({"writes_secrets": ["reader", "Bad Name"]}, "must be unique secret names"),
        ({"writes_secrets": ["reader"]}, "at least 2"),
        ({"writes_secrets": ["reader", "a", "b", "c", "d"]}, "at most 4"),
        ({"writes_secrets": ["reader", "master-key"]}, "must not also be declared"),
    ],
)
def test_bad_writes_secrets_are_refused(bootstrap, message):
    with pytest.raises(ValueError, match=message):
        ServiceSpec.model_validate(gateway(**bootstrap))


def test_a_bootstrap_that_writes_nothing_is_refused():
    spec = gateway()
    del spec["outputs"]["reader_key"]
    with pytest.raises(ValueError, match="must name the secrets it writes"):
        ServiceSpec.model_validate(spec)


def test_only_bootstrap_writes_several():
    spec = gateway(writes_secrets=["minted", "reader"])
    spec["init"] = {"command": ["true"], "writes_secrets": ["x", "y"]}
    with pytest.raises(ValueError, match="only bootstrap"):
        ServiceSpec.model_validate(spec)


def test_an_output_naming_an_unwritten_secret_is_refused():
    spec = gateway(writes_secrets=["minted", "other"])  # reader_key names `reader`
    with pytest.raises(ValueError, match="unknown secret reader"):
        ServiceSpec.model_validate(spec)


# -- the bootstrap's stdout -----------------------------------------------------------------------


def test_one_secret_is_the_whole_output():
    assert _bootstrap_values("svc", ("key",), b"raw value") == {"key": b"raw value"}


def test_several_secrets_come_from_one_json_object():
    values = _bootstrap_values("svc", tuple(KEYS), json.dumps(KEYS).encode())
    assert values == {name: value.encode() for name, value in KEYS.items()}
    assert list(values) == list(KEYS)


@pytest.mark.parametrize(
    "output, message",
    [
        (b"jwt-one.a.b", "did not write a JSON object"),
        (b'["jwt-one.a.b"]', "must write exactly these secrets: minted, reader"),
        (json.dumps({"minted": "jwt-one.a.b"}).encode(), "exactly these secrets"),
        (json.dumps({**KEYS, "extra": "jwt-three"}).encode(), "exactly these secrets"),
        (json.dumps({**KEYS, "reader": 5}).encode(), "invalid value for reader"),
        (json.dumps({**KEYS, "reader": ""}).encode(), "invalid value for reader"),
        (json.dumps({**KEYS, "reader": "jwt-two\nX: y"}).encode(), "invalid value for reader"),
        (json.dumps({**KEYS, "reader": " jwt-two"}).encode(), "invalid value for reader"),
        (json.dumps({**KEYS, "reader": "jwt\x7ftwo"}).encode(), "invalid value for reader"),
        (b"[" * 100000, "did not write a JSON object"),
    ],
)
def test_malformed_output_fails_without_quoting_it(output, message):
    with pytest.raises(ServiceError, match=message) as error:
        _bootstrap_values("svc", tuple(KEYS), output)
    assert error.value.code == "ONESHOT_OUTPUT"
    assert "jwt-one" not in str(error.value) and "jwt-two" not in str(error.value)


# -- the engine -----------------------------------------------------------------------------------


def test_up_stores_every_written_secret_once(stacked):
    docker = FakeDocker()
    docker.bootstrap_output = json.dumps(KEYS).encode()
    result, _ = up(stacked, docker)
    stored = secret_dir(result)
    assert (stored / "minted").read_text() == KEYS["minted"]
    assert (stored / "reader").read_text() == KEYS["reader"]
    redacted = {v.decode() if isinstance(v, bytes) else v for v in docker.redactions}
    assert set(KEYS.values()) <= redacted
    up(stacked, docker)
    assert boots(docker) == 1  # same data, both present: not minted again


def test_a_missing_written_secret_runs_bootstrap_again(stacked):
    docker = FakeDocker()
    docker.bootstrap_output = json.dumps(KEYS).encode()
    result, _ = up(stacked, docker)
    (secret_dir(result) / "reader").unlink()
    fresh = {"minted": "jwt-three.e.f", "reader": "jwt-four.g.h"}
    docker.bootstrap_output = json.dumps(fresh).encode()
    up(stacked, docker)
    assert boots(docker) == 2
    assert (secret_dir(result) / "reader").read_text() == fresh["reader"]
    assert (secret_dir(result) / "minted").read_text() == fresh["minted"]


def test_moving_to_a_version_that_writes_more_runs_bootstrap_again(env):
    # As a stack moving phoenix 1.0.0 -> 1.1.0 on the same data: read-key is new.
    single = gateway(writes_secret="minted")
    del single["outputs"]["reader_key"]
    write_project(env, stack=copy.deepcopy(STACK), gateway=single)
    docker = FakeDocker()
    docker.bootstrap_output = b"jwt-one.a.b"
    up(env, docker)
    write_project(env, stack=copy.deepcopy(STACK), gateway=gateway(writes_secrets=list(KEYS)))
    docker.bootstrap_output = json.dumps(KEYS).encode()
    result, _ = up(env, docker)
    assert boots(docker) == 2
    assert (secret_dir(result) / "reader").read_text() == KEYS["reader"]


def test_malformed_output_stores_nothing(stacked):
    docker = FakeDocker()
    docker.bootstrap_output = json.dumps({"minted": "jwt-one.a.b"}).encode()
    with pytest.raises(ServiceError, match="ONESHOT_OUTPUT"):
        up(stacked, docker)
    stacks = list((stacked.parent / "stacks").glob("*"))
    for stack_dir in stacks:
        state = StackState.open_directory(stack_dir)
        if state is None:
            continue
        try:
            assert state.read_secret("gateway", "minted") is None
            assert state.read_secret("gateway", "reader") is None
        finally:
            state.close()
