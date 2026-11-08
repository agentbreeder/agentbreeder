"""service.yaml / stack.yaml schema and stack reference checks (#737)."""

from __future__ import annotations

import copy

import pytest
import yaml

from agentbreeder import registry
from agentbreeder.agentfile import AgentFileError, load_agent_file
from agentbreeder.services import refs
from agentbreeder.services.schema import ServiceError, ServiceSpec, Stack, load_service, load_stack

DIGEST = "@sha256:" + "a" * 64

POSTGRES = {
    "image": "pgvector/pgvector" + DIGEST,
    "user": "999:999",
    "ports": {"pg": {"port": 5432, "protocol": "tcp", "expose": "agents"}},
    "params": {
        "databases": {"type": "list", "item": "db_name", "default": []},
        "init_sql": {"type": "file"},
    },
    "secrets": {
        "superuser": {"generate": "token"},
        "role": {"generate": "token", "per": "databases"},
    },
    "volumes": {"data": "/var/lib/postgresql/data"},
    "health": {"command": ["pg_isready", "-q"]},
    "init": {"command": ["python3", "/files/init.py"], "secrets": ["superuser", "role"]},
    "outputs": {
        "databases": {
            "type": "secret",
            "per": "databases",
            "port": "pg",
            "template": "postgresql://{item}:{secret.role}@{address}/{item}",
        }
    },
}
LITELLM = {
    "image": "ghcr.io/berriai/litellm-database" + DIGEST,
    "provides": "gateway/litellm",
    "command": ["litellm", "--config", "/run/params/config", "--port", "4000"],
    "ports": {"http": {"port": 4000, "expose": "agents", "guard": "litellm-auth"}},
    "params": {"config": {"type": "file", "required": True}},
    "env": {"LITELLM_TELEMETRY": "False"},
    "secrets": {
        "master-key": {"generate": "token"},
        "gemini-api-key": {"import": "GEMINI_API_KEY"},
        "openrouter-api-key": {"import": "OPENROUTER_API_KEY", "optional": True},
    },
    "inputs": {"database-url": {"type": "secret"}},
    "health": {"http": "/health/liveliness"},
    "egress": True,
    "outputs": {
        "url": {"type": "url", "port": "http"},
        "master_key": {"type": "secret", "secret": "master-key", "operator_only": True},
    },
}
PROXY = {
    "build": "Dockerfile",
    "ports": {"http": {"port": 8787, "expose": "agents", "guard": "host-allowlist"}},
    "env": {"PHOENIX_URL": "{inputs.phoenix}", "PROXY_NAME": "{params.label}"},
    "params": {"label": {"type": "string", "default": "proxy"}},
    "inputs": {"phoenix": {"type": "url"}},
    "health": {"http": "/healthz"},
    "outputs": {"url": {"type": "url", "port": "http"}},
}
PHOENIX = {
    "image": "arizephoenix/phoenix" + DIGEST,
    "ports": {"http": {"port": 6006, "expose": "host", "guard": "bearer"}},
    "inputs": {"database-url": {"type": "secret"}},
    "health": {"http": "/healthz"},
    "bootstrap": {"command": ["python", "/files/mint.py"], "writes_secret": "ingest-key"},
    "outputs": {
        "url": {"type": "url", "port": "http"},
        "ingest_key": {"type": "secret", "secret": "ingest-key"},
    },
}
STACK = {
    "kind": "stack",
    "schema_version": 1,
    "name": "northwind",
    "services": {
        "postgres": {
            "ref": "service/postgres-pgvector@1.0.0",
            "with": {"databases": ["support", "litellm", "phoenix"], "init_sql": "db/seed.sql"},
        },
        "litellm": {
            "ref": "service/litellm@1.0.0",
            "with": {"config": "litellm/config.yaml"},
            "wire": {"database-url": "postgres.databases.litellm"},
        },
        "phoenix": {
            "ref": "service/phoenix@1.0.0",
            "wire": {"database-url": "postgres.databases.phoenix"},
        },
        "proxy": {"ref": "service/northwind-proxy@1.0.0", "wire": {"phoenix": "phoenix.url"}},
    },
    "environment": {
        "gateway": "litellm",
        "models": ["support-model"],
        "ceilings": {"budget_usd": 50, "rpm": 300},
        "services": {"traces_url": "{proxy.url}/v1/traces", "tools_proxy_url": "{proxy.url}"},
        "bindings": {"DATABASE_URL": {"ref": "postgres.databases.support"}},
        "eval_database": {"ref": "postgres.databases.support"},
        "agents": {
            "northwind-support": {
                "sponsor": "lead@example.com",
                "review_by": "2027-06-30",
                "candidate": True,
            }
        },
    },
}
SPECS = {"postgres": POSTGRES, "litellm": LITELLM, "phoenix": PHOENIX, "proxy": PROXY}


