"""Hybrid retrieval over knowledge artifacts: pgvector similarity + BM25, fused with RRF.

Each knowledge artifact (`artifacts/knowledge/<name>/`: manifest.yaml + docs/*.md|*.txt) gets
its own pgvector table named by name and version, e.g. `kb_northwind_help_1_0_0`. Documents are
split into paragraph chunks; on first use each process syncs them: only new or changed chunks are
embedded (content hash) and chunks whose paragraph disappeared are deleted. Embeddings go through
the gateway alias in the knowledge manifest. If embedding or the database fails, search degrades
to BM25 alone rather than failing the conversation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import psycopg
import yaml

OPENER = build_opener(ProxyHandler({}))
RRF_K = 60
TOKEN = re.compile(r"[a-z0-9]+")
STOP = set(
    "a an and are as at be by can for from has have how i if in is it its my of on or the to "
    "we what when with you your do does".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


def table_name(name: str, version: str) -> str:
    raw = f"kb_{name}_{version}".replace("-", "_").replace(".", "_")
    if not re.fullmatch(r"[a-z0-9_]{1,63}", raw):
        raise ValueError(f"unsafe knowledge table name {raw!r}")
    return raw


def load_chunks(directory: Path) -> list[dict]:
    chunks = []
    files = sorted([*directory.glob("*.md"), *directory.glob("*.txt")])
    for path in files:
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
        for index, paragraph in enumerate(p.strip() for p in text.split("\n\n")):
            if paragraph and not paragraph.startswith("#"):
                chunks.append({"id": f"{path.stem}#{index}", "title": title, "text": paragraph})
    return chunks


def as_vector(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


class Embedder:
    """OpenAI-compatible /v1/embeddings on the gateway (Gemini task type + title passed on)."""

    def __init__(self, gateway_url: str, key: str, alias: str, dimensions: int):
        self.gateway_url, self.key, self.alias, self.dimensions = (
            gateway_url,
            key,
            alias,
            dimensions,
        )

    def __call__(
        self, texts: list[str], task: str, titles: list[str] | None = None
    ) -> list[list[float]]:
        vectors: list[list[float] | None] = [None] * len(texts)
        groups: dict[str | None, list[int]] = {}
        for index in range(len(texts)):
            groups.setdefault(titles[index] if titles else None, []).append(index)
        for title, indexes in groups.items():  # Gemini takes one title per request
            for start in range(0, len(indexes), 100):
                batch = indexes[start : start + 100]
                body = {
                    "model": self.alias,
                    "input": [texts[i] for i in batch],
                    "dimensions": self.dimensions,
                    "task_type": task,
                }
                if title:
                    body["title"] = title
                request = Request(
                    f"{self.gateway_url}/v1/embeddings",
                    data=json.dumps(body).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.key}",
                    },
                )
                with OPENER.open(request, timeout=60) as response:
                    data = sorted(json.load(response)["data"], key=lambda item: item["index"])
                if len(data) != len(batch):
                    raise ValueError("embedding count mismatch")
                for i, item in zip(batch, data, strict=True):
                    vectors[i] = item["embedding"]
        return vectors  # type: ignore[return-value]


class BM25:
    def __init__(self, chunks: list[dict], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.terms = [Counter(tokenize(c["title"] + " " + c["text"])) for c in chunks]
        self.lengths = [sum(t.values()) for t in self.terms]
        self.average = sum(self.lengths) / max(len(self.lengths), 1)
        frequency = Counter(term for terms in self.terms for term in terms)
        total = len(chunks)
        self.idf = {t: math.log(1 + (total - n + 0.5) / (n + 0.5)) for t, n in frequency.items()}

    def search(self, query: str, limit: int) -> list[dict]:
        scored = []
        for chunk, terms, length in zip(self.chunks, self.terms, self.lengths, strict=True):
            score = 0.0
            for term in set(tokenize(query)):
                count = terms.get(term, 0)
                if count:
                    norm = self.k1 * (1 - self.b + self.b * length / self.average)
                    score += self.idf[term] * count * (self.k1 + 1) / (count + norm)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [chunk for _, chunk in scored[:limit]]


class KnowledgeSet:
    """One knowledge artifact: its chunks, BM25 index and pgvector table."""

    def __init__(self, entry: dict, directory: Path, embedder_for):
        manifest = yaml.safe_load((directory / "manifest.yaml").read_text(encoding="utf-8"))
        embedding = manifest.get("embedding") or {}
        self.name, self.version = entry["name"], entry["version"]
        self.description = manifest.get("description") or ""
        self.table = table_name(self.name, self.version)
        self.dimensions = int(embedding.get("dimensions", 768))
        self.alias = embedding.get("alias", "")
        self.embed = embedder_for(self.alias, self.dimensions)
        self.chunks = load_chunks(directory / "docs")
        self.bm25 = BM25(self.chunks)
        self.synced = False
        self.lock = threading.Lock()

    def schema(self) -> str:
        return (
            "CREATE EXTENSION IF NOT EXISTS vector;\n"
            f"CREATE TABLE IF NOT EXISTS {self.table} (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
            f"text TEXT NOT NULL, hash TEXT NOT NULL, embedding vector({self.dimensions}) "
            "NOT NULL, updated TIMESTAMPTZ NOT NULL DEFAULT now());\n"
            f"CREATE INDEX IF NOT EXISTS {self.table}_embedding ON {self.table} "
            "USING hnsw (embedding vector_cosine_ops);"
        )

    def sync(self, db: psycopg.Connection) -> None:
        """Embed new/changed chunks and drop removed ones (once per process)."""
        if self.synced:
            return
        with self.lock:
            if self.synced:
                return
            with db.transaction():
                db.execute("SELECT pg_advisory_xact_lock(727002)")
                db.execute(self.schema())
            stored = dict(db.execute(f"SELECT id, hash FROM {self.table}").fetchall())
            for chunk in self.chunks:
                source = (
                    f"gateway:{self.alias}:{self.dimensions}\n{chunk['title']}\n{chunk['text']}"
                )
                chunk["hash"] = hashlib.sha256(source.encode()).hexdigest()
            stale = [c for c in self.chunks if stored.get(c["id"]) != c["hash"]]
            if stale:
                vectors = self.embed(
                    [c["text"] for c in stale], "RETRIEVAL_DOCUMENT", [c["title"] for c in stale]
                )
                with db.transaction():
                    for chunk, vector in zip(stale, vectors, strict=True):
                        db.execute(
                            f"INSERT INTO {self.table} (id, title, text, hash, embedding) "
                            "VALUES (%s, %s, %s, %s, %s::vector) ON CONFLICT (id) DO UPDATE SET "
                            "title=EXCLUDED.title, text=EXCLUDED.text, hash=EXCLUDED.hash, "
                            "embedding=EXCLUDED.embedding, updated=now()",
                            (
                                chunk["id"],
                                chunk["title"],
                                chunk["text"],
                                chunk["hash"],
                                as_vector(vector),
                            ),
                        )
            current = [c["id"] for c in self.chunks]
            db.execute(f"DELETE FROM {self.table} WHERE NOT (id = ANY(%s))", (current,))
            self.synced = True

    def vector_search(self, db: psycopg.Connection, query: str, limit: int) -> list[dict]:
        self.sync(db)
        (vector,) = self.embed([query], "RETRIEVAL_QUERY")
        rows = db.execute(
            f"SELECT id, title, text, 1 - (embedding <=> %s::vector) AS similarity "
            f"FROM {self.table} ORDER BY embedding <=> %s::vector LIMIT %s",
            (as_vector(vector), as_vector(vector), limit),
        ).fetchall()
        return [{"id": i, "title": t, "text": x, "similarity": round(s, 3)} for i, t, x, s in rows]


class Retriever:
    """All of an agent's knowledge sets, searched together and fused with RRF."""

    def __init__(self, sets: list[KnowledgeSet]):
        self.sets = sets

    @property
    def description(self) -> str:
        return " ".join(s.description for s in self.sets if s.description)

    def search(self, db: psycopg.Connection | None, query: str, limit: int = 5) -> list[dict]:
        rankings, mode = [], "hybrid"
        for knowledge in self.sets:  # semantic first, then keyword (same order as before)
            try:
                if db is None:
                    raise ValueError("no database")
                rankings.append(knowledge.vector_search(db, query, limit * 2))
            except (psycopg.Error, HTTPError, URLError, TimeoutError, KeyError, ValueError):
                mode = "keyword-only (vector search unavailable)"
            rankings.append(knowledge.bm25.search(query, limit * 2))
        fused: dict[str, dict] = {}
        for ranking in rankings:
            for rank, chunk in enumerate(ranking):
                entry = fused.setdefault(chunk["id"], {**chunk, "rrf": 0.0})
                entry["rrf"] += 1 / (RRF_K + rank + 1)
                if "similarity" in chunk:
                    entry["similarity"] = chunk["similarity"]
        results = sorted(fused.values(), key=lambda c: c["rrf"], reverse=True)[:limit]
        for result in results:
            result["mode"] = mode
        return results
