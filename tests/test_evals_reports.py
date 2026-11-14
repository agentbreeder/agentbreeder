"""`agentbreeder evals list|show` (#932): read the eval reports release saves."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentbreeder import cli, eval_reports, secret_files
from agentbreeder.eval_reports import Entry, ReportError

RUN = "20261113-213200-1a2b3c"
OLDER = "20261112-090000-abcdef"
TRACE = "ab" * 16
SECRET = "seeded-marker-" + "q" * 20


def _case(case_id="c1", passed=True, **extra) -> dict:
    row = {
        "case_id": case_id,
        "category": "billing",
        "attempts": 1,
        "passed": passed,
        "checks": {"agent_responded": True, "judge_correct": passed},
        "correctness": 0.9 if passed else 0.1,
        "explanation": "fine" if passed else "wrong\nanswer",
        "reply": "hello",
        "latency_ms": 120,
        "trace_id": TRACE,
        "tools": ["lookup_order"],
    }
    return row | extra


def _report(run=RUN, results=None, **extra) -> dict:
    results = [_case(), _case("c2", False)] if results is None else results
    passed = sum(1 for r in results if isinstance(r, dict) and r.get("passed") is True)
    return {
        "run_id": run,
        "endpoint": "http://127.0.0.1:8080",
        "eval_set": "eval/support@1.0.0",
        "judge": "judge-model",
        "cases": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "flaky": [],
        "failed": ["c2"],
        "mean_correctness": 0.5,
        "p50_latency_ms": 120,
        "results": results,
    } | extra


@pytest.fixture
def project(tmp_path) -> Path:
    (tmp_path / "proj" / "deploy").mkdir(parents=True)
    return tmp_path / "proj"


def _save(project: Path, data, agent="support", run=RUN) -> Path:
    path = project / "deploy" / f"{agent}.eval-{run}.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def _cli(project, tmp_path, *argv) -> int:
    command, *rest = argv
    return cli.main(
        ["evals", command, *rest, "--project", str(project), "--state-dir", str(tmp_path / "st")]
    )


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# --- list -------------------------------------------------------------------------------------


def test_list_rows_newest_first(project, tmp_path, capsys):
    _save(project, _report())
    _save(project, _report(OLDER), run=OLDER)
    _save(project, _report(), agent="other", run=OLDER.replace("abcdef", "000000"))
    (project / "deploy" / "support.eval-latest.json").write_text("{}")  # not a report name
    (project / "deploy" / "support.json").write_text("{}")
    assert _cli(project, tmp_path, "list", "--json") == 0
    data = _json(capsys)
    assert data["schema_version"] == 1 and data["total"] == 3 and not data["truncated"]
    assert [r["run_id"] for r in data["reports"]] == [RUN, OLDER, OLDER[:-6] + "000000"]
    first = data["reports"][0]
    assert first["agent"] == "support" and first["passed"] == 1 and first["cases"] == 2
    assert first["judge"] == "judge-model" and isinstance(first["modified"], int)


def test_list_text_filter_and_error_row(project, tmp_path, capsys):
    _save(project, _report())
    _save(project, "{nope", run=OLDER)
    _save(project, _report(), agent="other")
    assert _cli(project, tmp_path, "list", "--agent", "support") == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{RUN}  support  1/2 passed  judge judge-model",
        f"{OLDER}  support  [EVAL_REPORT_INVALID]",
    ]


def test_list_empty_and_truncated(project, tmp_path, capsys, monkeypatch):
    assert _cli(project, tmp_path, "list") == 0
    assert capsys.readouterr().out == "No eval reports.\n"
    _save(project, _report())
    _save(project, _report(OLDER), run=OLDER)
    monkeypatch.setattr(eval_reports, "MAX_LIST", 1)
    assert _cli(project, tmp_path, "list") == 0
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1 and "newest 1 of 2" in captured.err
    assert _cli(project, tmp_path, "list", "--json") == 0
    assert _json(capsys)["truncated"] is True


def test_list_unreadable_entry_row(project, monkeypatch):
    _save(project, _report())
    real = os.lstat

    def lstat(path, *args, **kwargs):
        if str(path).endswith(".json"):
            raise PermissionError(13, "Permission denied")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(eval_reports.os, "lstat", lstat)
    rows, total = eval_reports.summaries(project / "deploy", None, str)
    assert total == 1 and rows[0]["error"]["code"] == "EVAL_REPORT_UNREADABLE"


def test_missing_directory(tmp_path, capsys):
    assert _cli(tmp_path / "none", tmp_path, "list", "--json") == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[EVAL_REPORT_NOT_FOUND]")
    assert json.loads(captured.out)["error"]["code"] == "EVAL_REPORT_NOT_FOUND"


def test_directory_must_not_be_a_symlink_or_file(tmp_path, capsys):
    real = tmp_path / "real"
    (real / "deploy").mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "deploy").symlink_to(real / "deploy")
    assert _cli(linked, tmp_path, "list") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "deploy").write_text("x")
    assert _cli(plain, tmp_path, "list") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


def test_directory_unreadable(project, monkeypatch):
    def lstat(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(eval_reports.os, "lstat", lstat)
    with pytest.raises(ReportError) as caught:
        eval_reports.entries(project / "deploy")
    assert caught.value.code == "EVAL_REPORT_UNREADABLE"


def test_listing_fails(project, monkeypatch):
    def scandir(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(eval_reports.os, "scandir", scandir)
    with pytest.raises(ReportError) as caught:
        eval_reports.entries(project / "deploy")
    assert caught.value.code == "EVAL_REPORT_UNREADABLE"


def test_bad_agent_name(project, tmp_path, capsys):
    assert _cli(project, tmp_path, "list", "--agent", "../x") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


def test_output_flag_and_usage(project, tmp_path, capsys):
    _save(project, _report())
    state = str(tmp_path / "st")
    out = str(project / "deploy")
    assert cli.main(["evals", "list", "--output", out, "--state-dir", state]) == 0
    assert RUN in capsys.readouterr().out
    argv = ["evals", "list", "--output", out, "--project", str(project), "--state-dir", state]
    assert cli.main(argv) == 2
    assert capsys.readouterr().err.startswith("[USAGE]")


def test_default_project_is_cwd(project, tmp_path, capsys, monkeypatch):
    _save(project, _report())
    monkeypatch.chdir(project)
    assert cli.main(["evals", "show", "--state-dir", str(tmp_path / "st")]) == 0
    assert f"Run {RUN}" in capsys.readouterr().out


# --- show -------------------------------------------------------------------------------------


def test_show_newest_json(project, tmp_path, capsys):
    _save(project, _report(OLDER), run=OLDER)
    _save(project, _report())
    assert _cli(project, tmp_path, "show", "--json") == 0
    data = _json(capsys)
    assert data["run_id"] == RUN and data["agent"] == "support" and data["content"] is False
    assert data["file"] == f"support.eval-{RUN}.json" and data["notes"] == []
    assert (data["cases"], data["passed"], data["judge"]) == (2, 1, "judge-model")
    failed = data["results"][1]
    assert failed == {
        "id": "c2",
        "category": "billing",
        "passed": False,
        "attempts": 1,
        "checks": {"agent_responded": True, "judge_correct": False},
        "failed_checks": ["judge_correct"],
        "correctness": 0.1,
        "reason": "wrong answer",
        "latency_ms": 120,
        "trace_id": TRACE,
        "tools": ["lookup_order"],
    }
    assert "reply" not in failed and "explanation" not in failed


def test_show_by_run_text(project, tmp_path, capsys):
    flaky = _case("c3", attempts=2, trace_id="not-a-trace")
    _save(project, _report(OLDER, results=[_case(), _case("c2", False), flaky]), run=OLDER)
    _save(project, _report())
    assert _cli(project, tmp_path, "show", OLDER) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Run {OLDER}  agent support  eval set eval/support@1.0.0  judge judge-model"
    assert out[1] == "2/3 passed (66.7%), mean correctness 0.5, p50 120 ms"
    assert out[2] == "Endpoint http://127.0.0.1:8080"
    assert out[3] == f"PASS c1  0.90  120 ms  trace {TRACE}"
    assert out[4] == f"FAIL c2  0.10  120 ms  trace {TRACE}"
    assert out[5:7] == ["     failed: judge_correct", "     reason: wrong answer"]
    assert out[7] == "FLKY c3  0.90  120 ms"


def test_show_content_is_redacted_escaped_and_capped(project, tmp_path, capsys, monkeypatch):
    state = tmp_path / "st"
    secret_files.set_secret(state, "token", SECRET.encode())
    reply = f"key {SECRET}\x1b]52;c;eA==\x07\nnext"
    explanation = "x" * 5000
    case = _case(reply=reply, explanation=explanation, case_id=f"id-{SECRET}")
    _save(project, _report(results=[case], endpoint=f"http://u:{SECRET}@h/"))
    assert _cli(project, tmp_path, "show", "--content", "--json") == 0
    data = _json(capsys)
    row = data["results"][0]
    assert SECRET not in json.dumps(data)
    assert row["id"] == "id-[redacted]" and row["reply"].startswith("key [redacted]")
    assert len(row["explanation"]) == eval_reports.CONTENT_LIMIT and row["reason"].endswith("…")
    assert data["content"] is True
    assert _cli(project, tmp_path, "show", "--content") == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\\x1b]52" in out and "\n       next" in out
    assert SECRET not in out


def test_show_redaction_incomplete(project, tmp_path, capsys):
    state = tmp_path / "st"
    secret_files.set_secret(state, "token", SECRET.encode())
    (state / "secrets" / "token").chmod(0o666)
    _save(project, _report())
    assert _cli(project, tmp_path, "show") == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[REDACTION_INCOMPLETE]") and captured.out == ""


@pytest.mark.parametrize("run", ["latest", "20261113-213200-1A2B3C", "../x"])
def test_show_bad_run(project, tmp_path, capsys, run):
    assert _cli(project, tmp_path, "show", run) == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


def test_show_not_found_and_ambiguous(project, tmp_path, capsys):
    assert _cli(project, tmp_path, "show") == 2
    assert "any run" in capsys.readouterr().err
    _save(project, _report())
    assert _cli(project, tmp_path, "show", OLDER, "--agent", "support") == 2
    assert "of agent support" in capsys.readouterr().err
    _save(project, _report(), agent="other")
    assert _cli(project, tmp_path, "show") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_AMBIGUOUS]")
    assert _cli(project, tmp_path, "show", RUN, "--agent", "other") == 0


def test_show_refuses_symlink_and_fifo(project, tmp_path, capsys):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(_report()))
    (project / "deploy" / f"support.eval-{RUN}.json").symlink_to(target)
    assert _cli(project, tmp_path, "show") == 2
    assert "symlink" in capsys.readouterr().err
    os.mkfifo(project / "deploy" / f"support.eval-{OLDER}.json")
    assert _cli(project, tmp_path, "show", OLDER) == 2
    assert "not a regular file" in capsys.readouterr().err


def test_show_unreadable_file(project, tmp_path, capsys):
    path = _save(project, _report())
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running as root")
        assert _cli(project, tmp_path, "show") == 2
        assert capsys.readouterr().err.startswith("[EVAL_REPORT_UNREADABLE]")
    finally:
        path.chmod(0o644)


def test_vanished_file():
    with pytest.raises(ReportError) as caught:
        eval_reports.read_raw(Entry("support", RUN, Path("/nonexistent/support.json")))
    assert caught.value.code == "EVAL_REPORT_NOT_FOUND"


def test_read_error(project, monkeypatch):
    path = _save(project, _report())

    class Broken:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fileno(self):
            return self.handle.fileno()

        def read(self, _n):
            raise OSError(5, "Input/output error")

    real = os.fdopen
    monkeypatch.setattr(
        eval_reports.os, "fdopen", lambda fd, mode, **kw: Broken(real(fd, mode, **kw))
    )
    with pytest.raises(ReportError) as caught:
        eval_reports.read_raw(Entry("support", RUN, path))
    assert caught.value.code == "EVAL_REPORT_UNREADABLE"


def test_too_large(project, tmp_path, capsys, monkeypatch):
    _save(project, _report())
    monkeypatch.setattr(eval_reports, "MAX_BYTES", 100)
    assert _cli(project, tmp_path, "show") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_TOO_LARGE]")


def test_grew_while_reading(project, monkeypatch):
    path = _save(project, _report())
    real = os.fstat

    class Small:
        def __init__(self, info):
            self.st_mode, self.st_size = info.st_mode, 1

    monkeypatch.setattr(eval_reports.os, "fstat", lambda fd: Small(real(fd)))
    monkeypatch.setattr(eval_reports, "MAX_BYTES", 100)
    with pytest.raises(ReportError) as caught:
        eval_reports.read_raw(Entry("support", RUN, path))
    assert caught.value.code == "EVAL_REPORT_TOO_LARGE"


@pytest.mark.parametrize(
    "text",
    [
        "{nope",
        "[1]",
        '{"cases": NaN}',
        "[" * 100_000 + "]" * 100_000,
        b"\xff\xfe".decode("latin-1"),
        '{"cases": ' + "9" * 5000 + "}",
    ],
)
def test_invalid_json(project, tmp_path, capsys, text):
    _save(project, text)
    assert _cli(project, tmp_path, "show") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


def test_invalid_utf8(project, tmp_path, capsys):
    (project / "deploy" / f"support.eval-{RUN}.json").write_bytes(b'{"a": "\xff"}')
    assert _cli(project, tmp_path, "show") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


@pytest.mark.parametrize(
    "change",
    [
        {"cases": True},
        {"cases": -1},
        {"passed": 5},
        {"pass_rate": "1"},
        {"pass_rate": 10**400},
        {"judge": None},
        {"flaky": "c1"},
        {"failed": None},
        {"mean_correctness": None},
        {"p50_latency_ms": 2**60},
        {"run_id": OLDER},
        {"results": {}},
    ],
)
def test_invalid_header(project, tmp_path, capsys, change):
    _save(project, _report() | change)
    assert _cli(project, tmp_path, "show") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


@pytest.mark.parametrize(
    "bad",
    [
        "case",
        {"case_id": 1},
        {"passed": "yes"},
        {"checks": []},
        {"checks": {"a": 1}},
        {"checks": {f"c{i}": True for i in range(eval_reports.MAX_CHECKS + 1)}},
        {"attempts": 0},
        {"latency_ms": -1},
        {"correctness": "0.5"},
        {"tools": "x"},
        {"explanation": 3},
    ],
)
def test_malformed_case_skipped(project, tmp_path, capsys, bad):
    broken = bad if isinstance(bad, str) else _case("c9") | bad
    _save(project, _report(results=[_case(), broken]))
    assert _cli(project, tmp_path, "show", "--json") == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert [r["id"] for r in data["results"]] == ["c1"]
    assert data["notes"] == ["1 malformed case(s) skipped"]
    assert "NOTE: 1 malformed case(s) skipped" in captured.err


def test_case_cap_and_optional_fields(project, tmp_path, capsys, monkeypatch):
    sparse = _case("c2", category=None, explanation=None, reply=None)
    del sparse["tools"]
    _save(project, _report(results=[_case(), sparse, _case("c3")]))
    monkeypatch.setattr(eval_reports, "MAX_CASES", 2)
    assert _cli(project, tmp_path, "show", "--json", "--content") == 0
    data = _json(capsys)
    assert data["notes"] == ["only the first 2 of 3 cases are shown"]
    row = data["results"][1]
    assert row["category"] is None and row["tools"] == [] and row["reason"] == ""
    assert row["reply"] == "" and row["explanation"] == ""


def test_stack_flag_resolves_state(project, tmp_path, capsys):
    _save(project, _report())
    argv = ["evals", "show", "--project", str(project), "--stack", str(tmp_path / "nostack")]
    assert cli.main(argv) == 2
    assert capsys.readouterr().err  # the stack resolution error, before any report is read


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["evals", "--help"])
    out = capsys.readouterr().out
    assert "list" in out and "show" in out


# --- review round 1 (#932) --------------------------------------------------------------------


def test_contract_with_release_writer(project, tmp_path, capsys):
    """A report written exactly as release.py writes it, from evals' own types."""
    from agentbreeder import evals

    error_case = {
        "case_id": "c1",
        "category": None,
        "attempts": 1,
        "passed": False,
        "checks": {"agent_responded": False, "judge_correct": False},
        "correctness": 0,
        "explanation": "URLError: ",
        "reply": "",
        "latency_ms": 3,
        "trace_id": None,
        "tools": [],
    }
    for run, results in ((RUN, [error_case]), (OLDER, [])):
        report = evals.EvalReport(run, "http://127.0.0.1:1", "eval/x@1.0.0", "judge", results)
        path = project / "deploy" / f"support.eval-{run}.json"
        path.write_text(json.dumps({**report.summary(), "results": report.results}, indent=2))
    assert _cli(project, tmp_path, "show", "--json", "--content") == 0
    data = _json(capsys)
    assert data["notes"] == [] and data["passed"] == 0 and data["cases"] == 1
    row = data["results"][0]
    assert row["category"] is None and row["trace_id"] is None and row["reason"] == "URLError:"
    assert row["correctness"] == 0 and row["reply"] == ""
    assert _cli(project, tmp_path, "show", OLDER, "--json") == 0
    empty = _json(capsys)
    assert (empty["cases"], empty["results"], empty["notes"]) == (0, [], [])


