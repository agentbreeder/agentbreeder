"""The built-in MCP servers (#748; spec §7.5): `mcp/github@1.0.0` and `mcp/playwright@1.0.0` with
its upstream `service/playwright-mcp@1.0.0`.

Static checks on every built-in, and `services up` with the built-in entries (Docker faked as in
tests/test_mcp_stack.py). The live runs are tests/test_mcp_builtins_live.py.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml
from test_mcp_stack import AGENT, RelayDocker, run_up, stack_dir, tool
from test_services_engine import STACK, write_project

from agentbreeder import registry
from agentbreeder.mcp import load_mcp
from agentbreeder.operator_config import CONFIG_ENV
from agentbreeder.registry import Ref
from agentbreeder.services.engine import Engine
from agentbreeder.services.schema import load_service
from agentbreeder.services.state import ROOT_ENV

BUILTIN_MCP = sorted((registry.BUILTIN_ROOT / "mcp").glob("*/*"))
PLAYWRIGHT_TOOLS = [
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_close",
]
# Tools a built-in must never declare: arbitrary code, files and raw network access.
FORBIDDEN = {
    "browser_evaluate",
    "browser_run_code_unsafe",
    "browser_file_upload",
    "browser_network_requests",
    "browser_network_request",
}


def manifest(directory: Path) -> dict:
    return yaml.safe_load((directory / "manifest.yaml").read_text())


def test_the_shipped_built_ins():
    assert {f"{d.parent.name}@{d.name}" for d in BUILTIN_MCP} == {
        "github@1.0.0",
        "playwright@1.0.0",
    }


@pytest.mark.parametrize("directory", BUILTIN_MCP, ids=lambda d: d.parent.name)
def test_each_built_in_is_a_valid_mcp_version(directory):
    data = manifest(directory)
    assert data["kind"] == "mcp"
    assert (data["name"], data["version"]) == (directory.parent.name, directory.name)
    assert data["description"].strip()
    spec = load_mcp(directory)
    assert spec.tools and not FORBIDDEN & set(spec.tools)
    for name in spec.secrets.values():
        # Never the GitHub CLI's own variables (the import denylist).
        assert name.import_ not in {"GITHUB_TOKEN", "GH_TOKEN"}
    if spec.upstream.remote is not None:
        assert spec.upstream.remote.url.startswith("https://")
    else:
        ref = Ref.parse(spec.upstream.service.ref)
        service = registry.BUILTIN_ROOT / "service" / ref.name / ref.version
        port = load_service(service / "service.yaml").ports[spec.upstream.service.port]
        assert (port.protocol, port.scopes) == ("http", {"internal"})


def test_github_entry():
    spec = load_mcp(registry.BUILTIN_ROOT / "mcp/github/1.0.0")
    assert spec.upstream.remote.url == "https://api.githubcopilot.com/mcp/"
    assert spec.auth.type == "bearer"
    assert spec.secrets[spec.auth.secret].import_ == "GITHUB_MCP_TOKEN"
    assert spec.tools


def test_playwright_entry_and_its_service():
    spec = load_mcp(registry.BUILTIN_ROOT / "mcp/playwright/1.0.0")
    assert spec.auth.type == "none" and not spec.secrets
    assert spec.upstream.service.ref == "service/playwright-mcp@1.0.0"
    assert list(spec.tools) == PLAYWRIGHT_TOOLS
    assert spec.tools["browser_snapshot"].effect == "read"
    service = load_service(registry.BUILTIN_ROOT / "service/playwright-mcp/1.0.0/service.yaml")
    assert re.fullmatch(r"mcr\.microsoft\.com/playwright/mcp@sha256:[0-9a-f]{64}", service.image)
    assert service.egress is False  # no egress until the filtering forward proxy (#747)
    assert not service.secrets and not service.volumes and not service.inputs
    argv = service.command
    # In-memory profile, headless, no image responses, no page-registered (WebMCP) tools.
    for flag in ("--headless", "--isolated", "--no-webmcp"):
        assert flag in argv
    assert argv[argv.index("--image-responses") + 1] == "omit"
    assert argv[argv.index("--port") + 1] == str(service.ports["http"].port)
    assert "--allow-unrestricted-file-access" not in argv
    assert not any(a.startswith("--caps") for a in argv)


@pytest.fixture
def builtin_project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", "echo-canary-0000")
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "ghp_builtin_canary_0b1c")
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    data = copy.deepcopy(STACK)
    data["services"]["browser"] = {"ref": "service/playwright-mcp@1.0.0"}
    data["mcp"] = {
        "github": {"ref": "mcp/github@1.0.0"},
        "playwright": {"ref": "mcp/playwright@1.0.0"},
    }
    github_tools = sorted(load_mcp(registry.BUILTIN_ROOT / "mcp/github/1.0.0").tools)
    data["environment"]["agents"][AGENT]["mcp"] = {
        "github": github_tools[:1],
        "playwright": ["browser_navigate", "browser_snapshot"],
    }
    return write_project(tmp_path / "project", stack=data), github_tools


def test_up_with_the_built_ins(builtin_project):
    project, github_tools = builtin_project
    docker = RelayDocker()
    docker.upstream = {
        "github": [tool(name) for name in github_tools],
        "playwright": [tool(name) for name in PLAYWRIGHT_TOOLS],
    }
    # Built-in origins: no --allow-project-mcp needed, even with a credential.
    result, _ = run_up(project, docker, allow=())
    assert {server: entry["state"] for server, entry in result["mcp"].items()} == {
        "github": "ok",
        "playwright": "ok",
    }
    compose = yaml.safe_load((stack_dir(result) / "compose.yaml").read_text())
    services = compose["services"]
    assert services["browser"]["networks"] == ["mcp-playwright"]
    assert services["mcp-playwright"]["networks"] == ["agents", "mcp-playwright"]
    assert "edge" in services["mcp-github"]["networks"]  # remote upstream: egress
    assert services["browser"]["read_only"] is True
    assert services["browser"]["cap_drop"] == ["ALL"]
    assert "ports" not in services["browser"]
    lock = yaml.safe_load((project / "stack.lock").read_text())
    assert lock["mcp"]["github"]["origin"] == "built-in"
    assert lock["mcp"]["github"]["upstream"] == "https://api.githubcopilot.com:443"
    upstream = lock["mcp"]["playwright"]["upstream"]
    assert upstream == "service service/playwright-mcp@1.0.0 port http"
    config = json.loads(
        Path(
            RelayDocker._config(Engine(project, docker=docker).state, "mcp-playwright")
        ).read_text()
    )
    assert config["upstream"] == {"kind": "service", "url": "http://browser:8931/mcp"}
    assert config["auth"] == {"type": "none"}
    assert config["tools"]["browser_snapshot"] == "read"