def specs(**overrides):
    data = copy.deepcopy(SPECS)
    data.update(overrides)
    return {name: ServiceSpec.model_validate(value) for name, value in data.items()}


def stack(mutate=None):
    data = copy.deepcopy(STACK)
    if mutate:
        mutate(data)
    return Stack.model_validate(data)


def test_valid_stack_resolves_params_and_dependency_order():
    resolved = refs.check(stack(), specs())
    assert resolved.params["postgres"]["databases"] == ["support", "litellm", "phoenix"]
    assert resolved.params["proxy"] == {"label": "proxy"}
    order = resolved.order
    assert order.index("postgres") < order.index("litellm")
    assert order.index("postgres") < order.index("phoenix") < order.index("proxy")


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda s: s.update(image="pgvector/pgvector:latest"), "image"),
        (lambda s: s.update(build="Dockerfile"), "exactly one of image"),
        (lambda s: s.update(command=["sh", "-c", "echo hi"]), "exec form"),
        (lambda s: s.update(command=["/bin/bash", "-xc", "id"]), "exec form"),
        (lambda s: s.update(command=["busybox", "sh", "-c", "id"]), "exec form"),
        (lambda s: s.update(command=["env", "sh", "-c", "id"]), "exec form"),
        (lambda s: s.update(command=["ksh", "-c", "id"]), "exec form"),
        (lambda s: s.update(command=[]), "exec-form list"),
        (lambda s: s.update(user="image"), "fixed user"),
        (lambda s: s["ports"].update(web={"port": 80, "expose": "host"}), "needs a guard"),
        (lambda s: s["ports"]["pg"].update(guard="bearer"), "HTTP ports only"),
        (lambda s: s["secrets"].update(params={"generate": "token"}), "reserved"),
        (lambda s: s["secrets"].update(bad={"generate": "token", "import": "X"}), "exactly one"),
        (lambda s: s["secrets"].update(opt={"generate": "token", "optional": True}), "optional"),
        (lambda s: s["secrets"]["role"].update(per="nope"), "per names a list"),
        (lambda s: s["outputs"]["databases"].update(port="nope"), "unknown port"),
        (lambda s: s.update(volumes={"data": "relative/path"}), "absolute"),
        (lambda s: s.update(privileged=True), "privileged"),
        (lambda s: s.update(cap_add=["SYS_ADMIN"]), "cap_add"),
        (lambda s: s["init"].update(secrets=["unknown"]), "unknown secret"),
        (lambda s: s["init"].update(writes_secret="x"), "only bootstrap"),
        (lambda s: s.update(env={"lower": "x"}), "uppercase"),
        (lambda s: s["params"].update(p={"type": "list"}), "`item` is required"),
        (lambda s: s["params"].update(p={"type": "file", "default": "x"}), "no default"),
        (
            lambda s: s["params"].update(p={"type": "string", "required": True, "default": "x"}),
            "required",
        ),
        (lambda s: s.update(health={"http": "/h", "command": ["x"]}), "exactly one of http"),
        (lambda s: s.update(health={"http": "/h"}), "needs an HTTP port"),
        (
            lambda s: s["outputs"].update(u={"type": "url", "port": "pg", "secret": "superuser"}),
            "nothing secret",
        ),
        (lambda s: s["outputs"].update(s={"type": "secret"}), "exactly one of secret"),
        (lambda s: s["outputs"].update(s={"type": "secret", "template": "x"}), "needs the port"),
        (lambda s: s["outputs"].update(s={"type": "secret", "secret": "nope"}), "unknown secret"),
        (
            lambda s: s["outputs"].update(s={"type": "secret", "secret": "role", "per": "x"}),
            "per names a list",
        ),
        (lambda s: s.update(inputs={"superuser": {"type": "secret"}}), "both secret and input"),
    ],
)
def test_service_schema_rejects(change, message):
    data = copy.deepcopy(POSTGRES)
    change(data)
    with pytest.raises(ValueError, match=message):
        ServiceSpec.model_validate(data)