def test_directory_named_like_a_report(project, tmp_path, capsys):
    (project / "deploy" / f"support.eval-{RUN}.json").mkdir()
    assert _cli(project, tmp_path, "show", "--json") == 2
    captured = capsys.readouterr()
    assert (
        captured.err.startswith("[EVAL_REPORT_INVALID]") and "not a regular file" in captured.err
    )
    assert json.loads(captured.out)["error"]["code"] == "EVAL_REPORT_INVALID"
    assert _cli(project, tmp_path, "list", "--json") == 0
    assert _json(capsys)["reports"][0]["error"]["code"] == "EVAL_REPORT_INVALID"


def test_os_error_backstop(project, tmp_path, capsys, monkeypatch):
    def boom(*_a, **_k):
        raise OSError(5, None)

    monkeypatch.setattr(eval_reports, "find", boom)
    assert _cli(project, tmp_path, "show", "--json") == 2
    captured = capsys.readouterr()
    assert captured.err == "[EVAL_REPORT_UNREADABLE] OSError\n"


def test_why_without_strerror():
    assert eval_reports._why(OSError()) == "OSError"
    assert eval_reports._why(PermissionError(13, "Permission denied")) == "Permission denied"


def test_unicode_digits_are_not_run_ids(project, tmp_path, capsys):
    arabic = "٢٠٢٦١١١٣-٢١٣٢٠٠"
    _save(project, _report(), run=f"{arabic}-1a2b3c")
    _save(project, _report())
    assert _cli(project, tmp_path, "list", "--json") == 0
    assert [r["run_id"] for r in _json(capsys)["reports"]] == [RUN]
    assert _cli(project, tmp_path, "show", f"{arabic}-1a2b3c") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


