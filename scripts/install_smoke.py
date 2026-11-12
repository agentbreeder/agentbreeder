"""Smoke-test an installed agentbreeder wheel (spec 5.2, plan S8: install-smoke and release build).

Run it with the installed environment's interpreter, isolated from the checkout:

    <venv>/bin/python -I scripts/install_smoke.py --version 3.0.0a1 [--hardlinked]

It checks, from the installed package only: the version; with --hardlinked, that the built-in
registry files really are hardlinks (st_nlink > 1, uv's UV_LINK_MODE=hardlink), which is the
case #754 found broken; `--version`, `registry verify`, `example list`, `example northwind`,
`validate` on its agent.yaml and stack.yaml, `next --json` on the copy, `new agent --dry-run
--json` (nothing written), a locked build of the copy with a stub image builder
(every registry ref resolves; no image is built), and `quickstart --dry-run`, whose `doctor` needs
a reachable Docker engine (GitHub's ubuntu runners have one).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"install_smoke: {message}")


def cli(*args: str, cwd: Path, env: dict) -> str:
    command = [str(Path(sys.executable).parent / "agentbreeder"), *args]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    print(f"$ agentbreeder {' '.join(args)}\n{result.stdout}{result.stderr}", flush=True)
    if result.returncode != 0:
        fail(f"`agentbreeder {' '.join(args)}` exited {result.returncode}")
    return result.stdout


def check_installed(version: str):
    import agentbreeder
    from agentbreeder import registry

    location = Path(agentbreeder.__file__).resolve()
    if CHECKOUT in location.parents:
        fail(f"imported the checkout ({location}), not the installed package")
    if agentbreeder.__version__ != version:
        fail(f"installed version {agentbreeder.__version__}, expected {version}")
    return registry


def check_hardlinked(builtin_root: Path) -> None:
    files = [path for path in builtin_root.rglob("*") if path.is_file()]
    if not files:
        fail(f"no built-in files under {builtin_root}")
    single = [path for path in files if path.stat().st_nlink < 2]
    if single:
        fail(f"{len(single)} of {len(files)} built-in files are not hardlinked, e.g. {single[0]}")
    print(f"hardlinked: all {len(files)} built-in files have st_nlink > 1")


def operator_config(directory: Path) -> Path:
    """A static-provider operator config with a LiteLLM gateway, enough for a locked build."""
    import yaml

    directory.mkdir(mode=0o700)
    principals = directory / "principals.yaml"
    principals.write_text(
        yaml.safe_dump(
            {
                "principals": {
                    "agent-1": {
                        "kind": "agent",
                        "enabled": True,
                        "environment": "dev",
                        "review_by": "2099-01-01",
                        "sponsors": [{"id": "s", "name": "s@example.com", "active": True}],
                        "owners": [{"id": "o", "name": "o@example.com", "active": True}],
                    }
                }
            }
        )
    )
    identity = {"provider": "static", "ref": "agent-1", "environment": "dev"}
    config = directory / "operator.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "enforcement": "enforce",
                "default_environment": "dev",
                "providers": {"static": {"authority": "smoke", "file": "principals.yaml"}},
                "gateways": {
                    "litellm-local": {
                        "type": "litellm",
                        "url": "http://127.0.0.1:4000",
                        "agent_url": "http://host.docker.internal:4000",
                    }
                },
                "environments": {
                    "dev": {
                        "targets": ["local"],
                        "gateway": "litellm-local",
                        "models": ["support-model", "support-embedding"],
                        "ceilings": {"budget_usd": 50, "rpm": 300},
                        "bindings": {"DATABASE_URL": "secret://northwind-db"},
                        "agents": {
                            "northwind-support": {
                                # copies: PyYAML would write a shared dict as an alias,
                                # which operator configs refuse
                                "identity": dict(identity),
                                "candidate_identity": dict(identity),
                            }
                        },
                    }
                },
            }
        )
    )
    for path in (principals, config):
        path.chmod(0o600)
    return config


def check_next(output: str, project: Path) -> None:
    """`next --json` on the copied example: the contract parses and the project step is done."""
    result = json.loads(output)
    steps = {step["id"]: step for step in result.get("steps", [])}
    # `next` reports the absolute path as given, not symlink-resolved (macOS temp dirs are
    # under /var -> /private/var).
    if result.get("project") != os.path.normpath(project.absolute()) or "complete" not in result:
        fail("`next --json` did not describe the copied example")
    if steps.get("project", {}).get("state") != "done":
        fail(f"`next --json`: the example's project step is {steps.get('project')}")
    print("next --json: the checklist parses; the example's project step is done")


def check_new(output: str, target: Path) -> None:
    """`new agent --dry-run --json`: the starter's files are listed and nothing is written."""
    result = json.loads(output)
    paths = {entry["path"] for entry in result.get("files", [])}
    if result.get("dry_run") is not True or not {"agent.yaml", "stack.yaml"} <= paths:
        fail("`new agent --dry-run --json` did not list the starter's files")
    if target.exists():
        fail("`new agent --dry-run` wrote to its destination")
    print(f"new agent --dry-run --json: {len(paths)} files listed, nothing written")


