"""Offline evals of a deployed agent against its registry eval set (`eval/<name>@X.Y.Z`).

Each case runs in a fresh session (and, unless the case names one, a fresh customer). A case
passes when every rule check passes AND the LLM judge scores correctness against the reference
answer at least CORRECTNESS_PASS. The judge goes through the operator's LiteLLM gateway with
its own short-lived key (alias `agentbreeder-eval-<run>`), minted for the run and revoked after.

Rule checks: sources_any, tools_include / tools_exclude, must_match / must_not_match.
A turn is a message string, or {"message": ..., "new_session": true} (same customer, new
session) to test long-term memory. Fixtures (e.g. cancellation requests the cases create) are
reset first with the eval set's fixtures.sql against the environment's `eval_database`.
Ported from the Northwind reference agent's ops/offline_evals.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from agentbreeder import gateway_keys, secret_files
from agentbreeder.operator_config import OperatorConfig
from agentbreeder.strict_yaml import StrictYamlError, load_file

JUDGE_KEY_DURATION = "6h"  # judge keys expire even if a killed run never revokes them

CORRECTNESS_PASS = 0.7
MAX_CASES = 1000
_OPENER = build_opener(ProxyHandler({}))
SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "explanation": {"type": "string"}},
    "required": ["score", "explanation"],
    "additionalProperties": False,
}


class EvalError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass
class EvalSet:
    name: str
    version: str
    judge_alias: str
    cases: list[dict]
    fixtures_sql: str | None


@dataclass
class EvalReport:
    run_id: str
    endpoint: str
    eval_set: str
    judge: str
    results: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(r["passed"] for r in self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.results) if self.results else 0.0

    def summary(self) -> dict:
        latencies = [r["latency_ms"] for r in self.results]
        return {
            "run_id": self.run_id,
            "endpoint": self.endpoint,
            "eval_set": self.eval_set,
            "judge": self.judge,
            "cases": len(self.results),
            "passed": self.passed,
            "pass_rate": round(self.pass_rate, 4),
            "flaky": [r["case_id"] for r in self.results if r["passed"] and r["attempts"] > 1],
            "failed": [r["case_id"] for r in self.results if not r["passed"]],
            "mean_correctness": round(statistics.fmean(r["correctness"] for r in self.results), 3)
            if self.results
            else 0.0,
            "p50_latency_ms": int(statistics.median(latencies)) if latencies else 0,
        }


def _inside(directory: Path, name, field: str) -> Path:
    """A file named in the eval manifest must be a plain file in the (hashed) eval version."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
        raise EvalError("EVAL_INVALID", f"eval manifest `{field}` must be a plain file name")
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise EvalError("EVAL_INVALID", f"eval manifest `{field}` file is missing")
    return path


def load_eval_set(directory: Path) -> EvalSet:
    try:
        manifest = load_file(directory / "manifest.yaml")
    except StrictYamlError as error:
        raise EvalError("EVAL_INVALID", f"eval manifest: {error}") from error
    judge = (manifest.get("judge") or {}).get("alias")
    if not isinstance(judge, str) or not judge:
        raise EvalError("EVAL_INVALID", "eval manifest needs judge.alias")
    cases_file = _inside(directory, manifest.get("cases") or "golden.jsonl", "cases")
    cases = []
    for number, line in enumerate(cases_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except ValueError as error:
            raise EvalError("EVAL_INVALID", f"{cases_file.name}:{number} is not JSON") from error
        if not isinstance(case, dict) or not {"id", "turns", "reference"} <= set(case):
            raise EvalError(
                "EVAL_INVALID", f"{cases_file.name}:{number} needs id, turns, reference"
            )
        cases.append(case)
    if not cases or len(cases) > MAX_CASES:
        raise EvalError("EVAL_INVALID", f"eval set must have 1..{MAX_CASES} cases")
    fixtures = manifest.get("fixtures")
    sql = (
        _inside(directory, fixtures, "fixtures").read_text(encoding="utf-8") if fixtures else None
    )
    return EvalSet(str(manifest.get("name")), str(manifest.get("version")), judge, cases, sql)


def _post(url: str, body: dict, headers: dict | None = None, timeout: int = 180) -> dict:
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with _OPENER.open(request, timeout=timeout) as response:
        return json.load(response)


class Judge:
    def __init__(self, gateway_url: str, key: str, alias: str):
        self.url, self.key, self.alias = gateway_url.rstrip("/"), key, alias

    def grade(self, prompt: str) -> tuple[float, str]:
        data = _post(
            f"{self.url}/v1/chat/completions",
            {
                "model": self.alias,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "grade", "strict": True, "schema": SCHEMA},
                },
            },
            {"Authorization": f"Bearer {self.key}"},
            timeout=120,
        )
        verdict = json.loads(data["choices"][0]["message"]["content"])
        return max(0.0, min(1.0, float(verdict["score"]))), str(verdict["explanation"])[:1000]