def test_check_name_collisions_keep_failures(project, tmp_path, capsys):
    long = "matches:" + "a" * 90
    checks = {long + "1": False, long + "2": True, "a\n": False, "a ": True}
    _save(project, _report(results=[_case(passed=False, checks=checks)]))
    assert _cli(project, tmp_path, "show", "--json") == 0
    row = _json(capsys)["results"][0]
    assert len(row["checks"]) == 4
    shown = ("matches:" + "a" * 90)[:79] + "…"
    assert row["checks"] == {shown: False, shown + "#2": True, "a": False, "a#2": True}
    assert row["failed_checks"] == [shown, "a"]


def test_non_string_category_and_tools_are_coerced(project, tmp_path, capsys):
    _save(project, _report(results=[_case(category=3, tools=["t", 7, 1.5, True, {"x": 1}])]))
    assert _cli(project, tmp_path, "show", "--json") == 0
    data = _json(capsys)
    assert data["notes"] == []
    row = data["results"][0]
    assert row["category"] == "3" and row["tools"] == ["t", "7", "1.5"]
    _save(project, _report(results=[_case(category={"a": "b"})]))
    assert _cli(project, tmp_path, "show", "--json") == 0
    assert _json(capsys)["results"][0]["category"] is None


def test_summary_and_results_disagree(project, tmp_path, capsys):
    _save(project, _report() | {"cases": 5, "passed": 5})
    assert _cli(project, tmp_path, "show", "--json") == 0
    notes = _json(capsys)["notes"]
    assert notes == ["the summary says 5/5 passed, but the results list has 1/2"]