def locked_build(project: Path, config: Path) -> None:
    from agentbreeder import operator_config as operator_module
    from agentbreeder.build import build

    lock_before = (project / "agent.lock").read_bytes()
    built = []

    def stub_builder(context: Path, iidfile: Path) -> str:
        built.append(context)
        return "sha256:" + "b" * 64

    operator = operator_module.load_operator_config(config)
    build(project / "agent.yaml", operator, mode="locked", builder=stub_builder)
    if not built:
        fail("the locked build never reached the image builder")
    if (project / "agent.lock").read_bytes() != lock_before:
        fail("the locked build changed agent.lock")
    print("locked build: every ref resolved against the installed registry; agent.lock unchanged")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="the expected agentbreeder version")
    parser.add_argument("--hardlinked", action="store_true")
    args = parser.parse_args(argv)

    registry = check_installed(args.version)
    if args.hardlinked:
        check_hardlinked(registry.BUILTIN_ROOT)
    with tempfile.TemporaryDirectory(prefix="agentbreeder-smoke-") as scratch:
        work = Path(scratch)
        # A fresh HOME keeps the runner's ~/.agentbreeder out; Docker keeps its real context.
        docker_config = os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))
        env = {**os.environ, "HOME": str(work / "home"), "DOCKER_CONFIG": docker_config}
        env.pop("AGENTBREEDER_OPERATOR_CONFIG", None)
        (work / "home").mkdir()
        if cli("--version", cwd=work, env=env).strip() != args.version:
            fail("`agentbreeder --version` does not print the installed version")
        cli("registry", "verify", cwd=work, env=env)
        cli("example", "list", cwd=work, env=env)
        cli("example", "northwind", str(work / "nw"), cwd=work, env=env)
        cli("validate", str(work / "nw" / "agent.yaml"), cwd=work, env=env)
        cli("validate", str(work / "nw" / "stack.yaml"), cwd=work, env=env)
        check_next(cli("next", str(work / "nw"), "--json", cwd=work, env=env), work / "nw")
        check_new(
            cli(
                "new",
                "agent",
                "hello",
                "--model",
                "gemini",
                "--dir",
                str(work / "hello"),
                "--dry-run",
                "--json",
                cwd=work,
                env=env,
            ),  # fmt: skip
            work / "hello",
        )
        # In-process like the CLI calls' environment: the private HOME, not the runner's.
        real_home = os.environ.get("HOME")
        os.environ["HOME"] = env["HOME"]
        try:
            locked_build(work / "nw", operator_config(work / "operator"))
        finally:
            if real_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = real_home
        cli("quickstart", "--dry-run", cwd=work, env=env)
    print("install_smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
