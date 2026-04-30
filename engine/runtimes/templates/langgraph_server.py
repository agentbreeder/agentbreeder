"""AgentBreeder server wrapper for LangGraph agents.

This file is copied into the agent container at build time.
It wraps any LangGraph agent as a FastAPI server with /invoke, /resume, and /health endpoints.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")


def _verify_auth(authorization: str | None = Header(default=None)) -> None:
    """Bearer-token auth for protected endpoints.

    Disabled (no-op) when AGENT_AUTH_TOKEN env var is unset/empty so local dev
    works without ceremony. /health is intentionally NOT protected so Cloud Run
    and k8s liveness probes can hit it without credentials.
    """
    expected = os.getenv("AGENT_AUTH_TOKEN", "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    presented = authorization.removeprefix("Bearer ").strip()
    if presented != expected:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: dict[str, Any]
    config: dict[str, Any] | None = None
    thread_id: str | None = None


class ToolCall(BaseModel):
    """Structured record of a single tool invocation (#215).

    Shared shape across all runtime templates so the dashboard playground can
    render a tool-call timeline without regex-scraping message bodies.
    """

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    duration_ms: int = 0
    started_at: str = ""


class InvokeResponse(BaseModel):
    output: Any
    metadata: dict[str, Any] | None = None
    thread_id: str | None = None
    # Structured tool-call timeline (#215). Pulled from the LangGraph state's
    # ``messages`` array — pairs each ``AIMessage.tool_calls[*]`` with the
    # matching ``ToolMessage`` content.  Empty when the graph emitted no tool
    # calls.
    history: list[ToolCall] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    thread_id: str
    human_input: Any  # The human response


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _get_checkpointer() -> Any:
    """Select and return the appropriate checkpointer based on environment.

    Uses AsyncPostgresSaver when DATABASE_URL is set, otherwise falls back
    to in-memory MemorySaver. Imports are kept inside this function to avoid
    import errors in containers that may not have all dependencies installed.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            from langgraph.checkpoint.postgres.aio import (
                AsyncPostgresSaver,
            )

            checkpointer = AsyncPostgresSaver.from_conn_string(database_url)
            logger.info("Using AsyncPostgresSaver checkpointer (DATABASE_URL is set)")
            return checkpointer
        except ImportError:
            logger.warning(
                "DATABASE_URL is set but langgraph-checkpoint-postgres is not installed. "
                "Falling back to MemorySaver."
            )

    from langgraph.checkpoint.memory import MemorySaver

    logger.info("Using MemorySaver checkpointer (no DATABASE_URL)")
    return MemorySaver()


def _load_agent() -> Any:
    """Dynamically load the agent graph from agent.py."""
    sys.path.insert(0, "/app")
    try:
        module = importlib.import_module("agent")
    except ImportError as e:
        logger.error("Failed to import agent module: %s", e)
        raise

    # Look for common LangGraph exports
    for attr_name in ("graph", "app", "workflow", "agent"):
        if hasattr(module, attr_name):
            obj = getattr(module, attr_name)

            # Warn if an uncompiled StateGraph is exported — AgentBreeder will compile it
            # but the user may lose custom compilation options.
            try:
                from langgraph.graph import StateGraph

                if isinstance(obj, StateGraph):
                    logger.warning(
                        "agent.py exports an uncompiled StateGraph. "
                        "AgentBreeder will compile it, but you may lose custom"
                        " compilation options. "
                        "Consider exporting a compiled graph: 'graph = workflow.compile()'."
                    )
            except ImportError:
                pass  # langgraph not available yet at import time — skip check

            return obj

    msg = (
        "agent.py must export one of: 'graph', 'app', 'workflow', or 'agent'. "
        "This should be a compiled LangGraph graph or a runnable."
    )
    raise AttributeError(msg)


KB_TOP_K: int = int(os.getenv("KB_TOP_K", "5"))

# ---------------------------------------------------------------------------
# Knowledge-base context injection
# ---------------------------------------------------------------------------


async def _inject_kb_context(query: str, kb_index_ids: list[str], top_k: int = KB_TOP_K) -> str:
    """Run vector similarity search over each KB index and return a context string.

    The returned string is meant to be prepended to the agent's system prompt
    before each /invoke call so the LLM has relevant retrieved context.

    Args:
        query:        The user query (used as the search string).
        kb_index_ids: List of RAG index IDs (or slug names) to search.
        top_k:        Maximum number of chunks to retrieve per index.

    Returns:
        A formatted string of retrieved chunks, or an empty string if no
        results were found or the RAGStore is unavailable.
    """
    if not kb_index_ids or not query:
        return ""

    try:
        from api.services.rag_service import get_rag_store

        store = get_rag_store()
    except Exception:
        logger.warning("RAGStore not available; skipping KB context injection")
        return ""

    all_hits: list[str] = []
    for index_id in kb_index_ids:
        # If the id looks like a plain name/slug (no UUID format), try to resolve it
        # by listing indexes and matching on name — graceful fallback path.
        resolved_id = index_id
        idx = store.get_index(index_id)
        if idx is None:
            # Try name-based lookup
            all_indexes, _ = store.list_indexes(page=1, per_page=1000)
            for candidate in all_indexes:
                if candidate.name == index_id or candidate.name == index_id.split("/")[-1]:
                    resolved_id = candidate.id
                    break
            else:
                logger.debug("KB index %r not found in RAGStore; skipping", index_id)
                continue

        try:
            hits = await store.search(resolved_id, query, top_k=top_k)
        except Exception as exc:
            logger.warning("KB search failed for index %s: %s", resolved_id, exc)
            continue

        for hit in hits:
            all_hits.append(f"[source: {hit.source}]\n{hit.text}")

    if not all_hits:
        return ""

    chunks_text = "\n\n---\n\n".join(all_hits)
    return f"<knowledge_base_context>\n{chunks_text}\n</knowledge_base_context>"


# Module-level references populated during startup
_agent = None
_checkpointer = None
_tracer = None
_kb_index_ids: list[str] = []
_memory: Any = None  # MemoryManager instance


@app.on_event("startup")
async def startup() -> None:
    global _agent, _checkpointer, _tracer, _kb_index_ids, _memory  # noqa: PLW0603
    logger.info("Loading agent...")

    try:
        from _tracing import init_tracing

        _tracer = init_tracing()
    except ImportError:
        pass

    raw_agent = _load_agent()
    checkpointer = _get_checkpointer()

    # Run checkpointer setup if supported (AsyncPostgresSaver needs this to
    # create LangGraph's checkpoint tables; MemorySaver does not have setup()).
    if hasattr(checkpointer, "setup"):
        logger.info("Running checkpointer setup...")
        await checkpointer.setup()

    # Compile the graph with the checkpointer only when it is still a StateGraph.
    # If agent.py already exported a compiled graph, compile() won't be present.
    try:
        from langgraph.graph import StateGraph

        if isinstance(raw_agent, StateGraph):
            logger.info("Compiling StateGraph with checkpointer...")
            _agent = raw_agent.compile(checkpointer=checkpointer)
        else:
            _agent = raw_agent
    except ImportError:
        # langgraph not importable here — fall back to the object as-is
        _agent = raw_agent

    _checkpointer = checkpointer

    # Load knowledge base index IDs from environment (injected by resolver)
    kb_env = os.getenv("KB_INDEX_IDS", "")
    if kb_env:
        _kb_index_ids = [idx.strip() for idx in kb_env.split(",") if idx.strip()]
        logger.info(
            "Knowledge base context enabled — %d index(es): %s", len(_kb_index_ids), _kb_index_ids
        )
    else:
        _kb_index_ids = []

    # Initialise conversation-history manager (no-op when MEMORY_BACKEND=none)
    try:
        from memory_manager import MemoryManager

        _memory = MemoryManager()
        await _memory.connect()
        logger.info("Memory manager connected (backend=%s)", os.getenv("MEMORY_BACKEND", "none"))
    except ImportError:
        logger.debug("memory_manager not available — conversation history disabled")

    logger.info("Agent loaded successfully")


@app.on_event("shutdown")
async def shutdown() -> None:
    if _memory is not None:
        await _memory.close()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy" if _agent is not None else "loading",
        agent_name=os.getenv("AGENT_NAME", "unknown"),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
    )