def test_list_error_row_keeps_modified(project, tmp_path, capsys):
    _save(project, "{nope")
    assert _cli(project, tmp_path, "list", "--json") == 0
    row = _json(capsys)["reports"][0]
    assert isinstance(row["modified"], int) and row["error"]["code"] == "EVAL_REPORT_INVALID"


def test_redaction_window_bounds_the_work():
    seen = []

    def redact(text):
        seen.append(len(text))
        return text.replace(SECRET, "[redacted]")

    huge = "x" * (eval_reports.MAX_BYTES)
    assert eval_reports._text(redact, huge, 80) == "x" * 79 + "…"
    assert seen == [80 + eval_reports.REDACT_WINDOW]
    # A secret that starts in the shown part and runs past the cut is still redacted.
    value = "a" * 70 + SECRET + "b" * 100_000
    assert SECRET[:5] not in eval_reports._text(redact, value, 80)
    content = eval_reports._content(redact, "c" * 4090 + SECRET + "d" * 100_000)
    assert len(content) == eval_reports.CONTENT_LIMIT and SECRET[:5] not in content


def test_redaction_window_refuses_text_that_shrank_too_much():
    secret = "s" * 1000

    def redact(text):
        return text.replace(secret, "[redacted]")

    mostly = secret * 20 + "x" * 100_000
    assert eval_reports._text(redact, mostly, 80) == eval_reports.TOO_MUCH_REDACTED
    # The same text inside the window (nothing cut) is shown as redacted.
    assert eval_reports._text(redact, secret * 9, 80).startswith("[redacted][redacted]")