def test_bootstrap_and_gateway_contracts():
    data = copy.deepcopy(PHOENIX)
    data["bootstrap"].pop("writes_secret")
    data["outputs"].pop("ingest_key")
    with pytest.raises(ValueError, match="writes_secret"):
        ServiceSpec.model_validate(data)
    data = copy.deepcopy(PHOENIX)
    data["secrets"] = {"ingest-key": {"generate": "token"}}
    with pytest.raises(ValueError, match="must not also be declared"):
        ServiceSpec.model_validate(data)
    data = copy.deepcopy(LITELLM)
    data["outputs"].pop("master_key")
    with pytest.raises(ValueError, match="gateway/litellm needs"):
        ServiceSpec.model_validate(data)
    data = copy.deepcopy(LITELLM)
    data["outputs"]["master_key"]["operator_only"] = False
    with pytest.raises(ValueError, match="operator_only"):
        ServiceSpec.model_validate(data)
    assert "ingest-key" in ServiceSpec.model_validate(PHOENIX).mounted_secrets()
    assert "database-url" in ServiceSpec.model_validate(LITELLM).mounted_secrets()


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda d: d["services"].update(params={"ref": "service/x@1.0.0"}), "reserved"),
        (lambda d: d["services"]["proxy"].update(ref="tool/x@1.0.0"), "must be a service"),
        (lambda d: d["services"]["proxy"].update(ref="service/x"), "not a service/name"),
        (lambda d: d["environment"].update(gateway="nope"), "must name a service"),
        (lambda d: d["environment"]["bindings"].update(lower={"ref": "a.b"}), "uppercase"),
        (lambda d: d["environment"]["agents"]["northwind-support"].update(sponsor="x"), "sponsor"),
        (lambda d: d["environment"].update(enforcement="audit"), "enforcement"),
        (lambda d: d["environment"].update(pdp={"engine": "opa"}), "pdp"),
        (lambda d: d["environment"].update(agents={}), "agents"),
        (lambda d: d.update(kind="agent"), "kind"),
    ],
)
def test_stack_schema_rejects(mutate, message):
    with pytest.raises(ValueError, match=message):
        stack(mutate)


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d["services"]["postgres"]["with"].update(nope="x"), "PARAM_UNKNOWN"),
        (lambda d: d["services"]["litellm"].update({"with": {}}), "PARAM_MISSING"),
        (
            lambda d: d["services"]["postgres"]["with"].update(databases=["Bad-Name"]),
            "PARAM_INVALID",
        ),
        (lambda d: d["services"]["postgres"]["with"].update(databases="support"), "PARAM_INVALID"),
        (
            lambda d: d["services"]["postgres"]["with"].update(databases=["a", "a"]),
            "PARAM_INVALID",
        ),
        (
            lambda d: d["services"]["postgres"]["with"].update(init_sql="../outside.sql"),
            "PARAM_INVALID",
        ),
        (
            lambda d: d["services"]["postgres"]["with"].update(init_sql="/etc/passwd"),
            "PARAM_INVALID",
        ),
        (
            lambda d: d["services"]["litellm"]["wire"].update(extra="postgres.databases.support"),
            "INPUT_UNKNOWN",
        ),
        (lambda d: d["services"]["litellm"].update(wire={}), "INPUT_MISSING"),
        (
            lambda d: d["services"]["litellm"]["wire"].update({"database-url": "postgres"}),
            "REFERENCE_INVALID",
        ),
        (
            lambda d: d["services"]["litellm"]["wire"].update({"database-url": "nope.url"}),
            "REFERENCE_UNKNOWN",
        ),
        (
            lambda d: d["services"]["litellm"]["wire"].update({"database-url": "postgres.nope"}),
            "REFERENCE_UNKNOWN",
        ),
        (
            lambda d: d["services"]["litellm"]["wire"].update(
                {"database-url": "postgres.databases"}
            ),
            "REFERENCE_INVALID",
        ),
        (
            lambda d: d["services"]["litellm"]["wire"].update(
                {"database-url": "postgres.databases.other"}
            ),
            "REFERENCE_INVALID",
        ),
        (
            lambda d: d["services"]["proxy"]["wire"].update(phoenix="phoenix.url.x"),
            "REFERENCE_INVALID",
        ),
        (
            lambda d: d["services"]["litellm"]["wire"].update(
                {"database-url": "litellm.master_key"}
            ),
            "STACK_CYCLE",
        ),
        (
            lambda d: d["services"]["phoenix"]["wire"].update(
                {"database-url": "litellm.master_key"}
            ),
            "OUTPUT_OPERATOR_ONLY",
        ),
        (
            lambda d: d["environment"]["bindings"].update(KEY={"ref": "litellm.master_key"}),
            "OUTPUT_OPERATOR_ONLY",
        ),
        (
            lambda d: d["environment"].update(eval_database={"ref": "litellm.master_key"}),
            "OUTPUT_OPERATOR_ONLY",
        ),
        (
            lambda d: d["services"]["proxy"]["wire"].update(phoenix="phoenix.ingest_key"),
            "WIRE_TYPE",
        ),
        (
            lambda d: d["environment"]["services"].update(key="{phoenix.ingest_key}"),
            "SECRET_IN_TEMPLATE",
        ),
        (lambda d: d["environment"]["services"].update(key="{proxy.url"), "TEMPLATE_INVALID"),
        (
            lambda d: d["environment"]["bindings"].update(GATEWAY={"ref": "litellm.url"}),
            "BINDING_TYPE",
        ),
        (lambda d: d["environment"].update(eval_database={"ref": "proxy.url"}), "BINDING_TYPE"),
        (lambda d: d["environment"].update(gateway="proxy"), "GATEWAY_CONTRACT"),
    ],
)
def test_stack_references_rejected(mutate, code):
    with pytest.raises(ServiceError) as caught:
        refs.check(stack(mutate), specs())
    assert caught.value.code == code