def correctness_prompt(case: dict, reply: str) -> str:
    conversation = "\n".join(
        (
            "[new conversation, same customer]\n"
            if isinstance(t, dict) and t.get("new_session")
            else ""
        )
        + f"Customer: {t['message'] if isinstance(t, dict) else t}"
        for t in case["turns"]
    )
    return f"""Grade a customer support agent's final reply against a reference answer.

Conversation:
{conversation}

Reference answer (what a correct reply must convey):
{case["reference"]}

Agent's final reply:
{reply}

Score correctness from 0 to 1: 1 = conveys every essential point of the reference with nothing
wrong or invented; 0.5 = partially correct or missing an essential point; 0 = wrong, invented
policy, or harmful. Extra helpful detail is fine if it is not wrong. Explain in one sentence."""


def run_case(endpoint: str, run_id: str, case: dict, judge: Judge, attempt: int = 1) -> dict:
    suffix = f"-r{attempt}" if attempt > 1 else ""
    # run id (22) + a short hash of the full case id keeps sessions unique per case and attempt
    # within the runtime's 64-character id limit.
    case_key = hashlib.sha256(case["id"].encode()).hexdigest()[:12]
    session = f"eval-{run_id}-{case_key}{suffix}"
    customer = case.get("customer_id") or session
    responses, started, error = [], time.monotonic(), None
    try:
        current, sessions = session, 1
        for turn in case["turns"]:
            if isinstance(turn, dict) and turn.get("new_session"):
                sessions += 1
                current = f"{session[:60]}-s{sessions}"
            message = turn["message"] if isinstance(turn, dict) else turn
            responses.append(
                _post(
                    f"{endpoint}/invoke",
                    {"message": message, "session_id": current, "customer_id": customer},
                )
            )
    except (HTTPError, URLError, TimeoutError, ValueError) as failure:
        error = f"{type(failure).__name__}: {getattr(failure, 'code', '')}"
    latency = int((time.monotonic() - started) * 1000)
    final = responses[-1] if responses and not error else {}
    reply = final.get("reply", "")
    tools = [t for r in responses for t in r.get("tools", [])]
    sources = final.get("sources", [])
    expect = case.get("expect", {})
    checks: dict[str, bool] = {"agent_responded": bool(reply) and not error}
    if "sources_any" in expect:
        checks["retrieved_expected_article"] = any(s in sources[:3] for s in expect["sources_any"])
    for tool in expect.get("tools_include", []):
        checks[f"called:{tool}"] = tool in tools
    for tool in expect.get("tools_exclude", []):
        checks[f"did_not_call:{tool}"] = tool not in tools
    for pattern in expect.get("must_match", []):
        checks[f"matches:{pattern}"] = bool(re.search(pattern, reply, re.I))
    for pattern in expect.get("must_not_match", []):
        checks[f"avoids:{pattern}"] = not re.search(pattern, reply, re.I)
    correctness, explanation = 0.0, error or ""
    if reply and not error:
        try:
            correctness, explanation = judge.grade(correctness_prompt(case, reply))
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError, IndexError) as failure:
            explanation = f"judge failed: {type(failure).__name__}"
    checks["judge_correct"] = correctness >= CORRECTNESS_PASS
    return {
        "case_id": case["id"],
        "category": case.get("category"),
        "attempts": attempt,
        "passed": all(checks.values()),
        "checks": checks,
        "correctness": correctness,
        "explanation": explanation,
        "reply": reply,
        "latency_ms": latency,
        "trace_id": final.get("trace_id"),
        "tools": tools,
    }


