"""`agentbreeder release` / `eval` (#731): candidate, eval gate, exact-image promote, rollback."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentbreeder import evals, registry
from agentbreeder import release as release_module
from agentbreeder.deployment import DeploymentService
from agentbreeder.release import EXIT_DENIED, EXIT_EVAL, EXIT_OK, EXIT_RUNTIME, release, rollback

IMAGES = iter("sha256:" + c * 64 for c in "123456789")


class FakeRuntime:
    """Stateful stand-in for the Docker boundary: containers keyed by record name."""

    context = "local-test"

    def __init__(self):
        self.containers: dict[str, dict] = {}
        self.healthy_flag = True
        self.unhealthy_images: set[str] = set()
        self.missing_images: set[str] = set()

    def image_present(self, image):
        return image not in self.missing_images

    def authorize(self, context=None):
        return None

    def inspect(self, record):
        return self.containers.get(record["container"])

    def create(self, record, manifest, **kwargs):
        self.containers[record["container"]] = {
            "Id": record["container"],
            "State": {"Running": False},
            "image": manifest.image,
        }

    def start(self, info):
        info["State"]["Running"] = True

    def remove(self, info):
        self.containers.pop(info["Id"], None)

    def endpoint(self, info, manifest):
        return f"http://127.0.0.1/{info['Id']}"

    def healthy(self, endpoint, path):
        name = endpoint.rsplit("/", 1)[-1]
        image = self.containers.get(name, {}).get("image")
        return self.healthy_flag and image not in self.unhealthy_images


def version(root: Path, kind: str, name: str, files: dict, extra: str = "") -> None:
    path = root / kind / name / "1.0.0"
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(f"kind: {kind}\nname: {name}\nversion: 1.0.0\n{extra}")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


CASES = [
    {"id": "a", "turns": ["hi"], "reference": "says hi"},
    {"id": "b", "turns": ["bye"], "reference": "says bye", "expect": {"must_match": ["bye"]}},
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    project = tmp_path / "project"
    root = project / "registry"
    version(root, "runtime", "fake", {"Dockerfile": "FROM scratch\n", "runtime/x.py": ""})
    version(root, "prompt", "helper", {"prompt.md": "Be helpful."})
    version(
        root,
        "eval",
        "golden",
        {"golden.jsonl": "\n".join(json.dumps(c) for c in CASES) + "\n"},
        "judge: {alias: support-judge}\ncases: golden.jsonl\n",
    )
    (project / "agent.yaml").write_text(
        "kind: agent\nschema_version: 1\nname: helper\nruntime: runtime/fake@1.0.0\n"
        "model: {alias: support-model}\nprompt: {ref: prompt/helper@1.0.0}\n"
        "evals: {ref: eval/golden@1.0.0, threshold: 1.0, retries: 1}\n"
    )
    return project


@pytest.fixture
def operator(identity_env):
    identity_env.config["gateways"] = {
        "gw": {"type": "litellm", "url": "http://127.0.0.1:1", "agent_url": "http://x:1"}
    }
    identity = {"provider": "static", "ref": "agent-1", "environment": "dev"}
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "gw",
            "models": ["support-model", "support-judge"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "agents": {"helper": {"identity": identity, "candidate_identity": dict(identity)}},
        }
    )
    return identity_env


def builder_for(images):
    return lambda context, iidfile: images


class Evaluator:
    def __init__(self, passed=True):
        self.passed = passed
        self.endpoints = []

    def __call__(self, eval_set, endpoint, operator, environment, state, *, retries):
        self.endpoints.append(endpoint)
        results = [
            {"case_id": c["id"], "passed": self.passed, "attempts": 1, "correctness": 1.0,
             "latency_ms": 10, "checks": {}}
            for c in eval_set.cases
        ]  # fmt: skip
        return evals.EvalReport("run1", endpoint, "eval/golden@1.0.0", "support-judge", results)


def do_release(project, operator, state, runtime, image, evaluator=None, **kwargs):
    from conftest import actor

    service = DeploymentService(state, runtime, operator.load())
    return release(
        project / "agent.yaml",
        operator.load(),
        service,
        actor("dave"),
        "dev",
        builder=builder_for(image),
        evaluate=evaluator or Evaluator(),
        **kwargs,
    ), service


def prepare_lock(project, operator):
    from agentbreeder.build import build

    build(
        project / "agent.yaml", operator.load(), "dev", builder=builder_for("sha256:" + "0" * 64)
    )


def test_release_promotes_exact_image_and_cleans_candidate(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    evaluator = Evaluator()
    image = "sha256:" + "1" * 64
    outcome, service = do_release(project, operator, state, runtime, image, evaluator)
    assert outcome.code == EXIT_OK, outcome.message
    prod = state.active("helper")
    assert prod["state"] == "ready" and state.manifest(prod["id"])["image"] == image
    assert state.active("helper-candidate") is None
    assert evaluator.endpoints and "helper-candidate" not in str(runtime.containers)
    actions = [(r["action"], r["decision"]) for r in state.audit_rows(limit=200)]
    assert ("release", "promoted") in actions and ("release", "started") in actions
    report = json.loads(Path(outcome.details["eval_report"]).read_text())
    assert report["passed"] == 2
    # same image again: no-op
    again, _ = do_release(project, operator, state, runtime, image)
    assert again.code == EXIT_OK and "already in production" in again.message


def test_eval_failure_leaves_production_untouched(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    first, _ = do_release(project, operator, state, runtime, "sha256:" + "2" * 64)
    prod_id = state.active("helper")["id"]
    outcome, _ = do_release(
        project, operator, state, runtime, "sha256:" + "3" * 64, Evaluator(passed=False)
    )
    assert outcome.code == EXIT_EVAL and "production unchanged" in outcome.message
    assert state.active("helper")["id"] == prod_id
    assert state.active("helper-candidate") is None
    assert ("release", "eval_failed") in [
        (r["action"], r["decision"]) for r in state.audit_rows(limit=200)
    ]


def test_failed_promote_restores_previous_and_rollback_works(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    old, new = "sha256:" + "4" * 64, "sha256:" + "5" * 64
    do_release(project, operator, state, runtime, old)
    runtime.unhealthy_images = set()

    class FailProductionOnly(FakeRuntime):
        pass

    # candidate healthy, production of the new image unhealthy
    original = runtime.healthy

    def healthy(endpoint, path):
        if "helper-candidate" not in endpoint and "agentbreeder-" in endpoint:
            record = next(
                (
                    r
                    for r in state.active_all()
                    if r["container"] in endpoint and r["name"] == "helper"
                ),
                None,
            )
            if record and state.manifest(record["id"])["image"] == new:
                return False
        return original(endpoint, path)

    runtime.healthy = healthy
    outcome, service = do_release(project, operator, state, runtime, new)
    assert (
        outcome.code == EXIT_RUNTIME and "previous production restored (ready)" in outcome.message
    )
    assert state.manifest(state.active("helper")["id"])["image"] == old
    runtime.healthy = original
    good, service = do_release(project, operator, state, runtime, new)
    assert good.code == EXIT_OK
    from conftest import actor

    back = rollback(project / "agent.yaml", service, actor("dave"))
    assert back.code == EXIT_OK and old in back.message
    assert state.manifest(state.active("helper")["id"])["image"] == old


def test_preflight_deny_touches_nothing(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    operator.config["environments"]["dev"]["models"] = ["support-judge"]  # model not allowed
    outcome, _ = do_release(project, operator, state, runtime, "sha256:" + "6" * 64)
    assert outcome.code == EXIT_DENIED and "MODEL_NOT_ALLOWED" in outcome.message
    assert runtime.containers == {}


def test_candidate_start_failure(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    runtime.healthy_flag = False
    outcome, _ = do_release(project, operator, state, runtime, "sha256:" + "7" * 64)
    assert outcome.code == EXIT_RUNTIME and "Candidate failed" in outcome.message
    assert state.active("helper") is None and state.active("helper-candidate") is None


def test_eval_set_must_match_lock(project, operator, state):
    from agentbreeder.release import ReleaseError, _eval_set

    prepare_lock(project, operator)
    from agentbreeder.agentfile import load_agent_file

    agent = load_agent_file(project / "agent.yaml")
    assert len(_eval_set(agent, project).cases) == 2
    (project / "registry/eval/golden/1.0.0/golden.jsonl").write_text("{}\n")
    with pytest.raises(Exception) as error:
        _eval_set(agent, project)
    assert "HASH_MISMATCH" in str(error.value)
    (project / "agent.lock").unlink()
    with pytest.raises(ReleaseError, match="LOCK_STALE"):
        _eval_set(agent, project)


def test_release_lock_is_exclusive(tmp_path):
    from agentbreeder.release import ReleaseError, _ReleaseLock

    with _ReleaseLock(tmp_path):
        with pytest.raises(ReleaseError, match="RELEASE_BUSY"):
            with _ReleaseLock(tmp_path):
                pass


# -- evals ---------------------------------------------------------------------------------------


class Agent:
    """A fake agent /invoke endpoint."""

    def __init__(self, reply="bye now", tools=()):
        payload = {"reply": reply, "tools": list(tools), "sources": ["Refund policy"]}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class FakeJudge:
    def __init__(self, score=1.0):
        self.score = score

    def grade(self, prompt):
        return self.score, "ok"


def test_run_case_checks():
    agent = Agent(reply="bye now", tools=["lookup_orders"])
    case = {
        "id": "x",
        "turns": ["hi", {"message": "again", "new_session": True}],
        "reference": "r",
        "expect": {
            "must_match": ["bye"],
            "must_not_match": ["poem"],
            "tools_include": ["lookup_orders"],
            "tools_exclude": ["web_search"],
            "sources_any": ["Refund policy"],
        },
    }
    result = evals.run_case(agent.url, "run", case, FakeJudge())
    assert result["passed"] and result["checks"]["called:lookup_orders"]
    low = evals.run_case(agent.url, "run", case, FakeJudge(0.2))
    assert not low["passed"] and not low["checks"]["judge_correct"]
    down = evals.run_case("http://127.0.0.1:9", "run", case, FakeJudge())
    assert not down["passed"] and not down["checks"]["agent_responded"]
    agent.server.shutdown()


def test_run_with_injected_judge_and_retries(project, operator, state):
    agent = Agent(reply="bye")
    eval_set = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, None)

    class Flaky(FakeJudge):
        calls = 0

        def grade(self, prompt):
            Flaky.calls += 1
            return (0.1 if Flaky.calls == 1 else 1.0), "ok"

    report = evals.run(
        eval_set, agent.url, operator.load(), "dev", state.directory, retries=1, judge=Flaky()
    )
    summary = report.summary()
    assert summary["passed"] == 2 and len(summary["flaky"]) == 1
    agent.server.shutdown()


def test_run_requires_allowed_judge_and_gateway(project, operator, state):
    eval_set = evals.EvalSet("golden", "1.0.0", "not-allowed", CASES, None)
    with pytest.raises(evals.EvalError, match="MODEL_NOT_ALLOWED"):
        evals.run(eval_set, "http://x", operator.load(), "dev", state.directory)
    ok = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, None)
    with pytest.raises(evals.EvalError, match="GATEWAY_CREDENTIAL"):
        evals.run(ok, "http://x", operator.load(), "dev", state.directory)
    fixtures = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, "SELECT 1")
    with pytest.raises(evals.EvalError, match="EVAL_DATABASE_UNCONFIGURED"):
        evals.run(fixtures, "http://x", operator.load(), "dev", state.directory)


def test_load_eval_set_validation(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / "manifest.yaml").write_text(
        "kind: eval\nname: g\nversion: 1.0.0\njudge: {alias: j}\nfixtures: f.sql\n"
    )
    (good / "golden.jsonl").write_text(json.dumps(CASES[0]) + "\n\n")
    (good / "f.sql").write_text("SELECT 1;")
    loaded = evals.load_eval_set(good)
    assert loaded.judge_alias == "j" and loaded.fixtures_sql == "SELECT 1;"
    (good / "golden.jsonl").write_text("not json\n")
    with pytest.raises(evals.EvalError, match="not JSON"):
        evals.load_eval_set(good)
    (good / "golden.jsonl").write_text('{"id": "x"}\n')
    with pytest.raises(evals.EvalError, match="needs id"):
        evals.load_eval_set(good)
    (good / "golden.jsonl").write_text("")
    with pytest.raises(evals.EvalError, match="1.."):
        evals.load_eval_set(good)
    (good / "manifest.yaml").write_text("kind: eval\nname: g\nversion: 1.0.0\n")
    with pytest.raises(evals.EvalError, match="judge.alias"):
        evals.load_eval_set(good)


def test_run_mints_and_revokes_judge_key(project, operator, state, tmp_path):
    from test_gateway_keys import MASTER, FakeGateway

    gateway = FakeGateway()
    graded = []

    class JudgeHandler:  # chat completions on the same fake gateway
        pass

    master = tmp_path / "master"
    master.write_text(MASTER)
    master.chmod(0o600)
    operator.config["gateways"]["gw"].update({"url": gateway.url, "master_key": f"file:{master}"})
    agent = Agent(reply="bye")
    eval_set = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, None)

    def grade(self, prompt):
        minted = [k for a, k in gateway.keys.items() if "eval-" in a]
        assert minted and minted[0]["duration"] == evals.JUDGE_KEY_DURATION
        graded.append(self.key)
        assert self.key.startswith("vk-") and self.alias == "support-judge"
        return 1.0, "ok"

    original = evals.Judge.grade
    evals.Judge.grade = grade
    try:
        report = evals.run(eval_set, agent.url, operator.load(), "dev", state.directory, retries=0)
    finally:
        evals.Judge.grade = original
    assert report.summary()["passed"] == 2 and graded
    assert not [a for a in gateway.keys if "eval-" in a]  # revoked after the run
    agent.server.shutdown()
    gateway.server.shutdown()


def test_judge_grade_parses_structured_output():
    payload = {
        "choices": [{"message": {"content": json.dumps({"score": 1.7, "explanation": "x"})}}]
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["response_format"]["type"] == "json_schema"
            assert self.headers["Authorization"] == "Bearer judge-key"
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    judge = evals.Judge(f"http://127.0.0.1:{server.server_port}/", "judge-key", "support-judge")
    assert judge.grade("p") == (1.0, "x")  # clamped
    server.shutdown()


def test_reset_fixtures_paths(project, operator, state, monkeypatch):
    from agentbreeder import secret_files

    eval_set = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, "DELETE FROM t;")
    evals.reset_fixtures(
        evals.EvalSet("g", "1", "j", CASES, None), operator.load(), "dev", state.directory
    )
    operator.config["environments"]["dev"]["eval_database"] = "secret://evaldb"
    secret_files.set_secret(state.directory, "evaldb", b"postgresql://example")
    executed = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            executed.append(sql)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda url, **kw: FakeConnection())
    evals.reset_fixtures(eval_set, operator.load(), "dev", state.directory)
    assert executed == ["DELETE FROM t;"]


def test_cli_eval_and_release(project, operator, state, monkeypatch, capsys):
    from agentbreeder import cli

    prepare_lock(project, operator)
    operator.load()

    def fake_run(eval_set, endpoint, operator_, environment, state_dir, **kwargs):
        return Evaluator()(eval_set, endpoint, operator_, environment, state_dir, retries=0)

    monkeypatch.setattr(evals, "run", fake_run)
    code = cli.main(
        [
            "eval",
            str(project / "agent.yaml"),
            "--endpoint",
            "http://x",
            "--state-dir",
            str(state.directory),
        ]
    )
    assert code == 0 and "2/2 passed" in capsys.readouterr().out
    monkeypatch.setattr(
        release_module,
        "release",
        lambda *a, **k: release_module.Outcome(EXIT_EVAL, "Eval gate failed", {}),
    )
    code = cli.main(
        [
            "release",
            str(project / "agent.yaml"),
            "--environment",
            "dev",
            "--state-dir",
            str(state.directory),
        ]
    )
    assert code == EXIT_EVAL and "Eval gate failed" in capsys.readouterr().out
    code = cli.main(
        ["release", str(project / "agent.yaml"), "--rollback", "--state-dir", str(state.directory)]
    )
    assert code == 2 and "NO_RELEASE_JOURNAL" in capsys.readouterr().err


def test_retries_reset_fixtures_first(project, operator, state, monkeypatch):
    agent = Agent(reply="bye")
    eval_set = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, "RESET;")
    resets = []
    monkeypatch.setattr(evals, "reset_fixtures", lambda *a: resets.append(1))

    class FailOnce(FakeJudge):
        calls = 0

        def grade(self, prompt):
            FailOnce.calls += 1
            return (0.1 if FailOnce.calls == 1 else 1.0), "ok"

    evals.run(
        eval_set, agent.url, operator.load(), "dev", state.directory, retries=1, judge=FailOnce()
    )
    assert len(resets) == 2  # before the run and before the retry round
    agent.server.shutdown()


@pytest.mark.parametrize(
    "field,value",
    [("cases", "../outside.jsonl"), ("cases", "/etc/passwd"), ("fixtures", "sub/f.sql")],
)
def test_eval_manifest_file_names_stay_inside(tmp_path, field, value):
    d = tmp_path / "e"
    d.mkdir()
    (d / "golden.jsonl").write_text(json.dumps(CASES[0]) + "\n")
    (d / "manifest.yaml").write_text(
        f"kind: eval\nname: g\nversion: 1.0.0\njudge: {{alias: j}}\n{field}: {value}\n"
    )
    with pytest.raises(evals.EvalError, match="plain file name"):
        evals.load_eval_set(d)


def test_rollback_validates_journal_and_gate(project, operator, state):
    from conftest import actor

    from agentbreeder.release import ReleaseError, _journal_path

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, "sha256:" + "8" * 64)
    _, service = do_release(project, operator, state, runtime, "sha256:" + "9" * 64)
    journal = _journal_path(state.directory, "helper")
    assert journal.parent.stat().st_mode & 0o777 == 0o700
    data = json.loads(journal.read_text())
    data["previous"]["manifest"]["name"] = "someone-else"
    journal.write_text(json.dumps(data))
    with pytest.raises(ReleaseError, match="JOURNAL_MISMATCH"):
        rollback(project / "agent.yaml", service, actor("dave"))
    data["previous"]["manifest"]["name"] = "helper"
    journal.write_text(json.dumps(data))
    operator.config["environments"]["dev"]["models"] = ["support-judge"]  # now denied
    service = DeploymentService(state, runtime, operator.load())
    prod = state.active("helper")["id"]
    denied = rollback(project / "agent.yaml", service, actor("dave"))
    assert denied.code == EXIT_DENIED and state.active("helper")["id"] == prod  # untouched


def test_gateway_and_fixture_failures_are_eval_errors(project, operator, state, monkeypatch):
    from test_gateway_keys import FakeGateway

    gateway = FakeGateway()
    gateway.down = True
    master = project / "master"
    master.write_text("sk-master")
    master.chmod(0o600)
    operator.config["gateways"]["gw"].update({"url": gateway.url, "master_key": f"file:{master}"})
    eval_set = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, None)
    with pytest.raises(evals.EvalError) as caught:
        evals.run(eval_set, "http://127.0.0.1:9", operator.load(), "dev", state.directory)
    assert caught.value.code in ("GATEWAY_UNREACHABLE", "GATEWAY_ERROR")
    gateway.server.shutdown()

    psycopg = pytest.importorskip("psycopg")
    operator.config["environments"]["dev"]["eval_database"] = "secret://eval-db"
    from agentbreeder import secret_files

    secret_files.set_secret(state.directory, "eval-db", b"postgresql://u:pw@127.0.0.1:1/x")

    def refuse(*args, **kwargs):
        raise psycopg.OperationalError("connection to 127.0.0.1 user u password pw failed")

    monkeypatch.setattr(psycopg, "connect", refuse)
    with_fixtures = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, "SELECT 1;")
    with pytest.raises(evals.EvalError) as caught:
        evals.reset_fixtures(with_fixtures, operator.load(), "dev", state.directory)
    assert caught.value.code == "EVAL_FIXTURES" and "pw" not in str(caught.value)


def test_release_records_eval_errors(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()

    def broken(*args, **kwargs):
        raise evals.EvalError("GATEWAY_UNREACHABLE", "LiteLLM is unreachable")

    with pytest.raises(evals.EvalError):
        do_release(project, operator, state, runtime, "sha256:" + "7" * 64, evaluator=broken)
    rows = repr(state.audit_rows(limit=50))
    assert "GATEWAY_UNREACHABLE" in rows
    assert state.active("helper-candidate") is None  # candidate removed


def test_unknown_environment_is_a_deny_not_a_traceback(project, operator, state):
    from conftest import actor

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, "sha256:" + "e" * 64)
    _, service = do_release(project, operator, state, runtime, "sha256:" + "f" * 64)
    prod = state.active("helper")["id"]
    outcome = rollback(project / "agent.yaml", service, actor("dave"), environment="nope")
    assert outcome.code == EXIT_DENIED and "CONTEXT_ENVIRONMENT_UNKNOWN" in outcome.message
    assert state.active("helper")["id"] == prod


def test_rollback_whose_deploy_fails_restores_what_was_running(
    project, operator, state, monkeypatch
):
    from conftest import actor

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    old, new = "sha256:" + "1" * 63 + "a", "sha256:" + "2" * 63 + "b"
    do_release(project, operator, state, runtime, old)
    _, service = do_release(project, operator, state, runtime, new)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.image == old:
            from agentbreeder.store import DeploymentError as StoreError

            raise StoreError("container create failed")
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    outcome = rollback(project / "agent.yaml", service, actor("dave"))
    assert (
        outcome.code == EXIT_RUNTIME and "previous production restored (ready)" in outcome.message
    )
    assert state.manifest(state.active("helper")["id"])["image"] == new  # still running


def test_eval_only_rejects_unknown_ids(project, operator, state):
    eval_set = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, None)
    with pytest.raises(evals.EvalError) as caught:
        evals.run(
            eval_set,
            "http://127.0.0.1:9",
            operator.load(),
            "dev",
            state.directory,
            only={"nope"},
            judge=object(),
        )
    assert caught.value.code == "EVAL_INVALID"


def test_empty_master_key_is_an_eval_error(project, operator, state):
    master = project / "empty-master"
    master.write_text("")
    master.chmod(0o600)
    operator.config["gateways"]["gw"].update({"master_key": f"file:{master}"})
    eval_set = evals.EvalSet("golden", "1.0.0", "support-judge", CASES, None)
    with pytest.raises(evals.EvalError):
        evals.run(eval_set, "http://127.0.0.1:9", operator.load(), "dev", state.directory)


def test_rollback_refuses_when_previous_image_is_gone(project, operator, state):
    from conftest import actor

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    old = "sha256:" + "c" * 64
    do_release(project, operator, state, runtime, old)
    _, service = do_release(project, operator, state, runtime, "sha256:" + "d" * 64)
    prod = state.active("helper")["id"]
    runtime.missing_images = {old}
    outcome = rollback(project / "agent.yaml", service, actor("dave"))
    assert outcome.code == EXIT_RUNTIME and "no longer available" in outcome.message
    assert state.active("helper")["id"] == prod  # production untouched


def test_promote_that_raises_restores_previous(project, operator, state, monkeypatch):
    from agentbreeder.store import DeploymentError as StoreError

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    old = "sha256:" + "a" * 64
    do_release(project, operator, state, runtime, old)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.name == "helper" and manifest.image != old:
            raise StoreError("gateway key not minted")
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    outcome, _ = do_release(project, operator, state, runtime, "sha256:" + "b" * 64)
    assert (
        outcome.code == EXIT_RUNTIME and "previous production restored (ready)" in outcome.message
    )
    assert state.manifest(state.active("helper")["id"])["image"] == old


def test_promote_gate_denied_restores_and_exits_denied(project, operator, state, monkeypatch):
    from agentbreeder.gate import GateDenied

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    old = "sha256:" + "c" * 64
    do_release(project, operator, state, runtime, old)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.name == "helper" and manifest.image != old:
            raise GateDenied("PRINCIPAL_BLOCKED", "blocked during the eval window")
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    outcome, _ = do_release(project, operator, state, runtime, "sha256:" + "d" * 64)
    assert outcome.code == EXIT_DENIED and "restored (ready)" in outcome.message
    assert state.manifest(state.active("helper")["id"])["image"] == old


def test_audit_failure_during_promote_is_not_swallowed(project, operator, state, monkeypatch):
    from agentbreeder.deployment import AuditIncomplete

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    old = "sha256:" + "e" * 64
    do_release(project, operator, state, runtime, old)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.name == "helper" and manifest.image != old:
            raise AuditIncomplete()
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    with pytest.raises(AuditIncomplete):
        do_release(project, operator, state, runtime, "sha256:" + "f" * 64)


def test_failed_candidate_removal_aborts_release(project, operator, state, monkeypatch):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    original = DeploymentService.remove

    def remove(self, identifier, **kwargs):
        record = self.store.get(identifier)
        if record["name"] == "helper-candidate":
            return {**record, "state": "failed", "error": "engine timeout"}
        return original(self, identifier, **kwargs)

    monkeypatch.setattr(DeploymentService, "remove", remove)
    from agentbreeder.release import ReleaseError

    with pytest.raises(ReleaseError, match="REMOVE_FAILED"):
        do_release(project, operator, state, runtime, "sha256:" + "1" * 64)
    assert state.active("helper") is None  # never promoted


def test_eval_sessions_unique_for_long_case_ids():
    seen = []

    class Recorder:
        def grade(self, prompt):
            return 1.0, "ok"

    import agentbreeder.evals as ev

    original = ev._post

    def post(url, body, headers=None, timeout=180):
        seen.append(body["session_id"])
        return {"reply": "ok", "tools": [], "sources": []}

    ev._post = post
    try:
        long_a = {"id": "x" * 50 + "a", "turns": ["hi"], "reference": "r"}
        long_b = {"id": "x" * 50 + "b", "turns": ["hi"], "reference": "r"}
        for case in (long_a, long_b):
            for attempt in (1, 2):
                ev.run_case("http://x", "20261107-220940-67e281", case, Recorder(), attempt)
    finally:
        ev._post = original
    assert len(set(seen)) == 4 and all(len(s) <= 64 for s in seen)


def test_production_changed_during_evals_aborts(project, operator, state):
    from agentbreeder.release import ReleaseError

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, "sha256:" + "2" * 64)
    first = state.active("helper")["id"]

    class SwapDuringEval(Evaluator):
        def __call__(self, eval_set, endpoint, operator_, environment, state_dir, *, retries):
            # an operator replaces production with a plain deploy while the evals run
            from conftest import actor

            svc = DeploymentService(state, runtime, operator.load())
            svc.remove(first, local=True, actor=actor("ops"))
            manifest = state.manifest(first)
            from agentbreeder.manifest import Manifest

            svc.deploy(
                Manifest.model_validate({**manifest, "port": 9090}),
                local=True,
                environment="dev",
                deployer=actor("ops"),
            )
            return super().__call__(
                eval_set, endpoint, operator_, environment, state_dir, retries=retries
            )

    with pytest.raises(ReleaseError, match="PRODUCTION_CHANGED"):
        do_release(project, operator, state, runtime, "sha256:" + "3" * 64, SwapDuringEval())
    survivor = state.active("helper")
    assert survivor and survivor["id"] != first and state.manifest(survivor["id"])["port"] == 9090
    from agentbreeder.release import _journal_path

    journal = json.loads(_journal_path(state.directory, "helper").read_text())
    assert journal["image"] != "sha256:" + "3" * 64  # the aborted release wrote no journal
