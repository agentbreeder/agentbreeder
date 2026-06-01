# Cloud-Agnostic Epic — P4 (MCP Deployment) + P5 (Studio Self-Hosting) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `mcp_servers:` in `agent.yaml` actually reach the deployed agent (resolve refs → forward via the Go sidecar, optionally co-deploying MCP server containers), and ship a Helm chart + env-driven nginx so the whole AgentBreeder platform (Studio + API + DB) self-hosts on Kubernetes with one command.

**Architecture:**
- **P4:** A pure resolver turns each `McpServerRef` into a `ResolvedMcpServer` (remote URL, or a co-deployed container image on a localhost port). The 4 multi-container deployers (ECS, Cloud Run, Azure Container Apps, docker-compose) co-deploy image-backed MCP servers as non-essential containers and hand the AgentBreeder Go sidecar a `{name:{transport,url}}` JSON map via `AGENTBREEDER_SIDECAR_MCP_SERVERS`. The Go sidecar's `overlayEnv()` parses that env into its existing `MCPServers` map, so `POST localhost:9091/mcp/<server>` forwards correctly.
- **P5:** The dashboard nginx config becomes a runtime `envsubst` template (`API_UPSTREAM`, `CORS_*`, `TLS_*`) rendered by an entrypoint. A new Helm chart at `deploy/helm/agentbreeder/` reproduces the compose topology (api, dashboard, migrate Job) with Bitnami Postgres + Redis as **toggleable** subchart dependencies, a TLS-capable Ingress, and Secrets/ConfigMaps. A `self-hosting.mdx` doc covers install + config.

**Tech Stack:** Python 3.11+ (pydantic, pytest), Go 1.22 (sidecar), Helm 3 / Kubernetes, nginx (envsubst), Fumadocs MDX.

**One combined PR** (branch `feat/cloud-agnostic-p4p5`). Commit per task. Defer PR until both parts green locally. Get explicit OK before any `--admin` merge.

---

## File Structure

**P4 — created/modified:**
- Modify `engine/config_parser.py` — add optional `url`/`image`/`port` to `McpServerRef`.
- Modify `engine/schema/agent.schema.json` — mirror new `mcp_servers[]` item fields.
- Rewrite `engine/deployers/mcp_sidecar.py` — `ResolvedMcpServer`, `resolve_mcp_servers()`, `build_sidecar_env_map()`, and 4 container-injection helpers; keep `generate_sidecars`/`inject_into_compose` shims for the existing tests.
- Modify `engine/sidecar/config.py` — `SidecarConfig.mcp_servers` + populate in `from_agent_config`.
- Modify `engine/sidecar/injector.py` — emit `AGENTBREEDER_SIDECAR_MCP_SERVERS` in the 3 injectors.
- Modify `sidecar/internal/config/config.go` — parse MCP-servers JSON env in `overlayEnv()`.
- Modify `engine/deployers/{aws_ecs,gcp_cloudrun,azure_container_apps,docker_compose}.py` — call resolve + co-deploy injection.
- Tests: `tests/unit/test_mcp_resolve.py`, extend `tests/unit/test_mcp_packager.py`, `tests/unit/test_sidecar_injector.py`; `sidecar/internal/config/config_test.go`.
- Docs: `website/content/docs/mcp-servers.mdx`, `agent-yaml.mdx`, `sidecar.mdx`, `CHANGELOG.md`.

**P5 — created/modified:**
- Create `dashboard/nginx.conf.template`, `dashboard/docker-entrypoint.sh`; modify `dashboard/Dockerfile`; delete/retire `dashboard/nginx.conf` (replaced by template).
- Create `deploy/helm/agentbreeder/{Chart.yaml,values.yaml,.helmignore,README.md}` and `deploy/helm/agentbreeder/templates/{_helpers.tpl,configmap.yaml,secret.yaml,api-deployment.yaml,api-service.yaml,dashboard-deployment.yaml,dashboard-service.yaml,migrate-job.yaml,ingress.yaml,NOTES.txt}`.
- Tests: `tests/unit/test_nginx_template.py` (render check), `tests/unit/test_helm_chart.py` (lint/template, skipped if `helm` absent).
- Docs: `website/content/docs/self-hosting.mdx`, `website/content/docs/meta.json`, `README.md`, `CHANGELOG.md`.

---

## Part A — P4: MCP Server Deployment

### Task A1: Extend `McpServerRef` schema with inline connection fields

**Files:**
- Modify: `engine/config_parser.py` (`McpServerRef`, ~line 144-148)
- Modify: `engine/schema/agent.schema.json` (`mcp_servers` items, ~line 537-565)
- Test: `tests/unit/test_mcp_resolve.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_resolve.py
"""Tests for MCP server ref parsing + resolution (P4)."""

from __future__ import annotations


class TestMcpServerRefFields:
    def test_ref_only_defaults(self):
        from engine.config_parser import McpServerRef

        ref = McpServerRef(ref="mcp/zendesk")
        assert ref.ref == "mcp/zendesk"
        assert ref.transport == "stdio"
        assert ref.url is None
        assert ref.image is None
        assert ref.port is None

    def test_inline_remote(self):
        from engine.config_parser import McpServerRef

        ref = McpServerRef(ref="mcp/zendesk", transport="sse", url="https://mcp.acme.com/sse")
        assert ref.url == "https://mcp.acme.com/sse"

    def test_inline_image(self):
        from engine.config_parser import McpServerRef

        ref = McpServerRef(ref="mcp/slack", transport="sse", image="acme/mcp-slack:1.2.3", port=3100)
        assert ref.image == "acme/mcp-slack:1.2.3"
        assert ref.port == 3100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_mcp_resolve.py -v`
Expected: FAIL — `McpServerRef` has no field `url`.

- [ ] **Step 3: Implement**

In `engine/config_parser.py`, replace the `McpServerRef` class:

```python
class McpServerRef(BaseModel):
    """Reference to an MCP server to attach to an agent.

    ``ref`` is the registry reference (e.g. ``mcp/zendesk``). For self-contained
    deploys the connection can be given inline so no running registry is needed:
    set ``url`` for a remote HTTP/SSE MCP server, or ``image`` to co-deploy a
    container MCP server as a sidecar (reachable on ``port`` over localhost).
    """

    ref: str
    transport: str = "stdio"
    url: str | None = None
    image: str | None = None
    port: int | None = None
```

In `engine/schema/agent.schema.json`, extend the `mcp_servers` items `properties` (keep `ref` required, add):

```json
"url": {
  "type": "string",
  "description": "Remote HTTP/SSE MCP server URL. When set, the sidecar forwards to it directly."
},
"image": {
  "type": "string",
  "description": "Container image to co-deploy as an MCP sidecar (reachable over localhost)."
},
"port": {
  "type": "integer",
  "minimum": 1,
  "maximum": 65535,
  "description": "Localhost port for a co-deployed MCP sidecar container. Auto-assigned from 3100 if omitted."
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_mcp_resolve.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/config_parser.py engine/schema/agent.schema.json tests/unit/test_mcp_resolve.py
git commit -m "feat(mcp): inline url/image/port on McpServerRef for self-contained deploys"
```

---

### Task A2: `resolve_mcp_servers()` + `ResolvedMcpServer` + env map

**Files:**
- Modify (rewrite, keep back-compat shims): `engine/deployers/mcp_sidecar.py`
- Test: `tests/unit/test_mcp_resolve.py` (append)