def reset_fixtures(eval_set: EvalSet, operator: OperatorConfig, environment: str, state: Path):
    """Run the eval set's fixtures.sql against the environment's eval_database."""
    if not eval_set.fixtures_sql:
        return
    env = operator.model.environments[environment]
    if not env.eval_database:
        raise EvalError(
            "EVAL_DATABASE_UNCONFIGURED",
            f"{eval_set.name} has fixtures; set environments.{environment}.eval_database",
        )
    try:
        import psycopg
    except ImportError as error:  # optional dependency: pip install "agentbreeder[eval]"
        raise EvalError(
            "EVAL_DEPENDENCY", 'Install fixtures support: pip install "agentbreeder[eval]"'
        ) from error
    name = secret_files.secret_name(env.eval_database)
    url = secret_files._read(state / "secrets" / name).decode()
    with psycopg.connect(url, autocommit=True, connect_timeout=10) as db:
        db.execute(eval_set.fixtures_sql)


def run(
    eval_set: EvalSet,
    endpoint: str,
    operator: OperatorConfig,
    environment: str,
    state: Path,
    *,
    retries: int = 1,
    concurrency: int = 4,
    only: set[str] | None = None,
    judge: Judge | None = None,
) -> EvalReport:
    env = operator.model.environments.get(environment)
    if env is None or env.gateway is None:
        raise EvalError("MODEL_ACCESS_UNCONFIGURED", f"Environment {environment} has no gateway")
    if eval_set.judge_alias not in env.models:
        raise EvalError(
            "MODEL_NOT_ALLOWED",
            f"Judge alias {eval_set.judge_alias} is not allowed in {environment}",
        )
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    cases = [c for c in eval_set.cases if not only or c["id"] in only]
    reset_fixtures(eval_set, operator, environment, state)
    report = EvalReport(
        run_id, endpoint, f"eval/{eval_set.name}@{eval_set.version}", eval_set.judge_alias
    )
    client = None
    key_alias = f"{gateway_keys.PREFIX}eval-{run_id}"
    if judge is None:
        client = gateway_keys.client(operator, env.gateway)
        if client is None:
            raise EvalError(
                "GATEWAY_CREDENTIAL", "The gateway needs a master_key to mint a judge key"
            )
        # Revoked in `finally` below; the expiry covers a run that is killed before that.
        key = client.mint(
            key_alias,
            [eval_set.judge_alias],
            5.0,
            300,
            {"agentbreeder": True, "eval_run": run_id},
            duration=JUDGE_KEY_DURATION,
        )
        judge = Judge(operator.model.gateways[env.gateway].url, key, eval_set.judge_alias)
    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            results = list(pool.map(lambda c: run_case(endpoint, run_id, c, judge), cases))
            by_id = {c["id"]: c for c in cases}
            for attempt in range(2, retries + 2):
                failing = [i for i, r in enumerate(results) if not r["passed"]]
                if not failing:
                    break
                # A first attempt may have mutated fixtures (e.g. filed a cancellation request);
                # retries must start from the same state, or they test a different scenario.
                reset_fixtures(eval_set, operator, environment, state)
                retried = list(
                    pool.map(
                        lambda i, attempt=attempt: run_case(
                            endpoint, run_id, by_id[results[i]["case_id"]], judge, attempt
                        ),
                        failing,
                    )
                )
                for i, result in zip(failing, retried, strict=True):
                    results[i] = result
        report.results = results
    finally:
        if client is not None:
            client.revoke(key_alias)
    return report
