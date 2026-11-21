"""AgentBreeder's maintained Google ADK runtime (runtime/adk@1.5.1), driven by agent.config.json.

  LlmAgent (LiteLLM gateway alias)  +  Runner  +  DatabaseSessionService (Postgres)
  tools:   search_knowledge_base (built in, when knowledge is configured) + registry tools
           + one McpToolset per `mcp:` server, through the stack's relay (mcp_client.py)
  plugin:  AgentPlugin - step cap (last model call has tools off), agent events, token
           accounting, tool errors returned to the model, OpenInference span attributes
  cost:    CostClient - sums the gateway's `x-litellm-response-cost` header over a turn's
           model calls (`cost_usd` in the reply; null when any call had no cost header), counts
           the gateway's fallbacks (`fallbacks`, `served_by`) and retries a model call itself:
           transient failures up to 3 attempts, a rate limit only within RATE_LIMIT_WAIT
  errors:  a failed model call reaches the caller as a fixed code (ModelError, server.py), never
           the gateway's or provider's text; that goes, redacted, to the log and the turn record.
           A gateway guardrail block is MODEL_BLOCKED, with a guardrail_blocked event (1.4.0);
           the blocked turn's `turns` row and spans keep none of its text (1.5.0)
  usage:   cached prompt tokens, as the provider reported them through the gateway (1.4.0)
  tools:   a tool name the model invented is refused and listed under `unknown_tools`, never
           as a tool call (1.4.0)
  turns:   every callback finds its turn through Runtime.turn_for (the invocation id, else the
           session state key a sub-agent call set); an unknown turn raises TurnUnknown, so
           nothing is counted, capped or audited against a missing or wrong turn (1.5.0)
  agents:  `subagents:` are child LlmAgents the parent calls as tools, `ask_<name>(request)`
           (subagents.py): one turn id, caps per agent and per turn, cost and spans per
           sub-agent, `subagents[]` in the reply (1.5.0)
  skills:  `skills:` are instruction packs the model loads with `load_skill` and
           `load_skill_resource` (skills.py): hash-checked at start, capped per turn, and
           never copied into an event or a span; `skills[]` in the reply (1.5.0)

Retrieval-augmented generation runs before the model call: the top knowledge passages are added
to the customer's message. Each turn is recorded in `turns`. The customer is always the Runner's
user_id (set server-side from the request), never a value the model chooses.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import dataclasses
import json
import math
import re
import threading
import time
import uuid
import warnings
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import config as cfg
import mcp_client
import psycopg
import psycopg.sql
import redaction
import skills
import subagents
import telemetry
from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.run_config import RunConfig
from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.skill_toolset import LoadSkillResourceTool, LoadSkillTool
from google.adk.tools.tool_context import ToolContext
from google.genai import errors as genai_errors
from google.genai import types
from memory import Memory
from opentelemetry import trace as otel
from rag import Embedder, KnowledgeSet, Retriever
from telemetry import as_text, ids, set_documents, set_output, span
from tools import RegistryTool, ToolLoader, ToolRuntime, run_to_completion
from turns import TURN_KEY, TurnUnknown, caused_by

warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

TURN_TIMEOUT = 170
CANCEL_GRACE = 120  # seconds to wait for a cancelled turn (and any in-flight tool) to stop
REASONING = {"none": None, "low": "low", "medium": "medium", "high": "high"}


RUNTIME_REF = "runtime/adk@1.5.1"

NO_ANSWER = "Sorry, I couldn't put an answer together. Could you rephrase that?"


class SessionBusy(Exception):
    """A previous turn for this session is still finishing (e.g. after a timeout)."""


INFLIGHT: set[str] = set()
INFLIGHT_LOCK = threading.Lock()


redact = redaction.redact  # MCP tokens, the gateway key and key-shaped strings

# Never copied from what a tool recorded into its audit row: trace lists the runtime owns and
# row fields only the runtime sets.
RUNTIME_KEYS = frozenset(
    {"tools", "evidence", "sources", "mcp_unavailable", "unknown_tools"}
    | {"args", "agent", "tool", "mcp", "latency_ms", "ok", "error", "writes_request"}
)

# LiteLLM's SDK keeps the gateway's response headers, prefixed, in `_hidden_params`.
COST_HEADER = "llm_provider-x-litellm-response-cost"
FALLBACKS_HEADER = "llm_provider-x-litellm-attempted-fallbacks"
GROUP_HEADER = "llm_provider-x-litellm-model-group"
# A gateway alias as stack.yaml names one: never a provider model id, a URL or an API base.
ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
MAX_FALLBACKS = 100
# A gateway guardrail block (LiteLLM's Presidio hook, #1024 G0 §12.6): HTTP 400 with "Blocked
# entity detected: <ENTITY> by Guardrail: <name>. ..." after LiteLLM's exception prefix. Only
# the entity type and guardrail name are kept; the message holds no matched text. Matched only
# at the very start of the exception text (after LiteLLM's fixed prefixes), so a 400 that echoes
# the phrase anywhere else (customer text quoted by a provider) stays MODEL_REQUEST_REFUSED.
BLOCKED = re.compile(
    r"(?:litellm\.BadRequestError: )?(?:OpenAIException - )?Blocked entity detected: "
    r"([A-Z][A-Z0-9_]{0,63}) by Guardrail: ([A-Za-z0-9][A-Za-z0-9._-]{0,63}?)\.(?:\s|\Z)"
)
# A 400 with this phrase anywhere is stored as a block (no text kept) even when BLOCKED does
# not match, so a prefix the gateway or a wrapper adds cannot make the turn keep it (#1106).
BLOCK_PHRASE = "Blocked entity detected"
# What the `turns` row of a blocked turn holds in place of the customer's message (#1106).
BLOCKED_MESSAGE = "[blocked by a gateway guardrail]"
# ADK 2.12 stands a bare BaseTool with this description in for a tool name the model invented
# (flows/llm_flows/tools/_caller.py), and answers it through on_tool_error_callback (#1074).
TOOL_NOT_FOUND = "Tool not found"
UNKNOWN_TOOL_ERROR = "the model asked for a tool this agent does not have"
UNKNOWN_TOOL_REPLY = (
    "There is no tool with that name. Use only the tools you were given, or answer without one."
)
SHOWN_TOOL_NAME = re.compile(r"[^A-Za-z0-9_.-]")
MAX_UNKNOWN_TOOLS = 10
# The current turn's tally; set by chat_async, read by CostClient (same task context).
TURN_COST: contextvars.ContextVar[dict | None] = contextvars.ContextVar("turn_cost", default=None)

# The model client's own retries. The OpenAI client under LiteLLM would wait out a rate limit's
# retry-after (up to 120 s, twice) unseen, so it is switched off (MODEL_ARGS) and CostClient
# retries: a transient failure (RETRY_STATUSES, connection errors) up to MAX_ATTEMPTS, with
# backoff; a rate limit only while the turn's total wait stays within RATE_LIMIT_WAIT seconds.
# A timeout (408, 504) is not retried: the provider may have finished, and billed, the call, and
# three 90 s attempts would outlast the turn. No retry starts past the turn's RETRY_DEADLINE.
MODEL_ARGS = {"max_retries": 0}
MAX_ATTEMPTS = 3
RETRY_STATUSES = frozenset({409, 500, 502, 503})
TRANSIENT_4XX = frozenset({408, 409})  # a timeout or conflict: MODEL_UNAVAILABLE, "try again"
RETRY_BACKOFF = 0.5
RATE_LIMIT_WAIT = 10.0
MAX_RETRY_AFTER = 3600
RETRY_DEADLINE = TURN_TIMEOUT - 40  # seconds into a turn after which no retry starts


def new_tally() -> dict:
    """One turn's model calls: gateway cost, fallbacks and the time spent waiting on limits."""
    return {
        "calls": 0,
        "unknown": 0,
        "usd": 0.0,
        "last": None,  # the latest call's cost, for its model_called event
        "fallbacks": 0,
        "served_by": [],
        "route": None,  # the latest call's (fallbacks, model group), for its model_called event
        "waited": 0.0,
        "deadline": time.monotonic() + RETRY_DEADLINE,
        "mark": 0,  # `calls` when the current model call began (before_model_callback)
        # agent name ("" for the parent) -> {usd, unknown, calls}; sums to the turn's totals.
        "by_agent": {},
    }