def test_cycles_between_services_are_reported():
    phoenix = copy.deepcopy(PHOENIX)
    phoenix["inputs"]["proxy"] = {"type": "url"}

    def mutate(d):
        d["services"]["phoenix"]["wire"]["proxy"] = "proxy.url"

    with pytest.raises(ServiceError) as caught:
        refs.check(stack(mutate), specs(phoenix=phoenix))
    assert caught.value.code == "STACK_CYCLE" and "phoenix" in str(caught.value)


@pytest.mark.parametrize(
    "env,code",
    [
        ({"A": "{inputs.database-url}"}, "SECRET_IN_TEMPLATE"),
        ({"A": "{secrets.master-key}"}, "SECRET_IN_TEMPLATE"),
        ({"A": "{params.config}"}, "REFERENCE_UNKNOWN"),
        ({"A": "{postgres.databases.support}"}, "REFERENCE_UNKNOWN"),
        ({"A": "{}"}, "TEMPLATE_INVALID"),
        ({"A": "x}y"}, "TEMPLATE_INVALID"),
    ],
)
def test_secrets_never_interpolated_into_service_env(env, code):
    litellm = copy.deepcopy(LITELLM)
    litellm["env"] = env
    with pytest.raises(ServiceError) as caught:
        refs.check(stack(), specs(litellm=litellm))
    assert caught.value.code == code


def test_param_placeholder_without_value_and_optional_input():
    proxy = copy.deepcopy(PROXY)
    proxy["params"]["label"] = {"type": "string"}
    with pytest.raises(ServiceError, match="has no value"):
        refs.check(stack(), specs(proxy=proxy))
    proxy = copy.deepcopy(PROXY)
    proxy["inputs"]["phoenix"]["optional"] = True
    proxy["env"] = {}

    def mutate(d):
        d["services"]["proxy"]["wire"] = {}

    assert "proxy" in refs.check(stack(mutate), specs(proxy=proxy)).order


@pytest.mark.parametrize(
    "field,argv",
    [
        ("command", ["litellm", "--key", "{secrets.master-key}"]),
        ("command", ["{params.config}"]),
        ("command", ["python", "-c", "{inputs.database-url}"]),
        ("init", ["python", "/files/x.py", "{params.config}"]),
        ("health", ["check", "{params.config}"]),
    ],
)
def test_command_arguments_are_never_templated(field, argv):
    litellm = copy.deepcopy(LITELLM)
    if field == "command":
        litellm["command"] = argv
    elif field == "init":
        litellm["init"] = {"command": argv}
    else:
        litellm["health"] = {"command": argv}
    with pytest.raises(ServiceError) as caught:
        refs.check(stack(), specs(litellm=litellm))
    assert caught.value.code == "TEMPLATE_IN_ARGV"


