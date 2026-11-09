"""Offline evaluation: run the golden dataset against the deployed agent and score it.

Each case runs in a fresh session and a fresh customer ID (eval-<run>-<case>), so memory from
other runs cannot leak in. A case passes when every rule-based check passes AND the LLM judge
scores correctness against the reference answer >= CORRECTNESS_PASS.

Checks: sources_any (an expected article was retrieved), tools_include / tools_exclude,
must_match / must_not_match (case-insensitive regexes over the final reply).

A turn is a message string, or {"message": ..., "new_session": true} to continue as the same
customer in a fresh session (tests long-term memory across conversations).

Results go to eval_runs / eval_results (shown on the ops dashboard). Exit code 1 when the pass
rate is below --threshold, so this can gate a deploy or CI job.

  python ops/offline_evals.py [--threshold 0.8] [--label "after prompt change"] [--only id,...]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError

from common import JUDGE_MODEL, agent_endpoint, connect, judge, post_json

CORRECTNESS_PASS = 0.7
SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "explanation": {"type": "string"},
    },
    "required": ["score", "explanation"],
}


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


def run_case(endpoint: str, run_id: str, case: dict, attempt: int = 1) -> dict:
    suffix = f"-r{attempt}" if attempt > 1 else ""
    session = f"eval-{run_id}-{case['id'][:40]}{suffix}"[:64]
    # Cases about orders use fixed seeded customers (db/commerce.sql); others get a fresh one.
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
                post_json(
                    f"{endpoint}/invoke",
                    {"message": message, "session_id": current, "customer_id": customer},
                    timeout=180,
                )
            )
    except (HTTPError, URLError, TimeoutError) as failure:
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
            verdict = judge(correctness_prompt(case, reply), SCHEMA)
            correctness = max(0.0, min(1.0, float(verdict["score"])))
            explanation = verdict["explanation"]
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as failure:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(Path(__file__).with_name("golden.jsonl")))
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--label", default=None)
    parser.add_argument("--only", default=None, help="comma-separated case IDs")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="re-run a failing case up to N times (LLM nondeterminism); "
        "it passes only if a retry passes, and is reported as flaky",
    )
    args = parser.parse_args()

    cases = [
        json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line.strip()
    ]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    endpoint = args.endpoint or agent_endpoint()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    db = connect()
    # Reset fixtures that cases mutate (cancellation requests on the seeded eval customers).
    db.execute("DELETE FROM cancellation_requests WHERE customer_id LIKE 'eval-cust-%%'")
    db.execute("UPDATE orders SET status = 'paid' WHERE id IN ('NW-90002', 'NW-90004')")
    db.execute(
        "INSERT INTO eval_runs (id, endpoint, dataset, judge_model, label) "
        "VALUES (%s, %s, %s, %s, %s)",
        (run_id, endpoint, Path(args.dataset).name, JUDGE_MODEL, args.label),
    )
    print(
        f"Offline eval run {run_id}: {len(cases)} cases against {endpoint} (judge {JUDGE_MODEL})"
    )

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda c: run_case(endpoint, run_id, c), cases))
        by_id = {c["id"]: c for c in cases}
        for attempt in range(2, args.retries + 2):
            failing = [i for i, r in enumerate(results) if not r["passed"]]
            if not failing:
                break
            print(f"  retrying {len(failing)} failing case(s), attempt {attempt}")
            retried = pool.map(
                lambda i: run_case(endpoint, run_id, by_id[results[i]["case_id"]], attempt),
                failing,
            )
            for i, result in zip(failing, retried):
                results[i] = result

    for r in results:
        db.execute(
            "INSERT INTO eval_results (run_id, case_id, category, passed, checks, correctness, "
            "explanation, reply, latency_ms, trace_id, tools) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                r["case_id"],
                r["category"],
                r["passed"],
                json.dumps({**r["checks"], "_attempts": r["attempts"]}),
                r["correctness"],
                r["explanation"],
                r["reply"],
                r["latency_ms"],
                r["trace_id"],
                json.dumps(r["tools"]),
            ),
        )
        mark = ("PASS" if r["attempts"] == 1 else "FLKY") if r["passed"] else "FAIL"
        failed = [k for k, ok in r["checks"].items() if not ok]
        print(
            f"  {mark}  {r['case_id']:<26} correctness={r['correctness']:.2f} "
            f"{r['latency_ms'] / 1000:5.1f}s  {'failed: ' + ', '.join(failed) if failed else ''}"
        )

    passed = sum(r["passed"] for r in results)
    flaky = [r["case_id"] for r in results if r["passed"] and r["attempts"] > 1]
    if flaky:
        print(f"\nFlaky (passed on retry): {', '.join(flaky)}")
    rate = passed / len(results) if results else 0.0
    mean = statistics.mean(r["correctness"] for r in results) if results else 0.0
    p50 = int(statistics.median(r["latency_ms"] for r in results)) if results else 0
    db.execute(
        "UPDATE eval_runs SET finished=now(), cases=%s, passed=%s, pass_rate=%s, "
        "mean_correctness=%s, p50_latency_ms=%s WHERE id=%s",
        (len(results), passed, rate, mean, p50, run_id),
    )
    print(
        f"\n{passed}/{len(results)} passed ({rate:.0%}), mean correctness {mean:.2f}, "
        f"p50 latency {p50 / 1000:.1f}s — threshold {args.threshold:.0%}: "
        f"{'OK' if rate >= args.threshold else 'BELOW THRESHOLD'}"
    )
    return 0 if rate >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