Resolution rules (per `McpServerRef`, index `i`, `base_port=3100`):
1. `name = ref.split("/")[-1]`, `transport = ref.transport`.
2. If `ref.url` → **remote**: `url=ref.url`, `co_deploy=False`, `image=None`.
3. elif `ref.image` → **co-deploy**: `image=ref.image`, `port=ref.port or base_port+i`, `url=f"http://localhost:{port}"`, `co_deploy=True`.
4. else, if `registry_lookup(name)` returns info with `endpoint` → remote (`url=endpoint`); with `image_uri` → co-deploy (`image=image_uri`, port auto).
5. else → **convention co-deploy**: `image=build_image_tag(name, "latest", registry_prefix)`, port auto, `url=localhost:port`, `co_deploy=True`.

The Go sidecar only forwards `http`/`sse` (`stdio` returns an error). So `build_sidecar_env_map()` normalises a co-deployed `stdio` transport to `http` in the emitted map (the co-deployed container speaks HTTP on its port via `MCP_TRANSPORT`), and **skips** remote `stdio` servers (can't forward) with a logged warning.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_mcp_resolve.py

class TestResolveMcpServers:
    def test_remote_url(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import resolve_mcp_servers

        out = resolve_mcp_servers([McpServerRef(ref="mcp/z", transport="sse", url="https://x/sse")])
        assert len(out) == 1
        r = out[0]
        assert r.name == "z" and r.co_deploy is False
        assert r.url == "https://x/sse" and r.image is None

    def test_inline_image_autoport(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import resolve_mcp_servers

        out = resolve_mcp_servers(
            [McpServerRef(ref="mcp/a", transport="sse", image="img:1")],
            base_port=3100,
        )
        r = out[0]
        assert r.co_deploy is True and r.image == "img:1"
        assert r.port == 3100 and r.url == "http://localhost:3100"

    def test_convention_fallback(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import resolve_mcp_servers

        out = resolve_mcp_servers([McpServerRef(ref="mcp/slack", transport="sse")])
        r = out[0]
        assert r.co_deploy is True
        assert r.image == "agentbreeder/mcp-slack:latest"
        assert r.url == "http://localhost:3100"

    def test_registry_lookup_endpoint(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import McpServerInfo, resolve_mcp_servers

        def lookup(name):
            return McpServerInfo(name=name, transport="sse", endpoint="https://reg/sse")

        out = resolve_mcp_servers([McpServerRef(ref="mcp/z")], registry_lookup=lookup)
        assert out[0].url == "https://reg/sse" and out[0].co_deploy is False

    def test_env_map_skips_remote_stdio(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import build_sidecar_env_map, resolve_mcp_servers

        out = resolve_mcp_servers([McpServerRef(ref="mcp/z", transport="stdio", url="http://x")])
        env = build_sidecar_env_map(out)
        assert env == {}  # remote stdio can't be forwarded

    def test_env_map_normalises_codeploy_stdio_to_http(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import build_sidecar_env_map, resolve_mcp_servers

        out = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="stdio", image="img:1")])
        env = build_sidecar_env_map(out)
        assert env == {"a": {"transport": "http", "url": "http://localhost:3100"}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_mcp_resolve.py::TestResolveMcpServers -v`
Expected: FAIL — `resolve_mcp_servers` not defined.

- [ ] **Step 3: Implement** — replace `engine/deployers/mcp_sidecar.py` with:

```python
"""MCP sidecar deployer — resolves MCP server refs and co-deploys them.

When an agent references MCP servers in its config this module:
  * resolves each ``McpServerRef`` into a :class:`ResolvedMcpServer`
    (a remote HTTP/SSE URL, or a co-deployed container image on a localhost
    port), and
  * injects co-deployed MCP server containers into each cloud deployer's
    container set, alongside the AgentBreeder sidecar.

The AgentBreeder Go sidecar forwards ``POST localhost:9091/mcp/<server>`` to the
resolved URL; :func:`build_sidecar_env_map` produces the ``{name:{transport,url}}``
map handed to it via ``AGENTBREEDER_SIDECAR_MCP_SERVERS``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from engine.config_parser import McpServerRef
from engine.mcp.packager import build_image_tag, generate_sidecar_config

logger = logging.getLogger(__name__)

# Co-deployed MCP sidecar containers get localhost ports starting here. 3100 sits
# above the agent (8080/8081) and AgentBreeder sidecar localhost ports (9090-9092).
DEFAULT_BASE_PORT = 3100


@dataclass
class McpServerInfo:
    """Registry-sourced facts about an MCP server (best-effort enrichment)."""

    name: str
    transport: str = "stdio"
    endpoint: str | None = None
    image_uri: str | None = None


@dataclass
class ResolvedMcpServer:
    """A fully-resolved MCP server ready to wire into a deploy."""

    name: str
    transport: str
    url: str
    co_deploy: bool
    image: str | None = None
    port: int | None = None
    ref: str = ""


def resolve_mcp_servers(
    mcp_servers: list[McpServerRef],
    *,
    registry_lookup: Callable[[str], McpServerInfo | None] | None = None,
    base_port: int = DEFAULT_BASE_PORT,
    registry_prefix: str = "agentbreeder",
) -> list[ResolvedMcpServer]:
    """Resolve agent.yaml MCP refs into concrete, deployable servers."""
    resolved: list[ResolvedMcpServer] = []
    for i, mcp in enumerate(mcp_servers):
        name = mcp.ref.split("/")[-1]
        transport = mcp.transport
        port = mcp.port or (base_port + i)

        if mcp.url:
            resolved.append(
                ResolvedMcpServer(name, transport, mcp.url, False, ref=mcp.ref)
            )
            continue
        if mcp.image:
            resolved.append(
                ResolvedMcpServer(
                    name, transport, f"http://localhost:{port}", True,
                    image=mcp.image, port=port, ref=mcp.ref,
                )
            )
            continue

        info = registry_lookup(name) if registry_lookup else None
        if info and info.endpoint:
            resolved.append(
                ResolvedMcpServer(name, info.transport or transport, info.endpoint, False, ref=mcp.ref)
            )
            continue
        if info and info.image_uri:
            resolved.append(
                ResolvedMcpServer(
                    name, info.transport or transport, f"http://localhost:{port}", True,
                    image=info.image_uri, port=port, ref=mcp.ref,
                )
            )
            continue

        # Convention fallback: co-deploy a conventionally-named image.
        image = build_image_tag(name, "latest", registry_prefix)
        resolved.append(
            ResolvedMcpServer(
                name, transport, f"http://localhost:{port}", True,
                image=image, port=port, ref=mcp.ref,
            )
        )
        logger.info("Resolved MCP '%s' by convention → %s", name, image)

    return resolved


def build_sidecar_env_map(resolved: list[ResolvedMcpServer]) -> dict[str, dict[str, str]]:
    """Build the ``{name:{transport,url}}`` map for the Go sidecar.

    The Go sidecar can only forward HTTP/SSE. Co-deployed servers always speak
    HTTP on their localhost port (we set ``MCP_TRANSPORT`` on the container), so a
    co-deployed ``stdio`` transport is normalised to ``http``. A *remote* ``stdio``
    server cannot be forwarded and is skipped with a warning.
    """
    out: dict[str, dict[str, str]] = {}
    for r in resolved:
        transport = r.transport
        if transport == "stdio":
            if r.co_deploy:
                transport = "http"
            else:
                logger.warning(
                    "MCP '%s' uses remote stdio transport — the sidecar cannot "
                    "forward it; skipping. Use http/sse or co-deploy an image.",
                    r.name,
                )
                continue
        out[r.name] = {"transport": transport, "url": r.url}
    return out


# --------------------------------------------------------------------------- #
# Container co-deploy injection — one helper per multi-container deployer.
# All are idempotent (skip a server whose container already exists) and never
# mutate their input.
# --------------------------------------------------------------------------- #


def _codeploy(resolved: list[ResolvedMcpServer]) -> list[ResolvedMcpServer]:
    return [r for r in resolved if r.co_deploy and r.image]


def inject_mcp_containers_ecs(
    task_definition: dict[str, Any], resolved: list[ResolvedMcpServer]
) -> dict[str, Any]:
    """Append co-deployed MCP server containers to an ECS task definition."""
    import copy

    result = copy.deepcopy(task_definition)
    containers: list[dict[str, Any]] = result.setdefault("containerDefinitions", [])
    existing = {c.get("name") for c in containers}
    for r in _codeploy(resolved):
        cname = f"mcp-{r.name}"
        if cname in existing:
            continue
        containers.append(
            {
                "name": cname,
                "image": r.image,
                "essential": False,
                "portMappings": [{"containerPort": r.port, "protocol": "tcp"}],
                "environment": [
                    {"name": "MCP_TRANSPORT", "value": "http"},
                    {"name": "PORT", "value": str(r.port)},
                ],
            }
        )
    return result


def inject_mcp_containers_cloudrun(
    service_spec: dict[str, Any], resolved: list[ResolvedMcpServer]
) -> dict[str, Any]:
    """Append co-deployed MCP server containers to a Cloud Run v2 service spec."""
    import copy

    result = copy.deepcopy(service_spec)
    spec = result.setdefault("spec", {})
    template = spec.setdefault("template", {})
    tmpl_spec = template.setdefault("spec", {})
    containers: list[dict[str, Any]] = tmpl_spec.setdefault("containers", [])
    existing = {c.get("name") for c in containers}
    for r in _codeploy(resolved):
        cname = f"mcp-{r.name}"
        if cname in existing:
            continue
        containers.append(
            {
                "name": cname,
                "image": r.image,
                "env": [
                    {"name": "MCP_TRANSPORT", "value": "http"},
                    {"name": "PORT", "value": str(r.port)},
                ],
                "ports": [{"containerPort": r.port}],
            }
        )
    return result


def inject_mcp_containers_azure(
    containers: list[dict[str, Any]], resolved: list[ResolvedMcpServer]
) -> list[dict[str, Any]]:
    """Append co-deployed MCP containers to an Azure Container Apps container list."""
    import copy

    result = copy.deepcopy(containers)
    existing = {c.get("name") for c in result}
    for r in _codeploy(resolved):
        cname = f"mcp-{r.name}"
        if cname in existing:
            continue
        result.append(
            {
                "name": cname,
                "image": r.image,
                "env": [
                    {"name": "MCP_TRANSPORT", "value": "http"},
                    {"name": "PORT", "value": str(r.port)},
                ],
            }
        )
    return result


def inject_mcp_containers_compose(
    services: dict[str, Any], resolved: list[ResolvedMcpServer]
) -> dict[str, Any]:
    """Append co-deployed MCP server services to a docker-compose services dict."""
    import copy

    result = copy.deepcopy(services)
    for r in _codeploy(resolved):
        sname = f"mcp-{r.name}"
        if sname in result:
            continue
        result[sname] = {
            "image": r.image,
            "environment": {"MCP_TRANSPORT": "http", "PORT": str(r.port)},
            "restart": "unless-stopped",
        }
    return result


# --------------------------------------------------------------------------- #
# Back-compat shims — keep the original McpSidecarDeployer surface so existing
# tests (tests/unit/test_mcp_packager.py) and any callers keep working.
# --------------------------------------------------------------------------- #


class McpSidecarDeployer:
    """Legacy helper retained for compose generation + tests."""

    def generate_sidecars(
        self,
        mcp_servers: list[McpServerRef],
        agent_name: str,
        registry_prefix: str = "agentbreeder",
    ) -> list[dict[str, Any]]:
        sidecars: list[dict[str, Any]] = []
        for i, mcp in enumerate(mcp_servers):
            server_name = mcp.ref.split("/")[-1]
            image_uri = build_image_tag(server_name, "latest", registry_prefix)
            sidecar = generate_sidecar_config(
                name=server_name, image_uri=image_uri, transport=mcp.transport, port=3000 + i
            )
            sidecar["labels"] = {
                "agentbreeder.agent": agent_name,
                "agentbreeder.mcp-ref": mcp.ref,
            }
            sidecars.append(sidecar)
        return sidecars

    def inject_into_compose(
        self, compose_config: dict[str, Any], sidecars: list[dict[str, Any]]
    ) -> dict[str, Any]:
        services = compose_config.setdefault("services", {})
        for sidecar in sidecars:
            services[sidecar["name"]] = {
                "image": sidecar["image"],
                "environment": sidecar.get("environment", {}),
                "ports": [f"{sidecar['port']}:{sidecar['port']}"],
                "labels": sidecar.get("labels", {}),
                "restart": "unless-stopped",
            }
        return compose_config
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_mcp_resolve.py tests/unit/test_mcp_packager.py -v`
Expected: PASS (new resolve tests + the 14 existing packager tests still green).

- [ ] **Step 5: Commit**

```bash
git add engine/deployers/mcp_sidecar.py tests/unit/test_mcp_resolve.py
git commit -m "feat(mcp): resolve_mcp_servers + co-deploy container injectors"
```

---

### Task A3: Go sidecar parses MCP servers from env

**Files:**
- Modify: `sidecar/internal/config/config.go` (`overlayEnv`, ~line 162-189)
- Test: `sidecar/internal/config/config_test.go` (append)

- [ ] **Step 1: Write the failing Go test**

```go
// append to sidecar/internal/config/config_test.go (package config)

func TestOverlayEnvMCPServers(t *testing.T) {
	t.Setenv("AGENT_NAME", "a")
	t.Setenv("AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH", "1")
	t.Setenv("AGENTBREEDER_SIDECAR_MCP_SERVERS",
		`{"zendesk":{"transport":"http","url":"http://localhost:3100"}}`)

	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	spec, ok := cfg.MCPServers["zendesk"]
	if !ok {
		t.Fatalf("zendesk not parsed: %+v", cfg.MCPServers)
	}
	if spec.Transport != "http" || spec.URL != "http://localhost:3100" {
		t.Fatalf("bad spec: %+v", spec)
	}
}

func TestOverlayEnvMCPServersWinsOverFile(t *testing.T) {
	t.Setenv("AGENT_NAME", "a")
	t.Setenv("AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH", "1")
	t.Setenv("AGENTBREEDER_SIDECAR_MCP_SERVERS",
		`{"z":{"transport":"sse","url":"http://env"}}`)
	c := &Config{MCPServers: map[string]MCPServerSpec{
		"z": {Transport: "http", URL: "http://file"},
	}}
	c.overlayEnv()
	if c.MCPServers["z"].URL != "http://env" {
		t.Fatalf("env should win: %+v", c.MCPServers["z"])
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd sidecar && go test ./internal/config/ -run TestOverlayEnvMCPServers -v`
Expected: FAIL — env var not read.

- [ ] **Step 3: Implement** — in `sidecar/internal/config/config.go`, add `"encoding/json"` to imports, then at the end of `overlayEnv()` (after the OTLP headers block) add:

```go
	// MCP server map as JSON: {"name":{"transport":"http","url":"..."}}.
	// Env entries override file entries key-by-key so a deployer can wire MCP
	// upstreams without shipping a YAML file.
	if raw := os.Getenv("AGENTBREEDER_SIDECAR_MCP_SERVERS"); raw != "" {
		parsed := map[string]MCPServerSpec{}
		if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
			fmt.Fprintf(os.Stderr, "sidecar: ignoring malformed AGENTBREEDER_SIDECAR_MCP_SERVERS: %v\n", err)
		} else {
			if c.MCPServers == nil {
				c.MCPServers = map[string]MCPServerSpec{}
			}
			for name, spec := range parsed {
				c.MCPServers[name] = spec
			}
		}
	}
```

- [ ] **Step 4: Run tests**

Run: `cd sidecar && go test ./internal/config/ -v`
Expected: PASS (new + existing config tests).

- [ ] **Step 5: Commit**

```bash
git add sidecar/internal/config/config.go sidecar/internal/config/config_test.go
git commit -m "feat(sidecar): parse MCP servers from AGENTBREEDER_SIDECAR_MCP_SERVERS env"
```

---

### Task A4: `SidecarConfig.mcp_servers` populated from agent config

**Files:**
- Modify: `engine/sidecar/config.py` (`SidecarConfig`, `from_agent_config`)
- Test: `tests/unit/test_sidecar_injector.py` (append a `TestSidecarConfigMcp` class)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_sidecar_injector.py

class TestSidecarConfigMcp:
    def _agent(self, mcp):
        from engine.config_parser import McpServerRef

        class _A:
            guardrails = []
            mcp_servers = [McpServerRef(**m) for m in mcp]

        return _A()

    def test_mcp_servers_populated_from_image(self):
        from engine.sidecar.config import SidecarConfig

        sc = SidecarConfig.from_agent_config(
            self._agent([{"ref": "mcp/a", "transport": "sse", "image": "img:1", "port": 3100}])
        )
        assert sc.mcp_servers == {"a": {"transport": "sse", "url": "http://localhost:3100"}}

    def test_no_mcp_means_empty(self):
        from engine.sidecar.config import SidecarConfig

        sc = SidecarConfig.from_agent_config(self._agent([]))
        assert sc.mcp_servers == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_sidecar_injector.py::TestSidecarConfigMcp -v`
Expected: FAIL — `SidecarConfig` has no `mcp_servers`.

- [ ] **Step 3: Implement** — in `engine/sidecar/config.py`:

Add field to the dataclass (after `api_url_env`):

```python
    # MCP server forwarding map for the Go sidecar: {name: {transport, url}}.
    mcp_servers: dict[str, dict[str, str]] = field(default_factory=dict)
```

In `from_agent_config`, replace the body with:

```python
    @classmethod
    def from_agent_config(cls, agent_config: Any) -> SidecarConfig:
        """Build a SidecarConfig from a parsed AgentConfig.

        Reads top-level guardrails and resolves any ``mcp_servers`` into the
        forwarding map the Go sidecar consumes. MCP resolution here is offline
        (inline url/image + convention) — registry enrichment happens earlier
        in the deploy pipeline when a session is available.
        """
        guardrails = _normalise_guardrails(getattr(agent_config, "guardrails", []) or [])
        mcp_refs = getattr(agent_config, "mcp_servers", None) or []
        mcp_map: dict[str, dict[str, str]] = {}
        if mcp_refs:
            from engine.deployers.mcp_sidecar import build_sidecar_env_map, resolve_mcp_servers

            mcp_map = build_sidecar_env_map(resolve_mcp_servers(mcp_refs))
        return cls(enabled=True, guardrails=guardrails, mcp_servers=mcp_map)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_sidecar_injector.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/sidecar/config.py tests/unit/test_sidecar_injector.py
git commit -m "feat(sidecar): SidecarConfig.mcp_servers resolved from agent config"
```

---

### Task A5: Injectors emit `AGENTBREEDER_SIDECAR_MCP_SERVERS`

**Files:**
- Modify: `engine/sidecar/injector.py` (the 3 injectors)
- Test: `tests/unit/test_sidecar_injector.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_sidecar_injector.py
import json


class TestInjectorMcpEnv:
    def _cfg(self):
        from engine.sidecar.config import SidecarConfig

        return SidecarConfig(mcp_servers={"a": {"transport": "http", "url": "http://localhost:3100"}})

    def _env_value(self, env_list):
        # ECS/CloudRun env are lists of {"name","value"}
        for e in env_list:
            if e["name"] == "AGENTBREEDER_SIDECAR_MCP_SERVERS":
                return e["value"]
        return None

    def test_ecs_emits_mcp_env(self):
        from engine.sidecar.injector import inject_sidecar

        td = inject_sidecar({}, self._cfg())
        sidecar = [c for c in td["containerDefinitions"] if c["name"] == "agentbreeder-sidecar"][0]
        val = self._env_value(sidecar["environment"])
        assert json.loads(val) == {"a": {"transport": "http", "url": "http://localhost:3100"}}

    def test_cloudrun_emits_mcp_env(self):
        from engine.sidecar.injector import inject_cloudrun_sidecar

        spec = inject_cloudrun_sidecar({}, self._cfg())
        containers = spec["spec"]["template"]["spec"]["containers"]
        sidecar = [c for c in containers if c["name"] == "agentbreeder-sidecar"][0]
        assert self._env_value(sidecar["env"]) is not None

    def test_compose_emits_mcp_env(self):
        from engine.sidecar.injector import inject_compose_sidecar

        services = inject_compose_sidecar({}, self._cfg())
        env = services["agentbreeder-sidecar"]["environment"]
        assert json.loads(env["AGENTBREEDER_SIDECAR_MCP_SERVERS"]) == {
            "a": {"transport": "http", "url": "http://localhost:3100"}
        }

    def test_no_mcp_no_env(self):
        from engine.sidecar.config import SidecarConfig
        from engine.sidecar.injector import inject_compose_sidecar

        services = inject_compose_sidecar({}, SidecarConfig())
        assert "AGENTBREEDER_SIDECAR_MCP_SERVERS" not in services["agentbreeder-sidecar"]["environment"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_sidecar_injector.py::TestInjectorMcpEnv -v`
Expected: FAIL — env not emitted.

- [ ] **Step 3: Implement** — in `engine/sidecar/injector.py`, add at top: `import json`. Then in each injector, conditionally append the env entry.

In `inject_sidecar`, after building `sidecar_container["environment"]` list, before `containers.append`:

```python
    if config.mcp_servers:
        sidecar_container["environment"].append(
            {"name": "AGENTBREEDER_SIDECAR_MCP_SERVERS", "value": json.dumps(config.mcp_servers)}
        )
```

In `inject_cloudrun_sidecar`, after building `sidecar["env"]`, before `containers.append`:

```python
    if config.mcp_servers:
        sidecar["env"].append(
            {"name": "AGENTBREEDER_SIDECAR_MCP_SERVERS", "value": json.dumps(config.mcp_servers)}
        )
```

In `inject_compose_sidecar`, after the `result[SIDECAR_NAME] = {...}` assignment:

```python
    if config.mcp_servers:
        result[SIDECAR_NAME]["environment"]["AGENTBREEDER_SIDECAR_MCP_SERVERS"] = json.dumps(
            config.mcp_servers
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_sidecar_injector.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add engine/sidecar/injector.py tests/unit/test_sidecar_injector.py
git commit -m "feat(sidecar): emit AGENTBREEDER_SIDECAR_MCP_SERVERS env in all injectors"
```

---

### Task A6: Wire co-deploy + registry enrichment into the 4 deployers

Each multi-container deployer already calls `should_inject(config)` and builds `SidecarConfig.from_agent_config(config)`. Add: resolve once (with best-effort registry enrichment), co-deploy image-backed MCP containers, and ensure the resolved map reaches the sidecar (it already does via `from_agent_config`, but the deployer's resolution may have registry data the offline path lacks — so override `sc.mcp_servers`).

**Files:**
- Modify: `engine/deployers/aws_ecs.py` (~line 464-477)
- Modify: `engine/deployers/gcp_cloudrun.py` (~line 258-265, 308)
- Modify: `engine/deployers/azure_container_apps.py` (~line 334, 521)
- Modify: `engine/deployers/docker_compose.py` (~line 305-380)
- Test: `tests/unit/test_mcp_deploy_wiring.py` (new)

Registry enrichment is best-effort and **must not** require a DB: define a module-level helper in `mcp_sidecar.py` that returns `None` lookups by default; deployers pass `registry_lookup=None` for now (inline/convention resolution). This keeps deploy self-contained; a real session-backed lookup is a future enhancement noted in docs.

> Because `SidecarConfig.from_agent_config` already resolves the same refs offline, the deployer change is: **(a)** compute `resolved = resolve_mcp_servers(config.mcp_servers)` once when `config.mcp_servers` is truthy, and **(b)** call the matching `inject_mcp_containers_*` on the container structure. The sidecar env map is already correct from `from_agent_config`.

- [ ] **Step 1: Write the failing test** (drives ECS + compose wiring; cloud-run/azure mirror them)

```python
# tests/unit/test_mcp_deploy_wiring.py
"""MCP co-deploy container injection is wired into the deployers (P4)."""

from __future__ import annotations


def test_ecs_injects_codeploy_container():
    from engine.deployers.mcp_sidecar import inject_mcp_containers_ecs, resolve_mcp_servers
    from engine.config_parser import McpServerRef

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", image="img:1", port=3100)])
    td = inject_mcp_containers_ecs({"containerDefinitions": [{"name": "app"}]}, resolved)
    names = {c["name"] for c in td["containerDefinitions"]}
    assert "mcp-a" in names and "app" in names
    mcp = [c for c in td["containerDefinitions"] if c["name"] == "mcp-a"][0]
    assert mcp["image"] == "img:1"
    assert mcp["essential"] is False
    assert mcp["portMappings"][0]["containerPort"] == 3100


def test_ecs_skips_remote_only():
    from engine.deployers.mcp_sidecar import inject_mcp_containers_ecs, resolve_mcp_servers
    from engine.config_parser import McpServerRef

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", url="https://x/sse")])
    td = inject_mcp_containers_ecs({"containerDefinitions": []}, resolved)
    assert td["containerDefinitions"] == []  # remote → no container


def test_ecs_idempotent():
    from engine.deployers.mcp_sidecar import inject_mcp_containers_ecs, resolve_mcp_servers
    from engine.config_parser import McpServerRef

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", image="img:1")])
    td = inject_mcp_containers_ecs({"containerDefinitions": []}, resolved)
    td2 = inject_mcp_containers_ecs(td, resolved)
    assert len([c for c in td2["containerDefinitions"] if c["name"] == "mcp-a"]) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_mcp_deploy_wiring.py -v`
Expected: PASS already for the injector helpers (built in A2). If PASS, this test locks behavior; proceed to wire into deployers below. (These helper-level tests pass now; the deployer wiring is exercised by the deployers' own integration tests which mock cloud SDKs.)

- [ ] **Step 3: Wire into deployers.**

**`engine/deployers/aws_ecs.py`** — in the `if should_inject(config):` block (~line 475), after `partial = inject_sidecar(...)` add:

```python
            if config.mcp_servers:
                from engine.deployers.mcp_sidecar import (
                    inject_mcp_containers_ecs,
                    resolve_mcp_servers,
                )

                partial = inject_mcp_containers_ecs(
                    partial, resolve_mcp_servers(config.mcp_servers)
                )
```

**`engine/deployers/gcp_cloudrun.py`** — locate where `inject_cloudrun_sidecar` is applied to the service spec (near `sc = SidecarConfig.from_agent_config(config)` ~line 308). Immediately after the sidecar is injected into the spec, add:

```python
        if config.mcp_servers:
            from engine.deployers.mcp_sidecar import (
                inject_mcp_containers_cloudrun,
                resolve_mcp_servers,
            )

            service_spec = inject_mcp_containers_cloudrun(
                service_spec, resolve_mcp_servers(config.mcp_servers)
            )
```
(Use the actual local variable name holding the Cloud Run service-spec dict at that point — read the surrounding lines first; it is the dict passed to `inject_cloudrun_sidecar`.)

**`engine/deployers/azure_container_apps.py`** — find where the container list for the Container App template is assembled and the sidecar is appended (near `sc = SidecarConfig.from_agent_config(config)` ~line 334 and the `should_inject` block ~line 521). After the AB sidecar is appended to the containers list `containers`, add:

```python
        if config.mcp_servers:
            from engine.deployers.mcp_sidecar import (
                inject_mcp_containers_azure,
                resolve_mcp_servers,
            )

            containers = inject_mcp_containers_azure(
                containers, resolve_mcp_servers(config.mcp_servers)
            )
```
(Read the surrounding code; bind to the real container-list variable used when constructing the `template`/`Template` object.)

**`engine/deployers/docker_compose.py`** — the compose deployer starts containers imperatively via the docker SDK rather than emitting a compose file (`_start_sidecar` ~line 342). For co-deployed MCP servers, start each as its own container on the agent's network. In `_start_sidecar`'s caller (the `if should_inject(config):` block ~line 305), after the sidecar starts add:

```python
            if config.mcp_servers:
                await self._start_mcp_sidecars(client, config, container_name)
```

And add the method (mirror `_start_sidecar`'s container-run pattern — read it first to match the docker SDK calls, network, and labels):

```python
    async def _start_mcp_sidecars(self, client, config, agent_container_name):
        """Start each co-deployed MCP server as a sibling container."""
        from engine.deployers.mcp_sidecar import resolve_mcp_servers

        for r in resolve_mcp_servers(config.mcp_servers):
            if not (r.co_deploy and r.image):
                continue
            name = f"{config.name}-mcp-{r.name}"
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda r=r, name=name: client.containers.run(
                    r.image,
                    name=name,
                    detach=True,
                    environment={"MCP_TRANSPORT": "http", "PORT": str(r.port)},
                    network=f"container:{agent_container_name}",
                    labels={"agentbreeder.agent": config.name, "agentbreeder.mcp": r.name},
                ),
            )
```
(Confirm `asyncio` is imported in the file; if the file uses a different executor idiom for `_start_sidecar`, copy that idiom verbatim instead.)

- [ ] **Step 4: Run the deployer unit tests + full unit suite for regressions**

Run: `pytest tests/unit/test_mcp_deploy_wiring.py tests/unit/test_aws_ecs*.py tests/unit/test_gcp_cloudrun*.py tests/unit/test_azure*.py tests/unit/test_docker_compose*.py -v`
Expected: PASS. Fix any deployer test that asserts an exact container count to account for MCP containers only when `mcp_servers` is set (most agent fixtures have none, so they're unaffected).

- [ ] **Step 5: Commit**

```bash
git add engine/deployers/aws_ecs.py engine/deployers/gcp_cloudrun.py \
        engine/deployers/azure_container_apps.py engine/deployers/docker_compose.py \
        tests/unit/test_mcp_deploy_wiring.py
git commit -m "feat(mcp): co-deploy MCP server containers across ECS/CloudRun/Azure/compose"
```

---

### Task A7: P4 docs + CHANGELOG + cross-repo grep

**Files:**
- Modify: `website/content/docs/mcp-servers.mdx`, `website/content/docs/agent-yaml.mdx`, `website/content/docs/sidecar.mdx`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update `agent-yaml.mdx`** — in the `mcp_servers:` section, document the new optional `url`, `image`, `port` fields with a worked example:

```yaml
mcp_servers:
  - ref: mcp/zendesk          # registry reference
    transport: sse
    url: https://mcp.acme.com/sse   # remote server — sidecar forwards here
  - ref: mcp/slack
    transport: sse
    image: acme/mcp-slack:1.2.3     # co-deployed as a sidecar container
    port: 3100                       # localhost port (auto from 3100 if omitted)
```

- [ ] **Step 2: Update `mcp-servers.mdx` + `sidecar.mdx`** — add a "Deploying MCP servers with an agent" section explaining: remote `url` → forwarded; `image` → co-deployed container reachable over localhost; the agent calls `http://localhost:9091/mcp/<name>`; stdio is co-deploy-only (remote stdio is not forwardable); App Runner/single-container targets cannot host MCP sidecars.

- [ ] **Step 3: CHANGELOG** — under Unreleased/Added:

```markdown
- **MCP server deployment**: `agent.yaml` `mcp_servers` now deploy end-to-end — remote `url` servers are forwarded by the sidecar, and `image` servers are co-deployed as sidecar containers (AWS ECS, GCP Cloud Run, Azure Container Apps, docker-compose). The Go sidecar reads the forwarding map from `AGENTBREEDER_SIDECAR_MCP_SERVERS`.
```

- [ ] **Step 4: Cross-repo grep** (`feedback_cross_repo_sync`)

Run:
```bash
grep -rn "mcp_servers\|McpServerRef" /Users/rajit/personal-github/agentbreeder-cloud --include=*.py --include=*.ts 2>/dev/null | head
```
If hits reference the schema shape, note them; the new fields are additive/optional so no companion change is expected. Record the finding in the PR description.

- [ ] **Step 5: Commit**

```bash
git add website/content/docs/mcp-servers.mdx website/content/docs/agent-yaml.mdx website/content/docs/sidecar.mdx CHANGELOG.md
git commit -m "docs(mcp): document mcp_servers deployment (url/image/port) + sidecar forwarding"
```

---

## Part B — P5: Studio Self-Hosting

### Task B1: Env-driven nginx (upstream / CORS / TLS) via envsubst

The dashboard image must render its nginx config at runtime from env so the same image self-hosts anywhere: `API_UPSTREAM` (default `http://api:8000`), optional `EXTRA_CORS_HEADERS` toggle, and optional TLS. CORS for the API itself is already env-driven (`CORS_ORIGINS`), so nginx only needs upstream + optional TLS termination.

**Files:**
- Create: `dashboard/nginx.conf.template`
- Create: `dashboard/docker-entrypoint.sh`
- Modify: `dashboard/Dockerfile`
- Delete: `dashboard/nginx.conf` (superseded)
- Test: `tests/unit/test_nginx_template.py` (new — renders the template with `envsubst`-style substitution and asserts the output)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_nginx_template.py
"""The dashboard nginx template renders API_UPSTREAM from env (P5)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "dashboard" / "nginx.conf.template"


def _render(env: dict[str, str]) -> str:
    # Mirror the container entrypoint: envsubst with an explicit var allow-list.
    envsubst = shutil.which("envsubst")
    if not envsubst:
        pytest.skip("envsubst not installed")
    full = {**os.environ, **env}
    out = subprocess.run(
        [envsubst, "${API_UPSTREAM} ${LISTEN_PORT}"],
        input=TEMPLATE.read_text(),
        capture_output=True,
        text=True,
        env=full,
        check=True,
    )
    return out.stdout


def test_template_has_placeholders():
    text = TEMPLATE.read_text()
    assert "${API_UPSTREAM}" in text
    assert "${LISTEN_PORT}" in text


def test_renders_custom_upstream():
    rendered = _render({"API_UPSTREAM": "http://my-api:9000", "LISTEN_PORT": "3001"})
    assert "proxy_pass http://my-api:9000" in rendered
    assert "listen 3001;" in rendered
    assert "${" not in rendered  # all placeholders substituted
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_nginx_template.py -v`
Expected: FAIL — template file missing.

- [ ] **Step 3: Create the template** `dashboard/nginx.conf.template`:

```nginx
server {
    listen ${LISTEN_PORT};

    location /api/ {
        resolver 127.0.0.11 valid=30s ipv6=off;
        proxy_pass ${API_UPSTREAM};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        resolver 127.0.0.11 valid=30s ipv6=off;
        proxy_pass ${API_UPSTREAM};
        proxy_set_header Host $host;
    }

    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ /index.html;
    }
}
```

Create `dashboard/docker-entrypoint.sh` (renders template, preserving nginx's own `$host`/`$uri` runtime vars by only substituting our allow-listed vars):

```sh
#!/bin/sh
set -e

: "${API_UPSTREAM:=http://api:8000}"
: "${LISTEN_PORT:=3001}"
export API_UPSTREAM LISTEN_PORT

# Only substitute our vars; leave nginx runtime vars ($host, $uri, ...) intact.
envsubst '${API_UPSTREAM} ${LISTEN_PORT}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
```

- [ ] **Step 4: Update `dashboard/Dockerfile`** production stage — replace the `COPY nginx.conf ...` line and `CMD` with template + entrypoint, and ensure `envsubst` (from `gettext`) is present (it ships in `nginx:alpine` via `/usr/bin/envsubst`? It does NOT by default — install it):

Replace lines 27-41 of `dashboard/Dockerfile` with:

```dockerfile
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.sh /docker-entrypoint.d/40-render-template.sh

# nginx:alpine bundles envsubst (gettext) and an entrypoint that runs
# /docker-entrypoint.d/*.sh before starting. Make our render script executable.
RUN chmod +x /docker-entrypoint.d/40-render-template.sh \
    && chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 3001

CMD ["nginx", "-g", "daemon off;"]
```

> Note: `nginx:alpine` already includes `/docker-entrypoint.sh` which executes `/docker-entrypoint.d/*.sh` then `exec "$@"`. Our render script must therefore **not** itself `exec nginx`; rewrite `docker-entrypoint.sh` for this drop-in directory to just render and return:

```sh
#!/bin/sh
set -e
: "${API_UPSTREAM:=http://api:8000}"
: "${LISTEN_PORT:=3001}"
export API_UPSTREAM LISTEN_PORT
envsubst '${API_UPSTREAM} ${LISTEN_PORT}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf
```

Then delete the old static config:
```bash
git rm dashboard/nginx.conf
```

- [ ] **Step 5: Run tests + build check**

Run: `pytest tests/unit/test_nginx_template.py -v`
Expected: PASS.
(Optional, if Docker available) `docker build -t ab-dash-test dashboard/` to confirm the image builds.

- [ ] **Step 6: Commit**

```bash
git add dashboard/nginx.conf.template dashboard/docker-entrypoint.sh dashboard/Dockerfile tests/unit/test_nginx_template.py
git rm dashboard/nginx.conf
git commit -m "feat(dashboard): env-driven nginx upstream via runtime envsubst template"
```

---

### Task B2: Helm chart scaffold (Chart.yaml + values.yaml)

**Files:**
- Create: `deploy/helm/agentbreeder/Chart.yaml`
- Create: `deploy/helm/agentbreeder/values.yaml`
- Create: `deploy/helm/agentbreeder/.helmignore`

- [ ] **Step 1: Create `Chart.yaml`** (Bitnami Postgres + Redis as conditional dependencies):

```yaml
apiVersion: v2
name: agentbreeder
description: Self-host the AgentBreeder platform (Studio + API + Postgres + Redis) on Kubernetes.
type: application
version: 0.1.0
appVersion: "2.6.0"
home: https://agentbreeder.io
sources:
  - https://github.com/agentbreeder/agentbreeder
dependencies:
  - name: postgresql
    version: "15.x.x"
    repository: https://charts.bitnami.com/bitnami
    condition: postgresql.enabled
  - name: redis
    version: "19.x.x"
    repository: https://charts.bitnami.com/bitnami
    condition: redis.enabled
```

- [ ] **Step 2: Create `values.yaml`**:

```yaml
# AgentBreeder self-hosting values. See website/content/docs/self-hosting.mdx.

image:
  registry: docker.io
  repository: rajits
  apiImage: agentbreeder-api
  dashboardImage: agentbreeder-dashboard
  tag: latest
  pullPolicy: IfNotPresent

# Public hostname the platform is served on (used by Ingress + CORS).
host: agentbreeder.local

api:
  replicas: 1
  port: 8000
  resources:
    requests: { cpu: 250m, memory: 512Mi }
    limits: { cpu: "1", memory: 1Gi }
  # Extra non-secret env passed to the API container.
  env: {}

dashboard:
  replicas: 1
  port: 3001
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits: { cpu: 500m, memory: 256Mi }

# Secrets. In production set these via --set or a pre-created secret (existingSecret).
secrets:
  existingSecret: ""        # if set, all keys below are read from this Secret
  secretKey: "change-me-to-a-random-256-bit-key"
  jwtSecretKey: "change-me"

# Connection strings. When postgresql/redis subcharts are enabled these are
# auto-derived and these explicit values are ignored.
externalDatabaseUrl: ""     # e.g. postgresql+asyncpg://user:pw@host:5432/db
externalRedisUrl: ""        # e.g. redis://host:6379

# Run `alembic upgrade head` as a pre-install/upgrade Job.
migrate:
  enabled: true

ingress:
  enabled: true
  className: nginx
  annotations: {}
  tls:
    enabled: false
    secretName: agentbreeder-tls   # pre-created TLS secret, or cert-manager-managed

# Bundled datastores (toggle off to use externalDatabaseUrl/externalRedisUrl).
postgresql:
  enabled: true
  auth:
    username: agentbreeder
    password: agentbreeder
    database: agentbreeder
  primary:
    persistence:
      size: 8Gi

redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: false
```

- [ ] **Step 3: Create `.helmignore`** (standard):

```
.DS_Store
*.tmp
ci/
*.md
```

- [ ] **Step 4: Commit**

```bash
git add deploy/helm/agentbreeder/Chart.yaml deploy/helm/agentbreeder/values.yaml deploy/helm/agentbreeder/.helmignore
git commit -m "feat(helm): chart scaffold with toggleable Bitnami postgres/redis deps"
```

---

### Task B3: Helm templates

**Files (create all under `deploy/helm/agentbreeder/templates/`):** `_helpers.tpl`, `secret.yaml`, `configmap.yaml`, `api-deployment.yaml`, `api-service.yaml`, `dashboard-deployment.yaml`, `dashboard-service.yaml`, `migrate-job.yaml`, `ingress.yaml`, `NOTES.txt`

- [ ] **Step 1: `_helpers.tpl`** — names + derived DB/Redis URLs:

```yaml
{{- define "agentbreeder.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentbreeder.apiImage" -}}
{{- printf "%s/%s/%s:%s" .Values.image.registry .Values.image.repository .Values.image.apiImage .Values.image.tag -}}
{{- end -}}

{{- define "agentbreeder.dashboardImage" -}}
{{- printf "%s/%s/%s:%s" .Values.image.registry .Values.image.repository .Values.image.dashboardImage .Values.image.tag -}}
{{- end -}}

{{- define "agentbreeder.databaseUrl" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password .Release.Name .Values.postgresql.auth.database -}}
{{- else -}}
{{- .Values.externalDatabaseUrl -}}
{{- end -}}
{{- end -}}

{{- define "agentbreeder.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://%s-redis-master:6379" .Release.Name -}}
{{- else -}}
{{- .Values.externalRedisUrl -}}
{{- end -}}
{{- end -}}
```

- [ ] **Step 2: `secret.yaml`** (skipped when `existingSecret` set):

```yaml
{{- if not .Values.secrets.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "agentbreeder.fullname" . }}-secrets
type: Opaque
stringData:
  SECRET_KEY: {{ .Values.secrets.secretKey | quote }}
  JWT_SECRET_KEY: {{ .Values.secrets.jwtSecretKey | quote }}
  DATABASE_URL: {{ include "agentbreeder.databaseUrl" . | quote }}
  REDIS_URL: {{ include "agentbreeder.redisUrl" . | quote }}
{{- end }}
```

- [ ] **Step 3: `configmap.yaml`** (non-secret API env, incl. CORS for the public host):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "agentbreeder.fullname" . }}-config
data:
  AGENTBREEDER_ENV: "production"
  CORS_ORIGINS: {{ printf "https://%s,http://%s" .Values.host .Values.host | quote }}
  {{- range $k, $v := .Values.api.env }}
  {{ $k }}: {{ $v | quote }}
  {{- end }}
```

> The API reads `CORS_ORIGINS` as a list via pydantic-settings. Confirm list parsing accepts a comma-separated string; if it expects JSON, emit `'["https://host","http://host"]'` instead. (Check `api/config.py` `cors_origins` env parsing during implementation and match it.)

- [ ] **Step 4: `api-deployment.yaml`**:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "agentbreeder.fullname" . }}-api
  labels: { app: agentbreeder, component: api }
spec:
  replicas: {{ .Values.api.replicas }}
  selector:
    matchLabels: { app: agentbreeder, component: api }
  template:
    metadata:
      labels: { app: agentbreeder, component: api }
    spec:
      containers:
        - name: api
          image: {{ include "agentbreeder.apiImage" . }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - containerPort: {{ .Values.api.port }}
          envFrom:
            - configMapRef:
                name: {{ include "agentbreeder.fullname" . }}-config
            - secretRef:
                name: {{ .Values.secrets.existingSecret | default (printf "%s-secrets" (include "agentbreeder.fullname" .)) }}
          readinessProbe:
            httpGet: { path: /health, port: {{ .Values.api.port }} }
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /health, port: {{ .Values.api.port }} }
            initialDelaySeconds: 20
            periodSeconds: 20
          resources: {{- toYaml .Values.api.resources | nindent 12 }}
```

- [ ] **Step 5: `api-service.yaml`**:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "agentbreeder.fullname" . }}-api
  labels: { app: agentbreeder, component: api }
spec:
  selector: { app: agentbreeder, component: api }
  ports:
    - port: {{ .Values.api.port }}
      targetPort: {{ .Values.api.port }}
```

- [ ] **Step 6: `dashboard-deployment.yaml`** (sets `API_UPSTREAM` to the in-cluster API service):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "agentbreeder.fullname" . }}-dashboard
  labels: { app: agentbreeder, component: dashboard }
spec:
  replicas: {{ .Values.dashboard.replicas }}
  selector:
    matchLabels: { app: agentbreeder, component: dashboard }
  template:
    metadata:
      labels: { app: agentbreeder, component: dashboard }
    spec:
      containers:
        - name: dashboard
          image: {{ include "agentbreeder.dashboardImage" . }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - containerPort: {{ .Values.dashboard.port }}
          env:
            - name: API_UPSTREAM
              value: {{ printf "http://%s-api:%v" (include "agentbreeder.fullname" .) .Values.api.port | quote }}
            - name: LISTEN_PORT
              value: {{ .Values.dashboard.port | quote }}
          resources: {{- toYaml .Values.dashboard.resources | nindent 12 }}
```

- [ ] **Step 7: `dashboard-service.yaml`**:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "agentbreeder.fullname" . }}-dashboard
  labels: { app: agentbreeder, component: dashboard }
spec:
  selector: { app: agentbreeder, component: dashboard }
  ports:
    - port: {{ .Values.dashboard.port }}
      targetPort: {{ .Values.dashboard.port }}
```

- [ ] **Step 8: `migrate-job.yaml`** (pre-install/upgrade hook):

```yaml
{{- if .Values.migrate.enabled }}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "agentbreeder.fullname" . }}-migrate
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "0"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  backoffLimit: 3
  template:
    metadata:
      labels: { app: agentbreeder, component: migrate }
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: {{ include "agentbreeder.apiImage" . }}
          command: ["alembic", "upgrade", "head"]
          envFrom:
            - secretRef:
                name: {{ .Values.secrets.existingSecret | default (printf "%s-secrets" (include "agentbreeder.fullname" .)) }}
{{- end }}
```

- [ ] **Step 9: `ingress.yaml`** (dashboard root + `/api` to API; optional TLS):

```yaml
{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "agentbreeder.fullname" . }}
  annotations: {{- toYaml .Values.ingress.annotations | nindent 4 }}
spec:
  ingressClassName: {{ .Values.ingress.className }}
  {{- if .Values.ingress.tls.enabled }}
  tls:
    - hosts: [{{ .Values.host | quote }}]
      secretName: {{ .Values.ingress.tls.secretName }}
  {{- end }}
  rules:
    - host: {{ .Values.host | quote }}
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: {{ include "agentbreeder.fullname" . }}-api
                port: { number: {{ .Values.api.port }} }
          - path: /health
            pathType: Prefix
            backend:
              service:
                name: {{ include "agentbreeder.fullname" . }}-api
                port: { number: {{ .Values.api.port }} }
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{ include "agentbreeder.fullname" . }}-dashboard
                port: { number: {{ .Values.dashboard.port }} }
{{- end }}
```

- [ ] **Step 10: `NOTES.txt`**:

```
AgentBreeder is installing.

  Studio:  https://{{ .Values.host }}
  API:     https://{{ .Values.host }}/api/v1
  Docs:    https://{{ .Values.host }}/api/docs

{{- if not .Values.ingress.tls.enabled }}

⚠  TLS is disabled. For production, set ingress.tls.enabled=true and provide a
   TLS secret (or use cert-manager). Also override secrets.secretKey and
   secrets.jwtSecretKey with strong random values.
{{- end }}

Check rollout:
  kubectl get pods -l app=agentbreeder
```

- [ ] **Step 11: Commit**

```bash
git add deploy/helm/agentbreeder/templates/
git commit -m "feat(helm): api/dashboard/migrate/ingress/secret/configmap templates"
```

---

### Task B4: Helm lint/template test

**Files:**
- Test: `tests/unit/test_helm_chart.py` (new)

- [ ] **Step 1: Write the test** (skips cleanly if `helm` is not installed, so CI without helm stays green; runs `helm template` with deps disabled to avoid network fetch):

```python
# tests/unit/test_helm_chart.py
"""Helm chart renders cleanly (P5). Skips if helm is unavailable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "agentbreeder"


@pytest.fixture(scope="module")
def helm():
    exe = shutil.which("helm")
    if not exe:
        pytest.skip("helm not installed")
    return exe


def test_chart_lint_structure():
    # Chart.yaml + key templates exist regardless of helm availability.
    assert (CHART / "Chart.yaml").exists()
    for t in ["api-deployment.yaml", "dashboard-deployment.yaml", "ingress.yaml", "secret.yaml"]:
        assert (CHART / "templates" / t).exists()


def test_helm_template_renders(helm):
    # Disable subchart deps so no repo fetch is needed; supply external URLs.
    out = subprocess.run(
        [
            helm, "template", "ab", str(CHART),
            "--set", "postgresql.enabled=false",
            "--set", "redis.enabled=false",
            "--set", "externalDatabaseUrl=postgresql+asyncpg://u:p@db:5432/d",
            "--set", "externalRedisUrl=redis://r:6379",
            "--set", "host=example.com",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "kind: Deployment" in out.stdout
    assert "kind: Ingress" in out.stdout
    assert "DATABASE_URL" in out.stdout
    assert "rajits/agentbreeder-api" in out.stdout
```

- [ ] **Step 2: Run it**

Run: `pytest tests/unit/test_helm_chart.py -v`
Expected: structure test PASS; render test PASS if `helm` present, else SKIP. If `helm` is present, fix any template errors it surfaces.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_helm_chart.py
git commit -m "test(helm): chart structure + helm template render check"
```

---

### Task B5: `self-hosting.mdx` + nav + README + CHANGELOG + standalone compose image refs

**Files:**
- Create: `website/content/docs/self-hosting.mdx`
- Modify: `website/content/docs/meta.json` (add `self-hosting` under "Deploy & Run")
- Modify: `README.md` (link self-hosting)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Create `self-hosting.mdx`** with frontmatter + sections: Prerequisites (K8s, Helm 3, ingress controller, optional cert-manager); Quick start (`helm dependency update`, `helm install agentbreeder deploy/helm/agentbreeder --set host=...`); Using external Postgres/Redis (`postgresql.enabled=false` + `externalDatabaseUrl`); Secrets (override `secrets.secretKey`/`jwtSecretKey` or `existingSecret`); TLS (`ingress.tls.enabled=true` + secret or cert-manager); CORS (auto-set from `host`); Migrations (the pre-install Job); Docker-compose alternative (link `deploy/docker-compose.standalone.yml`); Troubleshooting. Use the same frontmatter + `Callout`/`Tabs` conventions as `quickstart.mdx`.

```mdx
---
title: Self-Hosting
description: Run the full AgentBreeder platform (Studio + API + Postgres + Redis) on your own Kubernetes cluster with Helm.
---
```

- [ ] **Step 2: Register in nav** — in `website/content/docs/meta.json`, add `"self-hosting"` to the `---Deploy & Run---` group right after `"deployment"`:

```json
    "---Deploy & Run---",
    "deployment",
    "self-hosting",
    "providers",
```

- [ ] **Step 3: README** — add a "Self-hosting" bullet/section linking the Helm chart (`deploy/helm/agentbreeder`) and the new doc.

- [ ] **Step 4: CHANGELOG** — under Added:

```markdown
- **Studio self-hosting**: a Helm chart (`deploy/helm/agentbreeder`) deploys the full platform (Studio + API + migrations) with toggleable bundled Postgres/Redis, TLS-capable Ingress, and env-driven nginx. See docs → Self-Hosting.
```

- [ ] **Step 5: Commit**

```bash
git add website/content/docs/self-hosting.mdx website/content/docs/meta.json README.md CHANGELOG.md
git commit -m "docs(self-hosting): Helm self-hosting guide + nav + README"
```

---

## Finalisation

- [ ] **Run the full local gate** (pre-push hook runs repo-wide ruff): `ruff format . && ruff check . && pytest tests/unit/ -q` and `cd sidecar && go test ./...`.
- [ ] **Cross-repo sync** (`feedback_cross_repo_sync`): grep `agentbreeder-cloud` for `mcp_servers`, dashboard nginx, and any Helm/self-host surface; note findings in PR body. Update `website/components/footer.tsx` version only if a release bump is part of this PR (it is not — leave as-is).
- [ ] **Open the combined PR** (`feat/cloud-agnostic-p4p5`) per `feedback_defer_pr_until_done`; do not `--admin` merge without explicit user OK (`feedback_confirm_before_safety_bypass`).

---

## Self-Review Notes

- **Spec coverage:** P4 revives `mcp_sidecar.py` (resolution + 4 co-deploy injectors) and uses `packager.py` (`build_image_tag` in convention fallback + back-compat shim); wires MCP endpoints into the Go sidecar (A3) via env consumed by injectors (A5) sourced from `SidecarConfig` (A4). P5 delivers Helm (B2/B3), env-driven nginx upstream + TLS via Ingress (B1/B3), and `self-hosting.mdx` (B5). ✅
- **Back-compat:** existing `tests/unit/test_mcp_packager.py` keeps passing because `McpSidecarDeployer.generate_sidecars`/`inject_into_compose` and all `packager` functions are retained (A2 shim). The one behavioral nuance: `generate_sidecars` now always uses `"latest"` version (previously also `"latest"`), so assertions are unaffected.
- **Type consistency:** `ResolvedMcpServer`/`McpServerInfo` field names are used identically in A2, A4, A6. The Go `MCPServerSpec` JSON tags (`transport`,`url`) match the Python `build_sidecar_env_map` keys exactly (A3 ↔ A2).
- **Risk:** `CORS_ORIGINS` list parsing format (comma vs JSON) — verified against `api/config.py` during B3 implementation. Cloud-Run/Azure deployer variable names for the container list/spec must be confirmed by reading the surrounding lines before editing (A6 notes this).
