"""AgentBreeder's maintained Google ADK runtime (runtime/adk@1.0.0), driven by agent.config.json.

  LlmAgent (LiteLLM gateway alias)  +  Runner  +  DatabaseSessionService (Postgres)
  tools:   search_knowledge_base (built in, when knowledge is configured) + registry tools
  plugin:  AgentPlugin - step cap (last model call has tools off), agent events, token
           accounting, tool errors returned to the model, OpenInference span attributes

Retrieval-augmented generation runs before the model call: the top knowledge passages are added
to the customer's message. Each turn is recorded in `turns`. The customer is always the Runner's
user_id (set server-side from the request), never a value the model chooses.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
import warnings
from urllib.parse import urlsplit, urlunsplit

import config as cfg
import telemetry
from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.run_config import RunConfig
from google.adk.models.lite_llm import LiteLlm
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
from tools import RegistryTool, ToolRuntime, load_functions

warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

TURN_TIMEOUT = 170
REASONING = {"none": None, "low": "low", "medium": "medium", "high": "high"}


class ModelError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


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
                **extra,
            ),
            instruction=self.instruction,
            tools=tools,
        )
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
        memory = await asyncio.to_thread(Memory, self.database_url, self.events)
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
        }
        self.turns[turn] = {
            "memory": memory,
            "customer": customer,
            "session": session,
            "trace": trace,
            "notes": [],
            "recorded": {},
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
                },
            ) as root:
                trace_id, span_id = ids(root)
                await asyncio.to_thread(
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
                    error = f"{type(failure).__name__}: {failure}"[:500]
                    await asyncio.to_thread(
                        memory.audit, turn, session, customer, "system", "turn_failed",
                        {"error": error},
                    )  # fmt: skip
                    if isinstance(failure, genai_errors.APIError):
                        raise ModelError(failure.code or 502, failure.message or "") from failure
                    status = getattr(failure, "status_code", None)
                    if type(failure).__module__.startswith("litellm") and isinstance(status, int):
                        raise ModelError(status, str(failure)[:300]) from failure
                    raise
            await asyncio.to_thread(
                memory.audit, turn, session, customer, "agent", "reply_sent", {"chars": len(reply)}
            )
            return {
                "reply": reply,
                "session_id": session,
                "customer_id": customer,
                "model": self.model_alias,
                "framework": "google-adk",
                "runtime": "runtime/adk@1.0.0",
                "turn_id": turn,
                "trace_id": trace_id,
                **{k: v for k, v in trace.items() if k != "evidence"},
            }
        finally:
            self.turns.pop(turn, None)
            try:
                await asyncio.to_thread(
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
                await asyncio.to_thread(memory.close)

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
                hits = await asyncio.to_thread(self.retriever.search, memory.db, query, self.top_k)
                set_documents(retrieval, hits)
                trace["retrieval"] = hits[0]["mode"] if hits else "none"
                retrieval.set_attribute("retrieval.mode", trace["retrieval"])
            await asyncio.to_thread(
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
            context["notes"] = await asyncio.to_thread(memory.notes, customer)
            if new_session and context["notes"]:
                await asyncio.to_thread(
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
        hits = await asyncio.to_thread(
            self.runtime.retriever.search, turn["memory"].db, str(args.get("query", ""))
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
            await asyncio.to_thread(
                turn["memory"].audit, context.invocation_id, turn["session"], turn["customer"],
                actor, action, detail,
            )  # fmt: skip

    async def before_model_callback(self, *, callback_context, llm_request):
        turn = self.runtime.turns.get(callback_context.invocation_id)
        if not turn:
            return None
        turn["trace"]["model_calls"] += 1
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
        output = (
            (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) if usage else 0
        )
        parts = (llm_response.content.parts or []) if llm_response.content else []
        visible = [p.model_dump(exclude_none=True, mode="json") for p in parts if not p.thought]
        set_output(otel.get_current_span(), visible)
        if turn:
            turn["trace"]["prompt_tokens"] += prompt
            turn["trace"]["output_tokens"] += output
            await self._audit(
                callback_context,
                "agent",
                "model_called",
                {
                    "step": turn["trace"]["model_calls"],
                    "model": self.runtime.model_alias,
                    "prompt_tokens": prompt,
                    "output_tokens": output,
                    "finish_reason": str(llm_response.finish_reason or ""),
                },
            )
        return None

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        turn = self.runtime.turns.get(tool_context.invocation_id)
        if turn:
            turn["trace"]["tools"].append(tool.name)
            turn["recorded"][tool_context.function_call_id] = {
                key: len(value) for key, value in turn["trace"].items() if isinstance(value, list)
            }
        current = otel.get_current_span()
        current.set_attribute("openinference.span.kind", "TOOL")
        current.set_attribute("tool.name", tool.name)
        current.set_attribute("tool.parameters", json.dumps(tool_args, default=str))
        current.set_attribute("input.value", json.dumps(tool_args, default=str))
        return None

    def _recorded(self, tool_context) -> dict:
        """What the tool added to the turn (tickets, request IDs …) for the event detail."""
        turn = self.runtime.turns.get(tool_context.invocation_id)
        if not turn:
            return {}
        before = turn["recorded"].pop(tool_context.function_call_id, {})
        added = {}
        for key, value in turn["trace"].items():
            if isinstance(value, list) and key not in ("tools", "evidence", "sources"):
                new = value[before.get(key, 0) :]
                if new:
                    added[key] = new
        return added

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        set_output(otel.get_current_span(), as_text(result))
        spec = getattr(tool, "spec", None)
        detail = {"args": tool_args, "ok": True, "error": None, **self._recorded(tool_context)}
        if spec is not None:
            detail["tool"] = f"tool/{spec.tool}@{spec.version}"
            if spec.writes_requests:
                detail["writes_request"] = True
        await self._audit(tool_context, "agent", f"tool:{tool.name}", detail)
        return None

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        """Return tool errors to the model instead of failing the turn."""
        otel.get_current_span().set_attribute("tool.error", str(error)[:300])
        self._recorded(tool_context)
        await self._audit(
            tool_context, "agent", f"tool:{tool.name}",
            {"args": tool_args, "ok": False, "error": str(error)[:300]},
        )  # fmt: skip
        return {"error": str(error)[:300]}

    async def before_agent_callback(self, *, agent, callback_context):
        otel.get_current_span().set_attribute("openinference.span.kind", "AGENT")
        return None


def sessions_url(database_url: str) -> str:
    """Same Postgres server, the sessions database, async psycopg driver."""
    parts = urlsplit(database_url)
    # Keep the query string (sslmode, connect_timeout, ...): dropping it would silently downgrade
    # the connection settings the operator configured.
    return urlunsplit(
        ("postgresql+psycopg", parts.netloc, "/" + cfg.sessions_database(), parts.query, "")
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
    future = asyncio.run_coroutine_threadsafe(
        runtime().chat_async(session, customer, message), LOOP
    )
    return future.result(timeout=TURN_TIMEOUT)
