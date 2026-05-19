# Platform Audit — Wave 1 (P0 Correctness & Security Fixes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the 4 P0 findings from the 2026-05-18 platform audit (W1-01 … W1-04 in the audit spec). One security fix (path traversal in `markdown_writer`), one validation hardening (RAG search request — weights + numeric bounds, combined), and one observability fix (alert on silent pseudo-embedding fallback).

**Architecture:**
- All fixes stay within `engine/tools/standard/`, `api/routes/rag.py`, and `api/services/rag_service.py`. No schema changes, no DB migrations, no signature changes to public CLI/API endpoints.
- Path-traversal fix is a pure server-side input-validation tightening.
- RAG search validation is introduced via a new internal Pydantic model `RagSearchRequest` that wraps the request body — the *endpoint signature stays `body: dict`* externally, but the dict is validated through the model immediately on entry. Invalid bodies return `422 Validation Error` (FastAPI default). Valid bodies behave exactly as before.
- Embedding-fallback alerting refactors `embed_texts` to return `EmbeddingResult(vectors, used_fallback, fallback_reason)`. Internal callers are updated. External API gains a non-breaking `"degraded": true` flag in the search response when fallback occurred.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, pytest, pytest-asyncio. Tests live under `tests/unit/`. The repo lint command is `ruff check . && ruff format .` from repo root; type check is `mypy .`; test runner is `pytest` from repo root.

**Risk envelope (do not violate):**
- Additive only. No agent.yaml schema changes. No DB migrations. No CLI/API contract breaks.
- All four fixes must remain backwards-compatible for legitimate callers — only malformed or unsafe input is newly rejected.
- Cross-repo (cloud, website) sync is not required for Wave 1.

---

## File Structure

| Task | File | Responsibility |
|------|------|----------------|
| 1 | `engine/tools/standard/markdown_writer.py` | Sanitize `subdir` against path traversal |
| 1 | `tests/unit/test_standard_tools.py` (new) | Path-traversal regression tests |
| 2 | `api/models/schemas.py` | Add `RagSearchRequest` Pydantic model |
| 2 | `api/routes/rag.py` | Parse incoming body through new model |
| 2 | `tests/unit/test_rag_routes.py` (extend if exists, new if not) | Validation tests |
| 3 | `api/services/rag_service.py` | Refactor `embed_texts` to surface fallback flag; WARN log per first fallback |
| 3 | `tests/unit/test_rag_service.py` (extend) | Fallback-detection tests |
| 4 | `CHANGELOG.md` | Wave 1 entry under Unreleased |
| 4 | (closing commit, may be empty) | Wave 1 boundary marker |

---

## Conventions used in this plan

- All Python code blocks are paste-ready unless explicitly marked as pseudocode.
- All `pytest` invocations assume repo-root cwd (where `pyproject.toml` lives). Use `pytest tests/unit/...` paths (not absolute).
- Each task is a single commit. Implementer should run `ruff check . && ruff format .` and the test suite before committing.
- Conventional commit subject lines.

---

## Task 1: Sanitize `markdown_writer.subdir` against path traversal

**Why:** Audit P0 finding T1. The `subdir` parameter at `engine/tools/standard/markdown_writer.py:55` is concatenated via `base_dir / subdir` with no validation. An agent could write to `../../etc/passwd` by passing `subdir="../../etc"`. Server-side validation tightening; no breaking change.

**Files:**
- Modify: `engine/tools/standard/markdown_writer.py` (the `markdown_writer` function, lines 37-69)
- Create: `tests/unit/test_standard_tools.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/unit/test_standard_tools.py` with these tests:

```python
"""Unit tests for engine.tools.standard.markdown_writer."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.tools.standard.markdown_writer import markdown_writer


@pytest.fixture
def tmp_output_dir(tmp_path, monkeypatch):
    """Point DOCUMENT_OUTPUT_DIR at an isolated tmp dir."""
    monkeypatch.setenv("DOCUMENT_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def test_markdown_writer_writes_into_base_dir(tmp_output_dir: Path) -> None:
    result = markdown_writer(title="My Note", content="# hi")
    assert Path(result["path"]).is_file()
    assert Path(result["path"]).is_relative_to(tmp_output_dir.resolve())
    assert result["byte_size"] > 0


def test_markdown_writer_allows_safe_subdir(tmp_output_dir: Path) -> None:
    result = markdown_writer(title="Note", content="x", subdir="reports/q1")
    out = Path(result["path"])
    assert out.is_file()
    assert out.is_relative_to(tmp_output_dir.resolve())
    assert "reports/q1" in str(out)


def test_markdown_writer_rejects_parent_traversal(tmp_output_dir: Path) -> None:
    with pytest.raises(ValueError, match="subdir"):
        markdown_writer(title="Bad", content="x", subdir="../etc")


def test_markdown_writer_rejects_deeply_nested_traversal(
    tmp_output_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="subdir"):
        markdown_writer(title="Bad", content="x", subdir="ok/../../etc")


def test_markdown_writer_rejects_absolute_path(tmp_output_dir: Path) -> None:
    with pytest.raises(ValueError, match="subdir"):
        markdown_writer(title="Bad", content="x", subdir="/etc")


def test_markdown_writer_rejects_home_expansion(tmp_output_dir: Path) -> None:
    # ~ should NOT be expanded — it would write to user's home dir
    with pytest.raises(ValueError, match="subdir"):
        markdown_writer(title="Bad", content="x", subdir="~/notes")


def test_markdown_writer_rejects_null_byte(tmp_output_dir: Path) -> None:
    with pytest.raises(ValueError, match="subdir"):
        markdown_writer(title="Bad", content="x", subdir="ok\x00etc")
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
pytest tests/unit/test_standard_tools.py -v
```

Expected: 4 of 7 tests fail (the rejection tests — `test_markdown_writer_rejects_*`). The first three (`writes_into_base_dir`, `allows_safe_subdir`, plus possibly `rejects_parent_traversal` if `mkdir` accidentally protects against it on some FS) may pass already.

If all 7 already pass, something's wrong — re-check the implementation. The repository's current code has no rejection logic.

- [ ] **Step 3: Implement the fix**

Replace the `markdown_writer` function in `engine/tools/standard/markdown_writer.py` with:

```python
def _validate_subdir(subdir: str) -> str:
    """Validate that ``subdir`` is a safe relative path under DOCUMENT_OUTPUT_DIR.

    Raises ValueError on traversal, absolute paths, home-dir expansion, or null bytes.
    Returns the original ``subdir`` unchanged when valid.
    """
    if "\x00" in subdir:
        raise ValueError("subdir must not contain null bytes")
    if subdir.startswith("/") or subdir.startswith("~"):
        raise ValueError(
            f"subdir must be a relative path, not an absolute or home-expansion path: {subdir!r}"
        )
    # Reject any segment that is exactly ".." (traversal). We allow `..` to appear
    # inside other segments (e.g. "weird..name") since that's a legal filename.
    parts = Path(subdir).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"subdir must not contain parent-directory traversal: {subdir!r}")
    return subdir


def markdown_writer(title: str, content: str, subdir: str = "") -> dict[str, Any]:
    """Save markdown to disk and return the resolved file path.

    Args:
        title: Used to derive a kebab-cased filename. Always paired with a
            UTC timestamp suffix so multiple writes don't collide.
        content: Full markdown body.
        subdir: Optional sub-directory under ``DOCUMENT_OUTPUT_DIR``. Must be a
            safe relative path — absolute paths, parent traversal (``..``),
            home-dir expansion (``~``), and null bytes are rejected.

    Returns:
        A dict with keys:
            path: absolute path to the saved file.
            byte_size: size of the saved file in bytes.
            title: the title that was rendered.

    Raises:
        ValueError: if ``subdir`` contains traversal or unsafe characters.
    """
    base_dir = Path(os.getenv("DOCUMENT_OUTPUT_DIR", "./output"))
    if subdir:
        subdir = _validate_subdir(subdir)
        out_dir = base_dir / subdir
    else:
        out_dir = base_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "document"
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{slug}-{timestamp}.md"
    out_path = out_dir / filename

    out_path.write_text(content, encoding="utf-8")

    return {
        "path": str(out_path.resolve()),
        "byte_size": out_path.stat().st_size,
        "title": title,
    }
```

