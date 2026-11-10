import importlib.util
import json
import subprocess
from pathlib import Path

from agentbreeder.runtime import Docker
from agentbreeder.store import Store


def load_demo(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "src/agentbreeder/examples/http-agent/quickstart.py"
    spec = importlib.util.spec_from_file_location("quickstart", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    directory = tmp_path / "demo"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kw: str(directory))
    monkeypatch.setattr(Docker, "authorize", lambda self: setattr(self, "context", "test-context"))
    return module, directory


def test_build_timeout_cleans_unique_tag(tmp_path, monkeypatch):
    demo, directory = load_demo(tmp_path, monkeypatch)
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if "build" in args:
            raise subprocess.TimeoutExpired("docker", 180)
        if "ls" in args:
            return "created-image-id"
        return ""

    monkeypatch.setattr(demo, "run", run)
    assert demo.main() == 1
    tag = calls[0][calls[0].index("--tag") + 1]
    assert calls[-1] == ("docker", "--context", "test-context", "image", "rm", tag)


def test_deploy_timeout_recovers_durable_id_for_cleanup(tmp_path, monkeypatch):
    demo, directory = load_demo(tmp_path, monkeypatch)
    removed = []

    def run(*args, **kwargs):
        if "build" in args:
            (directory / "image-id").write_text("sha256:" + "a" * 64)
        if "remove" in args:
            removed.append(args[args.index("remove") + 1])
            assert kwargs["env"]["DOCKER_CONTEXT"] == "test-context"
            return json.dumps({"state": "removed"})
        return ""

    def deploy(*args, **kwargs):
        manifest = (directory / "agent.yaml").read_text()
        name = next(
            line.split(": ")[1] for line in manifest.splitlines() if line.startswith("name:")
        )
        state = Store(directory / "state")
        try:
            state.create("owned-id", {"name": name}, "digest", "test-context")
        finally:
            state.close()
        raise subprocess.TimeoutExpired("agentbreeder", 180)

    monkeypatch.setattr(demo, "run", run)
    monkeypatch.setattr(demo.subprocess, "run", deploy)
    assert demo.main() == 1
    assert removed == ["owned-id"]