def _headers(response) -> dict:
    hidden = getattr(response, "_hidden_params", None)
    headers = hidden.get("additional_headers") if isinstance(hidden, dict) else None
    return headers if isinstance(headers, dict) else {}


def response_cost(response) -> float | None:
    """The gateway-reported cost of one model response in USD, or None if it reported none."""
    value = _headers(response).get(COST_HEADER)
    if isinstance(value, bool) or value is None:
        return None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return None
    return cost if math.isfinite(cost) and cost >= 0 else None


def response_fallback(response) -> tuple[int, str | None]:
    """How many fallbacks the gateway tried for one response (0 when it said nothing usable) and
    the model group (alias) that served it, or None when absent or not an alias."""
    headers = _headers(response)
    raw = headers.get(FALLBACKS_HEADER)
    count = 0
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= MAX_FALLBACKS:
        count = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() and len(raw) <= 3:
        count = int(raw) if int(raw) <= MAX_FALLBACKS else 0
    group = headers.get(GROUP_HEADER)
    return count, group if isinstance(group, str) and ALIAS.fullmatch(group) else None


def upstream_status(failure) -> int | None:
    """The HTTP status of a failed model call, or None when `failure` is not one."""
    if isinstance(failure, genai_errors.APIError):
        status = failure.code
    elif type(failure).__module__.split(".")[0] in ("litellm", "openai"):
        status = getattr(failure, "status_code", None)
    else:
        return None
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def upstream_kind(failure) -> str | None:
    """The gateway's error `type` (LiteLLM: budget_exceeded, throttling_error, ...)."""
    body = getattr(failure, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    kind = body.get("type") if isinstance(body, dict) else None
    return kind if isinstance(kind, str) else None


def retry_after(headers) -> int | None:
    """A `retry-after` in whole seconds (1..MAX_RETRY_AFTER), or None when absent or invalid."""
    try:
        if isinstance(headers, dict):  # httpx.Headers is case-insensitive; a plain dict is not
            headers = {str(k).lower(): v for k, v in headers.items()}
        raw = headers.get("retry-after") if headers is not None else None
    except Exception:  # noqa: BLE001 - a headers object of an unexpected kind
        return None
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 12 or int(raw) == 0:
        return None
    return min(int(raw), MAX_RETRY_AFTER)


def failure_headers(failure):
    """The gateway's response headers on a failed call: LiteLLM keeps them in
    `litellm_response_headers` (its RateLimitError's own `response` has none)."""
    for headers in (
        getattr(failure, "litellm_response_headers", None),
        getattr(getattr(failure, "response", None), "headers", None),
    ):
        if headers:
            return headers
    return None


class CostClient(LiteLLMClient):
    """ADK's LiteLLM client, also tallying each response's gateway cost and fallbacks for the
    current turn, and retrying a failed call within bounds (MAX_ATTEMPTS, RATE_LIMIT_WAIT).
    A streamed response has no gateway headers here, so its cost counts as unknown."""

    async def acompletion(self, model, messages, tools, **kwargs):
        tally = TURN_COST.get()
        attempt = 1
        while True:
            try:
                response = await super().acompletion(
                    model=model, messages=messages, tools=tools, **kwargs
                )
                break
            except Exception as failure:
                delay = retry_delay(failure, attempt, tally)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
                attempt += 1
        if tally is not None:
            tally["calls"] += 1
            cost = response_cost(response)
            tally["last"] = cost
            if cost is None:
                tally["unknown"] += 1
            else:
                tally["usd"] += cost
            fallbacks, group = response_fallback(response)
            tally["route"] = (fallbacks, group)
            tally["fallbacks"] += fallbacks
            if group is not None and group not in tally["served_by"]:
                tally["served_by"].append(group)
        return response


def retry_delay(failure, attempt: int, tally: dict | None) -> float | None:
    """Seconds to wait before trying a failed model call again, or None to give up. A spent
    budget is never retried; a rate limit only while the turn's waits stay within
    RATE_LIMIT_WAIT (its retry-after, else the backoff); a transient failure with backoff."""
    if attempt >= MAX_ATTEMPTS:
        return None
    if tally is not None and time.monotonic() + RETRY_BACKOFF > tally["deadline"]:
        return None
    status = upstream_status(failure)
    if status is None or upstream_kind(failure) == "budget_exceeded":
        return None
    backoff = RETRY_BACKOFF * 2 ** (attempt - 1)
    if status == 429:
        wait = retry_after(failure_headers(failure)) or backoff
        waited = tally["waited"] if tally is not None else 0.0
        if waited + wait > RATE_LIMIT_WAIT:
            return None
        if tally is not None and time.monotonic() + wait > tally["deadline"]:
            return None
        if tally is not None:
            tally["waited"] += wait
        return wait
    return backoff if status in RETRY_STATUSES else None


def turn_fallbacks(tally: dict) -> tuple[int | None, list[str]]:
    """The turn's (`fallbacks`, `served_by`): (None, []) when no gateway call was made."""
    if tally["calls"] == 0:
        return None, []
    return tally["fallbacks"], list(tally["served_by"])


def root_attributes(tally: dict) -> dict:
    """The fallback attributes of the turn's root span (none without a gateway call)."""
    fallbacks, served_by = turn_fallbacks(tally)
    if fallbacks is None:
        return {}
    attributes = {"agentbreeder.fallbacks": fallbacks}
    if served_by:
        attributes["agentbreeder.served_by"] = tuple(served_by)
    return attributes


def turn_cost_usd(tally: dict) -> float | None:
    """The turn's summed cost, or None when no gateway call was made or any lacked a cost."""
    if tally["calls"] == 0 or tally["unknown"]:
        return None
    return round(tally["usd"], 10)


class ModelError(Exception):
    """A failed model call, as a fixed code for the caller (server.py has the messages). It
    carries no upstream text: that is logged, redacted, under the turn id (`turn_id`, the
    correlation id in the reply). `guardrail` is (entity type, guardrail name) for
    MODEL_BLOCKED, for the event and the span, never the reply."""

    def __init__(
        self,
        code: str,
        retry_after: int | None = None,
        guardrail: tuple[str, str] | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.guardrail = guardrail
        self.turn_id: str | None = None
        self.trace_id: str | None = None


def guardrail_block(failure) -> tuple[str, str] | None:
    """(entity type, guardrail name) when `failure` is the gateway's guardrail block."""
    try:
        text = str(failure)
    except Exception:  # noqa: BLE001 - an exception whose __str__ fails is not a block
        return None
    found = BLOCKED.match(text[:2000])
    return (found.group(1), found.group(2)) if found else None


def mentions_block(failure) -> bool:
    """Whether a failed call's text names a guardrail block anywhere (#1106): storage then
    keeps none of the turn's text even when BLOCKED, anchored at the start, did not match."""
    if upstream_status(failure) != 400:
        return False
    try:
        return BLOCK_PHRASE in str(failure)
    except Exception:  # noqa: BLE001 - an exception whose __str__ fails names nothing
        return False


def classify(failure) -> ModelError | None:
    """The coded error for a failed model call, or None when `failure` is not one."""
    status = upstream_status(failure)
    if status is None:
        return None
    if upstream_kind(failure) == "budget_exceeded":
        return ModelError("MODEL_BUDGET_EXCEEDED")
    blocked = guardrail_block(failure) if status == 400 else None
    if blocked is not None:
        return ModelError("MODEL_BLOCKED", guardrail=blocked)
    if status == 429:
        return ModelError("MODEL_RATE_LIMITED", retry_after(failure_headers(failure)))
    if status in (401, 403):
        return ModelError("MODEL_ACCESS_DENIED")
    if 400 <= status < 500 and status not in TRANSIENT_4XX:
        return ModelError("MODEL_REQUEST_REFUSED")
    return ModelError("MODEL_UNAVAILABLE")


def quiet_library_logs() -> None:
    """Library log lines (ADK logs a failed model call with its traceback) as one redacted line
    each, and none of LiteLLM's "Give Feedback" banners on stdout."""
    import litellm

    litellm.suppress_debug_info = True
    mcp_client.quiet_library_logs()


class Runtime:
    """Everything built from one agent.config.json. Tests construct it with stubs."""

    def __init__(
        self, config: dict, database_url: str, gateway_key: str, model=None, child_models=None
    ):
        self.config = config
        self.app = config["name"]
        self.model_alias = config["model"]["alias"]
        self.max_steps = int(config["model"].get("max_model_calls", 4))
        self.database_url = database_url
        self.events = config.get("agent_events", "postgres") != "none"
        self.services = {
            "gateway_url": cfg.gateway_url(),
            "tools_proxy_url": cfg.service("tools_proxy_url"),
        }
        self.turns: dict[str, dict] = {}
        self.system = (
            (cfg.artifact(config["prompt"]["dir"]) / "prompt.md")
            .read_text(encoding="utf-8")
            .strip()
        )

        def embedder_for(alias, dimensions):
            return Embedder(cfg.gateway_url(), gateway_key, alias, dimensions)

        knowledge = config.get("knowledge") or []
        self.retriever = Retriever(
            [KnowledgeSet(k, cfg.artifact(k["dir"]), embedder_for) for k in knowledge]
        )
        self.top_k = max([int(k.get("top_k", 5)) for k in knowledge] or [5])
        self.extra_searches = max([int(k.get("extra_searches", 1)) for k in knowledge] or [0])
        # Instruction packs first: verified against the build's hashes before any tool code is
        # imported or anything is served.
        self.skills = skills.load(config.get("skills") or [], cfg.artifact)
        self.skill_index = {skill.name: skill for skill in self.skills}
        self.loader = ToolLoader(cfg.artifact)  # one import and shadowing check for all agents
        self.functions = self.loader.functions(config.get("tools") or [])
        tools: list[BaseTool] = [RegistryTool(f, self.tool_runtime) for f in self.functions]
        if knowledge:
            tools.insert(0, KnowledgeSearchTool(self))
        mcp_client.remember_secret(gateway_key)
        self.mcp_servers = mcp_client.servers(config)
        mcp_client.tool_index(self.mcp_servers, [tool.name for tool in tools])
        self.mcp_toolsets = [
            mcp_client.RelayToolset(server, self.mcp_unavailable) for server in self.mcp_servers
        ]
        quiet_library_logs()
        self.gateway_key = gateway_key
        self.plugin = AgentPlugin(self, name="agentbreeder_runtime")
        name = re.sub(r"[^a-zA-Z0-9_]", "_", self.app)
        declared = config.get("subagents") or []
        # Every model call of a turn, all agents together (None without sub-agents: the
        # parent's own cap is then the only one, as before 1.5.0).
        self.turn_cap: int | None = None
        if self.max_steps < 1:
            raise cfg.ConfigError("model.max_model_calls must be at least 1")
        if declared:
            limit = (config.get("limits") or {}).get("turn_model_calls")
            self.turn_cap = int(limit) if limit is not None else min(40, self.max_steps + 6)
            if not 1 <= self.turn_cap <= 40:  # ADK reads a limit of 0 or less as "no limit"
                raise cfg.ConfigError("limits.turn_model_calls must be 1 to 40")
        self.children: dict[str, subagents.Child] = {}  # ADK agent name (`ask_<name>`) -> child
        for entry in declared:
            child = self.child(entry, bool(knowledge), (child_models or {}).get(entry["name"]))
            taken = {name, *self.children, *(tool.name for tool in tools)}
            taken |= set(mcp_client.tool_index(self.mcp_servers, []))
            if child.tool in taken:
                raise cfg.ConfigError(f"sub-agent {child.name}: {child.tool} is already a name")
            self.children[child.tool] = child
        if len(self.children) > subagents.MAX_SUBAGENTS:
            raise cfg.ConfigError(f"at most {subagents.MAX_SUBAGENTS} sub-agents")
        tools += [subagents.SubAgentTool(child, self) for child in self.children.values()]
        self.skill_toolset = skills.toolset(self.skills) if self.skills else None
        if self.skills:
            names = {tool.name for tool in tools} | set(
                mcp_client.tool_index(self.mcp_servers, [])
            )
            clash = sorted(names & set(skills.TOOLS))
            if clash:
                raise cfg.ConfigError(f"a tool is named {clash[0]}, which loads skills")
            tools.append(self.skill_toolset)
        self.agent = LlmAgent(
            name=name,
            description=config.get("description", ""),
            model=model
            or self.model_for(config["model"], subagents.request_tags(None) if declared else {}),
            instruction=self.instruction,
            tools=[*tools, *self.mcp_toolsets],
        )
        # Closed together on SIGTERM: the parent's relay sessions and each sub-agent's.
        self.mcp_toolsets = [
            *self.mcp_toolsets,
            *(toolset for child in self.children.values() for toolset in child.toolsets),
        ]
        ensure_sessions_schema(database_url)
        self.sessions = DatabaseSessionService(
            db_url=sessions_url(database_url), pool_pre_ping=True
        )
        self.runner = Runner(
            app_name=self.app,
            agent=self.agent,
            session_service=self.sessions,
            plugins=[self.plugin],
            auto_create_session=True,
        )

    def model_for(self, model: dict, extra: dict) -> LiteLlm:
        """An agent's model client: its gateway alias, with its own cost-tallying client."""
        reasoning = REASONING[model.get("reasoning", "low")]
        return LiteLlm(
            # "openai/" = the gateway's OpenAI-compatible API; it maps the alias to providers.
            model=f"openai/{model['alias']}",
            api_base=cfg.gateway_url(),
            api_key=self.gateway_key,
            timeout=90,
            llm_client=CostClient(),
            **MODEL_ARGS,
            **({"reasoning_effort": reasoning} if reasoning else {}),
            **extra,
        )

    def child(self, entry: dict, has_knowledge: bool, model) -> subagents.Child:
        """One sub-agent from agent.config.json: its own model client, prompt (never the
        customer's notes), registry tools, knowledge search when asked for, and one relay
        session per MCP server, offering only the tools it lists."""
        name = entry.get("name")
        if not isinstance(name, str) or not subagents.NAME.fullmatch(name):
            raise cfg.ConfigError("a sub-agent's name must match [a-z][a-z0-9_]{0,31}")
        calls = int(entry.get("max_calls_per_turn", 2))
        if not 1 <= calls <= subagents.MAX_CALLS_PER_TURN:
            raise cfg.ConfigError(f"sub-agent {name}: max_calls_per_turn must be 1 to 3")
        child = subagents.Child(
            name=name,
            alias=entry["model"]["alias"],
            max_model_calls=int(entry["model"].get("max_model_calls", 4)),
            max_calls_per_turn=calls,
            description=str(entry.get("description", "")),
        )
        if not 1 <= child.max_model_calls <= 20:
            raise cfg.ConfigError(f"sub-agent {name}: max_model_calls must be 1 to 20")
        system = (
            (cfg.artifact(entry["prompt"]["dir"]) / "prompt.md").read_text(encoding="utf-8")
        ).strip()

        async def instruction(context: ReadonlyContext) -> str:
            # A function, so ADK never fills `{...}` in the prompt from session state.
            self.turn_for(context)
            return system

        tools: list[BaseTool] = [
            RegistryTool(f, self.tool_runtime)
            for f in self.loader.functions(entry.get("tools") or [])
        ]
        if entry.get("knowledge"):
            if not has_knowledge:
                raise cfg.ConfigError(f"sub-agent {name}: knowledge needs a knowledge set")
            tools.insert(0, KnowledgeSearchTool(self))
        parents = {server.name: server for server in self.mcp_servers}
        servers = []
        for listed in entry.get("mcp") or []:
            server = parents.get(listed["name"])
            wanted = tuple(listed["tools"])
            if server is None or server.version != listed["version"]:
                raise cfg.ConfigError(f"sub-agent {name}: mcp/{listed['name']} is not the agent's")
            # An empty list would mean "every tool" to ADK's filter: refused, never widened.
            if not wanted or not set(wanted) <= set(server.tools):
                raise cfg.ConfigError(
                    f"sub-agent {name}: mcp/{listed['name']} must list tools the agent has"
                )
            servers.append(dataclasses.replace(server, tools=wanted))
        mcp_client.tool_index(servers, [tool.name for tool in tools])
        child.toolsets = [mcp_client.RelayToolset(s, self.mcp_unavailable) for s in servers]
        child.agent = LlmAgent(
            name=child.tool,
            description=child.description,
            model=model or self.model_for(entry["model"], subagents.request_tags(name)),
            instruction=instruction,
            tools=[*tools, *child.toolsets],
        )
        return child

    def spent(self, turn: dict, child: subagents.Child) -> bool:
        """Whether a sub-agent may not start (again) in this turn: the turn's model calls,
        or its own, are used up."""
        if turn["trace"]["model_calls"] >= self.turn_cap:
            return True
        return turn["agent_calls"].get(child.tool, 0) >= child.max_model_calls

    @staticmethod
    def model_failed(failure) -> bool:
        return model_failure(failure) is not None

    @staticmethod
    def log_failure(turn: dict, what: str, failure) -> None:
        """One redacted log line for a failure whose text goes nowhere else."""
        detail = redaction.upstream(f"{type(failure).__name__}: {failure}")[:500]
        print(redaction.one_line(f"{what} failed: turn={turn['id']} detail={detail}"), flush=True)

    def child_of(self, context) -> subagents.Child | None:
        """The sub-agent a callback's context belongs to, or None for the parent."""
        return self.children.get(getattr(context, "agent_name", None))

    async def audit(self, turn: dict, actor: str, action: str, detail: dict) -> None:
        await run_to_completion(
            turn["memory"].audit, turn["id"], turn["session"], turn["customer"],
            actor, action, detail,
        )  # fmt: skip

    # -- MCP ---------------------------------------------------------------------------------

    def turn_for(self, context) -> dict:
        """The turn a callback's context belongs to: the one with its invocation id, else the
        one its session state names under TURN_KEY. Raises TurnUnknown otherwise (a turn that
        ended, an id nobody issued, no id at all): never a guess, never a skipped check."""
        invocation = getattr(context, "invocation_id", None)
        turn = self.turns.get(invocation) if isinstance(invocation, str) else None
        if turn is None:
            try:
                named = context.state.get(TURN_KEY)
            except Exception:  # noqa: BLE001 - no state, or state of an unexpected kind
                named = None
            turn = self.turns.get(named) if isinstance(named, str) else None
            # A sub-agent's session is created for the turn's customer (subagents.py): a
            # state key naming another customer's turn is never honoured.
            if turn is not None and getattr(context, "user_id", None) != turn["customer"]:
                turn = None
        if turn is None:
            raise TurnUnknown
        return turn

    async def mcp_unavailable(self, server, context, failed: bool) -> bool:
        """Whether `server` already failed in this turn; records the first failure (trace and
        one audit event) so the turn's later model steps don't reconnect or audit again."""
        if context is None:
            # ADK listing tools outside any invocation: there is no turn to record against.
            # Inside a turn ADK always passes the context; without one the memo is skipped.
            return False
        turn = self.turn_for(context)
        # By server name, for every agent of the turn: the parent's and a sub-agent's sessions
        # reach one relay with one token, so one failure stands for both.
        down = turn["trace"]["mcp_unavailable"]
        if server.name in down:
            return True
        if failed:
            down.append(server.name)
            await self.audit(
                turn, "system", "mcp_unavailable", {"server": server.name, "mcp": server.ref}
            )
        return False

    async def close_mcp(self, timeout: float) -> None:
        """Close every relay session (DELETE), bounded: called on SIGTERM."""
        if not self.mcp_toolsets:
            return
        closing = asyncio.gather(*(t.close() for t in self.mcp_toolsets), return_exceptions=True)
        try:
            await asyncio.wait_for(closing, timeout)
        except TimeoutError:
            pass

    # -- per-call helpers --------------------------------------------------------------------
    def tool_runtime(self, tool_context: ToolContext) -> ToolRuntime:
        turn = self.turn_for(tool_context)
        added = turn["added"].setdefault(AgentPlugin._call_key(tool_context), {})
        return ToolRuntime(
            added=added,
            customer=turn["customer"],  # the server-side customer, also for a sub-agent's tool
            session=turn["session"],
            turn=turn["id"],
            db=turn["memory"].db,
            memory=turn["memory"],
            config=self.config,
            services=self.services,
            trace=turn["trace"],
        )

    async def instruction(self, context: ReadonlyContext) -> str:
        """System prompt + this customer's saved notes (long-term memory across sessions)."""
        notes = self.turn_for(context)["notes"]
        if not notes:
            return self.system
        # Notes are customer-influenced text: neutralise markup so a note cannot close the
        # memory block and pose as instructions.
        remembered = "\n".join(
            "- " + n.replace("<", "\u2039").replace(">", "\u203a") for n in notes
        )
        return (
            f"{self.system}\n\n<customer_memory>\nNotes saved from this customer's earlier "
            f"conversations:\n{remembered}\n</customer_memory>"
        )

    # -- turns -------------------------------------------------------------------------------
    async def previous_customer_text(self, customer: str, session: str):
        existing = await self.sessions.get_session(
            app_name=self.app, user_id=customer, session_id=session
        )
        if not existing:
            return [], True
        texts = [
            p.text
            for e in existing.events
            if e.author == "user" and e.content
            for p in (e.content.parts or [])
            if p.text and not p.text.startswith("<")
        ]
        return texts[-1:], not existing.events

    async def chat_async(self, session: str, customer: str, message: str) -> dict:
        memory = await run_to_completion(Memory, self.database_url, self.events)
        turn = uuid.uuid4().hex
        started = time.monotonic()
        trace = {
            "sources": [],
            "web": [],
            "memory_saved": [],
            "tickets": [],
            "tools": [],
            "kb_searches": 0,
            "model_calls": 0,
            "retrieval": "none",
            "evidence": [],
            "prompt_tokens": 0,
            "output_tokens": 0,
            # Prompt tokens the provider read from its cache, as reported (1.4.0, #1056).
            "cached_prompt_tokens": 0,
            # Tool names the model asked for that this agent does not have (1.4.0, #1074).
            "unknown_tools": [],
            "mcp_unavailable": [],  # mcp: servers whose relay couldn't be reached this turn
        }
        cost = new_tally()
        cost_token = TURN_COST.set(cost)
        entry = self.turns[turn] = {
            "id": turn,  # the `turns` row's id: what every audit, ticket and request row carries
            "memory": memory,
            "customer": customer,
            "session": session,
            "trace": trace,
            "notes": [],
            "recorded": {},
            "started": {},  # function_call_id -> monotonic start, for detail.latency_ms
            "failed": set(),  # function_call_ids already audited by on_tool_error_callback
            "cost": cost,
            "added": {},  # tool call -> what that call recorded (ToolRuntime.record)
            "lost": False,  # a callback found no turn (run_turn): the fixed reply, and an error
            "agent_calls": {},  # ADK agent name -> model calls this turn
            "kb": {},  # ADK agent name -> knowledge searches this turn
            "subagents": {},  # sub-agent name -> its usage this turn, in first-call order
            "subagent_lock": asyncio.Lock(),  # one sub-agent call at a time
            "skills": skills.new_usage(),  # load counts and what was loaded, this turn
        }
        reply, error, trace_id, span_id = "", None, None, None
        blocked = False  # a gateway guardrail blocked the turn (#1106)
        try:
            with span(
                "agent_turn",
                "AGENT",
                input=message,
                **{
                    "session.id": session,
                    "user.id": customer,
                    "agent.turn_id": turn,
                    "agent.framework": "google-adk",
                    "agent.name": self.app,
                    # Exact trace -> deployment attribution (#907); omitted when unset.
                    "agentbreeder.deployment_id": cfg.deployment_id(),
                },
            ) as root:
                trace_id, span_id = ids(root)
                telemetry.hold(trace_id)  # the turn's spans go out when it ends (release)
                await run_to_completion(
                    memory.audit,
                    turn,
                    session,
                    customer,
                    "customer",
                    "message_received",
                    {"chars": len(message), "trace_id": trace_id},
                )
                try:
                    reply = await self.run_turn(turn, session, customer, message)
                    set_output(root, reply)
                    if entry["lost"]:
                        # Not a model's non-answer: say so in the turn record, an event and
                        # the log, so a lost turn is never mistaken for one.
                        error = "TurnUnknown: a callback found no running turn"
                        root.set_attribute("agentbreeder.error_code", "TURN_UNKNOWN")
                        await run_to_completion(
                            memory.audit, turn, session, customer, "system", "turn_failed",
                            {"error": error},
                        )  # fmt: skip
                        print(f"turn lost: turn={turn}", flush=True)
                except Exception as failure:
                    # The detail stays here: the turn record, its audit event and one log line.
                    error = redaction.upstream(f"{type(failure).__name__}: {failure}")[:500]
                    coded = classify(failure)
                    if coded is not None and coded.guardrail is not None:
                        # Only the two names the block was parsed into: the rest of the
                        # gateway's text is not known to be free of what matched (#1106).
                        blocked = True
                        error = "MODEL_BLOCKED: {} by guardrail {}".format(*coded.guardrail)
                    elif mentions_block(failure):
                        # The phrase, but not at the start BLOCKED expects (a wrapper's
                        # prefix): the code stays as classified, the storage is as for a
                        # block, since the text may hold what matched (#1106 review).
                        blocked = True
                        code = coded.code if coded is not None else type(failure).__name__
                        error = f"{code}: gateway text withheld (it names a guardrail block)"
                    await run_to_completion(
                        memory.audit, turn, session, customer, "system", "turn_failed",
                        {"error": error},
                    )  # fmt: skip
                    if coded is None:
                        raise
                    coded.turn_id, coded.trace_id = turn, trace_id
                    root.set_attribute("agentbreeder.error_code", coded.code)
                    if coded.guardrail is not None:
                        try:
                            await self.blocked(root, coded, memory, turn, session, customer)
                        except Exception as unrecorded:  # the caller still gets MODEL_BLOCKED
                            print(
                                f"guardrail event failed: {type(unrecorded).__name__}", flush=True
                            )
                    print(
                        redaction.one_line(
                            f"model call failed: turn={turn} code={coded.code} "
                            f"status={upstream_status(failure)} detail={error}"
                        ),
                        flush=True,
                    )
                    raise coded from None
                finally:
                    self.mark_root(root, cost, trace)
            await run_to_completion(
                memory.audit, turn, session, customer, "agent", "reply_sent", {"chars": len(reply)}
            )
            fallbacks, served_by = turn_fallbacks(cost)
            # The trace first: tools can add keys to it, never replace the runtime's own fields.
            return {
                **{k: v for k, v in trace.items() if k != "evidence"},
                "reply": reply,
                "session_id": session,
                "customer_id": customer,
                "model": self.model_alias,
                "framework": "google-adk",
                "runtime": RUNTIME_REF,
                "turn_id": turn,
                "trace_id": trace_id,
                "cost_usd": turn_cost_usd(cost),
                "fallbacks": fallbacks,
                "served_by": served_by,
                "rate_limit_wait_s": round(cost["waited"], 3),
                **self.subagent_reply(entry["subagents"]),
                # Skills loaded this turn; no key for an agent without skills.
                **({"skills": skills.reply_entries(entry["skills"])} if self.skills else {}),
            }
        finally:
            TURN_COST.reset(cost_token)
            self.turns.pop(turn, None)
            try:
                telemetry.release(trace_id, blocked)
            except Exception as failure:  # an export failure never costs the turn its record
                print(f"span release failed: {type(failure).__name__}", flush=True)
            if blocked:
                # The gateway does not say which text matched, so the turn record keeps none
                # of the turn's text: not the message, not what tools or searches returned.
                message = BLOCKED_MESSAGE
                trace["sources"], trace["evidence"] = [], []
            try:
                await run_to_completion(
                    memory.record_turn,
                    turn=turn,
                    session=session,
                    customer=customer,
                    message=message,
                    reply=reply,
                    trace_id=trace_id,
                    span_id=span_id,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    model_calls=trace["model_calls"],
                    tools=trace["tools"],
                    sources=trace["sources"],
                    evidence=trace["evidence"],
                    retrieval=trace["retrieval"],
                    prompt_tokens=trace["prompt_tokens"],
                    output_tokens=trace["output_tokens"],
                    error=error,
                )
            except Exception as failure:  # never hide the turn's own error behind a log error
                print(f"turn record failed: {type(failure).__name__}", flush=True)
            finally:
                await run_to_completion(memory.close)

    def subagent_reply(self, used: dict) -> dict:
        """`subagents` for the reply: one entry per sub-agent that ran (not one that was only
        asked and refused), in first-call order. No key for an agent without sub-agents."""
        if not self.children:
            return {}
        ran = [usage for usage in used.values() if usage["calls"] > 0]
        return {"subagents": [subagents.reply_entry(usage) for usage in ran]}

    def mark_root(self, root, tally: dict, trace: dict) -> None:
        """The turn's fallbacks (#1039), cached prompt tokens (#1056) and unknown tool names
        (#1074) on its root span, for `traces` and the console."""
        for key, value in root_attributes(tally).items():
            root.set_attribute(key, value)
        if trace["model_calls"]:
            root.set_attribute("agentbreeder.cached_prompt_tokens", trace["cached_prompt_tokens"])
        if trace["unknown_tools"]:
            root.set_attribute("agentbreeder.unknown_tools", tuple(trace["unknown_tools"]))

    async def blocked(self, root, coded: ModelError, memory, turn, session, customer) -> None:
        """A gateway guardrail block: one `guardrail_blocked` event and the root span's
        guardrail attributes. The entity type and guardrail name only, never matched text."""
        entity, guardrail = coded.guardrail
        root.set_attribute("agentbreeder.guardrail.action", "blocked")
        root.set_attribute("agentbreeder.guardrail.name", guardrail)
        root.set_attribute("agentbreeder.guardrail.entity", entity)
        await run_to_completion(
            memory.audit, turn, session, customer, "system", "guardrail_blocked",
            {"guardrail": guardrail, "entity": entity, "action": "blocked",
             "code": coded.code, "model": self.model_alias},
        )  # fmt: skip

    async def run_turn(self, turn: str, session: str, customer: str, message: str) -> str:
        context = self.turns[turn]
        memory, trace = context["memory"], context["trace"]
        previous, new_session = await self.previous_customer_text(customer, session)
        parts = []
        if self.retriever.sets:
            # Retrieve before the model call. Short follow-ups ("my annual one") are searched
            # together with the previous customer message.
            query = " ".join(previous + [message])
            with span("retrieve_knowledge", "RETRIEVER", input=query) as retrieval:
                hits = await run_to_completion(self.retriever.search, memory.db, query, self.top_k)
                set_documents(retrieval, hits)
                trace["retrieval"] = hits[0]["mode"] if hits else "none"
                retrieval.set_attribute("retrieval.mode", trace["retrieval"])
            await run_to_completion(
                memory.audit, turn, session, customer, "agent", "knowledge_retrieved",
                {"mode": trace["retrieval"], "chunks": [h["id"] for h in hits]},
            )  # fmt: skip
            for h in hits:
                if h["title"] not in trace["sources"]:
                    trace["sources"].append(h["title"])
            trace["evidence"].extend(
                {"source": "kb", "title": h["title"], "text": h["text"]} for h in hits
            )
            passages = "\n\n".join(f"[{h['title']}] {h['text']}" for h in hits) or "No matches."
            parts.append(
                types.Part(text=f"<knowledge_base_results>\n{passages}\n</knowledge_base_results>")
            )
        if self.config.get("memory", {}).get("notes"):
            context["notes"] = await run_to_completion(memory.notes, customer)
            if new_session and context["notes"]:
                await run_to_completion(
                    memory.audit, turn, session, customer, "agent", "memory_recalled",
                    {"notes": len(context["notes"])},
                )  # fmt: skip
        parts.append(types.Part(text=message))
        content = types.Content(role="user", parts=parts)
        reply = ""
        try:
            async for event in self.runner.run_async(
                user_id=customer,
                session_id=session,
                invocation_id=turn,
                new_message=content,
                run_config=RunConfig(max_llm_calls=(self.turn_cap or self.max_steps) + 1),
            ):
                if event.author != "user" and event.content and event.is_final_response():
                    text = "".join(
                        p.text or "" for p in (event.content.parts or []) if not p.thought
                    ).strip()
                    if text:
                        reply = text
        except LlmCallsLimitExceededError:
            pass  # backstop only; the plugin already forces a reply on the last allowed call
        except Exception as failure:
            if not caused_by(failure, TurnUnknown):
                raise
            reply = ""  # a callback lost its turn: nothing it produced is trusted
            context["lost"] = True
        return reply or NO_ANSWER


class KnowledgeSearchTool(BaseTool):
    """Built-in search over the agent's knowledge sets, capped per turn."""

    def __init__(self, runtime: Runtime):
        about = runtime.retriever.description or "the knowledge base"
        super().__init__(
            name="search_knowledge_base",
            description=f"Search {about} Returns the best-matching passages with titles.",
        )
        self.runtime = runtime

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords taken from the customer's question.",
                    }
                },
                "required": ["query"],
            },
        )

    async def run_async(self, *, args, tool_context: ToolContext):
        turn = self.runtime.turn_for(tool_context)
        trace = turn["trace"]
        # Counted per agent. The parent already has the passages retrieved before its first
        # model call; a sub-agent has none, so it may always search once.
        child = self.runtime.child_of(tool_context)
        allowed = max(1, self.runtime.extra_searches) if child else self.runtime.extra_searches
        searched = turn["kb"].get(tool_context.agent_name if child else "", 0)
        if searched >= allowed:
            return "Search limit reached. Answer now from the passages you already have."
        turn["kb"][tool_context.agent_name if child else ""] = searched + 1
        trace["kb_searches"] += 1
        hits = await run_to_completion(
            self.runtime.retriever.search,
            turn["memory"].db,
            str(args.get("query", "")),
            self.runtime.top_k,
        )
        trace["sources"].extend(h["title"] for h in hits if h["title"] not in trace["sources"])
        trace["evidence"].extend(
            {"source": "kb", "title": h["title"], "text": h["text"]} for h in hits
        )
        return "\n\n".join(f"[{h['title']}] {h['text']}" for h in hits) or "No matching articles."