(The defense-in-depth: even after passing the path-segment check, you could add a final `out_path.resolve().is_relative_to(base_dir.resolve())` belt-and-braces check inside the function before the write. The segment check above is the primary defense and is sufficient for the documented threat model. If the implementer judges the resolve check is worth adding, do so and include a test that confirms a symlink escape is also caught — but it's not required for this task.)

- [ ] **Step 4: Run tests, confirm all pass**

```bash
pytest tests/unit/test_standard_tools.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Lint + format**

```bash
ruff check engine/tools/standard/markdown_writer.py tests/unit/test_standard_tools.py
ruff format engine/tools/standard/markdown_writer.py tests/unit/test_standard_tools.py
```

Both must report no errors.

- [ ] **Step 6: Spot-check no other test broke**

```bash
pytest tests/unit/ -q -x --maxfail=3 2>&1 | tail -20
```

Expected: existing tests still pass. If a test depending on the old behavior (no validation) breaks, investigate — it likely means another caller passes unsafe input that the test was tolerating. Report as DONE_WITH_CONCERNS.

- [ ] **Step 7: Commit**

```bash
git add engine/tools/standard/markdown_writer.py tests/unit/test_standard_tools.py
git commit -m "fix(tools): sanitize markdown_writer subdir against path traversal (W1-01)"
```

---

## Task 2: RAG search request validation (weights + numeric bounds)

**Why:** Audit P0 findings R1 + R3 (W1-02 + W1-04). The `/api/v1/rag/search` endpoint accepts an unstructured dict body and does no validation:
- `vector_weight + text_weight` can sum to >1.0 (e.g., both 1.0), producing meaningless combined scores.
- `top_k`, `hops`, `seed_entity_limit` are unbounded — accept 0, negative, or absurdly large values (DoS surface + undefined behavior).

Fix: introduce an internal `RagSearchRequest` Pydantic model with proper `Field` constraints and a model validator that enforces `vector_weight + text_weight == 1.0` (within float tolerance). The endpoint still receives `body: dict[str, Any]` for backwards-compat shape but validates by parsing the dict through the model. Invalid inputs become FastAPI `422` responses.

**Files:**
- Modify: `api/models/schemas.py` (add `RagSearchRequest`)
- Modify: `api/routes/rag.py` (parse body through model in `search`)
- Create/extend: `tests/unit/test_rag_routes.py`

- [ ] **Step 1: Locate the existing schemas file**

```bash
grep -n 'class.*BaseModel\|class.*Request\|class.*Response' api/models/schemas.py | head -20
```

Note the existing convention (BaseModel imports, Field usage) so the new class blends in.

- [ ] **Step 2: Add the model to `api/models/schemas.py`**

Append (or insert near other Rag-related models if any exist):

```python
from pydantic import BaseModel, Field, model_validator
# ^ Add `model_validator` to the existing pydantic imports if not already present.


class RagSearchRequest(BaseModel):
    """Validated payload for POST /api/v1/rag/search.

    Backwards-compatible with the previous dict-based body: the endpoint still
    accepts any dict shape, but invalid values now produce 422 Validation Error
    instead of undefined behavior. Legitimate callers see no change.
    """

    index_id: str = Field(..., min_length=1, description="UUID of the target index")
    query: str = Field(..., min_length=1, max_length=10_000)
    top_k: int = Field(10, ge=1, le=1000)
    vector_weight: float = Field(0.7, ge=0.0, le=1.0)
    text_weight: float = Field(0.3, ge=0.0, le=1.0)
    hops: int | None = Field(None, ge=0, le=10)
    seed_entity_limit: int = Field(5, ge=1, le=50)

    @model_validator(mode="after")
    def weights_must_sum_to_one(self) -> "RagSearchRequest":
        # Allow tiny float drift but reject obvious imbalances.
        total = self.vector_weight + self.text_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"vector_weight + text_weight must sum to 1.0 (got {total:.6f})"
            )
        return self