# --- review round 2 (#932) --------------------------------------------------------------------


def test_duplicate_json_keys_are_invalid(project, tmp_path, capsys):
    text = json.dumps(_report()).replace(
        '"agent_responded": true', '"judge_correct": false, "judge_correct": true', 1
    )
    _save(project, text)
    assert _cli(project, tmp_path, "show") == 2
    assert capsys.readouterr().err.startswith("[EVAL_REPORT_INVALID]")


def test_budget_memoizes_skips_short_and_stops():
    calls = []

    def redact(text):
        calls.append(text)
        return text.upper()

    budget = eval_reports.Budget(redact)
    assert budget("abc") == "abc" and calls == []  # shorter than MIN_LITERAL
    assert budget("abcd") == "ABCD" and budget("abcd") == "ABCD" and calls == ["abcd"]
    budget.cache = {str(i): str(i) for i in range(eval_reports.MAX_REDACTIONS)}
    assert budget("efgh") == eval_reports.OVER_BUDGET and budget.exhausted
    assert budget("abcd") == eval_reports.OVER_BUDGET  # the cache was replaced above


def test_budget_note(project, tmp_path, capsys, monkeypatch):
    many = [_case(f"case-{i}", tools=[f"tool-{i}-{j}" for j in range(5)]) for i in range(20)]
    _save(project, _report(results=many) | {"failed": []})
    monkeypatch.setattr(eval_reports, "MAX_REDACTIONS", 10)
    assert _cli(project, tmp_path, "show", "--json") == 0
    data = _json(capsys)
    assert data["notes"] == [
        f"the report has more than 10 distinct strings; the rest show {eval_reports.OVER_BUDGET}"
    ]
    assert data["results"][-1]["id"] == eval_reports.OVER_BUDGET