class AgentPlugin(BasePlugin):
    """Step cap, agent events, token accounting and OpenInference span attributes."""

    def __init__(self, runtime: Runtime, name: str):
        super().__init__(name=name)
        self.runtime = runtime

    async def _audit(self, context, actor: str, action: str, detail: dict) -> None:
        await self.runtime.audit(self.runtime.turn_for(context), actor, action, detail)

    async def before_model_callback(self, *, callback_context, llm_request):
        turn = self.runtime.turn_for(callback_context)
        child = self.runtime.child_of(callback_context)
        turn["trace"]["model_calls"] += 1
        cost = turn["cost"]
        cost["last"], cost["route"], cost["mark"] = None, None, cost["calls"]
        # The model client reads the tally from this variable. Set here, in the context the
        # model call runs in, so it is the caller's turn even in a task that began without it.
        TURN_COST.set(cost)
        step = turn["trace"]["model_calls"]
        # Caps, per turn: this agent's own count, and every agent's together. At either, the
        # call runs without tools, so the model must reply: a sub-agent returns its text and
        # the parent's next call is its last.
        agent = child.tool if child else ""
        own = turn["agent_calls"][agent] = turn["agent_calls"].get(agent, 0) + 1
        final = own >= (child.max_model_calls if child else self.runtime.max_steps)
        if self.runtime.turn_cap is not None and step >= self.runtime.turn_cap:
            final = True
        if child:
            turn["subagents"][child.name]["model_calls"] += 1
        if final:  # last allowed call: no tools, so the model must reply
            llm_request.config.tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE")
            )
        # The provider SDK's span is the LLM span with token counts; label this one CHAIN so
        # tokens are not counted twice.
        current = otel.get_current_span()
        current.set_attribute("openinference.span.kind", "CHAIN")
        current.set_attribute(
            "input.value",
            json.dumps({"step": step, "contents": len(llm_request.contents), "final": final}),
        )
        mark_subagent(current, child)
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        turn = self.runtime.turn_for(callback_context)
        usage = llm_response.usage_metadata
        prompt = (usage.prompt_token_count or 0) if usage else 0
        # ADK maps LiteLLM's prompt_tokens_details.cached_tokens (and the provider variants)
        # to cached_content_token_count; a part of `prompt`, not added to it.
        cached = min((usage.cached_content_token_count or 0) if usage else 0, prompt)
        output = (
            (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) if usage else 0
        )
        parts = (llm_response.content.parts or []) if llm_response.content else []
        visible = [p.model_dump(exclude_none=True, mode="json") for p in parts if not p.thought]
        set_output(otel.get_current_span(), visible)
        turn["trace"]["prompt_tokens"] += prompt
        turn["trace"]["output_tokens"] += output
        turn["trace"]["cached_prompt_tokens"] += cached
        child = self.runtime.child_of(callback_context)
        self._attribute_cost(turn, child)
        await self._audit(
            callback_context,
            "agent",
            "model_called",
            {
                "step": turn["trace"]["model_calls"],
                # The deployed agent, and the sub-agent when one made the call (1.5.0).
                "agent": self.runtime.app,
                **({"subagent": child.name} if child else {}),
                "model": child.alias if child else self.runtime.model_alias,
                "prompt_tokens": prompt,
                "cached_prompt_tokens": cached,
                "output_tokens": output,
                "finish_reason": str(llm_response.finish_reason or ""),
                "cost_usd": turn["cost"]["last"],
                # Which alias served the call and after how many gateway fallbacks (#1039).
                "served_by": (turn["cost"]["route"] or (None, None))[1],
                "fallbacks": (turn["cost"]["route"] or (None, None))[0],
            },
        )
        return None

    def _attribute_cost(self, turn: dict, child) -> None:
        """This model call's gateway cost, to the agent that made it. Model calls of a turn
        are sequential, so `last` and `route` are this call's. A call the gateway never
        answered through CostClient (a stub model) adds nothing."""
        cost = turn["cost"]
        if cost["calls"] == cost["mark"]:
            return
        bucket = cost["by_agent"].setdefault(
            child.name if child else "", {"usd": 0.0, "unknown": 0, "calls": 0}
        )
        usage = turn["subagents"][child.name] if child else None
        bucket["calls"] += 1
        if usage:
            usage["cost_calls"] += 1
        if cost["last"] is None:
            bucket["unknown"] += 1
            if usage:
                usage["cost_unknown"] += 1
        else:
            bucket["usd"] += cost["last"]
            if usage:
                usage["usd"] += cost["last"]
        group = (cost["route"] or (0, None))[1]
        if usage and group is not None and group not in usage["served_by"]:
            usage["served_by"].append(group)

    @staticmethod
    def _call_key(tool_context) -> tuple:
        """One tool call of a turn: the agent that made it and its function call id (two
        agents' models may issue the same id)."""
        return (tool_context.agent_name, tool_context.function_call_id)

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        turn = self.runtime.turn_for(tool_context)
        current = otel.get_current_span()
        if unknown_tool(tool):
            # Not a tool call (#1074): listed apart, and the span is not a TOOL span. ADK then
            # answers it through on_tool_error_callback and skips the after-tool callbacks.
            name = shown_tool_name(tool.name)
            unknown = turn["trace"]["unknown_tools"]
            if name not in unknown and len(unknown) < MAX_UNKNOWN_TOOLS:
                unknown.append(name)
            current.set_attribute("openinference.span.kind", "CHAIN")
            current.set_attribute("agentbreeder.unknown_tool", name)
            return None
        child = self.runtime.child_of(tool_context)
        turn["trace"]["tools"].append(tool.name)  # every agent's calls, unqualified
        if child:
            turn["subagents"][child.name]["tools"].append(tool.name)
        turn["started"][self._call_key(tool_context)] = time.monotonic()
        turn["recorded"][self._call_key(tool_context)] = {
            key: len(value) for key, value in turn["trace"].items() if isinstance(value, list)
        }
        # The sub-agent's own spans, and the parent's `ask_<name>` call that starts it.
        mark_subagent(current, child or getattr(tool, "child", None))
        current.set_attribute("openinference.span.kind", "TOOL")
        current.set_attribute("tool.name", tool.name)
        current.set_attribute("tool.parameters", json.dumps(tool_args, default=str))
        current.set_attribute("input.value", json.dumps(tool_args, default=str))
        if skill_tool(tool):
            return self._skill_gate(turn, tool, tool_args, tool_context, current)
        return None

    def _skill_call(self, tool, tool_args) -> dict:
        """Which skill (and reference file) a load names, as validated values: `skill`,
        `version`, `sha256` when the skill is one of the agent's, and `resource` when the
        path is one of that skill's reference files. Never anything the model made up."""
        name = tool_args.get("skill_name")
        known = self.runtime.skill_index.get(name) if isinstance(name, str) else None
        if known is None:
            return {}
        found = {"skill": known.name, "version": known.version, "sha256": known.sha256}
        if tool.name == skills.LOAD_RESOURCE:
            reference = skills.reference_name(tool_args.get("file_path"))
            if reference in known.references:
                found["resource"] = reference
        return found

    def _skill_gate(self, turn, tool, tool_args, tool_context, current) -> dict | None:
        """The runtime's rules for a skill load, before ADK's tool runs: the per-turn caps,
        "already loaded", and the reference path rule. A dict answers the model instead of
        the tool."""
        usage = turn["skills"]
        call = self._skill_call(tool, tool_args)
        wrong = [key for key in ("skill_name", "file_path") if key in tool_args
                 and not isinstance(tool_args[key], str)]  # fmt: skip
        if wrong:
            return skill_refusal("INVALID_ARGUMENTS", f"{wrong[0]} must be text.")
        for key, value in call.items():
            current.set_attribute(
                "agentbreeder.skill." + ("name" if key == "skill" else key), value
            )
        if tool.name == skills.LOAD:
            usage["loads"] += 1
            if usage["loads"] > skills.MAX_LOADS:
                return skill_refusal(
                    skills.LOAD_LIMIT,
                    f"At most {skills.MAX_LOADS} skills can be loaded in a turn.",
                )
            # Only for the text this session already holds: after a redeploy with another
            # version of the skill, the hash differs and the new body is returned.
            active = self.runtime.skill_toolset.list_active_skills(tool_context)
            same = call and skills.loaded_hash(tool_context.state, call["skill"]) == call["sha256"]
            if same and call["skill"] in active:
                return {"skill_name": call["skill"], "status": skills.ALREADY_LOADED}
            return None
        usage["resource_loads"] += 1
        if usage["resource_loads"] > skills.MAX_RESOURCE_LOADS:
            return skill_refusal(
                skills.LOAD_LIMIT,
                f"At most {skills.MAX_RESOURCE_LOADS} skill files can be read in a turn.",
            )
        path = tool_args.get("file_path")
        if isinstance(path, str) and path and skills.reference_name(path) is None:
            return skill_refusal(
                "INVALID_RESOURCE_PATH",
                "Only a skill's reference files can be read: references/<file name>.",
            )
        return None

    async def _skill_result(self, turn, tool, tool_args, tool_context, result, detail) -> None:
        """A skill load's span output, row fields and `skill_loaded` event: the skill's name,
        version and hash and the file's name, never the text that was loaded."""
        loaded = result if isinstance(result, dict) else {}
        content = loaded.get("instructions" if tool.name == skills.LOAD else "content")
        set_output(otel.get_current_span(), {"chars": len(content or "")})
        call = self._skill_call(tool, tool_args)
        if loaded.get("error"):
            code = loaded.get("error_code")
            detail["ok"] = False
            detail["error"] = code if isinstance(code, str) and code.isupper() else "SKILL_ERROR"
            if "resource" in call:
                del call["resource"]
        detail.update(call)
        if loaded.get("status") == skills.ALREADY_LOADED:
            detail["already_loaded"] = True
        detail["args"] = skill_args(tool_args)
        if not call or not isinstance(content, str):
            return
        if tool.name == skills.LOAD:
            skills.remember_loaded(tool_context.state, call["skill"], call["sha256"])
        entry = turn["skills"]["loaded"].setdefault(
            call["skill"], {"version": call["version"], "resources": []}
        )
        if "resource" in call and call["resource"] not in entry["resources"]:
            entry["resources"].append(call["resource"])
        await self._audit(
            tool_context, "agent", "skill_loaded", {**call, "agent": self.runtime.app}
        )

    def _call_detail(self, tool, tool_args, tool_context) -> dict:
        """What every tool row carries: args, agent, registry tool or MCP server, latency."""
        detail = {"args": tool_args, "agent": self.runtime.app}
        child = self.runtime.child_of(tool_context)
        if child:
            detail["subagent"] = child.name
        spec = getattr(tool, "spec", None)
        if spec is not None:
            detail["tool"] = f"tool/{spec.tool}@{spec.version}"
        mcp_ref = getattr(tool, "agentbreeder_mcp", None)
        if mcp_ref is not None:
            detail["mcp"] = mcp_ref
        turn = self.runtime.turn_for(tool_context)
        started = turn["started"].pop(self._call_key(tool_context), None)
        if started is not None:
            detail["latency_ms"] = int((time.monotonic() - started) * 1000)
        return detail

    def _recorded(self, tool, tool_context) -> dict:
        """What the tool added to the turn (tickets, request IDs …) for the event detail, without
        RUNTIME_KEYS."""
        turn = self.runtime.turn_for(tool_context)
        before = turn["recorded"].pop(self._call_key(tool_context), {})
        own = turn["added"].pop(self._call_key(tool_context), {})
        if isinstance(tool, subagents.SubAgentTool):
            return {}  # what the sub-agent's tools recorded is on their own rows, not this one
        if self.runtime.children:
            # With sub-agents, tools of several agents run inside one another's calls, so
            # the lists cannot be told apart by position: only what this call recorded itself.
            return {key: list(value) for key, value in own.items() if key not in RUNTIME_KEYS}
        added = {}
        for key, value in turn["trace"].items():
            if isinstance(value, list) and key not in RUNTIME_KEYS:
                new = value[before.get(key, 0) :]
                if new:
                    added[key] = new
        return added

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        turn = self.runtime.turn_for(tool_context)
        if not skill_tool(tool):  # a skill's text is never a span attribute (_skill_result)
            set_output(otel.get_current_span(), as_text(result))
        if self._call_key(tool_context) in turn["failed"]:
            # ADK also runs this callback with the error callback's result; that call is
            # already audited as failed (runtime 1.1.0 wrote a second, "ok" row here).
            turn["failed"].discard(self._call_key(tool_context))
            return None
        spec = getattr(tool, "spec", None)
        # What the tool recorded first, so it cannot overwrite the runtime's own fields.
        detail = {
            **self._recorded(tool, tool_context),
            **self._call_detail(tool, tool_args, tool_context),
            "ok": True,
            "error": None,
        }
        if getattr(tool, "agentbreeder_mcp", None) is not None:
            # ADK returns MCP failures (relay refusals included) as results, never raises.
            failed = mcp_client.failure(result)
            if failed:
                detail["ok"], detail["error"] = False, redact(failed)[:300]
        if skill_tool(tool):
            await self._skill_result(turn, tool, tool_args, tool_context, result, detail)
        if spec is not None and spec.writes_requests:
            detail["writes_request"] = True
            child = self.runtime.child_of(tool_context)
            if child:
                # Which request of the parent's this sub-agent was answering when it filed
                # the row: a hash, never the text.
                detail["request_sha256"] = turn["subagents"][child.name]["request_sha256"]
        await self._audit(tool_context, "agent", f"tool:{tool.name}", detail)
        return None

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        """Return tool errors to the model instead of failing the turn."""
        turn = self.runtime.turn_for(tool_context)
        if unknown_tool(tool):
            # One `tool_unknown` event (not a `tool:` row, so tool logs don't count it as a
            # call) and a fixed answer to the model, without ADK's list of the agent's tools.
            name = shown_tool_name(tool.name)
            otel.get_current_span().set_attribute("tool.error", UNKNOWN_TOOL_ERROR)
            await self._audit(
                tool_context, "agent", "tool_unknown",
                {"name": name, "agent": self.runtime.app, "unknown": True, "ok": False,
                 "error": UNKNOWN_TOOL_ERROR,
                 **({"subagent": child.name} if (child := self.runtime.child_of(tool_context))
                    else {})},
            )  # fmt: skip
            return {"error": UNKNOWN_TOOL_REPLY}
        if isinstance(tool, subagents.SubAgentTool) and model_failure(error) is not None:
            # A sub-agent's failed model call ends the whole turn with its code, as the
            # parent's does: not answered here, so ADK raises it out of the turn.
            self._recorded(tool, tool_context)
            detail = self._call_detail(tool, tool_args, tool_context)
            detail.update(ok=False, error="the sub-agent's model call failed")
            await self._audit(tool_context, "agent", f"tool:{tool.name}", detail)
            return None
        message = redact(str(error))[:300]
        otel.get_current_span().set_attribute("tool.error", message)
        self._recorded(tool, tool_context)
        detail = self._call_detail(tool, tool_args, tool_context)
        detail.update(ok=False, error=message)
        if skill_tool(tool):
            detail["args"] = skill_args(tool_args)
        turn["failed"].add(self._call_key(tool_context))
        await self._audit(tool_context, "agent", f"tool:{tool.name}", detail)
        return {"error": message}

    async def before_agent_callback(self, *, agent, callback_context):
        current = otel.get_current_span()
        current.set_attribute("openinference.span.kind", "AGENT")
        mark_subagent(current, self.runtime.child_of(callback_context))
        return None