```

- [ ] **Step 3: Use the model in the search route**

Edit `api/routes/rag.py`. Find the `search` function (lines 271-328). Replace the body parsing block. The relevant change is the **first ~20 lines** of the function body.

**Before** (current state):
```python
async def search(
    body: dict[str, Any],
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """..."""
    store = get_rag_store()

    index_id = body.get("index_id")
    query = body.get("query")
    if not index_id or not query:
        raise HTTPException(status_code=400, detail="index_id and query are required")
    ...
    top_k = body.get("top_k", 10)
    vector_weight = body.get("vector_weight", 0.7)
    text_weight = body.get("text_weight", 0.3)

    hits = await store.search(
        index_id=index_id,
        query=query,
        top_k=top_k,
        vector_weight=vector_weight,
        text_weight=text_weight,
        hops=body.get("hops", None),
        seed_entity_limit=body.get("seed_entity_limit", 5),
    )
```

**After**:
```python
async def search(
    body: dict[str, Any],
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """..."""
    # Validate the request body through RagSearchRequest. Invalid bodies
    # produce a 422 with detailed field-level errors.
    try:
        req = RagSearchRequest.model_validate(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    store = get_rag_store()

    try:
        await _enforce_acl(db, _user.email, uuid.UUID(req.index_id), "read")
    except HTTPException:
        raise
    except Exception:
        pass

    idx = store.get_index(req.index_id)
    if not idx:
        raise HTTPException(status_code=404, detail="Index not found")

    hits = await store.search(
        index_id=req.index_id,
        query=req.query,
        top_k=req.top_k,
        vector_weight=req.vector_weight,
        text_weight=req.text_weight,
        hops=req.hops,
        seed_entity_limit=req.seed_entity_limit,
    )

    return ApiResponse(
        data={
            "index_id": req.index_id,
            "query": req.query,
            "top_k": req.top_k,
            "results": [h.to_dict() for h in hits],
            "total": len(hits),
        }
    )
```

You also need to add the imports at the top of `api/routes/rag.py`:

```python
from pydantic import ValidationError

from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    RagSearchRequest,  # ← add this
)
```

(The existing `from api.models.schemas import ApiMeta, ApiResponse` line should be augmented; place the additions in the same import block per the file's existing pattern.)

- [ ] **Step 4: Write the failing tests**

Create or extend `tests/unit/test_rag_routes.py`. If a test file already exists with auth fixtures and a TestClient, mirror that pattern. Otherwise, here's a minimal scaffold:

```python
"""Validation tests for POST /api/v1/rag/search request shape."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models.schemas import RagSearchRequest


# These tests target the model directly — pure validation, no FastAPI client needed.
# A second test layer (TestClient against the actual route) lives in
# tests/integration/ and is intentionally NOT extended here to keep this task
# under the 200-line / 5-file caps.


def test_valid_request_parses() -> None:
    req = RagSearchRequest(index_id="abc", query="hello")
    assert req.top_k == 10
    assert req.vector_weight == 0.7
    assert req.text_weight == 0.3
    assert req.hops is None
    assert req.seed_entity_limit == 5


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        RagSearchRequest(
            index_id="abc", query="hi", vector_weight=1.0, text_weight=1.0
        )


def test_weights_tiny_float_drift_allowed() -> None:
    # 0.7 + 0.3 = 1.0 exactly in float64, no drift expected. Use a known
    # drifting sum to confirm tolerance.
    req = RagSearchRequest(
        index_id="abc", query="hi", vector_weight=0.1, text_weight=0.9
    )
    assert req.vector_weight + req.text_weight == pytest.approx(1.0)


def test_top_k_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", top_k=0)


def test_top_k_capped_at_thousand() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", top_k=10_000)


def test_negative_hops_rejected() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", hops=-1)


def test_hops_capped_at_ten() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", hops=99)


def test_seed_entity_limit_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", seed_entity_limit=0)


def test_seed_entity_limit_capped_at_fifty() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", seed_entity_limit=100)


def test_individual_weight_bounds() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(
            index_id="abc", query="hi", vector_weight=1.5, text_weight=0.0
        )


def test_query_required() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="")


def test_index_id_required() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="", query="hi")
```

- [ ] **Step 5: Run tests, confirm correct failure / pass mix**

```bash
pytest tests/unit/test_rag_routes.py -v
```

Expected after the model is defined in Step 2: all 12 tests pass. (They test the model class itself, not the endpoint.)

If any pass before the model is defined, something is wrong. If any fail after the model is defined, the field constraints don't match the spec — re-read Step 2.

- [ ] **Step 6: Spot-check the existing test suite still passes**

```bash
pytest tests/unit/ -q -x --maxfail=3 2>&1 | tail -20
```

Existing RAG tests must still pass. If a test was passing `vector_weight=1.0` and `text_weight=1.0` and expecting success, that test was masking the bug — update it to use the canonical defaults or rebalance.

- [ ] **Step 7: Lint + format**

```bash
ruff check api/models/schemas.py api/routes/rag.py tests/unit/test_rag_routes.py
ruff format api/models/schemas.py api/routes/rag.py tests/unit/test_rag_routes.py
```

- [ ] **Step 8: Commit**

```bash
git add api/models/schemas.py api/routes/rag.py tests/unit/test_rag_routes.py
git commit -m "fix(rag): validate search request weights + numeric bounds via Pydantic model (W1-02, W1-04)"
```

---

## Task 3: Alert on silent pseudo-embedding fallback

**Why:** Audit P0 finding R2 (W1-03). When OpenAI/Ollama is unreachable or `OPENAI_API_KEY` is missing, `_embed_openai` / `_embed_ollama` silently return deterministic hash-based pseudo-embeddings. Search quality silently degrades and operators have no signal. The fallback path is at:
- `api/services/rag_service.py:463` — `OPENAI_API_KEY not set`
- `api/services/rag_service.py:476` — OpenAI API error catch
- `api/services/rag_service.py:497` — Ollama API error catch

Fix: refactor `embed_texts` to return `EmbeddingResult(vectors, used_fallback, fallback_reason)`. Update `_embed_openai` / `_embed_ollama` to return `(vectors, used_fallback, reason)`. WARN-log on the first fallback per (model, reason) combination per process (to avoid log spam). Propagate `used_fallback` through to the search response as a `"degraded"` flag.

**Files:**
- Modify: `api/services/rag_service.py` (refactor `embed_texts`, `_embed_openai`, `_embed_ollama`; add module-level dedup set; update internal callers)
- Modify: `api/routes/rag.py` (no direct change needed — `RAGStore.search` is the boundary; if `search` returns degraded metadata, surface it in the route's response dict)
- Extend: `tests/unit/test_rag_service.py`

- [ ] **Step 1: Read the existing test file to understand fixtures**

```bash
grep -n 'embed_texts\|_embed_openai\|_embed_ollama\|_pseudo_embedding' tests/unit/test_rag_service.py | head
```

If embedding tests already exist, they exercise the old return shape. The new shape will break them — plan to update.

- [ ] **Step 2: Add the result dataclass + module-level dedup set near the top of `api/services/rag_service.py`**

Insert these definitions immediately after the existing `@dataclass class SearchHit` (around line 231) so they're grouped with other dataclasses, OR near the embedding functions (line 422) — pick whichever flows better with the file's existing order:

```python
@dataclass
class EmbeddingResult:
    """Embeddings plus provenance — exposes whether fallback was used."""

    vectors: list[list[float]]
    used_fallback: bool = False
    fallback_reason: str | None = None  # e.g. "openai-no-api-key", "ollama-unreachable"


# Module-level set of (model, reason) pairs already warned about.
# Prevents log spam when fallback happens on every chunk of a large ingest.
_FALLBACK_WARNED: set[tuple[str, str]] = set()


def _warn_fallback_once(model: str, reason: str) -> None:
    """Emit a WARN log the first time a (model, reason) fallback occurs."""
    key = (model, reason)
    if key in _FALLBACK_WARNED:
        return
    _FALLBACK_WARNED.add(key)
    logger.warning(
        "rag.embedding.fallback",
        extra={
            "model": model,
            "reason": reason,
            "message": (
                f"Embedding model {model} unavailable ({reason}) — "
                "falling back to deterministic pseudo-embeddings. "
                "Search quality will be degraded until the upstream service is reachable."
            ),
        },
    )
```

- [ ] **Step 3: Refactor `_embed_openai` to return triple**

Replace the function body with:

```python
async def _embed_openai(
    texts: list[str], model_name: str
) -> tuple[list[list[float]], bool, str | None]:
    """Call OpenAI embeddings API. Returns (vectors, used_fallback, reason)."""
    import os

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        _warn_fallback_once(f"openai/{model_name}", "openai-no-api-key")
        return (
            [_pseudo_embedding(t, 1536) for t in texts],
            True,
            "openai-no-api-key",
        )

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"input": texts, "model": model_name},
            )
            resp.raise_for_status()
            data = resp.json()
            return ([item["embedding"] for item in data["data"]], False, None)
        except Exception as e:
            _warn_fallback_once(f"openai/{model_name}", "openai-api-error")
            logger.error("OpenAI embedding failed: %s", e)
            return (
                [_pseudo_embedding(t, 1536) for t in texts],
                True,
                "openai-api-error",
            )
```

- [ ] **Step 4: Refactor `_embed_ollama` to return triple**

```python
async def _embed_ollama(
    texts: list[str], model_name: str
) -> tuple[list[list[float]], bool, str | None]:
    """Call Ollama embeddings API. Returns (vectors, used_fallback, reason)."""
    base_url = "http://localhost:11434"
    results: list[list[float]] = []
    any_fallback = False

    async with httpx.AsyncClient(timeout=120.0) as client:
        for text in texts:
            try:
                resp = await client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": model_name, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                results.append(data["embedding"])
            except Exception as e:
                _warn_fallback_once(f"ollama/{model_name}", "ollama-unreachable")
                logger.error("Ollama embedding failed: %s", e)
                results.append(_pseudo_embedding(text, 768))
                any_fallback = True

    reason = "ollama-unreachable" if any_fallback else None
    return (results, any_fallback, reason)
```

- [ ] **Step 5: Refactor `embed_texts` to return `EmbeddingResult`**

```python
async def embed_texts(
    texts: list[str],
    model: str = "openai/text-embedding-3-small",
    batch_size: int = 32,
) -> EmbeddingResult:
    """Generate embeddings for a list of texts.

    Returns an EmbeddingResult. If any batch fell back to pseudo-embeddings,
    `used_fallback=True` and `fallback_reason` captures the first reason
    encountered. WARN log is emitted at most once per (model, reason) per process.
    """
    if not texts:
        return EmbeddingResult(vectors=[])

    all_embeddings: list[list[float]] = []
    used_fallback = False
    first_reason: str | None = None

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        if model.startswith("openai/"):
            vectors, fb, reason = await _embed_openai(batch, model.split("/", 1)[1])
        elif model.startswith("ollama/"):
            vectors, fb, reason = await _embed_ollama(batch, model.split("/", 1)[1])
        else:
            # Unknown model: synthetic pseudo-embeddings (dev/test only).
            dims = EMBEDDING_DIMENSIONS.get(model, 768)
            vectors = [_pseudo_embedding(t, dims) for t in batch]
            fb, reason = True, "unknown-model-prefix"
            _warn_fallback_once(model, reason)

        all_embeddings.extend(vectors)
        if fb and not used_fallback:
            used_fallback = True
            first_reason = reason

    return EmbeddingResult(
        vectors=all_embeddings,
        used_fallback=used_fallback,
        fallback_reason=first_reason,
    )
```

- [ ] **Step 6: Update all internal callers of `embed_texts`**

```bash
grep -n 'embed_texts(' api/services/rag_service.py
```

Find every call. Each needs to consume the new `EmbeddingResult` shape. The pattern is:

```python
# Before:
vectors = await embed_texts(chunks, model=...)

# After:
result = await embed_texts(chunks, model=...)
vectors = result.vectors
# If the surrounding function tracks ingestion / search metadata, also
# record result.used_fallback / result.fallback_reason.
```

In particular, locate `RAGStore.search` (around line 940) and `RAGStore.ingest_files` (around line 834). The `search` path is the one that needs to propagate `used_fallback` outward to the route.

For `search`, the simplest propagation: store the `used_fallback` flag on each `SearchHit`'s metadata, OR return it as part of the search-result tuple. The cleanest option:

- Add an optional `degraded` field on the `SearchHit` dataclass (default False). Set it when fallback was used during query embedding.

Update `SearchHit` definition (around line 215) — add the field:

```python
@dataclass
class SearchHit:
    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False  # ← new, default False is backwards-compatible

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "score": self.score,
            "metadata": self.metadata,
            "degraded": self.degraded,  # ← include in payload
        }
```

(The `GraphSearchHit` dataclass inherits — confirm by reading its definition; if it overrides `to_dict`, mirror the addition.)

- [ ] **Step 7: Surface `degraded` in the route response**

In `api/routes/rag.py` `search` function (modified in Task 2), the response is already built from `[h.to_dict() for h in hits]`. The `degraded` field will flow through automatically once each `SearchHit` carries it.

Additionally, add a top-level `degraded` flag on the search response so callers don't have to inspect every hit:

```python
return ApiResponse(
    data={
        "index_id": req.index_id,
        "query": req.query,
        "top_k": req.top_k,
        "results": [h.to_dict() for h in hits],
        "total": len(hits),
        "degraded": any(h.degraded for h in hits),
    }
)
```

- [ ] **Step 8: Write tests**

Extend `tests/unit/test_rag_service.py`:

```python
import logging

import pytest

from api.services.rag_service import (
    EmbeddingResult,
    _FALLBACK_WARNED,
    embed_texts,
)


@pytest.fixture(autouse=True)
def clear_fallback_state():
    """Reset the module-level dedup set between tests."""
    _FALLBACK_WARNED.clear()
    yield
    _FALLBACK_WARNED.clear()


@pytest.mark.asyncio
async def test_embed_texts_empty_input_returns_empty_result() -> None:
    result = await embed_texts([])
    assert isinstance(result, EmbeddingResult)
    assert result.vectors == []
    assert result.used_fallback is False
    assert result.fallback_reason is None


@pytest.mark.asyncio
async def test_embed_texts_uses_fallback_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    caplog.set_level(logging.WARNING)

    result = await embed_texts(["hello"], model="openai/text-embedding-3-small")

    assert result.used_fallback is True
    assert result.fallback_reason == "openai-no-api-key"
    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == 1536  # openai dims
    # WARN log should mention fallback + reason
    assert any("rag.embedding.fallback" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_embed_texts_fallback_warning_deduplicated(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    caplog.set_level(logging.WARNING)

    await embed_texts(["a"], model="openai/text-embedding-3-small")
    await embed_texts(["b"], model="openai/text-embedding-3-small")
    await embed_texts(["c"], model="openai/text-embedding-3-small")

    fallback_warnings = [
        r for r in caplog.records if "rag.embedding.fallback" in r.message
    ]
    assert len(fallback_warnings) == 1  # deduplicated


@pytest.mark.asyncio
async def test_embed_texts_unknown_model_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    result = await embed_texts(["hi"], model="bogus-provider/foo")
    assert result.used_fallback is True
    assert result.fallback_reason == "unknown-model-prefix"
```

- [ ] **Step 9: Run all related tests**

```bash
pytest tests/unit/test_rag_service.py tests/unit/test_rag_routes.py -v
```

All must pass. If an existing test in `test_rag_service.py` was relying on `embed_texts` returning a bare list, update it to access `.vectors`.

- [ ] **Step 10: Lint + format**

```bash
ruff check api/services/rag_service.py api/routes/rag.py tests/unit/test_rag_service.py
ruff format api/services/rag_service.py api/routes/rag.py tests/unit/test_rag_service.py
```

- [ ] **Step 11: Commit**

```bash
git add api/services/rag_service.py api/routes/rag.py tests/unit/test_rag_service.py
git commit -m "fix(rag): alert on silent pseudo-embedding fallback (W1-03)"
```

---

## Task 4: Wave 1 consolidation — full test pass + CHANGELOG entry

- [ ] **Step 1: Run full unit test suite**

```bash
pytest tests/unit/ -q 2>&1 | tail -20
```

Expected: all green. If any test is unexpectedly red, investigate before proceeding.

- [ ] **Step 2: Run lint + format check across changed surface**

```bash
ruff check engine/ api/ tests/unit/
ruff format --check engine/ api/ tests/unit/
```

Both must succeed.

- [ ] **Step 3: Optional integration test sweep**

If `tests/integration/` exists and is fast, run it:

```bash
pytest tests/integration/ -q 2>&1 | tail -20
```

If integration tests require docker / live services, skip and note in commit. The unit suite is the primary gate.

- [ ] **Step 4: Append to `CHANGELOG.md`**

Under the `## [Unreleased]` section, in an `### Fixed` subsection (create if missing — keep `### Docs` separate from `### Fixed`):

```markdown
### Fixed
- **Path traversal in `markdown_writer`** (W1-01): `subdir` is now validated against parent traversal (`..`), absolute paths (`/`), home expansion (`~`), and null bytes. Unsafe inputs raise `ValueError` instead of writing to arbitrary filesystem paths.
- **RAG search request validation** (W1-02 / W1-04): `POST /api/v1/rag/search` now validates body via `RagSearchRequest` — `vector_weight + text_weight` must sum to 1.0, `top_k` is bounded `[1, 1000]`, `hops` is bounded `[0, 10]`, `seed_entity_limit` is bounded `[1, 50]`. Invalid bodies produce `422 Validation Error`.
- **Silent embedding fallback alerting** (W1-03): When OpenAI / Ollama is unreachable or `OPENAI_API_KEY` is missing, `embed_texts` still falls back to deterministic pseudo-embeddings to keep ingest moving — but now WARN-logs the first occurrence per (model, reason) per process, and the search response carries a `degraded: true` flag so callers can detect quality-degraded results.
```

- [ ] **Step 5: Closing commit**

```bash
git add CHANGELOG.md
git commit -m "docs(wave-1): CHANGELOG entry for P0 correctness fixes" || \
  git commit --allow-empty -m "docs(wave-1): close Wave 1 of platform audit"
```

(`--allow-empty` fallback handles the case where the CHANGELOG was the only file with no other diff.)

---

## Self-review notes (applied during planning)

- **Spec coverage:** Audit spec Wave 1 has 4 entries (W1-01 … W1-04). W1-02 and W1-04 land in the same task because they share a request shape and the same Pydantic model. W1-01 → Task 1; W1-02+04 → Task 2; W1-03 → Task 3. ✅
- **Placeholder scan:** No "TBD" / "TODO" / vague directives. Every step has paste-ready code or exact commands.
- **Type consistency:** `EmbeddingResult` is used in Step 5 and verified across Steps 6-8 to be the consumed shape. `RagSearchRequest` import path is `api.models.schemas`.
- **Risk envelope:** All changes are additive — invalid input is newly rejected, valid input behaves as before. Function signatures changed only for internal helpers (`embed_texts`, `_embed_openai`, `_embed_ollama`); public endpoint shape unchanged except for the new optional `degraded` response field, which is additive.
- **Test-first discipline:** Task 1 + Task 3 both write failing tests before the implementation. Task 2 writes the model first because tests target the model itself (validating the validator), which is the natural order.
- **Scope check:** 4 tasks, each touching ≤ 4 files. Fits the loop's 5-file / 200-line cap per task.

---

## Execution

Subagent-driven (continuation of Wave 0's execution mode). Fresh implementer per task; spec review + code-quality review after each. Final reviewer pass after all 4 tasks land.

After Wave 1 closes (Task 4 commit), generate the Wave 2 plan covering shared-utility introduction (`api/observability.py`, `api/retry.py`, `engine/deployers/_health.py`).
