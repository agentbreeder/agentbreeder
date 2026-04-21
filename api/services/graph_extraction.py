"""Graph extraction service — LLM-based entity and relationship extraction.

Provides:
- extract_entities: Extract entities and relationships from a single text chunk
- extract_entities_batch: Batch extraction with configurable batch size
- Module-level SHA-256 keyed extraction cache
- _call_claude: Low-level Anthropic API caller
- _parse_extraction_result: JSON → GraphNode/GraphEdge converter
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from typing import Any

import httpx

from api.services.rag_service import DEFAULT_ENTITY_MODEL, GraphEdge, GraphNode

logger = logging.getLogger(__name__)

# Module-level in-process cache: SHA-256(chunk_text + model) → (nodes, edges)
_extraction_cache: dict[str, tuple[list[GraphNode], list[GraphEdge]]] = {}


async def extract_entities(
    text: str,
    model: str = DEFAULT_ENTITY_MODEL,
    cache: dict[str, tuple[list[GraphNode], list[GraphEdge]]] | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Extract entities and relationships from a text chunk using an LLM.

    Uses module-level cache by default. Pass cache={} to use a fresh cache.
    Returns ([], []) on LLM failure (never raises).

    Note: returned GraphNode/GraphEdge objects always have chunk_ids=[].
    The caller must append the source chunk_id after calling this function.
    """
    # Use module-level cache if none provided
    active_cache: dict[str, tuple[list[GraphNode], list[GraphEdge]]] = (
        _extraction_cache if cache is None else cache
    )

    # Cache key: SHA-256 of JSON-serialised (text, model) to avoid pipe-char collisions
    cache_key = hashlib.sha256(
        json.dumps({"text": text, "model": model}, sort_keys=True).encode()
    ).hexdigest()

    if cache_key in active_cache:
        return active_cache[cache_key]

    # Call LLM
    if model.startswith("ollama/"):
        raw = await _call_ollama(text, model[len("ollama/") :])
    else:
        raw = await _call_claude(text, model)

    # Parse results
    nodes, edges = _parse_extraction_result(raw, text)

    # Store in cache
    active_cache[cache_key] = (nodes, edges)

    return nodes, edges


async def extract_entities_batch(
    texts: list[str],
    model: str = DEFAULT_ENTITY_MODEL,
    batch_size: int = 5,
    cache: dict[str, tuple[list[GraphNode], list[GraphEdge]]] | None = None,
) -> list[tuple[list[GraphNode], list[GraphEdge]]]:
    """Extract entities from multiple chunks concurrently.

    batch_size controls max concurrent API calls (semaphore limit).
    Uses extract_entities() per chunk with shared cache.
    Returns list of (nodes, edges) tuples in same order as input texts.
    """
    if not texts:
        return []
    semaphore = asyncio.Semaphore(batch_size)

    async def _extract_with_semaphore(text: str) -> tuple[list[GraphNode], list[GraphEdge]]:
        async with semaphore:
            return await extract_entities(text, model=model, cache=cache)

    return list(await asyncio.gather(*[_extract_with_semaphore(t) for t in texts]))