def skill_tool(tool) -> bool:
    """ADK's `load_skill` or `load_skill_resource` (no other tool may have those names)."""
    return isinstance(tool, (LoadSkillTool, LoadSkillResourceTool))


def skill_args(tool_args) -> dict:
    """A skill load's arguments as an audit row keeps them: bounded text, with an exact copy of
    any loaded skill text replaced (a model can be steered to copy that into an argument)."""
    return {
        key: telemetry.withheld(value)[:100] if isinstance(value, str) else "?"
        for key, value in (tool_args if isinstance(tool_args, dict) else {}).items()
        if key in ("skill_name", "file_path")
    }


def skill_refusal(code: str, message: str) -> dict:
    """The runtime's answer to a skill load it refuses, in the shape of ADK's own errors."""
    return {"error": message, "error_code": code}


def mark_subagent(current, child) -> None:
    """`agentbreeder.subagent` on a span that belongs to a sub-agent (its agent, model and
    tool spans, and the parent's `ask_<name>` call)."""
    if child is not None:
        current.set_attribute("agentbreeder.subagent", child.name)


def model_failure(failure):
    """The failed model call in an exception's cause chain, or None."""
    seen = 0
    while failure is not None and seen < 20:
        if upstream_status(failure) is not None:
            return failure
        failure, seen = failure.__cause__ or failure.__context__, seen + 1
    return None


