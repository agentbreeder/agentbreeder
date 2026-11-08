"""Ops dashboard (http://127.0.0.1:8790) + the online-eval worker in a background thread.

Read-only over Postgres: KPIs, latency and eval trends, tool usage, recent turns with scores and
Phoenix trace links, offline eval runs and the audit log.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import online_evals
from common import OPENER, PHOENIX, PHOENIX_PROJECT, PHOENIX_PUBLIC, connect, phoenix_key

PAGE = Path(__file__).with_name("dashboard.html")
PORT = int(os.getenv("OPS_PORT", "8790"))
_project_id: str | None = None


def phoenix_project_id() -> str | None:
    global _project_id
    if _project_id is None and phoenix_key():
        try:
            request = Request(
                f"{PHOENIX}/v1/projects", headers={"Authorization": "Bearer " + phoenix_key()}
            )
            with OPENER.open(request, timeout=5) as response:
                for project in json.load(response)["data"]:
                    if project["name"] == PHOENIX_PROJECT:
                        _project_id = project["id"]
        except (URLError, TimeoutError, KeyError, ValueError):
            return None
    return _project_id


def rows(db, sql: str, params=()) -> list[dict]:
    cursor = db.execute(sql, params)
    names = [c.name for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def summary(hours: int) -> dict:
    window = (f"{hours} hours",)
    bucket = "minute" if hours <= 2 else "hour" if hours <= 72 else "day"
    with connect() as db:
        kpis = rows(
            db,
            """
          SELECT count(*) AS turns, count(DISTINCT session) AS sessions,
                 count(DISTINCT customer) AS customers,
                 percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                 percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                 avg(model_calls) AS avg_model_calls,
                 sum(prompt_tokens) AS prompt_tokens, sum(output_tokens) AS output_tokens,
                 avg((error IS NOT NULL)::int) AS error_rate,
                 avg((tools ? 'escalate_to_human')::int) AS escalation_rate,
                 avg((tools ? 'web_search')::int) AS web_search_rate
          FROM turns WHERE created > now() - %s::interval AND customer NOT LIKE 'eval-%%'""",
            window,
        )[0]
        scores = {
            r["name"]: r
            for r in rows(
                db,
                """
          SELECT e.name, avg(e.score) AS mean, count(*) AS n
          FROM online_evals e JOIN turns t ON t.id = e.turn_id
          WHERE t.created > now() - %s::interval AND e.score IS NOT NULL
          GROUP BY e.name""",
                window,
            )
        }
        series = rows(
            db,
            f"""
          WITH t AS (
            SELECT date_trunc('{bucket}', created) AS bucket, count(*) AS turns,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms
            FROM turns WHERE created > now() - %s::interval AND customer NOT LIKE 'eval-%%'
            GROUP BY 1),
          e AS (
            SELECT date_trunc('{bucket}', t.created) AS bucket,
                   avg(e.score) FILTER (WHERE e.name = 'groundedness') AS groundedness,
                   avg(e.score) FILTER (WHERE e.name = 'relevance') AS relevance,
                   avg(e.score) FILTER (WHERE e.name = 'policy') AS policy
            FROM online_evals e JOIN turns t ON t.id = e.turn_id
            WHERE t.created > now() - %s::interval AND t.customer NOT LIKE 'eval-%%'
            GROUP BY 1)
          SELECT t.*, e.groundedness, e.relevance, e.policy
          FROM t LEFT JOIN e USING (bucket) ORDER BY bucket""",
            window * 2,
        )
        tools = rows(
            db,
            """
          SELECT tool, count(*) AS n FROM turns, jsonb_array_elements_text(tools) AS tool
          WHERE created > now() - %s::interval AND customer NOT LIKE 'eval-%%'
          GROUP BY tool ORDER BY n DESC""",
            window,
        )
        retrieval = rows(
            db,
            """
          SELECT retrieval AS mode, count(*) AS n FROM turns
          WHERE created > now() - %s::interval AND customer NOT LIKE 'eval-%%'
          GROUP BY 1 ORDER BY n DESC""",
            window,
        )
        recent = rows(
            db,
            """
          SELECT t.id, t.created, t.customer, t.session, t.message, t.reply, t.latency_ms,
                 t.model_calls, t.tools, t.trace_id, t.error,
                 (SELECT jsonb_object_agg(name, jsonb_build_object('score', score, 'label', label,
                         'explanation', explanation)) FROM online_evals WHERE turn_id = t.id)
                   AS evals
          FROM turns t WHERE t.customer NOT LIKE 'eval-%%' ORDER BY t.created DESC LIMIT 40""",
        )
        flagged = rows(
            db,
            """
          SELECT t.created, t.customer, t.message, t.reply, t.trace_id, e.name, e.score, e.label,
                 e.explanation
          FROM online_evals e JOIN turns t ON t.id = e.turn_id
          WHERE e.kind = 'llm' AND e.score < 0.7 ORDER BY t.created DESC LIMIT 20""",
        )
        runs = rows(
            db,
            """
          SELECT id, started, label, cases, passed, pass_rate, mean_correctness, p50_latency_ms,
                 judge_model FROM eval_runs WHERE finished IS NOT NULL
                   AND coalesce(label, '') NOT LIKE '%%self-test%%'
          ORDER BY started DESC LIMIT 20""",
        )
        latest = (
            rows(
                db,
                """
          SELECT case_id, category, passed, checks, correctness, explanation, reply, latency_ms,
                 trace_id FROM eval_results WHERE run_id = %s ORDER BY passed, case_id""",
                (runs[0]["id"],),
            )
            if runs
            else []
        )
        audit = rows(
            db,
            """
          SELECT id, at, actor, action, customer, session, turn_id, detail
          FROM audit_events ORDER BY id DESC LIMIT 60""",
        )
        audit_counts = rows(
            db,
            """
          SELECT action, count(*) AS n FROM audit_events WHERE at > now() - %s::interval
          GROUP BY action ORDER BY n DESC""",
            window,
        )
        approvals = rows(
            db,
            """
          SELECT r.id, r.order_id, r.customer_id, c.name AS customer_name, r.reason, r.status,
                 r.requested_at, r.decided_at, r.decided_by, r.decision_note, r.session,
                 o.status AS order_status, o.total_cents,
                 (SELECT string_agg(i.quantity || ' x ' || p.name, ', ')
                    FROM order_items i JOIN products p ON p.sku = i.sku
                   WHERE i.order_id = r.order_id) AS items,
                 (SELECT t.trace_id FROM turns t WHERE t.id = r.turn_id) AS trace_id
          FROM cancellation_requests r JOIN orders o ON o.id = r.order_id
          JOIN customers c ON c.id = r.customer_id
          ORDER BY (r.status = 'pending') DESC, r.requested_at DESC LIMIT 50""",
        )
    project = phoenix_project_id()
    return {
        "hours": hours,
        "bucket": bucket,
        "kpis": kpis,
        "scores": scores,
        "series": series,
        "tools": tools,
        "retrieval": retrieval,
        "recent": recent,
        "flagged": flagged,
        "runs": runs,
        "latest_run": latest,
        "audit": audit,
        "audit_counts": audit_counts,
        "approvals": approvals,
        "phoenix": {"url": PHOENIX_PUBLIC, "project_id": project},
    }


def decide(request_id: str, decision: str, approver: str, note: str) -> tuple[int, dict]:
    """Human decision on a cancellation request. Approval cancels the order, but only if it is
    still unshipped, re-checked under row locks so a concurrent shipment can't be cancelled."""
    with connect() as db, db.transaction():
        row = db.execute(
            "SELECT order_id, customer_id, status, session FROM "
            "cancellation_requests WHERE id = %s FOR UPDATE",
            (request_id,),
        ).fetchone()
        if not row:
            return 404, {"error": "no such request"}
        order_id, customer, status, session = row
        if status != "pending":
            return 409, {"error": f"request already {status}"}
        outcome = "rejected"
        if decision == "approve":
            (order_status,) = db.execute(
                "SELECT status FROM orders WHERE id = %s FOR UPDATE", (order_id,)
            ).fetchone()
            if order_status in ("pending", "paid"):
                db.execute("UPDATE orders SET status = 'cancelled' WHERE id = %s", (order_id,))
                outcome = "approved"
            else:
                note = f"order is already {order_status}; cannot cancel"
        db.execute(
            "UPDATE cancellation_requests SET status = %s, decided_at = now(), "
            "decided_by = %s, decision_note = %s WHERE id = %s",
            (outcome, approver, note or None, request_id),
        )
        db.execute(
            "INSERT INTO audit_events (session, customer, actor, action, detail) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                session,
                customer,
                f"staff:{approver}",
                f"cancellation_{outcome}",
                json.dumps({"request": request_id, "order": order_id, "note": note}),
            ),
        )
    return 200, {"request": request_id, "order": order_id, "status": outcome, "note": note}


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return float(value) if hasattr(value, "__float__") else str(value)


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            self.send(403, b"forbidden", "text/plain")
            return
        url = urlparse(self.path)
        if url.path == "/healthz":
            self.send(200, b"ok", "text/plain")
        elif url.path == "/":
            self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/summary":
            try:
                hours = max(1, min(int(parse_qs(url.query).get("hours", ["24"])[0]), 24 * 90))
            except ValueError:
                hours = 24
            body = json.dumps(summary(hours), default=encode).encode()
            self.send(200, body, "application/json")
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        """POST /api/cancellations/<id>/decision {"decision": "approve"|"reject", "approver",
        "note"}. CSRF: requires a JSON body plus a custom header (forces a CORS preflight this
        server never approves), a matching Origin when sent, and a loopback Host."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        origin = self.headers.get("Origin")
        parts = urlparse(self.path).path.strip("/").split("/")
        if (
            host not in ("127.0.0.1", "localhost")
            or self.headers.get("X-Northwind-Ops") != "1"
            or (self.headers.get("Content-Type") or "").split(";")[0] != "application/json"
            or (origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"))
            or len(parts) != 4
            or parts[:2] != ["api", "cancellations"]
            or parts[3] != "decision"
        ):
            self.send(403, b'{"error":"forbidden"}', "application/json")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size)) if 0 < size <= 4096 else {}
            decision = body["decision"]
            approver = str(body["approver"]).strip()[:64]
            note = str(body.get("note", "")).strip()[:500]
            if decision not in ("approve", "reject") or not approver:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.send(
                400, b'{"error":"send decision (approve|reject) and approver"}', "application/json"
            )
            return
        status, result = decide(parts[2], decision, approver, note)
        self.send(status, json.dumps(result).encode(), "application/json")

    def log_message(self, format: str, *args) -> None:
        pass


if __name__ == "__main__":
    threading.Thread(
        target=online_evals.run_forever,
        daemon=True,
        kwargs={"log": lambda m: print(m, flush=True)},
    ).start()
    host = os.getenv("OPS_HOST", "127.0.0.1")
    print(f"Ops dashboard on http://{host}:{PORT} (online evals running)", flush=True)
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()