def test_literal_braces_in_argv_are_fine():
    litellm = copy.deepcopy(LITELLM)
    litellm["command"] = ["litellm", "--json", "{{}}"]
    assert refs.check(stack(), specs(litellm=litellm))


def test_operator_only_follows_the_secret_not_the_output_name():
    data = copy.deepcopy(LITELLM)
    data["outputs"]["also_key"] = {"type": "secret", "secret": "master-key"}
    with pytest.raises(ValueError, match="exposes an operator_only secret"):
        ServiceSpec.model_validate(data)
    data = copy.deepcopy(LITELLM)
    data["outputs"]["wrapped"] = {
        "type": "secret",
        "port": "http",
        "template": "http://x:{secret.master-key}@{address}",
    }
    with pytest.raises(ValueError, match="exposes an operator_only secret"):
        ServiceSpec.model_validate(data)
    data["outputs"]["wrapped"]["operator_only"] = True
    assert ServiceSpec.model_validate(data)
    data = copy.deepcopy(LITELLM)
    data["outputs"]["master_key"] = {
        "type": "secret",
        "port": "http",
        "template": "{secret.master-key}",
        "operator_only": True,
    }
    with pytest.raises(ValueError, match="name its secret"):
        ServiceSpec.model_validate(data)


def test_expand_is_single_pass_with_escapes():
    assert refs.expand("{a}-{{b}}", {"a": "{c}"}) == "{c}-{b}"
    assert refs.placeholders("{{x}} {y}", "w") == ["y"]


def test_load_service_and_stack_files(tmp_path):
    path = tmp_path / "service.yaml"
    path.write_text(yaml.safe_dump(LITELLM))
    assert load_service(path).provides == "gateway/litellm"
    path.write_text("image: [unclosed")
    with pytest.raises(ServiceError) as caught:
        load_service(path)
    assert caught.value.code == "SERVICE_INVALID"
    canary = "canary-secret-value-123"
    path.write_text(yaml.safe_dump({**LITELLM, "env": {"bad name": canary}}))
    with pytest.raises(ServiceError) as caught:
        load_service(path)
    assert canary not in str(caught.value)
    stack_path = tmp_path / "stack.yaml"
    stack_path.write_text(yaml.safe_dump(STACK))
    assert load_stack(stack_path).name == "northwind"
    stack_path.write_text(yaml.safe_dump({**STACK, "surprise": 1}))
    with pytest.raises(ServiceError) as caught:
        load_stack(stack_path)
    assert caught.value.code == "STACK_INVALID" and "surprise" in str(caught.value)


def test_service_kind_registered_and_scaffolded(tmp_path):
    assert registry.Ref.parse("service/postgres-pgvector@1.0.0").kind == "service"
    target = registry.scaffold("service", "echo", tmp_path)
    spec = load_service(target / "service.yaml")
    assert spec.outputs["url"].type == "url"


def test_agent_yaml_rejects_service_refs(tmp_path):
    base = {
        "kind": "agent",
        "schema_version": 1,
        "name": "helper",
        "runtime": "runtime/adk@1.0.4",
        "model": {"alias": "m"},
        "prompt": {"ref": "prompt/p@1.0.0"},
    }
    cases = [
        {"runtime": "service/litellm@1.0.0"},
        {"prompt": {"ref": "service/litellm@1.0.0"}},
        {"tools": [{"ref": "service/litellm@1.0.0"}]},
        {"knowledge": [{"ref": "service/litellm@1.0.0"}]},
        {"evals": {"ref": "service/litellm@1.0.0"}},
    ]
    for case in cases:
        path = tmp_path / "agent.yaml"
        path.write_text(yaml.safe_dump({**base, **case}))
        with pytest.raises(AgentFileError):
            load_agent_file(path)


def test_expose_accepts_a_list_of_scopes():
    from agentbreeder.services.schema import Port

    port = Port(port=4000, expose=["agents", "host"], guard="litellm-auth")
    assert port.scopes == {"internal", "agents", "host"}
    assert Port(port=5432, protocol="tcp").scopes == {"internal"}
    for bad in ([], ["host", "host"]):
        with pytest.raises(ValueError, match="once each"):
            Port(port=1, protocol="tcp", expose=bad)
    with pytest.raises(ValueError, match="needs a guard"):
        Port(port=1, expose=["host"])
