"""`agentbreeder services render --cluster` (#772 K3): a Helm 3 chart rendered from the stack plan.

No cluster and no `helm` binary: the chart is checked by parsing it with `helm_template` below,
an emulator of exactly the template subset the renderer writes (tier guards and the `{{`
escape). The opt-in test at the end runs the real `helm lint` and `helm template`
(AGENTBREEDER_HELM_TEST=1, Helm 3 on PATH) and compares the two.

Goldens: tests/fixtures/charts/<name>/. Regenerate after an intended change with
AGENTBREEDER_UPDATE_GOLDEN=1 and review the diff.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agentbreeder import cli
from agentbreeder.services import chart as chart_module
from agentbreeder.services.engine import Engine
from agentbreeder.services.schema import ServiceError, ServiceSpec
from agentbreeder.services.state import ROOT_ENV

ROOT = Path(__file__).resolve().parents[1]
NORTHWIND = ROOT / "src" / "agentbreeder" / "examples" / "northwind"
GOLDEN = Path(__file__).parent / "fixtures" / "charts"
UPDATE = os.environ.get("AGENTBREEDER_UPDATE_GOLDEN") == "1"
DIGEST = "sha256:" + "ab" * 32
NORTHWIND_IMAGES = {
    "proxy": f"registry.example.test/northwind/proxy@{DIGEST}",
    "ops": f"registry.example.test/northwind/ops@{DIGEST.replace('ab', 'cd')}",
}
IMPORTS = (
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "TAVILY_API_KEY",
    "ECHO_CANARY",
)
PLANTED = "planted-4f1c9d2e7a6b-value"
GATEWAY_CONFIG = {
    "model_list": [
        {
            "model_name": "m1",
            "litellm_params": {"model": "gemini/gemini-x", "api_key": "os.environ/GEMINI_API_KEY"},
        }
    ]
}
BUILTINS_STACK = {
    "kind": "stack",
    "schema_version": 1,
    "name": "builtins",
    "services": {
        "postgres": {
            "ref": "service/postgres-pgvector@1.2.0",
            "with": {
                "databases": ["litellm", "phoenix"],
                "init_sql": "db/init.sql",
                "init_sql_database": "litellm",
                "roles": ["reader_role"],
                "roles_database": "litellm",
            },
        },
        "analyzer": {"ref": "service/presidio-analyzer@1.0.0"},
        "anonymizer": {"ref": "service/presidio-anonymizer@1.0.0"},
        "litellm": {
            "ref": "service/litellm@1.4.0",
            "with": {"config": "gw/config.yaml"},
            "wire": {
                "database-url": "postgres.databases.litellm",
                "presidio-analyzer": "analyzer.url",
                "presidio-anonymizer": "anonymizer.url",
            },
        },
        "phoenix": {
            "ref": "service/phoenix@1.1.0",
            "wire": {"database-url": "postgres.databases.phoenix"},
        },
        "echo": {"ref": "service/echo@1.0.0", "with": {"label": "hello"}},
        "playwright": {"ref": "service/playwright-mcp@1.0.0"},
    },
    "environment": {
        "gateway": "litellm",
        "models": ["m1"],
        "ceilings": {"budget_usd": 5, "rpm": 60},
        "agents": {"demo-agent": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}},
    },
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    for name in IMPORTS:
        monkeypatch.delenv(name, raising=False)


def write_builtins(
    project: Path, stack: dict | None = None, config: dict | None = None, sql: str = ""
) -> Path:
    (project / "gw").mkdir(parents=True, exist_ok=True)
    (project / "db").mkdir(exist_ok=True)
    (project / "gw" / "config.yaml").write_text(yaml.safe_dump(config or GATEWAY_CONFIG))
    (project / "db" / "init.sql").write_text(
        "-- {{ not a template }} --\nCREATE TABLE notes (body text);\n" + sql
    )
    (project / "stack.yaml").write_text(yaml.safe_dump(stack or BUILTINS_STACK))
    lock(project)
    return project


def lock(project: Path) -> None:
    """Write stack.lock as a confirmed `services up` would (render itself never writes it)."""
    (project / "stack.lock").unlink(missing_ok=True)
    engine = Engine(project)
    try:
        engine._lock("plain")
        saved, path = engine._lock_state
        saved.save(path)
    finally:
        engine.close()


def render(project: Path, out: Path, environment: str = "dev", **kwargs) -> dict:
    engine = Engine(project)
    try:
        return engine.render_cluster(environment, out, **kwargs)
    finally:
        engine.close()


def chart_files(out: Path) -> dict[str, str]:
    return {
        str(path.relative_to(out)): path.read_text()
        for path in sorted(out.rglob("*"))
        if path.is_file()
    }


# -- the emulator ------------------------------------------------------------------------------

GUARD = re.compile(r"^\{\{- if \.Values\.tiers\.(tier[0-9]+) \}\}$")
ESCAPE = '{{"{{"}}'


def helm_template(out: Path, tiers: dict[str, bool] | None = None) -> list[dict]:
    """What `helm template` makes of the chart, for the only constructs the renderer writes:
    one `{{- if .Values.tiers.tierN }}` ... `{{- end }}` per file and the `{{"{{"}}` escape.
    Anything else that looks like a template action fails the test."""
    values = yaml.safe_load((out / "values.yaml").read_text())["tiers"]
    values.update(tiers or {})
    documents = []
    for path in sorted((out / "templates").glob("*.yaml")):
        lines = path.read_text().splitlines()
        guards = [i for i, line in enumerate(lines) if line.startswith("{{")]
        assert len(guards) == 2, path
        match = GUARD.match(lines[guards[0]])
        assert match and lines[guards[1]] == "{{- end }}", path
        if not values[match.group(1)]:
            continue
        body = "\n".join(lines[guards[0] + 1 : guards[1]])
        assert "{{" not in body.replace(ESCAPE, ""), path
        documents += [d for d in yaml.safe_load_all(body.replace(ESCAPE, "{{")) if d]
    return documents


def by_kind(documents: list[dict], kind: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in documents if d["kind"] == kind}


def jobs(out: Path) -> dict[str, dict]:
    return {
        path.stem: yaml.safe_load(path.read_text()) for path in sorted((out / "jobs").glob("*"))
    }


def pod_specs(documents: list[dict], out: Path) -> list[tuple[str, dict]]:
    pods = [
        (d["metadata"]["name"], d["spec"]["template"]["spec"])
        for d in documents
        if d["kind"] in ("Deployment", "StatefulSet")
    ]
    pods += [(name, job["spec"]["template"]["spec"]) for name, job in jobs(out).items()]
    return pods


# -- goldens -----------------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return text.replace(f'"agentbreeder {chart_module.renderer()}"', '"agentbreeder VERSION"')


def _golden(name: str, out: Path) -> None:
    rendered = {path: _normalise(text) for path, text in chart_files(out).items()}
    target = GOLDEN / name
    if UPDATE:
        shutil.rmtree(target, ignore_errors=True)
        for path, text in rendered.items():
            (target / path).parent.mkdir(parents=True, exist_ok=True)
            (target / path).write_text(text)
    expected = {
        str(path.relative_to(target)): path.read_text()
        for path in sorted(target.rglob("*"))
        if path.is_file()
    }
    assert sorted(rendered) == sorted(expected)
    for path, text in rendered.items():
        assert text == expected[path], f"{name}/{path} differs from the golden"


def test_northwind_golden(tmp_path):
    result = render(NORTHWIND, tmp_path / "chart", images=NORTHWIND_IMAGES)
    _golden("northwind", tmp_path / "chart")
    assert result["chart"] == "ab-northwind"
    assert result["tiers"] == [["postgres"], ["litellm", "phoenix"], ["ops", "proxy"]]


def test_builtins_golden(tmp_path):
    project = write_builtins(tmp_path / "project")
    render(project, tmp_path / "chart")
    _golden("builtins", tmp_path / "chart")


def test_render_is_deterministic(tmp_path):
    render(NORTHWIND, tmp_path / "one", images=NORTHWIND_IMAGES)
    render(NORTHWIND, tmp_path / "two", images=NORTHWIND_IMAGES)
    assert chart_files(tmp_path / "one") == chart_files(tmp_path / "two")


# -- shape ---------------------------------------------------------------------------------------


def test_chart_metadata_and_lock(tmp_path):
    out = tmp_path / "chart"
    render(NORTHWIND, out, environment="prod", images=NORTHWIND_IMAGES)
    digest = chart_module.hashlib.sha256((NORTHWIND / "stack.lock").read_bytes()).hexdigest()
    meta = yaml.safe_load((out / "Chart.yaml").read_text())
    assert meta["apiVersion"] == "v2"
    assert meta["name"] == "ab-northwind"
    assert meta["version"] == f"0.0.0+{digest[:12]}"
    assert meta["kubeVersion"] == ">=1.30.0-0"
    assert meta["annotations"]["io.agentbreeder/environment"] == "prod"
    record = json.loads((out / "agentbreeder.lock").read_text())
    assert record["stack_lock_sha256"] == digest
    assert record["environment"] == "prod"
    assert record["images"]["proxy"] == NORTHWIND_IMAGES["proxy"]
    assert record["images"]["postgres"].startswith("pgvector/pgvector@sha256:")
    assert all("@sha256:" in image for image in record["images"].values())
    # The gateway master key is operator-only (Northwind's phoenix@1.0.0 has no read key).
    assert [
        (record["secrets"][name]["service"], record["secrets"][name]["value"])
        for name in record["operator_only_secrets"]
    ] == [("litellm", "secret master-key")]
    assert (out / ".helmignore").read_text().splitlines() == ["jobs/", "agentbreeder.lock"]


def test_names_labels_and_selectors(tmp_path):
    out = tmp_path / "chart"
    render(NORTHWIND, out, images=NORTHWIND_IMAGES)
    documents = helm_template(out)
    services = by_kind(documents, "Service")
    # The names services up writes (http://litellm:4000, postgres:5432) resolve unchanged.
    assert {"postgres", "postgres-headless", "litellm", "phoenix", "proxy", "ops"} == set(services)
    assert services["litellm"]["spec"]["ports"][0]["port"] == 4000
    assert services["postgres-headless"]["spec"]["clusterIP"] == "None"
    for name, service in services.items():
        assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
        selector = service["spec"]["selector"]
        assert selector == {"io.agentbreeder/chart-service": name.removesuffix("-headless")}
    for document in documents:
        labels = document["metadata"]["labels"]
        assert labels["app.kubernetes.io/part-of"] == "agentbreeder-services"
        assert labels["app.kubernetes.io/managed-by"] == "agentbreeder"
        assert labels["io.agentbreeder/stack"] == "northwind"
        assert "io.agentbreeder/service" in labels
        assert "io.agentbreeder/deployment" not in labels
    workloads = {**by_kind(documents, "Deployment"), **by_kind(documents, "StatefulSet")}
    assert set(by_kind(documents, "StatefulSet")) == {"postgres"}
    for name, workload in workloads.items():
        pod_labels = workload["spec"]["template"]["metadata"]["labels"]
        assert pod_labels["io.agentbreeder/chart-service"] == name
        assert workload["spec"]["selector"]["matchLabels"] == {
            "io.agentbreeder/chart-service": name
        }
    # Job pods never carry the label chart Services select on.
    for job in jobs(out).values():
        assert "io.agentbreeder/chart-service" not in job["spec"]["template"]["metadata"]["labels"]


def test_every_pod_is_hardened(tmp_path):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    render(project, out)
    documents = helm_template(out)
    pods = pod_specs(documents, out)
    assert len(pods) >= 9
    for name, pod in pods:
        assert pod["automountServiceAccountToken"] is False, name
        assert pod["enableServiceLinks"] is False, name
        assert pod["securityContext"]["runAsNonRoot"] is True, name
        assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}, name
        assert pod["serviceAccountName"].startswith("ab-svc-"), name
        for key in ("hostNetwork", "hostPID", "hostIPC", "initContainers"):
            assert key not in pod, name
        for volume in pod.get("volumes", []):
            assert set(volume) - {"name"} <= {"emptyDir", "configMap", "projected"}, name
        (container,) = pod["containers"]
        context = container["securityContext"]
        assert context == {
            **context,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "seccompProfile": {"type": "RuntimeDefault"},
        }, name
        assert "privileged" not in context
        assert set(container["resources"]) == {"requests", "limits"}, name
        assert {"cpu", "memory"} <= set(container["resources"]["limits"]), name
        assert re.search(r"@sha256:[0-9a-f]{64}$", container["image"]), name
        assert all("hostPort" not in port for port in container.get("ports", [])), name
        mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
        assert "/tmp" in mounts
        assert all(m.get("readOnly") for p, m in mounts.items() if p.startswith("/run/")), name
    accounts = by_kind(documents, "ServiceAccount")
    assert accounts and all(a["automountServiceAccountToken"] is False for a in accounts.values())


def test_users_volumes_and_probes(tmp_path):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    render(project, out)
    documents = helm_template(out)
    postgres = by_kind(documents, "StatefulSet")["postgres"]
    pod = postgres["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsUser"] == 999
    assert pod["securityContext"]["fsGroup"] == 999
    claim = postgres["spec"]["volumeClaimTemplates"][0]
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    mounts = {m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
    assert {"/var/lib/postgresql/data", "/var/run/postgresql", "/files", "/run/params"} <= mounts
    probe = pod["containers"][0]["readinessProbe"]
    assert probe["exec"]["command"] == ["pg_isready", "-q", "-U", "postgres"]
    litellm = by_kind(documents, "Deployment")["litellm"]["spec"]["template"]["spec"]
    http = litellm["containers"][0]["readinessProbe"]["httpGet"]
    # The Host the local probe sends (a host-allowlist guard accepts it).
    assert http["httpHeaders"] == [{"name": "Host", "value": "litellm:4000"}]
    assert http["path"] == "/health/liveliness" and http["port"] == 4000
    # The image's own user: only runAsNonRoot (the kubelet refuses a root or non-numeric one).
    analyzer = by_kind(documents, "Deployment")["analyzer"]["spec"]["template"]["spec"]
    assert "runAsUser" not in analyzer["securityContext"]
    assert analyzer["containers"][0]["securityContext"]["runAsNonRoot"] is True


def test_env_command_and_dollar_escape(tmp_path):
    project = write_builtins(tmp_path / "project")
    stack = json.loads(json.dumps(BUILTINS_STACK))
    stack["services"]["echo"]["with"]["label"] = "cost $(HOME) $$"
    write_builtins(project, stack)
    out = tmp_path / "chart"
    render(project, out)
    documents = helm_template(out)
    deployments = by_kind(documents, "Deployment")
    litellm = deployments["litellm"]["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item["value"] for item in litellm["env"]}
    assert env["PRESIDIO_ANALYZER_API_BASE"] == "http://analyzer:3000"
    assert env["AGENTBREEDER_SERVICE"] == "litellm"
    assert litellm["command"] == ["/files/start.py"] and "args" not in litellm
    echo = by_kind(documents, "StatefulSet")["echo"]["spec"]["template"]["spec"]["containers"][0]
    echo_env = {item["name"]: item["value"] for item in echo["env"]}
    # Kubernetes expands $(VAR) in env, command and args; $$ is a literal $.
    assert echo_env["ECHO_LABEL"] == "cost $$(HOME) $$$$"
    assert echo["command"] == ["python3"]
    assert echo["args"][:3] == ["-m", "http.server", "8080"]


def test_files_and_params_as_configmaps(tmp_path):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    render(project, out)
    documents = helm_template(out)
    maps = by_kind(documents, "ConfigMap")
    # The service's files/ directory, byte for byte, executable scripts 0555.
    start = (
        ROOT / "src/agentbreeder/builtin_registry/service/litellm/1.4.0/files/start.py"
    ).read_text()
    files = maps["ab-svc-litellm-files"]
    assert files["data"]["start.py"] == start
    pod = by_kind(documents, "Deployment")["litellm"]["spec"]["template"]["spec"]
    volume = {v["name"]: v for v in pod["volumes"]}["files"]["configMap"]
    modes = {item["key"]: item["mode"] for item in volume["items"]}
    assert modes["start.py"] == 0o555
    assert modes["agentbreeder_request_filter.py"] == 0o444
    # File params at /run/params/<name>; a `{{` in content survives the template engine.
    params = maps["ab-svc-postgres-params"]["data"]
    assert params["init_sql"].startswith("-- {{ not a template }} --\n")
    assert yaml.safe_load(maps["ab-svc-litellm-params"]["data"]["config"]) == GATEWAY_CONFIG


def test_secrets_are_references_only(tmp_path):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    render(project, out)
    documents = helm_template(out)
    assert not by_kind(documents, "Secret")
    record = json.loads((out / "agentbreeder.lock").read_text())
    pod = by_kind(documents, "Deployment")["litellm"]["spec"]["template"]["spec"]
    projected = {v["name"]: v for v in pod["volumes"]}["secrets"]["projected"]
    sources = {
        s["secret"]["items"][0]["path"]: (s["secret"]["name"], s["secret"]["optional"])
        for s in projected["sources"]
    }
    assert projected["defaultMode"] == 0o444
    assert set(sources) == {
        "master-key",
        "gemini-api-key",
        "openrouter-api-key",
        "openai-api-key",
        "anthropic-api-key",
        "database-url",
    }
    assert sources["master-key"][1] is False
    assert sources["gemini-api-key"][1] is True  # an optional import
    url_secret = sources["database-url"][0]
    entry = record["secrets"][url_secret]
    assert (entry["service"], entry["value"]) == ("postgres", "output databases.litellm")
    operator_only = {record["secrets"][n]["value"] for n in record["operator_only_secrets"]}
    assert operator_only == {"secret master-key", "secret read-key"}
    for name, _ in sources.values():
        assert re.fullmatch(r"ab-svc-[a-z0-9-]+-[0-9a-f]{6}-g1", name) and len(name) <= 63
        assert name in record["secrets"]
    # Phoenix mounts what its bootstrap writes once that Secret exists.
    phoenix = by_kind(documents, "Deployment")["phoenix"]["spec"]["template"]["spec"]
    sources = {
        s["secret"]["items"][0]["path"]: s["secret"]["optional"]
        for s in {v["name"]: v for v in phoenix["volumes"]}["secrets"]["projected"]["sources"]
    }
    assert sources["ingest-key"] is True and sources["secret"] is False
    # A step mounts only the secrets it names.
    init = jobs(out)["postgres-init"]["spec"]["template"]["spec"]
    paths = {
        s["secret"]["items"][0]["path"]
        for s in {v["name"]: v for v in init["volumes"]}["secrets"]["projected"]["sources"]
    }
    assert paths == {
        "superuser",
        "console",
        "role/litellm",
        "role/phoenix",
        "login/reader_role",
    }


def test_secret_names_are_unique_and_bounded():
    long_key = "b" * 31 + "/secret/" + "c" * 70
    keys = ("a/secret/x.y", "a/secret/x-y", "a/output/x.y", long_key)
    names = {chart_module.secret_name(key) for key in keys}
    assert len(names) == 4
    assert all(len(name) <= 63 and re.fullmatch(r"[a-z0-9-]+", name) for name in names)
    assert chart_module.secret_name("a/secret/x", 3).endswith("-g3")


def test_jobs_are_outside_the_release(tmp_path):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    render(project, out)
    found = jobs(out)
    assert set(found) == {"postgres-init", "phoenix-bootstrap", "echo-init", "echo-bootstrap"}
    job = found["phoenix-bootstrap"]
    assert job["kind"] == "Job" and job["metadata"]["name"] == "ab-svc-phoenix-bootstrap"
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["/files/bootstrap.py"]
    assert "readinessProbe" not in container and "ports" not in container
    assert not any(d["kind"] == "Job" for d in helm_template(out))
    # No Helm hooks anywhere (spec §5.4).
    assert "helm.sh/hook" not in "".join(chart_files(out).values())


def test_tiers_follow_the_wiring(tmp_path):
    out = tmp_path / "chart"
    render(NORTHWIND, out, images=NORTHWIND_IMAGES)
    values = yaml.safe_load((out / "values.yaml").read_text())
    assert values == {"tiers": {"tier0": True, "tier1": True, "tier2": True}}
    first = helm_template(out, {"tier1": False, "tier2": False})
    assert {d["metadata"]["labels"]["io.agentbreeder/service"] for d in first} == {"postgres"}
    two = helm_template(out, {"tier2": False})
    assert {d["metadata"]["labels"]["io.agentbreeder/service"] for d in two} == {
        "postgres",
        "litellm",
        "phoenix",
    }


def test_resources_from_service_yaml():
    spec = ServiceSpec.model_validate(
        {
            "image": "python:3.12-slim@" + DIGEST,
            "resources": {"requests": {"cpu": "250m"}, "limits": {"memory": "512Mi"}},
        }
    )
    resources = chart_module.resources(spec)
    assert resources["requests"]["cpu"] == "250m"
    assert resources["limits"]["memory"] == "512Mi"
    assert resources["limits"]["cpu"] == chart_module.DEFAULT_RESOURCES["limits"]["cpu"]
    for bad in ({"limits": {"memory": "1G"}}, {"limits": {"cpu": "-1"}}, {"other": {}}):
        with pytest.raises(ValueError):
            ServiceSpec.model_validate({"image": "python:3.12-slim@" + DIGEST, "resources": bad})


# -- refusals ----------------------------------------------------------------------------------


def _code(callable_, *args, **kwargs) -> str:
    with pytest.raises(ServiceError) as raised:
        callable_(*args, **kwargs)
    return raised.value.code


def test_secret_leak_is_refused_and_nothing_written(tmp_path, monkeypatch):
    # A key pasted into a project file that becomes a ConfigMap.
    project = write_builtins(tmp_path / "project", sql=f"-- key {PLANTED}\n")
    out = tmp_path / "chart"
    # An imported value of this environment...
    monkeypatch.setenv("GEMINI_API_KEY", PLANTED)
    assert _code(render, project, out) == "CHART_SECRET_LEAK"
    assert not out.exists()
    monkeypatch.delenv("GEMINI_API_KEY")
    env_file = tmp_path / "keys.env"
    env_file.write_text(f"GEMINI_API_KEY={PLANTED}\n")
    with pytest.raises(ServiceError) as raised:
        render(project, out, env_file=env_file)
    assert raised.value.code == "CHART_SECRET_LEAK"
    assert PLANTED not in str(raised.value)
    assert "templates/postgres.yaml" in str(raised.value)
    # ... or a value stored in the local stack state.
    write_builtins(project)
    engine = Engine(project)
    try:
        engine.state.write_secret("echo", "token", b"stored-token-8c1d7e", "generate")
    finally:
        engine.close()
    stack = json.loads(json.dumps(BUILTINS_STACK))
    stack["services"]["echo"]["with"]["label"] = "stored-token-8c1d7e"
    write_builtins(project, stack)
    assert _code(render, project, out) == "CHART_SECRET_LEAK"
    assert not out.exists()


def test_scan_forms():
    files = {"templates/a.yaml": b"data: c2VjcmV0LXZhbHVlLTEyMzQ=\n"}
    with pytest.raises(ServiceError) as raised:
        chart_module.scan(files, [b"secret-value-1234"])
    assert raised.value.code == "CHART_SECRET_LEAK"
    chart_module.scan(files, [b"other-value-5678", b"abc"])  # short values are not scanned


def test_mcp_stacks_are_refused(tmp_path):
    project = tmp_path / "project"
    stack = json.loads(json.dumps(BUILTINS_STACK))
    stack["mcp"] = {"browser": {"ref": "mcp/playwright@1.0.0"}}
    write_builtins(project, stack)
    assert _code(render, project, tmp_path / "chart") == "CHART_MCP_UNSUPPORTED"


def test_built_images_must_be_given_by_digest(tmp_path):
    assert _code(render, NORTHWIND, tmp_path / "a") == "CHART_IMAGE_REQUIRED"
    bad = {**NORTHWIND_IMAGES, "proxy": "registry.example.test/northwind/proxy:latest"}
    assert _code(render, NORTHWIND, tmp_path / "b", images=bad) == "CHART_IMAGE_INVALID"
    other = {**NORTHWIND_IMAGES, "postgres": NORTHWIND_IMAGES["proxy"]}
    assert _code(render, NORTHWIND, tmp_path / "c", images=other) == "CHART_IMAGE_INVALID"
    unknown = {**NORTHWIND_IMAGES, "nope": NORTHWIND_IMAGES["proxy"]}
    assert _code(render, NORTHWIND, tmp_path / "d", images=unknown) == "CHART_IMAGE_INVALID"


def test_needs_a_stack_lock(tmp_path):
    project = write_builtins(tmp_path / "project")
    (project / "stack.lock").unlink()
    assert _code(render, project, tmp_path / "chart") == "CHART_LOCK_MISSING"
    assert not (project / "stack.lock").exists()


def test_stale_lock_is_refused(tmp_path):
    project = write_builtins(tmp_path / "project")
    (project / "db" / "init.sql").write_text("CREATE TABLE changed (id int);\n")
    before = (project / "stack.lock").read_bytes()
    with pytest.raises(Exception) as raised:
        render(project, tmp_path / "chart")
    assert getattr(raised.value, "code", "") == "HASH_MISMATCH"
    assert (project / "stack.lock").read_bytes() == before


def test_files_too_large(tmp_path):
    project = write_builtins(tmp_path / "project")
    (project / "db" / "init.sql").write_text("-- x\n" * (chart_module.FILES_LIMIT // 4))
    lock(project)
    assert _code(render, project, tmp_path / "chart") == "CHART_FILES_TOO_LARGE"


def test_ollama_gateway_is_refused(tmp_path):
    config = {
        "model_list": [
            {
                "model_name": "m1",
                "litellm_params": {
                    "model": "ollama_chat/llama3",
                    "api_base": "http://host.docker.internal:11434",
                },
            }
        ]
    }
    project = write_builtins(tmp_path / "project", config=config)
    assert _code(render, project, tmp_path / "chart") == "CHART_HOST_UNSUPPORTED"


def test_out_directory_rules(tmp_path):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    out.mkdir()
    (out / "keep.txt").write_text("mine")
    assert _code(render, project, out) == "CHART_OUT_NOT_EMPTY"
    assert (out / "keep.txt").read_text() == "mine"
    fresh = tmp_path / "fresh"
    render(project, fresh)
    (fresh / "templates" / "stale.yaml").write_text("stale")
    render(project, fresh)  # a previous render is replaced whole
    assert not (fresh / "templates" / "stale.yaml").exists()
    link = tmp_path / "link"
    link.symlink_to(fresh)
    assert _code(render, project, link) == "CHART_OUT_INVALID"
    file = tmp_path / "file"
    file.write_text("x")
    assert _code(render, project, file) == "CHART_OUT_INVALID"


def test_invalid_environment(tmp_path):
    project = write_builtins(tmp_path / "project")
    assert _code(render, project, tmp_path / "chart", environment="Bad_Env") == (
        "STACK_ENVIRONMENT"
    )


# -- the self-check ------------------------------------------------------------------------------


def _documents(tmp_path) -> list[dict]:
    project = write_builtins(tmp_path / "project")
    engine = Engine(project)
    try:
        plan = engine.plan("locked")
    finally:
        engine.close()
    return chart_module.build(plan, "dev", stack_lock_sha256="0" * 64).documents


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["spec"].__setitem__("type", "NodePort"),
        lambda d: d["spec"].__setitem__("type", "LoadBalancer"),
        lambda d: d["spec"].__setitem__("externalIPs", ["192.0.2.1"]),
    ],
)
def test_check_refuses_exposure(tmp_path, mutate):
    documents = _documents(tmp_path)
    service = next(d for d in documents if d["kind"] == "Service")
    mutate(service)
    assert _code(chart_module.check, documents) == "CHART_EXPOSURE_REFUSED"


def _pod(document: dict) -> dict:
    return document["spec"]["template"]["spec"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["containers"][0]["securityContext"].__setitem__(
            "readOnlyRootFilesystem", False
        ),
        lambda p: p["containers"][0]["securityContext"].__setitem__("privileged", True),
        lambda p: p["containers"][0]["securityContext"]["capabilities"].__setitem__("add", ["X"]),
        lambda p: p.__setitem__("automountServiceAccountToken", True),
        lambda p: p.__setitem__("hostNetwork", True),
        lambda p: p["volumes"].append({"name": "h", "hostPath": {"path": "/"}}),
        lambda p: p["containers"][0].__setitem__("ports", [{"containerPort": 1, "hostPort": 1}]),
        lambda p: p["containers"][0].__setitem__("image", "postgres:16"),
        lambda p: p["containers"][0].pop("resources"),
        lambda p: p["securityContext"].__setitem__("runAsUser", 0),
        lambda p: p.__setitem__("initContainers", [{}]),
    ],
)
def test_check_refuses_unhardened_pods(tmp_path, mutate):
    documents = _documents(tmp_path)
    workload = next(d for d in documents if d["kind"] in ("Deployment", "StatefulSet"))
    mutate(_pod(workload))
    assert _code(chart_module.check, documents) == "CHART_HARDENING"


def test_check_refuses_other_kinds(tmp_path):
    documents = _documents(tmp_path)
    documents.append({"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {}})
    assert _code(chart_module.check, documents) == "CHART_EXPOSURE_REFUSED"
    documents[-1] = {"apiVersion": "v1", "kind": "Secret", "metadata": {}}
    assert _code(chart_module.check, documents) == "CHART_HARDENING"


def test_user_host_is_refused():
    spec = ServiceSpec.model_validate({"image": "python:3.12-slim@" + DIGEST, "user": "host"})
    with pytest.raises(ServiceError) as raised:
        chart_module.pod_security("svc", spec)
    assert raised.value.code == "CHART_USER_HOST"
    root = ServiceSpec.model_validate({"image": "python:3.12-slim@" + DIGEST, "user": "0:0"})
    with pytest.raises(ServiceError) as raised:
        chart_module.pod_security("svc", root)
    assert raised.value.code == "CHART_ROOT_USER"


# -- CLI -----------------------------------------------------------------------------------------


def test_cli_render_json(tmp_path, capsys):
    out = tmp_path / "chart"
    images = [f"--image={k}={v}" for k, v in NORTHWIND_IMAGES.items()]
    code = cli.main(
        ["services", "render", str(NORTHWIND), "--cluster", "--environment", "dev"]
        + ["--out", str(out), "--json", *images]
    )
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1
    assert result["chart"] == "ab-northwind"
    assert result["out"] == str(out.resolve())
    assert (out / "Chart.yaml").is_file()


def test_cli_render_text(tmp_path, capsys):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    code = cli.main(
        ["services", "render", str(project), "--cluster", "--environment", "dev"]
        + ["--out", str(out)]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "Rendered chart ab-builtins" in text
    assert "tier 0: analyzer, anonymizer, echo, playwright, postgres" in text
    assert "helm template" in text


def test_cli_render_errors(tmp_path, capsys):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    base = ["services", "render", str(project), "--environment", "dev", "--out", str(out)]
    assert cli.main([*base, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "RENDER_TARGET"
    assert cli.main([*base, "--cluster", "--image", "nope", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CHART_IMAGE_INVALID"
    assert cli.main([*base, "--cluster", "--image", "nope"]) == 2
    assert "CHART_IMAGE_INVALID" in capsys.readouterr().err
    (project / "stack.lock").unlink()
    assert cli.main([*base, "--cluster"]) == 2
    assert "CHART_LOCK_MISSING" in capsys.readouterr().err


# -- opt-in: the real helm ----------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENTBREEDER_HELM_TEST") != "1" or shutil.which("helm") is None,
    reason="opt-in: AGENTBREEDER_HELM_TEST=1 with Helm 3 on PATH",
)
@pytest.mark.parametrize("name", ["northwind", "builtins"])
def test_real_helm_lint_and_template(tmp_path, name):  # pragma: no cover - opt-in
    out = tmp_path / "chart"
    if name == "northwind":
        render(NORTHWIND, out, images=NORTHWIND_IMAGES)
    else:
        render(write_builtins(tmp_path / "project"), out)
    helm = shutil.which("helm")
    subprocess.run([helm, "lint", "--strict", str(out)], check=True, capture_output=True)
    for tiers in ({}, {"tier1": False, "tier2": False}):
        flags = [f"--set=tiers.{key}={str(value).lower()}" for key, value in tiers.items()]
        result = subprocess.run(
            [helm, "template", "ab-test", str(out), *flags],
            check=True,
            capture_output=True,
            text=True,
        )
        real = [d for d in yaml.safe_load_all(result.stdout) if d]
        expected = helm_template(out, tiers)
        key = lambda d: (d["kind"], d["metadata"]["name"])  # noqa: E731
        assert sorted(real, key=key) == sorted(expected, key=key)


# -- review fixes (round 1) --------------------------------------------------------------------


def test_ambiguous_scalars_are_quoted():
    text = chart_module._dump(
        {"a": "1e3", "b": "y", "c": "0o17", "d": "123456789e12", "e": "plain", "f": "Off"}
    )
    for value in ("-.5", "+.5e3", ".5"):
        assert f"'{value}'" in chart_module._dump({"k": value})
    assert yaml.safe_load(text) == {
        "a": "1e3",
        "b": "y",
        "c": "0o17",
        "d": "123456789e12",
        "e": "plain",
        "f": "Off",
    }
    for value in ("'1e3'", "'y'", "'0o17'", "'123456789e12'", "'Off'"):
        assert value in text
    assert "e: plain" in text


def test_image_with_registry_port(tmp_path):
    images = {**NORTHWIND_IMAGES, "proxy": f"localhost:5001/northwind/proxy@{DIGEST}"}
    result = render(NORTHWIND, tmp_path / "chart", images=images)
    assert result["chart"] == "ab-northwind"
    for bad in (f"user:pw@host/x@{DIGEST}", f"host:99999999/x@{DIGEST}"):
        images = {**NORTHWIND_IMAGES, "proxy": bad}
        assert _code(render, NORTHWIND, tmp_path / "bad", images=images) == ("CHART_IMAGE_INVALID")


def test_binary_files_count_encoded(tmp_path):
    project = write_builtins(tmp_path / "project")
    (project / "db" / "init.sql").write_bytes(b"\xff" * (chart_module.FILES_LIMIT * 3 // 4 + 10))
    lock(project)
    assert _code(render, project, tmp_path / "chart") == "CHART_FILES_TOO_LARGE"


def test_scan_sees_through_escapes(tmp_path):
    secret = "pa$(word)-{{x}}-it's-9c1e"
    project = write_builtins(tmp_path / "project")
    engine = Engine(project)
    try:
        engine.state.write_secret("echo", "token", secret.encode(), "generate")
    finally:
        engine.close()
    stack = json.loads(json.dumps(BUILTINS_STACK))
    stack["services"]["echo"]["with"]["label"] = secret
    write_builtins(project, stack)
    with pytest.raises(ServiceError) as raised:
        render(project, tmp_path / "chart")
    assert raised.value.code == "CHART_SECRET_LEAK"
    assert "(" not in str(raised.value).partition("]")[2].split(" contains")[0]


def test_every_env_file_value_is_scanned(tmp_path):
    project = write_builtins(tmp_path / "project", sql=f"-- {PLANTED}\n")
    env_file = tmp_path / "other.env"
    env_file.write_text(f"SOME_OTHER_TOKEN={PLANTED}\n")
    assert _code(render, project, tmp_path / "chart", env_file=env_file) == "CHART_SECRET_LEAK"


def test_previous_render_with_added_files_is_kept(tmp_path):
    project = write_builtins(tmp_path / "project")
    out = tmp_path / "chart"
    render(project, out)
    (out / "README.md").write_text("mine")
    assert _code(render, project, out) == "CHART_OUT_NOT_EMPTY"
    assert (out / "README.md").read_text() == "mine"