@app.post("/invoke", response_model=InvokeResponse, dependencies=[Depends(_verify_auth)])
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    # Resolve or generate a thread_id for this invocation.
    thread_id = request.thread_id or str(uuid.uuid4())

    try:
        # Merge caller-supplied config with the thread_id configurable.
        base_config: dict[str, Any] = request.config or {}
        configurable = base_config.get("configurable", {})
        configurable["thread_id"] = thread_id
        config: dict[str, Any] = {**base_config, "configurable": configurable}

        input_data = request.input

        # Pre-invoke: load conversation history from memory store.
        if _memory is not None:
            prior_messages = await _memory.load(thread_id)
            if prior_messages and "messages" in input_data:
                input_data = dict(input_data)
                input_data["messages"] = prior_messages + list(input_data["messages"])

        # Pre-invoke: inject knowledge base context as a system prefix.
        if _kb_index_ids:
            query = _extract_query(input_data)
            kb_context = await _inject_kb_context(query, _kb_index_ids)
            if kb_context:
                input_data = _prepend_kb_context(input_data, kb_context)
                logger.debug(
                    "Injected %d-char KB context for query %r", len(kb_context), query[:80]
                )

        result = await _run_agent(input_data, config)

        # Post-invoke: persist updated messages to memory store.
        if _memory is not None and isinstance(result, dict) and "messages" in result:
            await _memory.save(thread_id, result["messages"])

        # Check whether the graph paused at a HITL interrupt breakpoint.
        if hasattr(_agent, "aget_state"):
            state = await _agent.aget_state(config)
            if state.next:  # Pending nodes mean the graph is interrupted.
                return InvokeResponse(
                    output={
                        "status": "interrupted",
                        "thread_id": thread_id,
                        "awaiting": list(state.next),
                    },
                    thread_id=thread_id,
                    metadata={"interrupted": True},
                    history=_extract_tool_history(result),
                )

        return InvokeResponse(
            output=result,
            thread_id=thread_id,
            history=_extract_tool_history(result),
        )
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/resume", response_model=InvokeResponse, dependencies=[Depends(_verify_auth)])
async def resume(request: ResumeRequest) -> InvokeResponse:
    """Resume a paused HITL graph with human input."""
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    config = {"configurable": {"thread_id": request.thread_id}}
    try:
        from langgraph.types import Command

        result = await _agent.ainvoke(Command(resume=request.human_input), config=config)
        return InvokeResponse(
            output=result,
            thread_id=request.thread_id,
            history=_extract_tool_history(result),
        )
    except Exception as e:
        logger.exception("Agent resume failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _extract_query(input_data: dict[str, Any]) -> str:
    """Extract a search query string from the agent input dict.

    LangGraph agents typically receive input in one of these shapes:
      - {"messages": [{"role": "user", "content": "..."}]}
      - {"messages": [HumanMessage(content="...")]}
      - {"query": "..."}
      - {"input": "..."}

    Falls back to the JSON-serialised input if no known key is found.
    """
    # HumanMessage list (LangChain message format)
    messages = input_data.get("messages")
    if messages and isinstance(messages, list):
        last = messages[-1]
        if isinstance(last, dict):
            return str(last.get("content", ""))
        # LangChain BaseMessage object
        if hasattr(last, "content"):
            return str(last.content)

    for key in ("query", "input", "question", "text"):
        if key in input_data:
            return str(input_data[key])

    return str(input_data)


def _prepend_kb_context(input_data: dict[str, Any], kb_context: str) -> dict[str, Any]:
    """Return a copy of input_data with KB context prepended to the system message.

    If the input contains a ``messages`` list, a system message is inserted at
    position 0 (or merged with an existing system message).  For other input
    shapes a ``__kb_context__`` key is added so custom agents can consume it.
    """
    import copy

    data = copy.deepcopy(input_data)

    messages = data.get("messages")
    if messages and isinstance(messages, list):
        first = messages[0] if messages else None
        if isinstance(first, dict) and first.get("role") == "system":
            # Merge into existing system message
            existing = first.get("content", "")
            first["content"] = f"{kb_context}\n\n{existing}" if existing else kb_context
        else:
            # Prepend a new system message
            messages.insert(0, {"role": "system", "content": kb_context})
        data["messages"] = messages
    else:
        # Generic fallback — add a dedicated key
        data["__kb_context__"] = kb_context

    return data


async def _run_agent(input_data: dict[str, Any], config: dict[str, Any]) -> Any:
    """Run the agent, handling both sync and async graphs."""
    if hasattr(_agent, "ainvoke"):
        return await _agent.ainvoke(input_data, config=config)
    elif hasattr(_agent, "invoke"):
        return _agent.invoke(input_data, config=config)
    else:
        msg = "Agent does not have invoke or ainvoke method"
        raise TypeError(msg)


def _extract_tool_history(result: Any) -> list[ToolCall]:
    """Pair AIMessage.tool_calls with their ToolMessage outputs from the graph state (#215).

    LangGraph graphs that use the standard ``MessagesState`` (or any state with
    a ``messages`` list) record tool invocations as:
        - an ``AIMessage`` whose ``tool_calls`` field carries one or more
          ``{name, args, id}`` entries, and
        - one ``ToolMessage`` per invocation, keyed by the matching
          ``tool_call_id`` and carrying the result string in ``.content``.

    This walk pairs them up by id and produces one ``ToolCall`` per entry.
    Graphs whose state has no ``messages`` array (e.g. custom dict-only state)
    return an empty history — explicit telemetry can be added in user code.
    """
    if not isinstance(result, dict):
        return []
    messages = result.get("messages") or []
    if not isinstance(messages, list):
        return []

    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _msg_attr(msg: Any, key: str, default: Any = None) -> Any:
        if isinstance(msg, dict):
            return msg.get(key, default)
        return getattr(msg, key, default)

    for msg in messages:
        # Accept both LangChain BaseMessage objects and plain dicts.
        msg_type = _msg_attr(msg, "type") or msg.__class__.__name__
        # 1) AIMessage carrying tool_calls.
        tool_calls = _msg_attr(msg, "tool_calls") or []
        if tool_calls:
            for tc in tool_calls:
                tc_id = (
                    tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                ) or f"_anon_{len(order)}"
                tc_name = (
                    tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                ) or ""
                tc_args = (
                    tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
                ) or {}
                if not isinstance(tc_args, dict):
                    tc_args = {"raw": str(tc_args)}
                entry = by_id.setdefault(tc_id, {"name": tc_name, "args": tc_args, "result": ""})
                entry["name"] = tc_name or entry.get("name", "")
                entry["args"] = tc_args or entry.get("args", {})
                if tc_id not in order:
                    order.append(tc_id)
        # 2) ToolMessage carrying the result string for one tool_call_id.
        if msg_type in ("tool", "ToolMessage") or _msg_attr(msg, "tool_call_id"):
            tc_id = _msg_attr(msg, "tool_call_id")
            content = _msg_attr(msg, "content", "")
            if not tc_id:
                continue
            entry = by_id.setdefault(tc_id, {"name": "", "args": {}, "result": ""})
            if isinstance(content, list):
                # Some message contents are lists of content blocks; stringify.
                try:
                    entry["result"] = json.dumps(content)
                except (TypeError, ValueError):
                    entry["result"] = str(content)
            else:
                entry["result"] = str(content) if content is not None else ""
            if tc_id not in order:
                order.append(tc_id)

    return [
        ToolCall(
            name=str(by_id[i].get("name", "") or ""),
            args=by_id[i].get("args", {}) or {},
            result=str(by_id[i].get("result", "") or ""),
        )
        for i in order
    ]
