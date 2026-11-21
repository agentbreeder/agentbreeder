"""Agent state in Postgres: customer notes, tickets, turns and the agent's own event log.

As `runtime/adk@1.3.0`'s `memory.py` (memory schema 2, unchanged DDL), plus `history()`: a kit
runtime keeps no framework session store, so a conversation's history is read back from the
`turns` table this module already writes (spec §7.5).

The container is stateless (AgentBreeder rejects images with volumes); Postgres runs separately,
so data survives redeploys. `audit_events` here is the agent's event log, NOT AgentBreeder's
deploy audit chain.
"""

from __future__ import annotations

import threading
import uuid

import psycopg
from psycopg.types.json import Jsonb

ANONYMOUS = "guest"  # the customer id used when a request names none
HISTORY_TURNS = 20  # turns replayed into a kit turn (spec §7.5)
HISTORY_BYTES = 64 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id BIGSERIAL PRIMARY KEY, customer TEXT NOT NULL, note TEXT NOT NULL,
  created TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS notes_customer ON notes (customer, created);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, customer TEXT NOT NULL, session TEXT NOT NULL, priority TEXT NOT NULL,
  summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  created TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY, session TEXT NOT NULL, customer TEXT NOT NULL, message TEXT NOT NULL,
  reply TEXT NOT NULL, trace_id TEXT, span_id TEXT, latency_ms INTEGER NOT NULL,
  model_calls INTEGER NOT NULL, tools JSONB NOT NULL, sources JSONB NOT NULL,
  evidence JSONB NOT NULL, retrieval TEXT NOT NULL, prompt_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL, error TEXT, created TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS turns_created ON turns (created);
CREATE TABLE IF NOT EXISTS audit_events (
  id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), turn_id TEXT,
  session TEXT, customer TEXT, actor TEXT NOT NULL, action TEXT NOT NULL,
  detail JSONB NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS audit_events_at ON audit_events (at);
-- Append-only for the application: UPDATE, DELETE and TRUNCATE raise. The database owner can
-- still drop the trigger, so this prevents accidents and app bugs; it is not tamper-proof.
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_events is append-only'; END $$;
CREATE OR REPLACE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
CREATE OR REPLACE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
"""

TABLES = ("notes", "tickets", "turns", "audit_events")


def owner_managed(db) -> bool:
    """True when every runtime table is already visible (the database owner created them and
    granted this role only what it uses): then the runtime runs no DDL. A partial set is an
    error: an unqualified CREATE could otherwise put a shadow copy in the role's own schema."""
    (found,) = db.execute(
        "SELECT count(to_regclass(name)) FROM unnest(%s::text[]) AS name", (list(TABLES),)
    ).fetchone()
    if 0 < found < len(TABLES):
        raise RuntimeError("some runtime tables exist and some don't; create all or none")
    return found == len(TABLES)


_schema_ready = False
_schema_lock = threading.Lock()


class Memory:
    def __init__(self, database_url: str, events: bool = True):
        global _schema_ready
        self.events_enabled = events
        self.db = psycopg.connect(database_url, connect_timeout=5, autocommit=True)
        if not _schema_ready:  # created on first use, so the agent starts even if the DB is late
            with _schema_lock, self.db.transaction():
                # Concurrent DDL from parallel first requests deadlocks; serialize it across
                # threads (lock) and processes/replicas (advisory lock).
                self.db.execute("SELECT pg_advisory_xact_lock(727001)")
                if not _schema_ready:
                    if not owner_managed(self.db):
                        self.db.execute(SCHEMA)
                    _schema_ready = True

    def close(self) -> None:
        self.db.close()

    def notes(self, customer: str) -> list[str]:
        if customer == ANONYMOUS:
            return []  # one shared identity: notes would leak between anonymous visitors
        rows = self.db.execute(
            "SELECT note FROM (SELECT note, created FROM notes WHERE customer=%s "
            "ORDER BY created DESC LIMIT 20) recent ORDER BY created",
            (customer,),
        )
        return [note for (note,) in rows]

    def history(self, customer: str, session: str) -> list[tuple[str, str]]:
        """The (customer, session) conversation's last HISTORY_TURNS answered turns, oldest
        first, as (message, reply); at most HISTORY_BYTES of text, dropping the oldest first.
        Failed turns (an error, no reply) are left out. Another customer's turns under the same
        session id are never read."""
        rows = self.db.execute(
            "SELECT message, reply FROM turns WHERE customer=%s AND session=%s "
            "AND error IS NULL AND reply <> '' ORDER BY created DESC LIMIT %s",
            (customer, session, HISTORY_TURNS),
        ).fetchall()
        kept: list[tuple[str, str]] = []
        size = 0
        for message, reply in rows:  # newest first
            size += len(message.encode()) + len(reply.encode())
            if size > HISTORY_BYTES:
                break
            kept.append((message, reply))
        return kept[::-1]

    def add_note(self, customer: str, note: str) -> None:
        if customer == ANONYMOUS:
            raise ValueError("Notes need an identified customer; nothing was saved")
        self.db.execute("INSERT INTO notes (customer, note) VALUES (%s, %s)", (customer, note))

    def audit(
        self, turn: str, session: str, customer: str, actor: str, action: str, detail: dict
    ) -> None:
        if not self.events_enabled:
            return
        self.db.execute(
            "INSERT INTO audit_events (turn_id, session, customer, actor, action, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (turn, session, customer, actor, action, Jsonb(detail)),
        )

    def record_turn(self, **turn) -> None:
        columns = ["id" if k == "turn" else k for k in turn]
        values = [Jsonb(v) if isinstance(v, (list, dict)) else v for v in turn.values()]
        placeholders = ", ".join(["%s"] * len(values))
        self.db.execute(
            f"INSERT INTO turns ({', '.join(columns)}) VALUES ({placeholders})", values
        )

    def open_ticket(self, customer: str, session: str, priority: str, summary: str) -> str:
        ticket = "T-" + uuid.uuid4().hex[:8].upper()
        self.db.execute(
            "INSERT INTO tickets (id, customer, session, priority, summary) "
            "VALUES (%s, %s, %s, %s, %s)",
            (ticket, customer, session, priority, summary),
        )
        return ticket