async def _call_claude(text: str, model: str) -> dict[str, Any]:
    """Call Claude API to extract entities and relationships.

    Returns a dict with 'entities' and 'relationships' keys.
    On any error (network, bad JSON, missing key): logs warning and returns empty result.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping entity extraction")
        return {"entities": [], "relationships": []}

    system_prompt = (
        "You are an information extraction assistant. "
        "Extract entities and relationships from text. "
        "Return ONLY valid JSON — no prose."
    )

    user_prompt = (
        f"Extract from the following text chunk:\n"
        f"<chunk>{text}</chunk>\n\n"
        "Return JSON with this exact schema:\n"
        "{\n"
        '  "entities": [\n'
        '    {"entity": "string", "type": "organization|person|concept|location|event|other", "description": "string"}\n'
        "  ],\n"
        '  "relationships": [\n'
        '    {"subject": "entity name", "predicate": "relationship verb", "object": "entity name"}\n'
        "  ]\n"
        "}"
    )

    payload = {
        "model": model,
        "max_tokens": 1024,
        "temperature": 0,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            text_content = data["content"][0]["text"]
            return json.loads(text_content)
    except json.JSONDecodeError as e:
        logger.warning("Entity extraction: failed to parse JSON response: %s", e)
        return {"entities": [], "relationships": []}
    except httpx.HTTPError as e:
        logger.warning("Entity extraction: HTTP error calling Claude API: %s", e)
        return {"entities": [], "relationships": []}
    except (KeyError, IndexError) as e:
        logger.warning("Entity extraction: unexpected response structure: %s", e)
        return {"entities": [], "relationships": []}
    except Exception as e:
        logger.warning("Entity extraction: unexpected error: %s", e)
        return {"entities": [], "relationships": []}


async def _call_ollama(text: str, model_name: str) -> dict[str, Any]:
    """Call local Ollama chat API to extract entities and relationships.

    model_name is the bare name (e.g. "qwen2.5:7b"), without the "ollama/" prefix.
    Uses OLLAMA_BASE_URL env var; defaults to http://localhost:11434.
    On any error: logs warning and returns empty result (never raises).
    """
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    system_prompt = (
        "You are an information extraction assistant. "
        "Extract entities and relationships from text. "
        "Return ONLY valid JSON — no prose."
    )
    user_prompt = (
        f"Extract from the following text chunk:\n"
        f"<chunk>{text}</chunk>\n\n"
        "Return JSON with this exact schema:\n"
        "{\n"
        '  "entities": [\n'
        '    {"entity": "string", "type": "organization|person|concept|location|event|other", "description": "string"}\n'
        "  ],\n"
        '  "relationships": [\n'
        '    {"subject": "entity name", "predicate": "relationship verb", "object": "entity name"}\n'
        "  ]\n"
        "}"
    )
    payload = {
        "model": model_name,
        "format": "json",
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["message"]["content"]
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("Entity extraction (Ollama): failed to parse JSON response: %s", e)
        return {"entities": [], "relationships": []}
    except httpx.HTTPError as e:
        logger.warning("Entity extraction (Ollama): HTTP error calling Ollama: %s", e)
        return {"entities": [], "relationships": []}
    except (KeyError, IndexError) as e:
        logger.warning("Entity extraction (Ollama): unexpected response structure: %s", e)
        return {"entities": [], "relationships": []}
    except Exception as e:
        logger.warning("Entity extraction (Ollama): unexpected error: %s", e)
        return {"entities": [], "relationships": []}


def _parse_extraction_result(
    data: dict[str, Any],
    chunk_text: str,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Parse raw LLM JSON into GraphNode/GraphEdge objects.

    - entities list → GraphNode (id=uuid4, entity=name, entity_type=type,
      description=description, chunk_ids=[])
    - relationships list → GraphEdge, but ONLY if both subject and object appear
      in extracted entities (prevents dangling edges).
    - Skips malformed entries with a debug log.
    - Never raises — returns partial results on bad data.
    """
    nodes: list[GraphNode] = []
    # name → node_id mapping for edge resolution
    name_to_id: dict[str, str] = {}

    raw_entities = data.get("entities", [])
    if not isinstance(raw_entities, list):
        logger.debug("_parse_extraction_result: 'entities' is not a list, skipping")
        raw_entities = []

    for entry in raw_entities:
        if not isinstance(entry, dict):
            logger.debug("_parse_extraction_result: skipping non-dict entity entry: %r", entry)
            continue
        entity_name = entry.get("entity")
        entity_type = entry.get("type")
        if not entity_name or not entity_type:
            logger.debug(
                "_parse_extraction_result: skipping malformed entity (missing 'entity' or 'type'): %r",
                entry,
            )
            continue
        description = entry.get("description", "")
        node_id = str(uuid.uuid4())
        node = GraphNode(
            id=node_id,
            entity=entity_name,
            entity_type=entity_type,
            description=description,
            chunk_ids=[],
        )
        nodes.append(node)
        # Index by normalized name for relationship resolution
        norm_name = _normalize_entity_name(entity_name)
        name_to_id[norm_name] = node_id

    edges: list[GraphEdge] = []

    raw_relationships = data.get("relationships", [])
    if not isinstance(raw_relationships, list):
        logger.debug("_parse_extraction_result: 'relationships' is not a list, skipping")
        raw_relationships = []

    for rel in raw_relationships:
        if not isinstance(rel, dict):
            logger.debug("_parse_extraction_result: skipping non-dict relationship: %r", rel)
            continue
        subject_name = rel.get("subject")
        predicate = rel.get("predicate")
        object_name = rel.get("object")

        if not subject_name or not predicate or not object_name:
            logger.debug(
                "_parse_extraction_result: skipping malformed relationship (missing fields): %r",
                rel,
            )
            continue

        norm_subject = _normalize_entity_name(subject_name)
        norm_object = _normalize_entity_name(object_name)

        subject_id = name_to_id.get(norm_subject)
        object_id = name_to_id.get(norm_object)

        # Only create edge if both entities were extracted (no dangling edges)
        if subject_id is None or object_id is None:
            logger.debug(
                "_parse_extraction_result: skipping relationship with unknown entity "
                "(subject=%r, object=%r)",
                subject_name,
                object_name,
            )
            continue

        edge = GraphEdge(
            id=str(uuid.uuid4()),
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            chunk_ids=[],
        )
        edges.append(edge)

    return nodes, edges


def _normalize_entity_name(name: str) -> str:
    """Lowercase, strip, collapse internal whitespace."""
    return " ".join(name.strip().lower().split())


def get_extraction_cache() -> dict[str, tuple[list[GraphNode], list[GraphEdge]]]:
    """Return the module-level extraction cache (for inspection/testing)."""
    return _extraction_cache


def clear_extraction_cache() -> None:
    """Clear the module-level extraction cache."""
    _extraction_cache.clear()
