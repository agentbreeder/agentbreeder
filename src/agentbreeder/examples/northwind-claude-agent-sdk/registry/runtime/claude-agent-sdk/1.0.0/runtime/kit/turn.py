"""One customer turn, the same for every framework (spec §7.1 `turn.py`, §7.5).

Taken from `runtime/adk@1.3.0`'s `Runtime.chat_async` and `run_turn`: a turn id, the trace
dict, the root `agent_turn` span, retrieval before the model call (the knowledge block in the
customer's message), the customer's notes in the system prompt, the `message_received`,
`knowledge_retrieved`, `memory_recalled`, `turn_failed` and `reply_sent` events, the reply and
the `turns` row.

What differs from 1.3.0, by design:
- History is the (customer, session) conversation's user and assistant text, read back from
  `turns` and given to the framework as structured messages (§7.5). ADK replayed its whole event
  history, tool calls and knowledge blocks included.
- Model calls go through the proxy (`proxy.py`); the turn's model counters, cost and result rules
  come from the proxy's state for the turn (`proxy.decide`).
- A model error's log line names the code and status only, never the gateway's text, which goes
  (redacted) to the turn record and its `turn_failed` event.
"""

from __future__ import annotations

import time
import uuid

import httpx

from . import config as cfg
from . import errors, redaction
from .adapter import Adapter, Turn
from .context import CURRENT, TOKEN, TurnContext, run_to_completion
from .errors import ModelError
from .memory import Memory
from .proxy import REASONING, ModelProxy, TurnAuth, TurnState, decide
from .rag import Embedder, KnowledgeSet, Retriever
from .telemetry import ids, set_documents, set_output, span
from .tools import KitFunction, load_functions, registry_function

KNOWLEDGE_TOOL = "search_knowledge_base"
FALLBACK_REPLY = "Sorry, I couldn't put an answer together. Could you rephrase that?"
# The framework's own client timeout is above the turn, so the proxy's and the turn's fire
# first (spec §7.2.3).
CLIENT_TIMEOUT = errors.TURN_TIMEOUT + 30


def knowledge_function(kit: Kit) -> KitFunction:
    """`search_knowledge_base`: further searches over the agent's knowledge, at most
    `extra_searches` per turn (1.3.0's KnowledgeSearchTool)."""
    about = kit.retriever.description or "the knowledge base"

    async def invoke(turn: TurnContext, args: dict, added: dict):
        trace = turn.trace
        if trace["kb_searches"] >= kit.extra_searches:
            return "Search limit reached. Answer now from the passages you already have."
        trace["kb_searches"] += 1
        hits = await run_to_completion(
            kit.retriever.search, turn.memory.db, str(args.get("query", "")), kit.top_k
        )
        trace["sources"].extend(h["title"] for h in hits if h["title"] not in trace["sources"])
        trace["evidence"].extend(
            {"source": "kb", "title": h["title"], "text": h["text"]} for h in hits
        )
        return "\n\n".join(f"[{h['title']}] {h['text']}" for h in hits) or "No matching articles."

    return KitFunction(
        name=KNOWLEDGE_TOOL,
        description=f"Search {about} Returns the best-matching passages with titles.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords taken from the customer's question.",
                }
            },
            "required": ["query"],
        },
        invoke=invoke,
    )


def new_trace() -> dict:
    return {
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
        "mcp_unavailable": [],  # kit 1.0.0 has no MCP client; always empty
    }