def unknown_tool(tool) -> bool:
    """ADK's stand-in for a tool name the model invented (no tool of the agent has that name)."""
    return type(tool) is BaseTool and tool.description == TOOL_NOT_FOUND


def shown_tool_name(name) -> str:
    """A model-chosen tool name as it may be shown and stored: at most 64 characters of
    [A-Za-z0-9_.-] (anything else is "?"), redacted."""
    text = name if isinstance(name, str) else ""
    return redact(SHOWN_TOOL_NAME.sub("?", text[:64])) or "?"


def sessions_url(database_url: str) -> str:
    """The agent's own database with ADK's schema first on the search path, async psycopg."""
    parts = urlsplit(database_url)
    # Keep the query string (sslmode, connect_timeout, ...): dropping it would silently downgrade
    # the connection settings the operator configured. Server options are kept too.
    query = parse_qsl(parts.query, keep_blank_values=True)
    options = [value for key, value in query if key == "options"]
    query = [(key, value) for key, value in query if key != "options"]
    query.append(("options", " ".join([*options, f"-csearch_path={cfg.sessions_schema()}"])))
    return urlunsplit(("postgresql+psycopg", parts.netloc, parts.path, urlencode(query), ""))


def ensure_sessions_schema(database_url: str) -> None:
    """Create ADK's schema in the agent's database unless it exists (the database owner may
    pre-create it for a role that can't create schemas)."""
    with psycopg.connect(database_url, connect_timeout=5, autocommit=True) as db:
        exists = db.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (cfg.sessions_schema(),)
        ).fetchone()
        if exists:
            return
        db.execute(
            psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                psycopg.sql.Identifier(cfg.sessions_schema())
            )
        )


