"""Build and verify the sample through the installed AgentBreeder CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

DEMO_PRINCIPALS = """# Demo only: real operators verify agents in their identity provider.
principals:
  demo-agent:
    kind: agent
    enabled: true
    environment: dev
    review_by: "2099-01-01"
    sponsors: [{id: demo-sponsor, name: demo@example.com, active: true}]
"""
DEMO_OPERATOR = """# Demo operator config: enforcement on; a static adapter stands in for an IdP.
enforcement: enforce
default_environment: dev
environments:
  dev: {targets: [local]}
providers:
  demo: {type: static, authority: demo, file: principals.yaml}
"""


def write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)


def run(*arguments: str, timeout: int = 60, env=None) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        # External build/CLI output can include local details; don't copy it into error reports.
        raise RuntimeError(f"{arguments[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def main() -> int:
    directory = Path(tempfile.mkdtemp(prefix="agentbreeder-demo-"))
    tag = "agentbreeder-demo-" + uuid.uuid4().hex
    identifier = None
    build_attempted = False
    environment = os.environ.copy()
    failed = False
    context = None

    def cli(*arguments: str):
        return json.loads(
            run(
                sys.executable,
                "-m",
                "agentbreeder",
                *arguments,
                "--state-dir",
                str(directory / "state"),
                "--json",
                timeout=180,
                env=environment,
            )
        )

    print(f"Demo history and recovery files: {directory}", flush=True)
    write_private(directory / "principals.yaml", DEMO_PRINCIPALS)
    write_private(directory / "operator.yaml", DEMO_OPERATOR)
    environment["AGENTBREEDER_OPERATOR_CONFIG"] = str(directory / "operator.yaml")
    try:
        from agentbreeder.runtime import Docker

        runtime = Docker()
        runtime.authorize()
        context = runtime.context
        environment["DOCKER_CONTEXT"] = context
        build_attempted = True
        print("1/5 Docker ready. Building the sample…", flush=True)
        run(
            "docker",
            "--context",
            context,
            "build",
            "--tag",
            tag,
            "--iidfile",
            str(directory / "image-id"),
            str(Path(__file__).parent),
            timeout=180,
        )
        image = (directory / "image-id").read_text().strip()
        manifest = directory / "agent.yaml"
        manifest.write_text(
            f"schema_version: 2\nname: {tag}\nimage: {image}\ntarget: local\n"
            "identity: {provider: demo, ref: demo-agent, environment: dev}\n"
        )
        print("2/5 Checking the demo identity gate, deploying and checking health…", flush=True)
        # Retain the ID even if deploy records a runtime failure and exits nonzero.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentbreeder",
                "deploy",
                str(manifest),
                "--local",
                "--environment",
                "dev",
                "--state-dir",
                str(directory / "state"),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            env=environment,
        )
        if result.stdout:
            deployment = json.loads(result.stdout)
            identifier = deployment["id"]
        if result.returncode:
            raise RuntimeError("Deployment failed; inspect the retained state and retry")
        endpoint = deployment["endpoint"]
        request = Request(endpoint + "/invoke", data=b'{"message":"hello"}')
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            if json.load(response).get("input") != {"message": "hello"}:
                raise RuntimeError("Unexpected sample response")
        print(f"3/5 Invocation succeeded: {endpoint}", flush=True)
        repeated = cli("deploy", str(manifest), "--local", "--environment", "dev")
        if repeated["id"] != identifier or repeated["state"] != "ready":
            raise RuntimeError("Retry did not reuse the healthy deployment")
        history = cli("status", identifier)
        if len(history["events"]) < 4:
            raise RuntimeError("Deployment history was not persisted")
        print(f"4/5 Retry reused {identifier}; history persisted.", flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, ImportError) as error:
        print(f"Demo stopped: {error}", file=sys.stderr)
        print(
            "Check your installation and start Docker; use agentbreeder doctor.", file=sys.stderr
        )
        failed = True
    finally:
        if identifier is None and (directory / "state/deployments.sqlite3").exists():
            try:
                from agentbreeder.store import Store

                state = Store(directory / "state")
                try:
                    record = state.active(tag)
                    identifier = record["id"] if record else None
                finally:
                    state.close()
            except Exception:
                failed = True
                print(
                    f"State recovery required at {directory / 'state'}. "
                    f"Retry: AGENTBREEDER_OPERATOR_CONFIG={directory / 'operator.yaml'} "
                    f"agentbreeder deploy {directory / 'agent.yaml'} --local --environment dev "
                    f"--state-dir {directory / 'state'}, then remove the returned ID.",
                    file=sys.stderr,
                )
        if identifier:
            try:
                if cli("remove", identifier, "--local")["state"] != "removed":
                    raise RuntimeError("Removal not verified")
                print("5/5 Sample container removed. History retained.", flush=True)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                print(
                    f"Cleanup incomplete. Run: "
                    f"AGENTBREEDER_OPERATOR_CONFIG={directory / 'operator.yaml'} "
                    f"agentbreeder remove {identifier} --local "
                    f"--state-dir {directory / 'state'}",
                    file=sys.stderr,
                )
                failed = True
        if build_attempted and context:
            try:
                if run(
                    "docker",
                    "--context",
                    context,
                    "image",
                    "ls",
                    "--quiet",
                    "--filter",
                    "reference=" + tag,
                ):
                    run("docker", "--context", context, "image", "rm", tag)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                print(
                    f"Check sample tag cleanup: docker --context {context} image rm {tag}",
                    file=sys.stderr,
                )
                failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