def test_list_does_not_redact_ids(project, monkeypatch):
    _save(project, _report() | {"failed": [f"id-{i}" for i in range(500)]})
    calls = []

    def redact(text):
        calls.append(text)
        return text

    rows, _ = eval_reports.summaries(project / "deploy", None, redact)
    assert rows[0]["passed"] == 1 and not any(c.startswith("id-") for c in calls)
    _save(project, _report() | {"failed": "c2"})
    rows, _ = eval_reports.summaries(project / "deploy", None, redact)
    assert rows[0]["error"]["code"] == "EVAL_REPORT_INVALID"  # still type-checked


def test_list_byte_budget(project, tmp_path, capsys, monkeypatch):
    first = _save(project, _report())
    _save(project, _report(OLDER), run=OLDER)
    monkeypatch.setattr(eval_reports, "LIST_BYTES", first.stat().st_size + 10)
    assert _cli(project, tmp_path, "list", "--json") == 0
    rows = _json(capsys)["reports"]
    assert "error" not in rows[0] and rows[1]["error"]["code"] == "EVAL_REPORT_TOO_LARGE"
    assert f"use evals show {OLDER}" in rows[1]["error"]["message"]


def test_redaction_window_growth_does_not_offset_shrink():
    big, small = "S" * 9000, "s" * 4  # 9000 removed; the short one grows to the 10-char mark

    def redact(text):
        return text.replace(big, redaction_mark()).replace(small, redaction_mark())

    value = big + small * 2000 + "x" * 100_000  # net shrink below the bound, removed above it
    assert eval_reports._text(redact, value, 80) == eval_reports.TOO_MUCH_REDACTED


