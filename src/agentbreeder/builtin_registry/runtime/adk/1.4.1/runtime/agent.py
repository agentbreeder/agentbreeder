"""AgentBreeder's maintained Google ADK runtime (runtime/adk@1.4.1), driven by agent.config.json.

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
           A gateway guardrail block is MODEL_BLOCKED, with a guardrail_blocked event (1.4.0)
  usage:   cached prompt tokens, as the provider reported them through the gateway (1.4.0)
  tools:   a tool name the model invented is refused and listed under `unknown_tools`, never
           as a tool call (1.4.0)

Retrieval-augmented generation runs before the model call: the top knowledge passages are added
to the customer's message. Each turn is recorded in `turns`. The customer is always the Runner's
user_id (set server-side from the request), never a value the model chooses.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
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
from google.adk.tools.tool_context import ToolContext
from google.genai import errors as genai_errors
from google.genai import types
from memory import Memory
from opentelemetry import trace as otel
from rag import Embedder, KnowledgeSet, Retriever
from telemetry import as_text, ids, set_documents, set_output, span
from tools import RegistryTool, ToolRuntime, load_functions, run_to_completion

warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

TURN_TIMEOUT = 170
CANCEL_GRACE = 120  # seconds to wait for a cancelled turn (and any in-flight tool) to stop
REASONING = {"none": None, "low": "low", "medium": "medium", "high": "high"}


RUNTIME_REF = "runtime/adk@1.4.1"


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

    def __init__(self, config: dict, database_url: str, gateway_key: str, model=None):
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
        self.functions = load_functions(config.get("tools") or [], cfg.artifact)
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
        reasoning = REASONING[config["model"].get("reasoning", "low")]
        extra = {"reasoning_effort": reasoning} if reasoning else {}
        self.agent = LlmAgent(
            name=re.sub(r"[^a-zA-Z0-9_]", "_", self.app),
            description=config.get("description", ""),
            # "openai/" = the gateway's OpenAI-compatible API; it maps the alias to providers.
            model=model
            or LiteLlm(
                model=f"openai/{self.model_alias}",
                api_base=cfg.gateway_url(),
                api_key=gateway_key,
                timeout=90,
                llm_client=CostClient(),
                **MODEL_ARGS,
                **extra,
            ),
            instruction=self.instruction,
            tools=[*tools, *self.mcp_toolsets],
        )
        ensure_sessions_schema(database_url)
        self.sessions = DatabaseSessionService(
            db_url=sessions_url(database_url), pool_pre_ping=True
        )
        self.runner = Runner(
            app_name=self.app,
            agent=self.agent,
            session_service=self.sessions,
            plugins=[AgentPlugin(self, name="agentbreeder_runtime")],
            auto_create_session=True,
        )

    # -- MCP ---------------------------------------------------------------------------------

    async def mcp_unavailable(self, server, context, failed: bool) -> bool:
        """Whether `server` already failed in this turn; records the first failure (trace and
        one audit event) so the turn's later model steps don't reconnect or audit again."""
        turn_id = getattr(context, "invocation_id", None)
        turn = self.turns.get(turn_id)
        if turn is None:
            return False
        down = turn["trace"]["mcp_unavailable"]
        if server.name in down:
            return True
        if failed:
            down.append(server.name)
            await run_to_completion(
                turn["memory"].audit, turn_id, turn["session"], turn["customer"], "system",
                "mcp_unavailable", {"server": server.name, "mcp": server.ref},
            )  # fmt: skip
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
        turn = self.turns[tool_context.invocation_id]
        return ToolRuntime(
            customer=tool_context.user_id,
            session=turn["session"],
            turn=tool_context.invocation_id,
            db=turn["memory"].db,
            memory=turn["memory"],
            config=self.config,
            services=self.services,
            trace=turn["trace"],
        )

    async def instruction(self, context: ReadonlyContext) -> str:
        """System prompt + this customer's saved notes (long-term memory across sessions)."""
        notes = self.turns.get(context.invocation_id, {}).get("notes") or []
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
        self.turns[turn] = {
            "memory": memory,
            "customer": customer,
            "session": session,
            "trace": trace,
            "notes": [],
            "recorded": {},
            "started": {},  # function_call_id -> monotonic start, for detail.latency_ms
            "failed": set(),  # function_call_ids already audited by on_tool_error_callback
            "cost": cost,
        }
        reply, error, trace_id, span_id = "", None, None, None
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
                except Exception as failure:
                    # The detail stays here: the turn record, its audit event and one log line.
                    error = redaction.upstream(f"{type(failure).__name__}: {failure}")[:500]
                    await run_to_completion(
                        memory.audit, turn, session, customer, "system", "turn_failed",
                        {"error": error},
                    )  # fmt: skip
                    coded = classify(failure)
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
            }
        finally:
            TURN_COST.reset(cost_token)
            self.turns.pop(turn, None)
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
                run_config=RunConfig(max_llm_calls=self.max_steps + 1),
            ):
                if event.author != "user" and event.content and event.is_final_response():
                    text = "".join(
                        p.text or "" for p in (event.content.parts or []) if not p.thought
                    ).strip()
                    if text:
                        reply = text
        except LlmCallsLimitExceededError:
            pass  # backstop only; the plugin already forces a reply on the last allowed call
        return reply or "Sorry, I couldn't put an answer together. Could you rephrase that?"


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
        turn = self.runtime.turns[tool_context.invocation_id]
        trace = turn["trace"]
        if trace["kb_searches"] >= self.runtime.extra_searches:
            return "Search limit reached. Answer now from the passages you already have."
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
        turn = self.runtime.turns.get(context.invocation_id)
        if turn:
            await run_to_completion(
                turn["memory"].audit, context.invocation_id, turn["session"], turn["customer"],
                actor, action, detail,
            )  # fmt: skip

    async def before_model_callback(self, *, callback_context, llm_request):
        turn = self.runtime.turns.get(callback_context.invocation_id)
        if not turn:
            return None
        turn["trace"]["model_calls"] += 1
        turn["cost"]["last"] = None
        turn["cost"]["route"] = None
        step = turn["trace"]["model_calls"]
        final = step >= self.runtime.max_steps
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
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        turn = self.runtime.turns.get(callback_context.invocation_id)
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
        if turn:
            turn["trace"]["prompt_tokens"] += prompt
            turn["trace"]["output_tokens"] += output
            turn["trace"]["cached_prompt_tokens"] += cached
            await self._audit(
                callback_context,
                "agent",
                "model_called",
                {
                    "step": turn["trace"]["model_calls"],
                    "model": self.runtime.model_alias,
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

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        turn = self.runtime.turns.get(tool_context.invocation_id)
        current = otel.get_current_span()
        if unknown_tool(tool):
            # Not a tool call (#1074): listed apart, and the span is not a TOOL span. ADK then
            # answers it through on_tool_error_callback and skips the after-tool callbacks.
            name = shown_tool_name(tool.name)
            if turn:
                unknown = turn["trace"]["unknown_tools"]
                if name not in unknown and len(unknown) < MAX_UNKNOWN_TOOLS:
                    unknown.append(name)
            current.set_attribute("openinference.span.kind", "CHAIN")
            current.set_attribute("agentbreeder.unknown_tool", name)
            return None
        if turn:
            turn["trace"]["tools"].append(tool.name)
            turn["started"][tool_context.function_call_id] = time.monotonic()
            turn["recorded"][tool_context.function_call_id] = {
                key: len(value) for key, value in turn["trace"].items() if isinstance(value, list)
            }
        current.set_attribute("openinference.span.kind", "TOOL")
        current.set_attribute("tool.name", tool.name)
        current.set_attribute("tool.parameters", json.dumps(tool_args, default=str))
        current.set_attribute("input.value", json.dumps(tool_args, default=str))
        return None

    def _call_detail(self, tool, tool_args, tool_context) -> dict:
        """What every tool row carries: args, agent, registry tool or MCP server, latency."""
        detail = {"args": tool_args, "agent": self.runtime.app}
        spec = getattr(tool, "spec", None)
        if spec is not None:
            detail["tool"] = f"tool/{spec.tool}@{spec.version}"
        mcp_ref = getattr(tool, "agentbreeder_mcp", None)
        if mcp_ref is not None:
            detail["mcp"] = mcp_ref
        turn = self.runtime.turns.get(tool_context.invocation_id)
        started = turn["started"].pop(tool_context.function_call_id, None) if turn else None
        if started is not None:
            detail["latency_ms"] = int((time.monotonic() - started) * 1000)
        return detail

    def _recorded(self, tool_context) -> dict:
        """What the tool added to the turn (tickets, request IDs …) for the event detail, without
        RUNTIME_KEYS."""
        turn = self.runtime.turns.get(tool_context.invocation_id)
        if not turn:
            return {}
        before = turn["recorded"].pop(tool_context.function_call_id, {})
        added = {}
        for key, value in turn["trace"].items():
            if isinstance(value, list) and key not in RUNTIME_KEYS:
                new = value[before.get(key, 0) :]
                if new:
                    added[key] = new
        return added

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        set_output(otel.get_current_span(), as_text(result))
        turn = self.runtime.turns.get(tool_context.invocation_id)
        if turn and tool_context.function_call_id in turn["failed"]:
            # ADK also runs this callback with the error callback's result; that call is
            # already audited as failed (runtime 1.1.0 wrote a second, "ok" row here).
            turn["failed"].discard(tool_context.function_call_id)
            return None
        spec = getattr(tool, "spec", None)
        # What the tool recorded first, so it cannot overwrite the runtime's own fields.
        detail = {
            **self._recorded(tool_context),
            **self._call_detail(tool, tool_args, tool_context),
            "ok": True,
            "error": None,
        }
        if getattr(tool, "agentbreeder_mcp", None) is not None:
            # ADK returns MCP failures (relay refusals included) as results, never raises.
            failed = mcp_client.failure(result)
            if failed:
                detail["ok"], detail["error"] = False, redact(failed)[:300]
        if spec is not None and spec.writes_requests:
            detail["writes_request"] = True
        await self._audit(tool_context, "agent", f"tool:{tool.name}", detail)
        return None

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        """Return tool errors to the model instead of failing the turn."""
        if unknown_tool(tool):
            # One `tool_unknown` event (not a `tool:` row, so tool logs don't count it as a
            # call) and a fixed answer to the model, without ADK's list of the agent's tools.
            name = shown_tool_name(tool.name)
            otel.get_current_span().set_attribute("tool.error", UNKNOWN_TOOL_ERROR)
            await self._audit(
                tool_context, "agent", "tool_unknown",
                {"name": name, "agent": self.runtime.app, "unknown": True, "ok": False,
                 "error": UNKNOWN_TOOL_ERROR},
            )  # fmt: skip
            return {"error": UNKNOWN_TOOL_REPLY}
        message = redact(str(error))[:300]
        otel.get_current_span().set_attribute("tool.error", message)
        self._recorded(tool_context)
        detail = self._call_detail(tool, tool_args, tool_context)
        detail.update(ok=False, error=message)
        turn = self.runtime.turns.get(tool_context.invocation_id)
        if turn:
            turn["failed"].add(tool_context.function_call_id)
        await self._audit(tool_context, "agent", f"tool:{tool.name}", detail)
        return {"error": message}

    async def before_agent_callback(self, *, agent, callback_context):
        otel.get_current_span().set_attribute("openinference.span.kind", "AGENT")
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
