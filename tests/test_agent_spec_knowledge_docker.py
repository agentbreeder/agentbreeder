"""Opt-in real Docker run (#989): a project written by `new agent --from-spec` with a knowledge
version comes up, releases through its eval gate, indexes the version at the first search and
answers from it, and `knowledge status` reads the index the runtime built.

    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_TEST_ENV_FILE=/path/to/keys.env \\
        pytest tests/test_agent_spec_knowledge_docker.py

`AGENTBREEDER_TEST_ENV_FILE` is a 0600 file holding a `GEMINI_API_KEY=` line: the run calls Gemini
for chat, the judge and the embeddings. Every CLI command runs as a subprocess with a fresh HOME
(its own Docker context: the CLI refuses DOCKER_HOST) and a minimal environment, so the user's
state, operator config and stacks are never touched. The key is never printed: outputs are
checked for it before the test ends, and so is every file the run left in the sandbox.
Teardown removes the deployments and runs `services down --purge`.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentbreeder import registry

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1" or not os.getenv("AGENTBREEDER_TEST_ENV_FILE"),
    reason="Opt-in real Docker test with a Gemini key file",
)

CODE = "KB-ACME-7Q3"
QUESTION = "What reference code do staff use to look up an Acme engraving job?"
MANIFEST = (
    "kind: knowledge\nname: acme-help\nversion: 1.0.0\ndescription: Acme help articles.\n"
    "chunking: paragraph\nembedding: {alias: help-embedding, dimensions: 768}\nstore: pgvector\n"
)
DOCS = {
    "hours.md": "# Support hours\n\nAcme support is open 9am to 5pm Pacific, Monday to Friday.\n",
    "engraving.md": (
        "# Engraving\n\nAcme engraves pens for 9 dollars.\n\n"
        f"Staff look up engraving jobs with the reference code {CODE} in the ops dashboard.\n"
    ),
}
PROMPT = (
    "You are kbhelper, the Acme help desk. Search the knowledge base before you answer, answer "
    "in one or two sentences from what it says, and say so when it does not say."
)


def _key(path: Path) -> str:
    info = path.stat()
    assert stat.S_IMODE(info.st_mode) == 0o600, "the key file must be 0600"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            secret = line.split("=", 1)[1].strip()
            assert secret, "the GEMINI_API_KEY line is empty"
            return secret
    raise AssertionError("the key file has no GEMINI_API_KEY line")


class Sandbox:
    def __init__(self, root: Path, key_file: Path):
        self.home = root / "home"
        self.home.mkdir(mode=0o700)
        (self.home / "tmp").mkdir(mode=0o700)
        self.registry = root / "shared-registry"
        self.project = self.home / "kbhelper"
        self.keys = self.home / "keys.env"
        secret = _key(key_file)
        self.secret = secret
        fd = os.open(self.keys, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            writer.write(f"GEMINI_API_KEY={secret}\n")
        self.outputs: list[str] = []
        docker = shutil.which("docker")
        assert docker, "docker is not on PATH"
        self.env = {
            "HOME": str(self.home),
            "TMPDIR": str(self.home / "tmp"),
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), str(Path(docker).parent)])
            + ":/usr/bin:/bin",
            "LANG": "C.UTF-8",
            registry.PATH_ENV: str(self.registry),
        }
        host = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for args in (["create", "sandbox", "--docker", f"host={host}"], ["use", "sandbox"]):
            subprocess.run(
                ["docker", "context", *args], env=self.env, capture_output=True, check=True
            )

    def run(self, *args: str, timeout: int = 900, ok: tuple[int, ...] = (0,)):
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "agentbreeder", *args],
            cwd=self.home,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        self.outputs += [result.stdout, result.stderr]
        took = time.monotonic() - started
        print(f"{' '.join(args[:3])}: exit {result.returncode} in {took:.1f}s")  # noqa: T201
        assert result.returncode in ok, (
            f"{args[:3]} exited {result.returncode}: {self.mask(result.stderr)}"
        )
        return result

    def mask(self, text: str) -> str:
        """`text` with the key replaced: nothing the test prints or asserts carries it."""
        return text.replace(self.secret, "[key]")

    def show(self, data: dict) -> None:
        print(self.mask(json.dumps(data))[:2000])  # noqa: T201

    def json(self, *args: str, timeout: int = 900) -> dict:
        return json.loads(self.run(*args, "--json", timeout=timeout).stdout)

    def leaks(self) -> list[str]:
        found = [f"output {i}" for i, text in enumerate(self.outputs) if self.secret in text]
        for path in self.home.rglob("*"):
            if path == self.keys or not path.is_file() or path.is_symlink():
                continue
            try:
                if self.secret.encode() in path.read_bytes():
                    found.append(str(path.relative_to(self.home)))
            except OSError:
                continue
        return found


def _publish(sandbox: Sandbox, tmp_path: Path) -> None:
    source = tmp_path / "knowledge-source"
    (source / "docs").mkdir(parents=True)
    (source / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    for name, text in DOCS.items():
        (source / "docs" / name).write_text(text, encoding="utf-8")
    registry.publish(source, sandbox.registry)


def _spec(sandbox: Sandbox) -> Path:
    spec = {
        "schema_version": 1,
        "name": "kbhelper",
        "model": "gemini",
        "max_model_calls": 4,
        "prompt": {"text": PROMPT},
        "evals": {
            "cases": [
                {
                    "id": "engraving-code",
                    "turns": [QUESTION],
                    "expect": {"must_match": ["7Q3"]},
                    "reference": f"The code is {CODE}, from the engraving article.",
                }
            ],
            "threshold": 1.0,
            "retries": 1,
        },
        "knowledge": [{"ref": "knowledge/acme-help@1.0.0", "top_k": 5, "extra_searches": 1}],
    }
    path = sandbox.home / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def sandbox(tmp_path):
    try:
        yield Sandbox(tmp_path, Path(os.environ["AGENTBREEDER_TEST_ENV_FILE"]))
    finally:
        (tmp_path / "home" / "keys.env").unlink(missing_ok=True)


def test_a_generated_knowledge_agent_releases_and_retrieves(tmp_path, sandbox):
    _publish(sandbox, tmp_path)
    stack = ("--stack", str(sandbox.project), "--environment", "dev")
    agent = str(sandbox.project / "agent.yaml")
    up = False
    try:
        written = sandbox.json("new", "agent", "kbhelper", "--from-spec", str(_spec(sandbox)))
        assert written["knowledge"][0]["alias"] == "help-embedding"
        assert written["combination"]["verified"] is False

        up = True
        sandbox.run(
            "services", "up", str(sandbox.project), "--environment", "dev",
            "--env-file", str(sandbox.keys), "--yes",
        )  # fmt: skip
        status = sandbox.json(
            "knowledge", "status", "knowledge/acme-help@1.0.0",
            "--project", str(sandbox.project), "--environment", "dev",
        )  # fmt: skip
        sandbox.show(status)
        before = json.dumps(status)
        assert "not_indexed" in sandbox.mask(before)

        sandbox.run("build", agent, *stack, timeout=1200)
        sandbox.run("release", agent, *stack, timeout=1800)

        status = sandbox.json(
            "knowledge", "status", "knowledge/acme-help@1.0.0",
            "--project", str(sandbox.project), "--environment", "dev",
        )  # fmt: skip
        sandbox.show(status)
        after = json.dumps(status)
        after = sandbox.mask(after)
        assert '"fresh"' in after and '"table_dimensions": 768' in after

        reply = sandbox.json("invoke", "kbhelper", QUESTION, *stack, timeout=300)
        sandbox.show(reply)
        assert "7Q3" in sandbox.mask(json.dumps(reply))
    finally:
        try:
            if up:
                listed = json.loads(sandbox.run("list", *stack, "--json").stdout)  # a JSON list
                for row in listed:
                    if row.get("state") != "removed" and row.get("id"):
                        sandbox.run("remove", str(row["id"]), "--local", *stack, ok=(0, 1, 2))
                sandbox.run(
                    "services", "down", str(sandbox.project), "--purge", "--yes", ok=(0, 1, 2)
                )
            leaks = sandbox.leaks()
        finally:
            sandbox.keys.unlink(missing_ok=True)
        assert leaks == [], f"the key leaked into {len(leaks)} place(s): {leaks}"