class Kit:
    """Everything built from one agent.config.json, for one framework adapter."""

    def __init__(
        self,
        config: dict,
        database_url: str,
        gateway_key: str,
        adapter: Adapter,
        *,
        runtime_ref: str,
        framework: str,
        proxy: ModelProxy | None = None,
        memory_factory=Memory,
    ):
        self.config = config
        self.app = config["name"]
        self.alias = config["model"]["alias"]
        self.max_steps = int(config["model"].get("max_model_calls", 4))
        self.reasoning = REASONING[config["model"].get("reasoning", "low")]
        self.runtime_ref = runtime_ref
        self.framework = framework
        self.database_url = database_url
        self.events = config.get("agent_events", "postgres") != "none"
        self.memory_factory = memory_factory
        self.services = {
            "gateway_url": cfg.gateway_url(),
            "tools_proxy_url": cfg.service("tools_proxy_url"),
        }
        self.system = (
            (cfg.artifact(config["prompt"]["dir"]) / "prompt.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        redaction.remember(gateway_key)

        def embedder_for(alias, dimensions):
            return Embedder(cfg.gateway_url(), gateway_key, alias, dimensions)

        knowledge = config.get("knowledge") or []
        self.retriever = Retriever(
            [KnowledgeSet(k, cfg.artifact(k["dir"]), embedder_for) for k in knowledge]
        )
        self.top_k = max([int(k.get("top_k", 5)) for k in knowledge] or [5])
        self.extra_searches = max([int(k.get("extra_searches", 1)) for k in knowledge] or [0])
        functions = [
            registry_function(s) for s in load_functions(config.get("tools") or [], cfg.artifact)
        ]
        if knowledge:
            functions.insert(0, knowledge_function(self))
        self.functions: tuple[KitFunction, ...] = tuple(functions)
        self.proxy = proxy or ModelProxy(cfg.gateway_url(), gateway_key)
        self.adapter = adapter

    # -- what an adapter uses ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self.proxy.base_url

    def http_client(self) -> httpx.AsyncClient:
        """The turn-aware client: its auth hook sends the running turn's token (§7.2.2)."""
        return httpx.AsyncClient(auth=TurnAuth(TOKEN), timeout=CLIENT_TIMEOUT, trust_env=False)

    def sync_http_client(self) -> httpx.Client:
        return httpx.Client(auth=TurnAuth(TOKEN), timeout=CLIENT_TIMEOUT, trust_env=False)

    async def start(self) -> None:
        """Start the proxy, then build the adapter (it needs the proxy's URL)."""
        await self.proxy.start()
        self.adapter.build(self)

    async def close(self) -> None:
        try:
            self.adapter.close()
        finally:
            await self.proxy.close()

    def system_for(self, notes: list[str]) -> str:
        """System prompt + this customer's saved notes (long-term memory across sessions)."""
        if not notes:
            return self.system
        # Notes are customer-influenced text: neutralise markup so a note cannot close the
        # memory block and pose as instructions.
        remembered = "\n".join("- " + n.replace("<", "‹").replace(">", "›") for n in notes)
        return (
            f"{self.system}\n\n<customer_memory>\nNotes saved from this customer's earlier "
            f"conversations:\n{remembered}\n</customer_memory>"
        )

    # -- turns ---------------------------------------------------------------------------------

    async def chat_async(self, session: str, customer: str, message: str) -> dict:
        memory = await run_to_completion(self.memory_factory, self.database_url, self.events)
        turn_id = uuid.uuid4().hex
        started = time.monotonic()
        trace = new_trace()

        async def model_called(row: dict) -> None:
            await run_to_completion(
                memory.audit, turn_id, session, customer, "agent", "model_called", row
            )

        state = TurnState(
            turn_id=turn_id,
            max_steps=self.max_steps,
            aliases=frozenset({self.alias}),
            reasoning=self.reasoning,
            toolset=frozenset(f.name for f in self.functions),
            on_model_called=model_called,
        )
        context = TurnContext(
            id=turn_id,
            session=session,
            customer=customer,
            memory=memory,
            trace=trace,
            agent=self.app,
            config=self.config,
            services=self.services,
            extra_searches=self.extra_searches,
            proxy_state=state,
        )
        token = self.proxy.open_turn(state)
        context_token, token_token = CURRENT.set(context), TOKEN.set(token)
        reply, error, trace_id, span_id = "", None, None, None
        try:
            with span(
                "agent_turn",
                "AGENT",
                input=message,
                **{
                    "session.id": session,
                    "user.id": customer,
                    "agent.turn_id": turn_id,
                    "agent.framework": self.framework,
                    "agent.name": self.app,
                    # Exact trace -> deployment attribution (#907); omitted when unset.
                    "agentbreeder.deployment_id": cfg.deployment_id(),
                },
            ) as root:
                trace_id, span_id = ids(root)
                await run_to_completion(
                    memory.audit,
                    turn_id,
                    session,
                    customer,
                    "customer",
                    "message_received",
                    {"chars": len(message), "trace_id": trace_id},
                )
                try:
                    reply = await self.run_turn(context, state, message)
                    set_output(root, reply)
                except Exception as failure:
                    coded = failure if isinstance(failure, ModelError) else None
                    if coded is not None:
                        error = f"{coded.code}: {state.detail}" if state.detail else coded.code
                    else:
                        error = redaction.upstream(f"{type(failure).__name__}: {failure}")[:500]
                    await run_to_completion(
                        memory.audit,
                        turn_id,
                        session,
                        customer,
                        "system",
                        "turn_failed",
                        {"error": error},
                    )
                    if coded is None:
                        raise
                    coded.turn_id, coded.trace_id = turn_id, trace_id
                    root.set_attribute("agentbreeder.error_code", coded.code)
                    print(
                        redaction.one_line(
                            f"model call failed: turn={turn_id} code={coded.code} "
                            f"status={coded.status}"
                        ),
                        flush=True,
                    )
                    raise coded from None
                finally:
                    trace["model_calls"] = state.calls
                    trace["prompt_tokens"] = state.prompt_tokens
                    trace["output_tokens"] = state.output_tokens
                    for key, value in errors.root_attributes(state.tally).items():
                        root.set_attribute(key, value)
            await run_to_completion(
                memory.audit,
                turn_id,
                session,
                customer,
                "agent",
                "reply_sent",
                {"chars": len(reply)},
            )
            fallbacks, served_by = errors.turn_fallbacks(state.tally)
            # The trace first: tools can add keys to it, never replace the runtime's own fields.
            return {
                **{k: v for k, v in trace.items() if k != "evidence"},
                "reply": reply,
                "session_id": session,
                "customer_id": customer,
                "model": self.alias,
                "framework": self.framework,
                "runtime": self.runtime_ref,
                "turn_id": turn_id,
                "trace_id": trace_id,
                "cost_usd": errors.turn_cost_usd(state.tally),
                "fallbacks": fallbacks,
                "served_by": served_by,
                "rate_limit_wait_s": round(state.tally["waited"], 3),
            }
        finally:
            CURRENT.reset(context_token)
            TOKEN.reset(token_token)
            self.proxy.close_turn(token)
            try:
                await run_to_completion(
                    memory.record_turn,
                    turn=turn_id,
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

    async def run_turn(self, context: TurnContext, state: TurnState, message: str) -> str:
        memory, trace = context.memory, context.trace
        session, customer, turn_id = context.session, context.customer, context.id
        history = await run_to_completion(memory.history, customer, session)
        text = message
        if self.retriever.sets:
            # Retrieve before the model call. Short follow-ups ("my annual one") are searched
            # together with the previous customer message.
            previous = [history[-1][0]] if history else []
            query = " ".join(previous + [message])
            with span("retrieve_knowledge", "RETRIEVER", input=query) as retrieval:
                hits = await run_to_completion(self.retriever.search, memory.db, query, self.top_k)
                set_documents(retrieval, hits)
                trace["retrieval"] = hits[0]["mode"] if hits else "none"
                retrieval.set_attribute("retrieval.mode", trace["retrieval"])
            await run_to_completion(
                memory.audit,
                turn_id,
                session,
                customer,
                "agent",
                "knowledge_retrieved",
                {"mode": trace["retrieval"], "chunks": [h["id"] for h in hits]},
            )
            for h in hits:
                if h["title"] not in trace["sources"]:
                    trace["sources"].append(h["title"])
            trace["evidence"].extend(
                {"source": "kb", "title": h["title"], "text": h["text"]} for h in hits
            )
            passages = "\n\n".join(f"[{h['title']}] {h['text']}" for h in hits) or "No matches."
            text = f"<knowledge_base_results>\n{passages}\n</knowledge_base_results>\n\n{message}"
        notes: list[str] = []
        if self.config.get("memory", {}).get("notes"):
            notes = await run_to_completion(memory.notes, customer)
            if not history and notes:
                await run_to_completion(
                    memory.audit,
                    turn_id,
                    session,
                    customer,
                    "agent",
                    "memory_recalled",
                    {"notes": len(notes)},
                )
        system = self.system_for(notes)
        state.system = system
        turn = Turn(
            id=turn_id,
            system=system,
            history=tuple(
                item
                for previous_message, previous_reply in history
                for item in (
                    {"role": "user", "content": previous_message},
                    {"role": "assistant", "content": previous_reply},
                )
            ),
            message=text,
            functions=self.functions,
        )
        try:
            reply = await self.adapter.run_turn(turn)
        except Exception as raised:  # noqa: BLE001 - the proxy's state decides first
            return decide(state, None, raised) or FALLBACK_REPLY
        return (decide(state, reply, None) or "").strip() or FALLBACK_REPLY