# -- process singleton, used by server.py ----------------------------------------------------
_RUNTIME: Runtime | None = None
_LOCK = threading.Lock()
LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, name="adk-loop", daemon=True).start()


def runtime() -> Runtime:
    global _RUNTIME
    with _LOCK:
        if _RUNTIME is None:
            config = cfg.config()
            telemetry.setup(
                config["name"],
                cfg.service("traces_url"),
                enabled=config.get("traces", "otlp") == "otlp",
            )
            _RUNTIME = Runtime(config, cfg.secret("DATABASE_URL"), cfg.secret("LLM_GATEWAY_KEY"))
        return _RUNTIME


def chat(session: str, customer: str, message: str) -> dict:
    """Run one customer turn (called from HTTP handler threads)."""
    with INFLIGHT_LOCK:
        if session in INFLIGHT:
            raise SessionBusy(session)
        INFLIGHT.add(session)
    finished = threading.Event()

    async def turn():
        try:
            return await runtime().chat_async(session, customer, message)
        finally:
            # Runs only once the coroutine has really unwound (in-flight tool and DB work
            # included); until then the session stays busy even if chat() gave up waiting.
            with INFLIGHT_LOCK:
                INFLIGHT.discard(session)
            finished.set()

    try:
        future = asyncio.run_coroutine_threadsafe(turn(), LOOP)
    except BaseException:
        with INFLIGHT_LOCK:
            INFLIGHT.discard(session)
        raise
    try:
        return future.result(timeout=TURN_TIMEOUT)
    except concurrent.futures.TimeoutError:
        # Cancel the turn and wait for the coroutine itself to stop (not just the cross-thread
        # future, which reports cancelled at once) before the caller releases the session lock,
        # so a retry cannot run alongside it. Tool and DB work runs via run_to_completion, so
        # an in-flight tool write finishes before the turn counts as stopped.
        future.cancel()
        finished.wait(CANCEL_GRACE)
        raise