def redaction_mark() -> str:
    from agentbreeder import redaction

    return redaction.MARK


# --- review round 3 (#932) --------------------------------------------------------------------


def test_window_grows_for_a_long_secret():
    from agentbreeder import redaction

    secret = "Zq9" * 14000  # 42000 characters on one line: longer than REDACT_WINDOW // 2
    budget = eval_reports.Budget(redaction.Redactor([secret]))
    assert budget.margin >= len(secret)
    value = "a" * 100 + secret + "b" * 200_000
    assert "Zq9" not in eval_reports._text(budget, value, 80)
    assert "Zq9" not in eval_reports._content(budget, value)
    assert eval_reports._content(budget, value).startswith("a" * 100 + redaction.MARK)
    plain = eval_reports.Budget(lambda text: text)
    assert plain.margin == eval_reports.REDACT_WINDOW // 2


def test_over_budget_is_shown_for_long_strings_too():
    budget = eval_reports.Budget(lambda text: text)
    budget.cache = {str(i): str(i) for i in range(eval_reports.MAX_REDACTIONS)}
    assert eval_reports._text(budget, "x" * 200_000, 80) == eval_reports.OVER_BUDGET


def test_list_byte_budget_ignores_oversize_files(project, monkeypatch):
    older = _save(project, _report(OLDER), run=OLDER)
    size = older.stat().st_size
    _save(project, "x" * (size + 100))  # the newest: over MAX_BYTES, refused unread
    monkeypatch.setattr(eval_reports, "MAX_BYTES", size + 10)
    monkeypatch.setattr(eval_reports, "LIST_BYTES", size + 10)
    rows, _ = eval_reports.summaries(project / "deploy", None, str)
    assert (
        rows[0]["error"]["code"] == "EVAL_REPORT_TOO_LARGE"
        and "over" in rows[0]["error"]["message"]
    )
    assert "error" not in rows[1] and rows[1]["passed"] == 1  # the big one was not counted
